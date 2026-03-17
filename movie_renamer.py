#!/usr/bin/env python3
"""
Movie Folder Renamer for Jellyfin
Processes movie folders and renames them to Jellyfin convention:
- Folder: Movie_Name (year)
- Video: Movie_Name (year) [imdbid-ttXXXXXX].ext
"""

import os
import re
import time
import requests
import argparse
import json
import subprocess
from bs4 import BeautifulSoup
from urllib.parse import quote_plus
import shutil

HOME_DIR = os.path.expanduser("~")
DEFAULT_BACKLOG_FILE = os.path.join(HOME_DIR, "backlog.txt")
DEFAULT_SUCCESS_FILE = os.path.join(HOME_DIR, "success.txt")

def transliterate_german(text):
    """Transliterate German umlauts and ß to ASCII equivalents"""
    transliterations = {
        'ä': 'ae',
        'ö': 'oe', 
        'ü': 'ue',
        'Ä': 'Ae',
        'Ö': 'Oe',
        'Ü': 'Ue',
        'ß': 'ss'
    }
    for char, replacement in transliterations.items():
        text = text.replace(char, replacement)
    return text

# Parse command-line arguments
parser = argparse.ArgumentParser(description='Movie Folder Renamer for Jellyfin')
parser.add_argument('--input', type=str, help='Read movies from input file')
parser.add_argument('--generate-inputfile-from', type=str, help='Scan directory and generate input file')
parser.add_argument('--fileoutput', type=str, default='.', help='Directory to place generated input file (default: current directory)')
parser.add_argument(
    '--backlog-file',
    type=str,
    default=DEFAULT_BACKLOG_FILE,
    help='File to write movies without video files to (default: ~/backlog.txt)'
)
parser.add_argument(
    '--success-file',
    type=str,
    default=DEFAULT_SUCCESS_FILE,
    help='File to write successfully processed movies to (default: ~/success.txt)'
)
parser.add_argument('--execute', action='store_true', help='Actually rename files (disable dry-run)')
parser.add_argument('--debug', action='store_true', help='Enable verbose IMDB search debugging')
args = parser.parse_args()

# Configuration
BASE_PATH = "/mnt/nfs/huddle/media/Filme"
DRY_RUN = not args.execute  # Set to False if --execute is used
WAIT_TIME = 1  # Seconds between IMDB requests to be respectful
VIDEO_EXTENSIONS = {
    '.mkv', '.mp4', '.avi', '.mov', '.wmv', '.flv', '.webm', '.m4v', '.m2ts', '.mts', '.ts'
}
TEXT_METADATA_EXTENSIONS = {'.nfo', '.txt', '.xml'}

def derive_related_output_path(base_file, suffix):
    """Create sibling output file path based on the configured base file."""
    base_root, base_ext = os.path.splitext(base_file)
    extension = base_ext or '.txt'
    return f"{base_root}{suffix}{extension}"

def is_real_video_file(file_path):
    """Check whether a file is actually a video file, not just named like one."""
    _, ext = os.path.splitext(file_path)
    if ext.lower() not in VIDEO_EXTENSIONS:
        return False

    try:
        result = subprocess.run(
            ['file', '--brief', '--mime-type', file_path],
            capture_output=True,
            text=True,
            check=False
        )
    except OSError:
        return True

    mime_type = result.stdout.strip().lower()
    return mime_type.startswith('video/')

def extract_year_candidates(text):
    """Extract plausible movie years from free text."""
    if not text:
        return []
    return re.findall(r'\b((?:19|20)\d{2})\b', text)

def clean_search_term(folder_name):
    """Clean folder name for IMDB search"""
    clean_name = folder_name

    # Remove container metadata and common release tags.
    clean_name = re.sub(r'\s*\[\s*.*?\s*\]', ' ', clean_name)
    clean_name = re.sub(r'\s*\{\s*.*?\s*\}', ' ', clean_name)
    clean_name = re.sub(r'\s*\(\s*(?:19|20)\d{2}\s*\)\s*', ' ', clean_name)
    clean_name = re.sub(
        r'\b(?:german|dubbed|uncut|director(?:s)?\s*cut|extended|remastered|'
        r'bluray|brrip|bdrip|dvdrip|webrip|web-dl|xvid|x264|x265|h\.?264|h\.?265|'
        r'1080p|720p|2160p|uhd|hd|sd|ac3|dts)\b',
        ' ',
        clean_name,
        flags=re.IGNORECASE
    )
    clean_name = clean_name.replace('.', ' ')
    clean_name = clean_name.replace('_', ' ')
    clean_name = clean_name.replace('-', ' ')
    clean_name = re.sub(r'\s+', ' ', clean_name).strip()
    return clean_name

def has_year_in_folder_name(folder_name):
    """Check if folder name contains a year in parentheses"""
    return bool(re.search(r'\(\d{4}\)', folder_name))

def extract_year_from_folder_name(folder_name):
    """Extract year from folder name if present."""
    year_match = re.search(r'\((\d{4})\)', folder_name)
    return year_match.group(1) if year_match else None

def collect_local_year_clues(folder_path, debug=False):
    """Collect year hints from filenames and small metadata files inside a movie folder."""
    clue_years = []

    try:
        for root, dirs, files in os.walk(folder_path):
            dirs[:] = [d for d in dirs if not d.startswith('.') and not d.startswith('@')]

            for file_name in files:
                file_path = os.path.join(root, file_name)
                _, ext = os.path.splitext(file_name)
                ext = ext.lower()

                for year in extract_year_candidates(file_name):
                    clue_years.append(year)

                if ext in TEXT_METADATA_EXTENSIONS:
                    try:
                        # Small metadata files are enough for disambiguation hints.
                        with open(file_path, 'r', encoding='utf-8', errors='ignore') as handle:
                            text = handle.read(20000)
                        for year in extract_year_candidates(text):
                            clue_years.append(year)
                    except OSError:
                        continue
    except OSError:
        return []

    unique_years = sorted(set(clue_years))
    if debug and unique_years:
        print(f"  Found local year clues: {', '.join(unique_years)}")
    return unique_years

def collect_local_search_clues(folder_path, original_folder_name, debug=False):
    """Collect extra search phrases from filenames and relevant metadata lines."""
    clue_terms = []
    target_words = set(normalize_title_for_match(original_folder_name))

    try:
        for root, dirs, files in os.walk(folder_path):
            dirs[:] = [d for d in dirs if not d.startswith('.') and not d.startswith('@')]

            for file_name in files:
                file_path = os.path.join(root, file_name)
                file_stem, ext = os.path.splitext(file_name)
                ext = ext.lower()

                cleaned_stem = clean_search_term(file_stem)
                if cleaned_stem:
                    clue_terms.append(cleaned_stem)

                if ext in TEXT_METADATA_EXTENSIONS:
                    try:
                        with open(file_path, 'r', encoding='utf-8', errors='ignore') as handle:
                            for line in handle.read(20000).splitlines():
                                cleaned_line = clean_search_term(line)
                                if not cleaned_line:
                                    continue
                                line_words = set(normalize_title_for_match(cleaned_line))
                                if not line_words or not target_words:
                                    continue
                                overlap = target_words & line_words
                                if len(overlap) >= max(1, min(2, len(target_words))):
                                    clue_terms.append(cleaned_line)
                    except OSError:
                        continue
    except OSError:
        return []

    unique_terms = []
    for term in clue_terms:
        if term and term not in unique_terms:
            unique_terms.append(term)

    if debug and unique_terms:
        print(f"  Found local search clues: {', '.join(unique_terms[:5])}")
    return unique_terms[:5]

def sanitize_title_for_fs(title):
    """Normalize title to filesystem-safe Jellyfin style title token."""
    title = transliterate_german(title or '')
    title = re.sub(r'[\[\]\{\}]', ' ', title)
    title = re.sub(r'[._-]+', ' ', title)
    title = re.sub(r'[^A-Za-z0-9 ]+', ' ', title)
    title = re.sub(r'\s+', ' ', title).strip()
    return title.replace(' ', '_')

def normalize_title_for_match(title):
    """Normalize a title for fuzzy title comparison."""
    title = transliterate_german((title or '').lower())
    title = re.sub(r'[\(\)\[\]\{\}]', ' ', title)
    title = re.sub(r'[^a-z0-9 ]+', ' ', title)
    words = [word for word in title.split() if word]
    stop_words = {
        'a', 'an', 'and', 'das', 'dem', 'den', 'der', 'des', 'die', 'ein', 'eine', 'einer',
        'eines', 'im', 'in', 'of', 'the', 'und', 'von', 'zu', 'zum', 'zur'
    }
    filtered_words = [word for word in words if word not in stop_words]
    return filtered_words or words

def titles_match(search_title, candidate_title):
    """Check whether an IMDB candidate title is a close enough title match."""
    search_words = set(normalize_title_for_match(search_title))
    candidate_words = set(normalize_title_for_match(candidate_title))

    if not search_words or not candidate_words:
        return False

    if search_words.issubset(candidate_words):
        return True

    overlap = search_words & candidate_words
    if len(search_words) <= 2:
        return overlap == search_words

    minimum_overlap = len(search_words) - 1
    return len(overlap) >= minimum_overlap

def extract_imdb_title(soup):
    """Extract display title from IMDB movie page."""
    h1 = soup.find('h1')
    if h1:
        title_text = h1.get_text(" ", strip=True)
        title_text = re.sub(r'^Original title:\s*', '', title_text, flags=re.IGNORECASE).strip()
        title_text = re.sub(r'\s*\((?:19|20)\d{2}\)\s*$', '', title_text).strip()
        if title_text:
            return title_text

    meta_og = soup.find('meta', attrs={'property': 'og:title'})
    if meta_og and meta_og.get('content'):
        title_text = re.sub(r'\s*\((?:19|20)\d{2}\).*$', '', meta_og['content']).strip()
        if title_text:
            return title_text

    if soup.title and soup.title.string:
        title_text = re.sub(r'\s*-\s*IMDb.*$', '', soup.title.string).strip()
        title_text = re.sub(r'\s*\((?:19|20)\d{2}\)\s*$', '', title_text).strip()
        if title_text:
            return title_text
    return None

def extract_imdb_year(soup):
    """Extract release year from IMDB page."""
    meta_og = soup.find('meta', attrs={'property': 'og:title'})
    if meta_og and meta_og.get('content'):
        match = re.search(r'\((\d{4})\)', meta_og['content'])
        if match:
            return match.group(1)

    for script in soup.find_all('script', attrs={'type': 'application/ld+json'}):
        raw = script.string or script.get_text()
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except Exception:
            continue
        nodes = data if isinstance(data, list) else [data]
        for node in nodes:
            if not isinstance(node, dict):
                continue
            date_published = node.get('datePublished')
            if date_published:
                match = re.search(r'(\d{4})', str(date_published))
                if match:
                    return match.group(1)

    page_text = soup.get_text(" ", strip=True)
    match = re.search(r'\b((?:19|20)\d{2})\b', page_text)
    return match.group(1) if match else None

def fetch_imdb_movie_details(imdb_id, debug=False):
    """Fetch IMDB movie detail page and prefer German-localized title when available."""
    movie_url = f"https://www.imdb.com/title/{imdb_id}/"
    base_headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                      '(KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36'
    }

    def fetch_soup(language_header):
        headers = dict(base_headers)
        headers['Accept-Language'] = language_header
        response = requests.get(movie_url, headers=headers, timeout=12)
        response.raise_for_status()
        return BeautifulSoup(response.content, 'html.parser')

    try:
        soup_de = fetch_soup('de-DE,de;q=0.9,en-US;q=0.8,en;q=0.7')
        soup_en = fetch_soup('en-US,en;q=0.9')
    except Exception as exc:
        if debug:
            print(f"  Error fetching IMDB details for {imdb_id}: {exc}")
        return None

    title_de = extract_imdb_title(soup_de)
    title_en = extract_imdb_title(soup_en) or title_de
    year = extract_imdb_year(soup_de) or extract_imdb_year(soup_en)

    if not year or not (title_de or title_en):
        return None

    preferred_title = title_de or title_en
    return {
        'title': preferred_title,
        'title_de': title_de,
        'title_en': title_en,
        'year': year,
        'imdb_id': imdb_id
    }

def search_imdb(original_folder_name, folder_name_for_year, clue_years=None, clue_terms=None, debug=False):
    """Search IMDB for movie and return result with year and ID, or None if ambiguous"""
    try:
        search_base = clean_search_term(original_folder_name)

        search_terms = [
            search_base,
            search_base.split(' - ')[0].strip(),
            ' '.join(search_base.split()[:3]),
        ]
        if clue_years:
            for clue_year in clue_years:
                search_terms.append(f"{search_base} {clue_year}")
        if clue_terms:
            search_terms.extend(clue_terms)

        # Preserve order while dropping empty/duplicate search terms.
        search_terms = list(dict.fromkeys(term.strip() for term in search_terms if term.strip()))

        all_results = []
        seen_ids = set()
        results_by_id = {}
        candidate_scores = {}

        for search_index, search_term in enumerate(search_terms):
            if not search_term.strip():
                continue

            if debug:
                print(f"  Trying search: '{search_term}'")

            is_clue_term = bool(clue_terms and search_term in clue_terms)
            is_year_hint_term = bool(clue_years and any(year in search_term for year in clue_years))
            term_weight = 1
            if search_index == 0:
                term_weight += 3
            elif search_index == 1:
                term_weight += 2
            if is_year_hint_term:
                term_weight += 1
            if is_clue_term:
                term_weight += 3

            search_url = f"https://www.imdb.com/find?q={quote_plus(search_term)}&s=tt"
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
            }
            response = requests.get(search_url, headers=headers, timeout=10)
            response.raise_for_status()
            soup = BeautifulSoup(response.content, 'html.parser')

            title_links = soup.find_all('a', href=re.compile(r'/title/tt\d+/'))
            if debug:
                print(f"DEBUG: Processing {len(title_links)} title links")

            for i, link in enumerate(title_links[:8]):
                href = link.get('href', '')
                if debug:
                    print(f"DEBUG: Link {i}: {href}")
                title_match = re.search(r'/title/(tt\d+)/', href)
                if title_match:
                    imdb_id = title_match.group(1)
                    if imdb_id in results_by_id:
                        details = results_by_id[imdb_id]
                    else:
                        if imdb_id in seen_ids:
                            continue
                        seen_ids.add(imdb_id)
                        details = fetch_imdb_movie_details(imdb_id, debug=debug)
                        if not details:
                            continue
                        if not titles_match(search_base, details['title']) and not titles_match(search_base, details.get('title_en')):
                            if debug:
                                print(f"  Ignoring loosely related result: {details['title']} ({details['year']})")
                            continue
                        results_by_id[imdb_id] = details
                    score = candidate_scores.setdefault(imdb_id, {
                        'score': 0,
                        'hits': 0,
                        'clue_hits': 0,
                        'year_distance': None
                    })
                    score['score'] += term_weight
                    score['hits'] += 1
                    if is_clue_term:
                        score['clue_hits'] += 1
                    if clue_years:
                        distances = [abs(int(details['year']) - int(year)) for year in clue_years]
                        if distances:
                            best_distance = min(distances)
                            if score['year_distance'] is None or best_distance < score['year_distance']:
                                score['year_distance'] = best_distance
                    if imdb_id not in [result['imdb_id'] for result in all_results]:
                        all_results.append(details)
                    if debug:
                        print(
                            f"  ✅ Found candidate: {details['title']} ({details['year']}) "
                            f"[imdbid-{imdb_id}]"
                        )

            results = soup.find_all('td', class_='result_text')
            for result in results[:8]:
                link = result.find('a')
                if not link:
                    continue
                href = link.get('href', '')
                title_match = re.search(r'/title/(tt\d+)/', href)
                if title_match:
                    imdb_id = title_match.group(1)
                    if imdb_id in results_by_id:
                        details = results_by_id[imdb_id]
                    else:
                        if imdb_id in seen_ids:
                            continue
                        seen_ids.add(imdb_id)
                        details = fetch_imdb_movie_details(imdb_id, debug=debug)
                        if not details:
                            continue
                        if not titles_match(search_base, details['title']) and not titles_match(search_base, details.get('title_en')):
                            continue
                        results_by_id[imdb_id] = details
                    score = candidate_scores.setdefault(imdb_id, {
                        'score': 0,
                        'hits': 0,
                        'clue_hits': 0,
                        'year_distance': None
                    })
                    score['score'] += term_weight
                    score['hits'] += 1
                    if is_clue_term:
                        score['clue_hits'] += 1
                    if clue_years:
                        distances = [abs(int(details['year']) - int(year)) for year in clue_years]
                        if distances:
                            best_distance = min(distances)
                            if score['year_distance'] is None or best_distance < score['year_distance']:
                                score['year_distance'] = best_distance
                    if imdb_id not in [result['imdb_id'] for result in all_results]:
                        all_results.append(details)

        unique_results = all_results

        if not unique_results:
            if debug:
                print(f"  ❌ No suitable matches found on IMDB for any search term")
            return None

        years = set(r['year'] for r in unique_results)
        target_year = extract_year_from_folder_name(folder_name_for_year)
        clue_year_matches = []
        if clue_years and not target_year:
            clue_year_set = set(clue_years)
            clue_year_matches = [result for result in unique_results if result['year'] in clue_year_set]

        if len(all_results) > 1 and len(years) > 1:
            if target_year:
                for result in unique_results:
                    if result['year'] == target_year:
                        if debug:
                            print(
                                f"  Selected version matching folder year ({target_year}): "
                                f"{result['title']} ({result['year']})"
                            )
                        return result
                if debug:
                    print(
                        f"  No IMDB result matches the year in folder name ({target_year}). Skipping."
                    )
                return None

            ranked_results = sorted(
                unique_results,
                key=lambda result: (
                    candidate_scores.get(result['imdb_id'], {}).get('clue_hits', 0),
                    candidate_scores.get(result['imdb_id'], {}).get('score', 0),
                    candidate_scores.get(result['imdb_id'], {}).get('hits', 0),
                    -(
                        candidate_scores.get(result['imdb_id'], {}).get('year_distance', 999)
                        if candidate_scores.get(result['imdb_id'], {}).get('year_distance', None) is not None
                        else 999
                    )
                ),
                reverse=True
            )
            best_result = ranked_results[0]
            best_metrics = candidate_scores.get(best_result['imdb_id'], {})
            tied_results = [
                result for result in ranked_results
                if (
                    candidate_scores.get(result['imdb_id'], {}).get('clue_hits', 0),
                    candidate_scores.get(result['imdb_id'], {}).get('score', 0),
                    candidate_scores.get(result['imdb_id'], {}).get('hits', 0),
                    (
                        candidate_scores.get(result['imdb_id'], {}).get('year_distance', 999)
                        if candidate_scores.get(result['imdb_id'], {}).get('year_distance', None) is not None
                        else 999
                    )
                ) == (
                    best_metrics.get('clue_hits', 0),
                    best_metrics.get('score', 0),
                    best_metrics.get('hits', 0),
                    best_metrics.get('year_distance', 999)
                    if best_metrics.get('year_distance', None) is not None
                    else 999
                )
            ]
            if len(tied_results) == 1 and (
                best_metrics.get('clue_hits', 0) > 0 or best_metrics.get('hits', 0) > 1
            ):
                if debug:
                    print(
                        f"  Selected highest-confidence IMDB result: "
                        f"{best_result['title']} ({best_result['year']})"
                    )
                return best_result
            if debug:
                print(
                    f"  Multiple versions found with different years {sorted(years)}, "
                    f"but no year in folder name. Skipping to avoid ambiguity."
                )
            return None

        result = unique_results[0]
        if debug:
            print(f"  Selected: {result['title']} ({result['year']}) [imdbid-{result['imdb_id']}]")
        return result

    except Exception as e:
        if debug:
            print(f"  Error searching IMDB: {e}")
        return None

def debug_imdb_search(movie_title):
    """Debug IMDB search to see what we get"""
    try:
        search_url = f"https://www.imdb.com/find?q={quote_plus(movie_title)}&s=tt"
        print(f"DEBUG: Searching URL: {search_url}")
        
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
        
        response = requests.get(search_url, headers=headers, timeout=10)
        print(f"DEBUG: Response status: {response.status_code}")
        
        soup = BeautifulSoup(response.content, 'html.parser')
        
        # Look for different possible result structures
        results = soup.find_all('td', class_='result_text')
        print(f"DEBUG: Found {len(results)} result_text elements")
        
        if results:
            print("DEBUG: First result text:", results[0].get_text()[:200])
            link = results[0].find('a')
            if link:
                print("DEBUG: First link href:", link.get('href'))
        
        # Also try finding any links with title patterns
        title_links = soup.find_all('a', href=re.compile(r'/title/tt\d+/'))
        print(f"DEBUG: Found {len(title_links)} title links")
        if title_links:
            print("DEBUG: First title link:", title_links[0].get('href'))
            print("DEBUG: First title text:", title_links[0].get_text().strip())
            print("DEBUG: First title parent:", title_links[0].parent.get_text()[:200] if title_links[0].parent else "No parent")
            print("DEBUG: First title grandparent:", title_links[0].parent.parent.get_text()[:200] if title_links[0].parent and title_links[0].parent.parent else "No grandparent")
            
        return response.content[:1000]  # Return first 1000 chars for inspection
        
    except Exception as e:
        print(f"DEBUG: Error: {e}")
        return None

def scan_directory_for_movies(base_dir):
    """Scan directory for movie folders and return list of folder names"""
    movies = []
    try:
        for item in sorted(os.listdir(base_dir)):
            item_path = os.path.join(base_dir, item)
            if os.path.isdir(item_path) and not item.startswith('.'):
                # Exclude common non-movie files/folders if any
                movies.append(item)
    except OSError as e:
        print(f"Error scanning directory {base_dir}: {e}")
    return movies

def get_video_files(folder_path):
    """Get video files from folder and subfolders, return list of (relative_path, filename) tuples"""
    video_files = []
    
    try:
        for root, dirs, files in os.walk(folder_path):
            # Skip hidden/system subfolders (e.g. Synology .@__thumb, @eaDir).
            dirs[:] = [d for d in dirs if not d.startswith('.') and not d.startswith('@')]
            for file in files:
                file_path = os.path.join(root, file)
                if is_real_video_file(file_path):
                    rel_path = os.path.relpath(root, folder_path)
                    if rel_path == '.':
                        rel_path = ''
                    video_files.append((rel_path, file))
    except OSError:
        pass
        
    return video_files

def create_folder_name(movie_info):
    """Create Jellyfin-compliant folder name from lookup title and year."""
    clean_title = sanitize_title_for_fs(movie_info['title'])
    return f"{clean_title} ({movie_info['year']})"

def create_video_base_name(folder_name, movie_info):
    """Create video file base name (without extension)."""
    return f"{folder_name} [imdbid-{movie_info['imdb_id']}]"

def extract_part_number(filename):
    """Extract part number from a multipart filename, e.g. cd1/part2/disc3."""
    stem = os.path.splitext(filename)[0]
    patterns = [
        r'(?:^|[\s._-])(cd|disc|disk|dvd|part|pt)[\s._-]*0*(\d{1,2})(?:$|[\s._-])',
        r'(?:^|[\s._-])0*(\d{1,2})[\s._-]*(?:of)[\s._-]*0*\d{1,2}(?:$|[\s._-])',
    ]
    for pattern in patterns:
        match = re.search(pattern, stem, flags=re.IGNORECASE)
        if not match:
            continue
        number_group = 2 if match.lastindex and match.lastindex >= 2 else 1
        try:
            return int(match.group(number_group))
        except (TypeError, ValueError):
            return None
    return None

def sort_video_files(video_files):
    """Sort by detected part number first (if available), then by path/name."""
    def key(item):
        rel_path, filename = item
        part = extract_part_number(filename)
        has_part = 0 if part is not None else 1
        part_value = part if part is not None else 999
        return (has_part, part_value, rel_path.lower(), filename.lower())
    return sorted(video_files, key=key)

def rename_movie_folder(folder_name, debug=False):
    """Process and rename a single movie folder"""
    print(f"\nProcessing: {folder_name}")
    
    folder_path = os.path.join(BASE_PATH, folder_name)
    
    # Check if folder exists
    if not os.path.isdir(folder_path):
        print(f"  Folder not found: {folder_path}")
        return False
    
    # Use original folder name for IMDB search
    if debug:
        print(f"  Searching IMDB for: '{folder_name}'")

    clue_years = []
    clue_terms = []
    if not has_year_in_folder_name(folder_name):
        clue_years = collect_local_year_clues(folder_path, debug=debug)
        clue_terms = collect_local_search_clues(folder_path, folder_name, debug=debug)
    
    # Search IMDB
    movie_info = search_imdb(
        folder_name,
        folder_name,
        clue_years=clue_years,
        clue_terms=clue_terms,
        debug=debug
    )
    if not movie_info:
        if debug:
            print(f"  Skipping: Could not find match on IMDB")
        return False
    
    # Create new folder name strictly from lookup title.
    new_folder_name = create_folder_name(movie_info)
    new_folder_path = os.path.join(BASE_PATH, new_folder_name)
    
    print(f"  New folder name: {new_folder_name}")
    
    # Get video files (now returns list of (rel_path, filename) tuples)
    video_files = get_video_files(folder_path)
    print(f"  Found {len(video_files)} video file(s)")
    for rel_path, filename in video_files:
        if rel_path:
            print(f"    {rel_path}/{filename}")
        else:
            print(f"    {filename}")

    result_info = {
        'original_folder': folder_name,
        'new_folder': new_folder_name,
        'has_videos': len(video_files) > 0,
        'video_moves': []
    }
    
    # Always rename folder if we have movie info
    if DRY_RUN:
        print("  [DRY RUN] Would rename folder and video files")
        print(f"    Folder: {folder_name} -> {new_folder_name}")
        
        all_video_files = sort_video_files(video_files)
        
        for idx, (rel_path, filename) in enumerate(all_video_files):
            _, ext = os.path.splitext(filename)
            base_name = create_video_base_name(new_folder_name, movie_info)
            cd_suffix = f"-cd{idx + 1}" if len(all_video_files) > 1 else ""
            new_video_name = f"{base_name}{cd_suffix}{ext.lower()}"
            source_display = f"{rel_path}/{filename}" if rel_path else filename
            result_info['video_moves'].append((source_display, new_video_name))
            if rel_path:
                print(f"    Move and rename: {rel_path}/{filename} -> {new_video_name}")
            else:
                print(f"    Rename: {filename} -> {new_video_name}")
    else:
        try:
            # Rename folder unless already in the target name.
            current_folder_path = folder_path
            if os.path.abspath(folder_path) != os.path.abspath(new_folder_path):
                if os.path.exists(new_folder_path):
                    print(f"  Error renaming: destination folder already exists: {new_folder_path}")
                    return False
                shutil.move(folder_path, new_folder_path)
                current_folder_path = new_folder_path
                print(f"  Renamed folder: {folder_name} -> {new_folder_name}")
            else:
                print("  Folder already matches target naming")

            all_video_files = sort_video_files(video_files)
            subfolders_to_remove = set()
            
            for idx, (rel_path, filename) in enumerate(all_video_files):
                _, ext = os.path.splitext(filename)
                base_name = create_video_base_name(new_folder_name, movie_info)
                cd_suffix = f"-cd{idx + 1}" if len(all_video_files) > 1 else ""
                new_video_name = f"{base_name}{cd_suffix}{ext.lower()}"
                
                # Source path
                if rel_path:
                    old_video_path = os.path.join(current_folder_path, rel_path, filename)
                    rel_cursor = rel_path
                    while rel_cursor and rel_cursor != '.':
                        subfolders_to_remove.add(os.path.join(current_folder_path, rel_cursor))
                        next_cursor = os.path.dirname(rel_cursor)
                        rel_cursor = '' if next_cursor == rel_cursor else next_cursor
                else:
                    old_video_path = os.path.join(current_folder_path, filename)
                
                new_video_path = os.path.join(current_folder_path, new_video_name)
                
                if os.path.exists(old_video_path):
                    if os.path.abspath(old_video_path) == os.path.abspath(new_video_path):
                        source_display = f"{rel_path}/{filename}" if rel_path else filename
                        result_info['video_moves'].append((source_display, new_video_name))
                        continue
                    shutil.move(old_video_path, new_video_path)
                    source_display = f"{rel_path}/{filename}" if rel_path else filename
                    result_info['video_moves'].append((source_display, new_video_name))
                    print(f"    Renamed video: {filename} -> {new_video_name}")
            
            # Remove empty subfolders
            for subfolder_path in sorted(subfolders_to_remove, reverse=True):  # Remove deepest first
                try:
                    if not os.listdir(subfolder_path):  # Check if empty
                        os.rmdir(subfolder_path)
                        print(f"    Removed empty subfolder: {os.path.basename(subfolder_path)}")
                except OSError:
                    pass  # Folder not empty or doesn't exist
                    
        except Exception as e:
            print(f"  Error renaming: {e}")
            return False
    
    # Return True if we renamed the folder, regardless of video files
    # But we'll handle backlog logic in main()
    return result_info

def main():
    """Main function"""
    # Handle generate input file mode
    if args.generate_inputfile_from:
        print(f"Scanning directory: {args.generate_inputfile_from}")
        movies = scan_directory_for_movies(args.generate_inputfile_from)
        output_file = os.path.join(args.fileoutput, 'movielist.txt')
        try:
            with open(output_file, 'w') as f:
                for movie in movies:
                    f.write(movie + '\n')
            print(f"Generated input file: {output_file} with {len(movies)} movie folders")
        except Exception as e:
            print(f"Error writing input file: {e}")
        return
    
    # Require input file for processing
    if not args.input:
        parser.error("Either --input or --generate-inputfile-from must be specified")
    
    # Load movies from input file
    try:
        with open(args.input, 'r') as f:
            movies_to_process = [line.strip() for line in f if line.strip()]
        print(f"Loaded {len(movies_to_process)} movies from {args.input}")
    except Exception as e:
        print(f"Error reading input file {args.input}: {e}")
        return
    
    print("Movie Folder Renamer for Jellyfin")
    print("=" * 50)
    print(f"Base path: {BASE_PATH}")
    print(f"Dry run mode: {DRY_RUN}")
    print(f"Processing {len(movies_to_process)} movies")
    
    if DRY_RUN:
        print("\n⚠️  DRY RUN MODE - No files will be actually renamed")
        print("Use --execute to perform actual renaming")
    
    print("\nNaming convention:")
    print("  Folder: Movie_Name (year)")
    print("  Video:  Movie_Name (year) [imdbid-ttXXXXXX].ext")
    
    print("\n" + "=" * 50)
    
    success_count = 0
    skipped_count = 0
    backlog_count = 0
    success_movies = []
    skipped_movies = []
    backlog_movies = []
    
    for i, movie_folder in enumerate(movies_to_process, 1):
        print(f"\n[{i}/{len(movies_to_process)}]", end="")
        
        result = rename_movie_folder(movie_folder, debug=args.debug)
        if result is False:
            # IMDB lookup failed
            skipped_count += 1
            skipped_movies.append(movie_folder)
        else:
            success_count += 1
            success_movies.append(result)
            if not result['has_videos']:
                backlog_count += 1
                backlog_movies.append(movie_folder)
            
        # Be respectful to IMDB
        if i < len(movies_to_process):
            print(f"  Waiting {WAIT_TIME}s before next request...")
            time.sleep(WAIT_TIME)
    
    print("\n" + "=" * 50)
    print("SUMMARY")
    print("=" * 50)
    print(f"Successfully processed: {success_count}")
    print(f"Skipped (no IMDB match): {skipped_count}")
    print(f"Added to backlog (no videos): {backlog_count}")
    print(f"Total: {len(movies_to_process)}")

    # Write success file for successfully processed movies
    if success_movies:
        try:
            with open(args.success_file, 'w') as f:
                for item in success_movies:
                    f.write(f"Folder: {item['original_folder']} -> {item['new_folder']}\n")
                    if item['video_moves']:
                        for old_name, new_name in item['video_moves']:
                            f.write(f"Video: {old_name} -> {new_name}\n")
                    else:
                        f.write("Video: none found\n")
                    f.write("\n")
            print(f"Success list written to: {args.success_file} ({len(success_movies)} entries)")
        except Exception as e:
            print(f"Error writing success file: {e}")
    
    # Write backlog file for movies without video files
    if backlog_movies:
        try:
            with open(args.backlog_file, 'w') as f:
                for movie in backlog_movies:
                    f.write(movie + '\n')
            print(f"\nBacklog written to: {args.backlog_file} ({len(backlog_movies)} entries - no video files found)")
        except Exception as e:
            print(f"Error writing backlog file: {e}")
    
    # Write skipped file for IMDB lookup failures
    if skipped_movies:
        try:
            skipped_file = derive_related_output_path(args.backlog_file, '_skipped')
            with open(skipped_file, 'w') as f:
                for movie in skipped_movies:
                    f.write(movie + '\n')
            print(f"Skipped movies written to: {skipped_file} ({len(skipped_movies)} entries - IMDB lookup failed)")
        except Exception as e:
            print(f"Error writing skipped file: {e}")
    
    if DRY_RUN:
        print(f"\n⚠️  This was a DRY RUN - no files were actually renamed")
        print("Use --execute to perform actual renaming")

if __name__ == "__main__":
    main()
