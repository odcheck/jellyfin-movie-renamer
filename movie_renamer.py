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
import difflib
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
parser.add_argument('--base-path', type=str, help='Base directory containing the listed movie folders (default: input file header or ~)')
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
DRY_RUN = not args.execute  # Set to False if --execute is used
WAIT_TIME = 1  # Seconds between IMDB requests to be respectful
VIDEO_EXTENSIONS = {
    '.mkv', '.mp4', '.avi', '.mov', '.wmv', '.flv', '.webm', '.m4v', '.m2ts', '.mts', '.ts',
    '.mpg', '.mpeg'
}
TEXT_METADATA_EXTENSIONS = {'.nfo', '.txt', '.xml'}

def derive_related_output_path(base_file, suffix):
    """Create sibling output file path based on the configured base file."""
    base_root, base_ext = os.path.splitext(base_file)
    extension = base_ext or '.txt'
    return f"{base_root}{suffix}{extension}"

def resolve_base_path(input_file=None):
    """Resolve the base path for movie processing."""
    if args.base_path:
        return os.path.abspath(os.path.expanduser(args.base_path))

    if input_file:
        try:
            with open(input_file, 'r', encoding='utf-8', errors='ignore') as handle:
                first_line = handle.readline().strip()
            if first_line.startswith('# BASE_PATH='):
                return os.path.abspath(os.path.expanduser(first_line.split('=', 1)[1].strip()))
        except OSError:
            pass

    return HOME_DIR

def read_metadata_text(file_path):
    """Read metadata text using a few common encodings."""
    for encoding in ('utf-8', 'iso-8859-1', 'cp1252'):
        try:
            with open(file_path, 'r', encoding=encoding, errors='strict') as handle:
                return handle.read(20000)
        except (OSError, UnicodeDecodeError):
            continue
    try:
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as handle:
            return handle.read(20000)
    except OSError:
        return ''

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

    # Normalize separators first so release-tag stripping can match words
    # surrounded by underscores, dots, or dashes.
    clean_name = clean_name.replace('.', ' ')
    clean_name = clean_name.replace('_', ' ')
    clean_name = clean_name.replace('-', ' ')

    # Remove container metadata and common release tags.
    clean_name = re.sub(r'\s*\[\s*.*?\s*\]', ' ', clean_name)
    clean_name = re.sub(r'\s*\{\s*.*?\s*\}', ' ', clean_name)
    clean_name = re.sub(r'\s*\(\s*(?:19|20)\d{2}\s*\)\s*', ' ', clean_name)
    clean_name = re.sub(r'\b(?:19|20)\d{2}\b', ' ', clean_name)
    clean_name = re.sub(
        r'\b(?:german|dubbed|uncut|director(?:s)?\s*cut|extended|remastered|'
        r'bluray|brrip|bdrip|dvdrip|webrip|web-dl|xvid|x264|x265|h\.?264|h\.?265|'
        r'1080p|720p|2160p|uhd|hd|sd|ac3|dts|r5|videomann)\b',
        ' ',
        clean_name,
        flags=re.IGNORECASE
    )
    clean_name = re.sub(r'\bby\s+[A-Za-z0-9_-]+\b', ' ', clean_name, flags=re.IGNORECASE)
    clean_name = re.sub(r'\bby\b', ' ', clean_name, flags=re.IGNORECASE)
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
                    text = read_metadata_text(file_path)
                    for year in extract_year_candidates(text):
                        clue_years.append(year)
    except OSError:
        return []

    unique_years = sorted(set(clue_years))
    if debug and unique_years:
        print(f"  Found local year clues: {', '.join(unique_years)}")
    return unique_years

def collect_metadata_title_clues(folder_path, debug=False):
    """Collect likely movie titles from local metadata files."""
    title_clues = []
    title_patterns = [
        r'<title>\s*(.*?)\s*</title>',
        r'<originaltitle>\s*(.*?)\s*</originaltitle>',
        r'<localtitle>\s*(.*?)\s*</localtitle>',
        r'<sorttitle>\s*(.*?)\s*</sorttitle>',
        r'^\s*title\s*[:=]\s*(.+?)\s*$',
        r'^\s*original\s*title\s*[:=]\s*(.+?)\s*$',
        r'^\s*originaltitle\s*[:=]\s*(.+?)\s*$',
        r'^\s*german\s*title\s*[:=]\s*(.+?)\s*$',
        r'^\s*local\s*title\s*[:=]\s*(.+?)\s*$',
        r'^\s*deutscher?\s*titel\s*[:=]\s*(.+?)\s*$',
    ]

    def maybe_add_title(raw_text):
        cleaned_title = clean_search_term(raw_text)
        if not cleaned_title:
            return
        words = normalize_title_for_match(cleaned_title)
        if not words:
            return
        # Ignore lines that are mostly technical metadata instead of title clues.
        technical_tokens = {
            'audio', 'codec', 'container', 'duration', 'episode', 'file', 'format', 'fps',
            'genre', 'language', 'plot', 'rating', 'release', 'runtime', 'sample', 'size',
            'source', 'subtitle', 'summary', 'video'
        }
        if len(words) == 1 and len(words[0]) <= 2:
            title_clues.append(cleaned_title)
            return
        if sum(1 for word in words if word in technical_tokens) >= max(1, len(words) // 2):
            return
        if len(words) <= 10:
            title_clues.append(cleaned_title)

    try:
        for root, dirs, files in os.walk(folder_path):
            dirs[:] = [d for d in dirs if not d.startswith('.') and not d.startswith('@')]

            for file_name in files:
                _, ext = os.path.splitext(file_name)
                if ext.lower() not in TEXT_METADATA_EXTENSIONS:
                    continue

                file_path = os.path.join(root, file_name)
                text = read_metadata_text(file_path)
                if not text:
                    continue

                for pattern in title_patterns:
                    for match in re.findall(pattern, text, flags=re.IGNORECASE | re.MULTILINE):
                        maybe_add_title(match)

                # Also mine generic text content, because many metadata files are inconsistent.
                for line in text.splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    maybe_add_title(line)
                    # Try the right-hand side of free-form key/value lines too.
                    if ':' in line:
                        maybe_add_title(line.split(':', 1)[1])
                    if '=' in line:
                        maybe_add_title(line.split('=', 1)[1])
    except OSError:
        return []

    unique_titles = []
    for title in title_clues:
        if title not in unique_titles:
            unique_titles.append(title)

    if debug and unique_titles:
        print(f"  Found metadata title clues: {', '.join(unique_titles[:5])}")
    return unique_titles[:5]

def collect_imdb_id_clues(folder_path, debug=False):
    """Collect direct IMDb IDs from local metadata files."""
    imdb_ids = []

    try:
        for root, dirs, files in os.walk(folder_path):
            dirs[:] = [d for d in dirs if not d.startswith('.') and not d.startswith('@')]

            for file_name in files:
                _, ext = os.path.splitext(file_name)
                if ext.lower() not in TEXT_METADATA_EXTENSIONS:
                    continue

                file_path = os.path.join(root, file_name)
                text = read_metadata_text(file_path)
                if not text:
                    continue

                imdb_ids.extend(re.findall(r'\b(tt\d{7,9})\b', text, flags=re.IGNORECASE))
    except OSError:
        return []

    unique_ids = []
    for imdb_id in imdb_ids:
        normalized = imdb_id.lower()
        if normalized not in unique_ids:
            unique_ids.append(normalized)

    if debug and unique_ids:
        print(f"  Found metadata IMDb IDs: {', '.join(unique_ids)}")
    return unique_ids

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
                    text = read_metadata_text(file_path)
                    for line in text.splitlines():
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
    title = re.sub(r"['`´]", '', title)
    title = re.sub(r'[\(\)\[\]\{\}]', ' ', title)
    title = re.sub(r'[^a-z0-9 ]+', ' ', title)
    words = [word for word in title.split() if word]
    stop_words = {
        'a', 'an', 'and', 'das', 'dem', 'den', 'der', 'des', 'die', 'ein', 'eine', 'einer',
        'eines', 'im', 'in', 'of', 'the', 'und', 'von', 'zu', 'zum', 'zur'
    }
    filtered_words = [
        word for word in words
        if word not in stop_words and (len(word) > 1 or word.isdigit())
    ]
    return filtered_words or words

def titles_match(search_title, candidate_title):
    """Check whether an IMDB candidate title is a close enough title match."""
    return title_match_score(search_title, candidate_title) > 0

def title_match_score(search_title, candidate_title):
    """Return a quality score for how well a candidate title matches the search title."""
    search_list = normalize_title_for_match(search_title)
    candidate_list = normalize_title_for_match(candidate_title)
    search_words = set(search_list)
    candidate_words = set(candidate_list)

    if not search_words or not candidate_words:
        return 0

    if search_list == candidate_list:
        if len(search_list) == 1:
            return 300
        return 250

    if search_words == candidate_words and len(search_list) <= 4 and len(candidate_list) <= 4:
        order_ratio = difflib.SequenceMatcher(
            None,
            ' '.join(search_list),
            ' '.join(candidate_list)
        ).ratio()
        if order_ratio >= 0.9:
            return 220
        return 0

    if len(search_words) == 1:
        # One-word titles are too ambiguous to match by substring alone.
        return 300 if candidate_words == search_words else 0

    if len(search_words) <= 2:
        if candidate_words == search_words:
            return 250
        if len(candidate_words) == len(search_words):
            search_list = sorted(search_words)
            candidate_list = sorted(candidate_words)
            if all(difflib.SequenceMatcher(None, s, c).ratio() >= 0.85 for s, c in zip(search_list, candidate_list)):
                return 180
        return 0

    overlap = search_words & candidate_words
    if search_words.issubset(candidate_words):
        subseq_index = 0
        for word in candidate_list:
            if subseq_index < len(search_list) and word == search_list[subseq_index]:
                subseq_index += 1
        if subseq_index < len(search_list):
            return 0
        extra_words = candidate_words - search_words
        exact_bonus = 50 if candidate_words == search_words else 0
        return 200 + exact_bonus - (len(extra_words) * 15)

    if candidate_words.issubset(search_words) and len(candidate_words) >= 3:
        subseq_index = 0
        for word in search_list:
            if subseq_index < len(candidate_list) and word == candidate_list[subseq_index]:
                subseq_index += 1
        if subseq_index < len(candidate_list):
            return 0
        missing_words = search_words - candidate_words
        return 170 - (len(missing_words) * 12)

    minimum_overlap = len(search_words) - 1
    if len(overlap) >= minimum_overlap:
        extra_words = candidate_words - search_words
        return 100 + (len(overlap) * 10) - (len(extra_words) * 20)
    return 0

def build_title_variants(title):
    """Build a few safe title variants from a local title string."""
    variants = []
    cleaned_title = clean_search_term(title or '')
    if cleaned_title:
        variants.append(cleaned_title)

        words = cleaned_title.split()
        if len(words) >= 3:
            variants.append(' '.join(words[:3]))

        if ':' in cleaned_title:
            variants.append(cleaned_title.split(':', 1)[0].strip())
        if ' - ' in cleaned_title:
            variants.append(cleaned_title.split(' - ', 1)[0].strip())

    return [variant for variant in dict.fromkeys(variant for variant in variants if variant)]

def candidate_matches_titles(details, title_candidates, base_title=None):
    """Check whether IMDB result matches trusted local titles without ignoring the folder title."""
    return candidate_match_score(details, title_candidates, base_title=base_title) > 0

def candidate_match_score(details, title_candidates, base_title=None):
    """Return the best title-match score, requiring compatibility with the base folder title."""
    candidate_title_de = details.get('title')
    candidate_title_en = details.get('title_en')
    if base_title:
        base_variants = build_title_variants(base_title)
        base_score = 0
        for variant in base_variants:
            base_score = max(
                base_score,
                title_match_score(variant, candidate_title_de),
                title_match_score(variant, candidate_title_en)
            )
        if base_score <= 0:
            return 0
    else:
        base_score = 0

    best_score = 0
    for title in title_candidates:
        best_score = max(
            best_score,
            title_match_score(title, candidate_title_de),
            title_match_score(title, candidate_title_en)
        )
    return max(best_score, base_score)

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

def fetch_imdb_details_from_search(imdb_id, search_terms, debug=False):
    """Recover IMDb details from localized search results when the title page blocks direct fetches."""
    terms = []
    for term in search_terms or []:
        cleaned_term = clean_search_term(term)
        if cleaned_term and cleaned_term not in terms:
            terms.append(cleaned_term)
    if not terms:
        terms.append(imdb_id)
    elif imdb_id not in terms:
        terms.append(imdb_id)

    for search_term in terms:
        search_url = f"https://www.imdb.com/find?q={quote_plus(search_term)}&s=tt"
        try:
            response_de = requests.get(
                search_url,
                headers=build_imdb_search_headers(),
                timeout=10
            )
            response_de.raise_for_status()
            soup_de = BeautifulSoup(response_de.content, 'html.parser')

            response_en = requests.get(
                search_url,
                headers=build_imdb_search_headers('en-US,en;q=0.9'),
                timeout=10
            )
            response_en.raise_for_status()
            soup_en = BeautifulSoup(response_en.content, 'html.parser')
        except Exception as exc:
            if debug:
                print(f"  Search fallback failed for IMDb ID {imdb_id} via '{search_term}': {exc}")
            continue

        link = find_search_result_link(soup_de, imdb_id)
        if not link:
            continue
        candidate = extract_search_result_candidate(link, imdb_id, english_soup=soup_en)
        if candidate:
            if search_term != imdb_id:
                match_score = max(
                    title_match_score(search_term, candidate.get('title')),
                    title_match_score(search_term, candidate.get('title_en'))
                )
                if match_score <= 0:
                    continue
            if debug:
                print(f"  Recovered IMDb details for {imdb_id} from search term '{search_term}'")
            return candidate
    return None

def build_imdb_search_headers(language_header='de-DE,de;q=0.9,en-US;q=0.8,en;q=0.7'):
    """Build IMDb search headers for a chosen localization."""
    return {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                      '(KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36',
        'Accept-Language': language_header
    }

def extract_search_result_title(link):
    """Extract a display title from an IMDb search-result node."""
    title_text = link.get_text(" ", strip=True)
    if not title_text:
        heading = link.find('h3')
        title_text = heading.get_text(" ", strip=True) if heading else ''
    if not title_text:
        aria_label = link.get('aria-label', '')
        match = re.search(r'for\s+(.+)$', aria_label)
        title_text = match.group(1).strip() if match else ''
    return title_text or None

def extract_search_result_year(link):
    """Extract a release year from an IMDb search-result node."""
    year = None
    ancestor = link
    for _ in range(6):
        ancestor = ancestor.parent
        if not ancestor:
            break
        year_match = re.search(r'\b((?:19|20)\d{2})\b', ancestor.get_text(" ", strip=True))
        if year_match:
            year = year_match.group(1)
            break
    return year

def find_search_result_link(soup, imdb_id):
    """Find the best title link for a given IMDb ID within a search-result page."""
    if not soup:
        return None
    preferred_patterns = [
        rf'/title/{imdb_id}/.*ref_=fn_t_',
        rf'/title/{imdb_id}/'
    ]
    for pattern in preferred_patterns:
        link = soup.find('a', href=re.compile(pattern))
        if link:
            return link
    return None

def is_feature_candidate_link(link):
    """Reject obvious podcast/episode/short-form search results."""
    if not link:
        return False

    context_parts = []
    ancestor = link
    for _ in range(4):
        ancestor = ancestor.parent
        if not ancestor:
            break
        context_parts.append(ancestor.get_text(" ", strip=True))
    context_text = ' '.join(context_parts).lower()

    blocked_terms = (
        'podcast', 'rss feed', 'episode', 'folge', 'tv episode',
        'kurzfilm', 'short'
    )
    return not any(term in context_text for term in blocked_terms)

def extract_search_result_candidate(link, imdb_id, english_soup=None):
    """Build a candidate directly from localized IMDb search-result HTML."""
    title_text = extract_search_result_title(link)
    if not title_text:
        return None

    year = extract_search_result_year(link)

    if not year:
        return None

    title_text = re.sub(r'\s*\((?:19|20)\d{2}\)\s*$', '', title_text).strip()
    english_title = title_text
    english_link = find_search_result_link(english_soup, imdb_id)
    if english_link:
        extracted_english_title = extract_search_result_title(english_link)
        if extracted_english_title:
            english_title = re.sub(r'\s*\((?:19|20)\d{2}\)\s*$', '', extracted_english_title).strip()
    return {
        'title': title_text,
        'title_de': title_text,
        'title_en': english_title,
        'year': year,
        'imdb_id': imdb_id
    }

def search_imdb(
    original_folder_name,
    folder_name_for_year,
    clue_years=None,
    clue_terms=None,
    authoritative_titles=None,
    debug=False
):
    """Search IMDB for movie and return result with year and ID, or None if ambiguous"""
    try:
        search_base = clean_search_term(original_folder_name)
        title_candidates = []
        for title in (authoritative_titles or []) + [search_base]:
            if title and title not in title_candidates:
                title_candidates.append(title)

        search_terms = []
        for title in title_candidates:
            search_terms.extend([
                title,
                title.split(' - ')[0].strip(),
                ' '.join(title.split()[:3]),
            ])
        if clue_years:
            for clue_year in clue_years:
                for title in title_candidates:
                    search_terms.append(f"{title} {clue_year}")
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
            headers = build_imdb_search_headers()
            response = requests.get(search_url, headers=headers, timeout=10)
            response.raise_for_status()
            soup = BeautifulSoup(response.content, 'html.parser')

            english_soup = None
            try:
                response_en = requests.get(
                    search_url,
                    headers=build_imdb_search_headers('en-US,en;q=0.9'),
                    timeout=10
                )
                response_en.raise_for_status()
                english_soup = BeautifulSoup(response_en.content, 'html.parser')
            except Exception:
                english_soup = None

            title_links = soup.find_all('a', href=re.compile(r'/title/tt\d+/.*ref_=fn_t_'))
            if debug:
                print(f"DEBUG: Processing {len(title_links)} title links")

            for i, link in enumerate(title_links[:8]):
                href = link.get('href', '')
                if debug:
                    print(f"DEBUG: Link {i}: {href}")
                if not is_feature_candidate_link(link):
                    if debug:
                        print("  Ignoring non-feature result from IMDb search page")
                    continue
                title_match = re.search(r'/title/(tt\d+)/', href)
                if title_match:
                    imdb_id = title_match.group(1)
                    if imdb_id in results_by_id:
                        details = results_by_id[imdb_id]
                    else:
                        if imdb_id in seen_ids:
                            continue
                        details = fetch_imdb_movie_details(imdb_id, debug=debug)
                        if not details:
                            details = extract_search_result_candidate(link, imdb_id, english_soup=english_soup)
                        if not details:
                            continue
                        seen_ids.add(imdb_id)
                        title_score = candidate_match_score(details, title_candidates, base_title=search_base)
                        if title_score <= 0:
                            if debug:
                                print(f"  Ignoring loosely related result: {details['title']} ({details['year']})")
                            continue
                        results_by_id[imdb_id] = details
                    title_score = candidate_match_score(details, title_candidates, base_title=search_base)
                    score = candidate_scores.setdefault(imdb_id, {
                        'score': 0,
                        'hits': 0,
                        'clue_hits': 0,
                        'year_distance': None,
                        'title_score': 0
                    })
                    score['score'] += term_weight + title_score
                    score['hits'] += 1
                    score['title_score'] = max(score['title_score'], title_score)
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
                if not is_feature_candidate_link(link):
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
                        details = fetch_imdb_movie_details(imdb_id, debug=debug)
                        if not details:
                            details = extract_search_result_candidate(link, imdb_id, english_soup=english_soup)
                        if not details:
                            continue
                        seen_ids.add(imdb_id)
                        title_score = candidate_match_score(details, title_candidates, base_title=search_base)
                        if title_score <= 0:
                            continue
                        results_by_id[imdb_id] = details
                    title_score = candidate_match_score(details, title_candidates, base_title=search_base)
                    score = candidate_scores.setdefault(imdb_id, {
                        'score': 0,
                        'hits': 0,
                        'clue_hits': 0,
                        'year_distance': None,
                        'title_score': 0
                    })
                    score['score'] += term_weight + title_score
                    score['hits'] += 1
                    score['title_score'] = max(score['title_score'], title_score)
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

        if len(unique_results) > 1:
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
                    candidate_scores.get(result['imdb_id'], {}).get('title_score', 0),
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
                    candidate_scores.get(result['imdb_id'], {}).get('title_score', 0),
                    candidate_scores.get(result['imdb_id'], {}).get('clue_hits', 0),
                    candidate_scores.get(result['imdb_id'], {}).get('score', 0),
                    candidate_scores.get(result['imdb_id'], {}).get('hits', 0),
                    (
                        candidate_scores.get(result['imdb_id'], {}).get('year_distance', 999)
                        if candidate_scores.get(result['imdb_id'], {}).get('year_distance', None) is not None
                        else 999
                    )
                ) == (
                    best_metrics.get('title_score', 0),
                    best_metrics.get('clue_hits', 0),
                    best_metrics.get('score', 0),
                    best_metrics.get('hits', 0),
                    best_metrics.get('year_distance', 999)
                    if best_metrics.get('year_distance', None) is not None
                    else 999
                )
            ]
            if len(tied_results) == 1 and (
                best_metrics.get('title_score', 0) > 0 or
                best_metrics.get('clue_hits', 0) > 0 or
                best_metrics.get('hits', 0) > 1
            ):
                if debug:
                    print(
                        f"  Selected highest-confidence IMDB result: "
                        f"{best_result['title']} ({best_result['year']})"
                    )
                return best_result
            if debug:
                if len(years) > 1:
                    print(
                        f"  Multiple versions found with different years {sorted(years)}, "
                        f"but no year in folder name. Skipping to avoid ambiguity."
                    )
                else:
                    print("  Multiple similar IMDB results found. Skipping to avoid ambiguity.")
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
        
        headers = build_imdb_search_headers()
        
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

def has_dvd_structure(folder_path):
    """Detect a classic DVD folder that should keep its VIDEO_TS contents untouched."""
    video_ts_path = os.path.join(folder_path, 'VIDEO_TS')
    if not os.path.isdir(video_ts_path):
        return False

    try:
        for item in os.listdir(video_ts_path):
            upper_name = item.upper()
            if upper_name.endswith('.IFO') or upper_name.endswith('.VOB') or upper_name.endswith('.BUP'):
                return True
    except OSError:
        return False
    return False

def has_iso_image(folder_path):
    """Detect ISO disc images that should be preserved like DVD folders."""
    try:
        for item in os.listdir(folder_path):
            item_path = os.path.join(folder_path, item)
            if os.path.isfile(item_path) and item.lower().endswith('.iso'):
                return True
    except OSError:
        return False
    return False

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

def rename_movie_folder(folder_name, base_path, debug=False):
    """Process and rename a single movie folder"""
    print(f"\nProcessing: {folder_name}")
    
    folder_path = os.path.join(base_path, folder_name)
    
    # Check if folder exists
    if not os.path.isdir(folder_path):
        print(f"  Folder not found: {folder_path}")
        return False
    
    # Use original folder name for IMDB search
    if debug:
        print(f"  Searching IMDB for: '{folder_name}'")

    clue_years = collect_local_year_clues(folder_path, debug=debug)
    clue_terms = collect_local_search_clues(folder_path, folder_name, debug=debug)
    metadata_titles = collect_metadata_title_clues(folder_path, debug=debug)
    metadata_imdb_ids = collect_imdb_id_clues(folder_path, debug=debug)
    
    # Search IMDB
    if len(metadata_imdb_ids) == 1:
        if debug:
            print(f"  Using IMDb ID from metadata: {metadata_imdb_ids[0]}")
        movie_info = fetch_imdb_movie_details(metadata_imdb_ids[0], debug=debug)
        if not movie_info:
            movie_info = fetch_imdb_details_from_search(
                metadata_imdb_ids[0],
                [folder_name] + metadata_titles + clue_terms,
                debug=debug
            )
        if not movie_info and debug:
            print("  IMDb ID recovery failed, falling back to title-based search with metadata clues")
    else:
        movie_info = None

    if not movie_info:
        movie_info = search_imdb(
            folder_name,
            folder_name,
            clue_years=clue_years,
            clue_terms=clue_terms,
            authoritative_titles=metadata_titles,
            debug=debug
        )
    if not movie_info:
        if debug:
            print(f"  Skipping: Could not find match on IMDB")
        return False
    
    # Create new folder name strictly from lookup title.
    new_folder_name = create_folder_name(movie_info)
    new_folder_path = os.path.join(base_path, new_folder_name)
    
    print(f"  New folder name: {new_folder_name}")
    
    # Get video files (now returns list of (rel_path, filename) tuples)
    video_files = get_video_files(folder_path)
    dvd_structure = has_dvd_structure(folder_path)
    iso_image = has_iso_image(folder_path)
    print(f"  Found {len(video_files)} video file(s)")
    for rel_path, filename in video_files:
        if rel_path:
            print(f"    {rel_path}/{filename}")
        else:
            print(f"    {filename}")
    if dvd_structure and not video_files:
        print("  Detected DVD VIDEO_TS structure; folder will be renamed but DVD files kept unchanged")
    if iso_image and not video_files:
        print("  Detected ISO image; folder will be renamed but ISO files kept unchanged")

    result_info = {
        'original_folder': folder_name,
        'new_folder': new_folder_name,
        'has_videos': len(video_files) > 0 or dvd_structure or iso_image,
        'dvd_structure': dvd_structure,
        'iso_image': iso_image,
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
        if dvd_structure and not all_video_files:
            print("    DVD structure preserved: VIDEO_TS contents stay unchanged")
        if iso_image and not all_video_files:
            print("    ISO image preserved: disc image stays unchanged")
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

            if dvd_structure and not all_video_files:
                print("    Preserved DVD structure inside VIDEO_TS")
            if iso_image and not all_video_files:
                print("    Preserved ISO image inside movie folder")
            
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
        source_dir = os.path.abspath(os.path.expanduser(args.generate_inputfile_from))
        movies = scan_directory_for_movies(source_dir)
        output_file = os.path.join(args.fileoutput, 'movielist.txt')
        try:
            with open(output_file, 'w', encoding='utf-8') as f:
                f.write(f"# BASE_PATH={source_dir}\n")
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
        with open(args.input, 'r', encoding='utf-8', errors='ignore') as f:
            movies_to_process = [
                line.strip() for line in f
                if line.strip() and not line.strip().startswith('#')
            ]
        print(f"Loaded {len(movies_to_process)} movies from {args.input}")
    except Exception as e:
        print(f"Error reading input file {args.input}: {e}")
        return

    base_path = resolve_base_path(args.input)
    
    print("Movie Folder Renamer for Jellyfin")
    print("=" * 50)
    print(f"Base path: {base_path}")
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
        
        result = rename_movie_folder(movie_folder, base_path, debug=args.debug)
        if result is False:
            # IMDB lookup failed
            skipped_count += 1
            skipped_movies.append({
                'folder': movie_folder,
                'reason': 'No confident IMDB match found from folder, file, and metadata clues'
            })
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
                for item in skipped_movies:
                    f.write(f"{item['folder']} | {item['reason']}\n")
            print(f"Skipped movies written to: {skipped_file} ({len(skipped_movies)} entries - IMDB lookup failed)")
        except Exception as e:
            print(f"Error writing skipped file: {e}")
    
    if DRY_RUN:
        print(f"\n⚠️  This was a DRY RUN - no files were actually renamed")
        print("Use --execute to perform actual renaming")

if __name__ == "__main__":
    main()
