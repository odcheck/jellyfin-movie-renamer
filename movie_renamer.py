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
from urllib.parse import quote, quote_plus
import shutil

HOME_DIR = os.path.expanduser("~")
DEFAULT_BACKLOG_FILE = os.path.join(HOME_DIR, "backlog.txt")
DEFAULT_SUCCESS_FILE = os.path.join(HOME_DIR, "success.txt")
WIKIDATA_GERMAN_TITLE_CACHE = {}

def soup_from_response(response):
    """Parse HTML without BeautifulSoup UnicodeDammit warning spam."""
    encoding = response.encoding or getattr(response, 'apparent_encoding', None) or 'utf-8'
    text = response.content.decode(encoding, errors='replace')
    return BeautifulSoup(text, 'html.parser')

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
parser.add_argument('--limit', type=int, default=None, help='Process at most N movies from the input file')
parser.add_argument('--offset', type=int, default=0, help='Skip the first N movies in the input file')
args = parser.parse_args()

# Configuration
DRY_RUN = not args.execute  # Set to False if --execute is used
WAIT_TIME = 1  # Seconds between IMDB requests to be respectful
VIDEO_EXTENSIONS = {
    '.mkv', '.mp4', '.avi', '.mov', '.wmv', '.flv', '.webm', '.m4v', '.m2ts', '.mts', '.ts',
    '.mpg', '.mpeg'
}
TEXT_METADATA_EXTENSIONS = {'.nfo', '.txt', '.xml'}
MIN_MAIN_VIDEO_BYTES = 100 * 1024 * 1024
EXTRA_TYPE_ALIASES = {
    'trailer': 'trailer',
    'trailers': 'trailer',
    'sample': 'sample',
    'samples': 'sample',
    'scene': 'scene',
    'scenes': 'scene',
    'clip': 'clip',
    'clips': 'clip',
    'interview': 'interview',
    'interviews': 'interview',
    'behindthescenes': 'behindthescenes',
    'behindthescene': 'behindthescenes',
    'deleted': 'deleted',
    'deletedscene': 'deletedscene',
    'deletedscenes': 'deletedscene',
    'featurette': 'featurette',
    'featurettes': 'featurette',
    'short': 'short',
    'shorts': 'short',
    'other': 'other',
    'extras': 'extra',
    'extra': 'extra',
}

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

def extract_franchise_number(text):
    """Extract a sequel/franchise number from a search string if present."""
    if not text:
        return None
    cleaned = transliterate_german(text.lower())
    for pattern, value in (
        (r'\bpart\s*([1-9]|i{1,3}|iv|v|vi{0,3}|ix|x)\b', None),
        (r'\bteil\s*([1-9]|i{1,3}|iv|v|vi{0,3}|ix|x)\b', None),
        (r'\bchapter\s*([1-9]|i{1,3}|iv|v|vi{0,3}|ix|x)\b', None),
        (r'\b([1-9])\b', None),
        (r'\b(iii|ii|iv|ix|viii|vii|vi|v|x)\b', None),
    ):
        match = re.search(pattern, cleaned)
        if not match:
            continue
        token = match.group(1)
        roman_map = {'i': 1, 'ii': 2, 'iii': 3, 'iv': 4, 'v': 5, 'vi': 6, 'vii': 7, 'viii': 8, 'ix': 9, 'x': 10}
        if token.isdigit():
            return int(token)
        return roman_map.get(token)
    return None

def candidate_franchise_number(title):
    """Extract a sequel/franchise number from a candidate title when obvious."""
    if not title:
        return None
    cleaned = transliterate_german(title.lower())
    patterns = [
        r'\bpart\s*([1-9]|i{1,3}|iv|v|vi{0,3}|ix|x)\b',
        r'\bchapter\s*([1-9]|i{1,3}|iv|v|vi{0,3}|ix|x)\b',
        r'\b([1-9])\b',
        r'\b(iii|ii|iv|ix|viii|vii|vi|v|x)\b',
    ]
    roman_map = {'i': 1, 'ii': 2, 'iii': 3, 'iv': 4, 'v': 5, 'vi': 6, 'vii': 7, 'viii': 8, 'ix': 9, 'x': 10}
    for pattern in patterns:
        match = re.search(pattern, cleaned)
        if not match:
            continue
        token = match.group(1)
        if token.isdigit():
            return int(token)
        return roman_map.get(token)
    return None

def clean_search_term(folder_name):
    """Clean folder name for IMDB search"""
    clean_name = folder_name

    # Split glued title parts like "JungfrauenquelleDas" or "SchweigenI"
    # before separator normalization removes the signal.
    clean_name = re.sub(r'([a-zäöüß])([A-ZÄÖÜ][a-zäöüß])', r'\1 \2', clean_name)
    clean_name = re.sub(r'([a-zäöüß]{3,})([IVX]{1,4})\b', r'\1 \2', clean_name)

    # Strip trailing scene-release group suffixes while the original dash
    # structure is still intact, e.g. "- GMA - pij" or "-EPiC - Le".
    clean_name = re.sub(r'(?:\s*-\s*[A-Za-z]{2,5}){1,3}\s*$', ' ', clean_name)

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
        r'1080p|720p|2160p|uhd|hd|sd|ac3|dts|r5|ld|proper|repack|readnfo|limited|'
        r'videomann)\b',
        ' ',
        clean_name,
        flags=re.IGNORECASE
    )
    # Strip XML/ASCII-art noise down to title words before final collapsing.
    clean_name = re.sub(r'[^0-9A-Za-zÄÖÜäöüß ]+', ' ', clean_name)
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
            rel_root = os.path.relpath(root, folder_path)
            if rel_root and rel_root != '.':
                for year in extract_year_candidates(rel_root):
                    clue_years.append(year)

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

    def maybe_add_title(raw_text, explicit=False):
        if not raw_text:
            return
        if raw_text.lstrip().startswith('<?xml'):
            return
        if not explicit and raw_text.lstrip().startswith('<') and raw_text.rstrip().endswith('>'):
            return
        # Skip obvious ASCII art / binary garbage / low-signal lines from scene NFOs.
        signal_chars = re.findall(r'[A-Za-z0-9ÄÖÜäöüß]', raw_text)
        if len(signal_chars) < 4:
            return
        cleaned_title = clean_search_term(raw_text)
        if not cleaned_title:
            return
        if cleaned_title.lower() in {'true', 'false'}:
            return
        words = normalize_title_for_match(cleaned_title)
        if not words:
            return
        # Ignore lines that are mostly technical metadata instead of title clues.
        technical_tokens = {
            'audio', 'codec', 'container', 'duration', 'episode', 'file', 'format', 'fps',
            'genre', 'genres', 'language', 'plot', 'rating', 'release', 'runtime', 'sample',
            'size', 'source', 'subtitle', 'summary', 'video', 'added', 'actor', 'actors',
            'awards', 'budget', 'country', 'description', 'director', 'directors', 'drama',
            'fantasy', 'frankreich', 'france', 'genres', 'horror', 'imdbrating', 'languagecode',
            'mystery', 'outline', 'persons', 'plot', 'popularity', 'productionyear', 'revenue',
            'role', 'roles', 'runningtime', 'sorttitle', 'studio', 'studios', 'synopsis',
            'tag', 'tags', 'thriller', 'tmdbid', 'votes', 'writerslist'
        }
        if explicit and len(words) == 1 and len(words[0]) <= 2:
            title_clues.append(cleaned_title)
            return
        if not explicit and len(words) <= 1:
            return
        if not explicit and len(words) > 5 and re.search(r'[,:;.!?]', raw_text):
            return
        if sum(1 for word in words if word in technical_tokens) >= max(1, len(words) // 2):
            return
        max_words = 10 if explicit else 6
        if len(words) <= max_words:
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
                        maybe_add_title(match, explicit=True)

                # Also mine generic text content, because many metadata files are inconsistent.
                for line in text.splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    if line.startswith('<') and line.endswith('>'):
                        tag_match = re.match(r'^<([A-Za-z_]+)>\s*([^<]+?)\s*</\1>$', line)
                        if tag_match:
                            tag_name = clean_search_term(tag_match.group(1)).lower()
                            tag_value = tag_match.group(2)
                            if any(token in tag_name for token in ('title', 'titel', 'original', 'sort', 'local')):
                                maybe_add_title(tag_value, explicit=True)
                        continue
                    if '=' in line:
                        key = clean_search_term(line.split('=', 1)[0]).lower()
                        if key and not any(token in key for token in ('title', 'titel', 'original', 'sort', 'local')):
                            continue
                    maybe_add_title(line, explicit=False)
                    # Try the right-hand side of free-form key/value lines too.
                    if ':' in line:
                        key = clean_search_term(line.split(':', 1)[0]).lower()
                        if key and ('title' in key or 'titel' in key or 'original' in key or 'sort' in key):
                            maybe_add_title(line.split(':', 1)[1], explicit=True)
                    if '=' in line:
                        key = clean_search_term(line.split('=', 1)[0]).lower()
                        if key and ('title' in key or 'titel' in key or 'original' in key or 'sort' in key):
                            maybe_add_title(line.split('=', 1)[1], explicit=True)
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
    ignored_stem_pattern = re.compile(
        r'^(?:backdrop\d*|banner\d*|cdart\d*|clearart\d*|fanart\d*|folder|logo\d*|movie|poster\d*|sample)$',
        flags=re.IGNORECASE
    )

    try:
        for root, dirs, files in os.walk(folder_path):
            dirs[:] = [d for d in dirs if not d.startswith('.') and not d.startswith('@')]
            rel_root = os.path.relpath(root, folder_path)
            if rel_root and rel_root != '.':
                cleaned_root = clean_search_term(rel_root)
                if cleaned_root and not ignored_stem_pattern.match(cleaned_root.lower()):
                    clue_terms.append(cleaned_root)

            for file_name in files:
                file_path = os.path.join(root, file_name)
                file_stem, ext = os.path.splitext(file_name)
                ext = ext.lower()

                cleaned_stem = clean_search_term(file_stem)
                if cleaned_stem and not ignored_stem_pattern.match(cleaned_stem.lower()):
                    clue_terms.append(cleaned_stem)

                if ext in TEXT_METADATA_EXTENSIONS:
                    text = read_metadata_text(file_path)
                    for line in text.splitlines():
                        line = line.strip()
                        if not line:
                            continue
                        if line.startswith('<') and line.endswith('>'):
                            tag_match = re.match(r'^<([A-Za-z_]+)>\s*([^<]+?)\s*</\1>$', line)
                            if tag_match:
                                candidate_line = tag_match.group(2)
                            else:
                                continue
                        elif ':' in line:
                            key = clean_search_term(line.split(':', 1)[0]).lower()
                            candidate_line = line.split(':', 1)[1] if (
                                'title' in key or 'titel' in key or 'original' in key or 'sort' in key
                            ) else line
                        elif '=' in line:
                            key = clean_search_term(line.split('=', 1)[0]).lower()
                            if key and not any(token in key for token in ('title', 'titel', 'original', 'sort', 'local')):
                                continue
                            candidate_line = line.split('=', 1)[1]
                        else:
                            candidate_line = line
                        cleaned_line = clean_search_term(candidate_line)
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
    search_numbers = {word for word in search_list if word.isdigit()}
    candidate_numbers = {word for word in candidate_list if word.isdigit()}

    if not search_words or not candidate_words:
        return 0

    search_joined = ''.join(search_list)
    candidate_joined = ''.join(candidate_list)
    if search_joined and search_joined == candidate_joined:
        return 235
    if len(search_list) >= 2 and candidate_list and candidate_list[0] == search_joined:
        return 155
    if len(candidate_list) >= 2 and search_list and search_list[0] == candidate_joined:
        return 155

    # If both titles carry explicit numbers, they must agree. This prevents
    # false matches like "Ice Age 2" -> "2012: Ice Age".
    if search_numbers and candidate_numbers and search_numbers != candidate_numbers:
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

    if len(candidate_words) == 1 and len(search_list) >= 3:
        anchor = candidate_list[0]
        if search_list[0] == anchor and len(anchor) >= 7 and len(search_list[1]) > 2:
            return 135

    if len(search_list) == len(candidate_list) and len(search_list) >= 3:
        positional_ratios = [
            difflib.SequenceMatcher(None, search_word, candidate_word).ratio()
            for search_word, candidate_word in zip(search_list, candidate_list)
        ]
        if all(ratio >= 0.85 for ratio in positional_ratios):
            return 185
        if sum(1 for ratio in positional_ratios if ratio >= 0.85) >= len(search_list) - 1:
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

def build_raw_search_variants(title):
    """Build search variants from the raw folder title before cleanup erases separators."""
    raw_title = transliterate_german(title or '')
    split_parts = [part for part in re.split(r'\s*[-:]\s*', raw_title) if part.strip()]
    variants = []
    if split_parts:
        cleaned = clean_search_term(split_parts[0])
        if cleaned:
            variants.append(cleaned)
    return [variant for variant in dict.fromkeys(variants)]

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
        return soup_from_response(response)

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

def is_generic_imdb_title(title):
    """Reject generic IMDb labels that are not actual movie titles."""
    normalized = clean_search_term(title or '').lower()
    generic_phrases = (
        'besetzung und crew mitglieder',
        'cast and crew',
        'cast crew',
        'full cast crew',
        'mitglieder',
    )
    return any(phrase in normalized for phrase in generic_phrases)

def expand_search_term_variants(term):
    """Add a few safe sequel-title variants for search recovery."""
    cleaned = clean_search_term(term or '')
    variants = [cleaned] if cleaned else []

    replacements = [
        (r'\bthe next chapter\b', '2'),
        (r'\bpart ii\b', '2'),
        (r'\bpart 2\b', '2'),
    ]
    for pattern, replacement in replacements:
        if re.search(pattern, cleaned, flags=re.IGNORECASE):
            variant = re.sub(pattern, replacement, cleaned, flags=re.IGNORECASE).strip()
            if variant and variant not in variants:
                variants.append(variant)
    return variants

def is_presentable_local_title(term):
    """Reject technical or placeholder strings as final display titles."""
    cleaned = clean_search_term(term)
    if not cleaned:
        return False
    if cleaned.lower() in {'true', 'false'}:
        return False
    words = normalize_title_for_match(cleaned)
    if not words or len(words) > 8:
        return False
    lowered = cleaned.lower()
    if any(token in lowered for token in ('videosource', 'audiosource', 'resolution', 'iconfile', 'hotkey', 'prop3')):
        return False
    return True

def choose_preferred_local_title(candidate, search_terms, matched_search_term=None):
    """Prefer a clean local title variant over a noisy folder slug when possible."""
    candidate_titles = [candidate.get('title'), candidate.get('title_en')]
    cleaned_terms = []
    for term in search_terms or []:
        cleaned = clean_search_term(term)
        if cleaned and cleaned not in cleaned_terms:
            cleaned_terms.append(cleaned)

    cleaned_matched_term = clean_search_term(matched_search_term) if matched_search_term else None
    if cleaned_matched_term and is_presentable_local_title(cleaned_matched_term):
        if len(normalize_title_for_match(cleaned_matched_term)) >= 2:
            return cleaned_matched_term

    best_term = None
    best_score = 0
    for term in cleaned_terms:
        if not is_presentable_local_title(term):
            continue
        score = max(title_match_score(term, movie_title) for movie_title in candidate_titles if movie_title)
        if score > best_score:
            best_score = score
            best_term = term

    if best_score > 0 and best_term:
        return best_term
    if cleaned_matched_term and is_presentable_local_title(cleaned_matched_term):
        return cleaned_matched_term
    return candidate.get('title')

def normalize_wikidata_german_title(title):
    """Strip common Wikidata/Wikipedia disambiguators from German article titles."""
    cleaned = (title or '').strip()
    if not cleaned:
        return None
    cleaned = re.sub(
        r'\s*\((?:\d{4}|film|fernsehfilm|fernsehserie|miniserie|serie|zeichentrickfilm|'
        r'dokumentarfilm|us-amerikanischer film|deutscher film|britischer film|'
        r'franzoesischer film|französischer film|italienischer film|japanischer film|'
        r'suedkoreanischer film|südkoreanischer film|spanischer film|oesterreichischer film|'
        r'österreichischer film|schweizer film)[^)]*\)\s*$',
        '',
        cleaned,
        flags=re.IGNORECASE
    ).strip()
    return cleaned or None

def fetch_wikidata_german_title(imdb_id, debug=False):
    """Look up a German title by IMDb ID via Wikidata as a fallback when IMDb JSON is English-only."""
    if not imdb_id:
        return None
    if imdb_id in WIKIDATA_GERMAN_TITLE_CACHE:
        return WIKIDATA_GERMAN_TITLE_CACHE[imdb_id]

    query = f'''
    SELECT ?deTitle WHERE {{
      ?item wdt:P345 "{imdb_id}".
      ?deArticle schema:about ?item ;
                 schema:isPartOf <https://de.wikipedia.org/> ;
                 schema:name ?deTitle .
    }} LIMIT 1
    '''
    try:
        response = requests.get(
            'https://query.wikidata.org/sparql',
            params={'format': 'json', 'query': query},
            headers={'User-Agent': 'Mozilla/5.0'},
            timeout=12
        )
        response.raise_for_status()
        data = response.json()
        bindings = data.get('results', {}).get('bindings', [])
        if bindings:
            title = bindings[0].get('deTitle', {}).get('value')
            normalized = normalize_wikidata_german_title(title)
            WIKIDATA_GERMAN_TITLE_CACHE[imdb_id] = normalized
            return normalized
    except Exception as exc:
        if debug:
            print(f"  Wikidata German title lookup failed for {imdb_id}: {exc}")

    WIKIDATA_GERMAN_TITLE_CACHE[imdb_id] = None
    return None

def apply_preferred_output_title(movie_info, folder_name, metadata_titles=None, clue_terms=None, debug=False):
    """Prefer strong local German titles and fall back to Wikidata German titles by IMDb ID."""
    if not movie_info:
        return movie_info

    localized = dict(movie_info)
    metadata_titles = list(metadata_titles or [])
    clue_terms = list(clue_terms or [])
    local_terms = [folder_name] + metadata_titles + clue_terms
    metadata_locked = False

    preferred_metadata = choose_preferred_local_title(
        localized,
        metadata_titles,
        matched_search_term=None
    ) if metadata_titles else None
    if preferred_metadata:
        metadata_score = max(
            title_match_score(preferred_metadata, localized.get('title') or ''),
            title_match_score(preferred_metadata, localized.get('title_en') or '')
        )
        if metadata_score > 0:
            localized['title'] = preferred_metadata
            localized['title_de'] = preferred_metadata
            metadata_locked = True

    if not metadata_locked:
        preferred_local = choose_preferred_local_title(
            localized,
            local_terms,
            matched_search_term=folder_name
        )
        if preferred_local:
            local_score = max(
                title_match_score(preferred_local, localized.get('title') or ''),
                title_match_score(preferred_local, localized.get('title_en') or '')
            )
            if local_score > 0:
                localized['title'] = preferred_local
                localized['title_de'] = preferred_local

    german_title = None if metadata_locked else fetch_wikidata_german_title(localized.get('imdb_id'), debug=debug)
    if german_title:
        localized['title'] = german_title
        localized['title_de'] = german_title
        if debug:
            print(f"  Using German title from Wikidata: {german_title}")

    return localized

def fetch_imdb_details_from_search(imdb_id, search_terms, debug=False):
    """Recover IMDb details from localized search results when the title page blocks direct fetches."""
    terms = []
    for term in search_terms or []:
        for variant in expand_search_term_variants(term):
            if variant and variant not in terms:
                terms.append(variant)
    if not terms:
        terms.append(imdb_id)
    elif imdb_id not in terms:
        terms.append(imdb_id)

    for search_term in terms:
        suggestion_candidates = fetch_imdb_suggestion_candidates(
            search_term,
            debug=debug,
            prefer_search_title=False,
            allow_video=True
        )
        for candidate in suggestion_candidates:
            if candidate.get('imdb_id') != imdb_id:
                continue
            candidate = dict(candidate)
            candidate['title'] = choose_preferred_local_title(candidate, terms, matched_search_term=search_term)
            candidate['title_de'] = candidate['title']
            if debug:
                print(
                    f"  Recovered IMDb details for {imdb_id} from search term "
                    f"'{search_term}' via suggestion JSON"
                )
            return candidate

        search_url = f"https://www.imdb.com/find?q={quote_plus(search_term)}&s=tt"
        try:
            response_de = requests.get(
                search_url,
                headers=build_imdb_search_headers(),
                timeout=10
            )
            response_de.raise_for_status()
            soup_de = soup_from_response(response_de)

            response_en = requests.get(
                search_url,
                headers=build_imdb_search_headers('en-US,en;q=0.9'),
                timeout=10
            )
            response_en.raise_for_status()
            soup_en = soup_from_response(response_en)
        except Exception as exc:
            if debug:
                print(f"  Search fallback failed for IMDb ID {imdb_id} via '{search_term}': {exc}")
            continue

        link = find_search_result_link(soup_de, imdb_id)
        if not link:
            continue
        candidate = extract_search_result_candidate(link, imdb_id, english_soup=soup_en)
        if candidate:
            if is_generic_imdb_title(candidate.get('title')):
                continue
            if search_term != imdb_id:
                match_score = max(
                    title_match_score(search_term, candidate.get('title')),
                    title_match_score(search_term, candidate.get('title_en'))
                )
                if match_score <= 0:
                    continue
            if debug:
                print(f"  Recovered IMDb details for {imdb_id} from search term '{search_term}' via HTML search")
            return candidate
    return None

def build_imdb_search_headers(language_header='de-DE,de;q=0.9,en-US;q=0.8,en;q=0.7'):
    """Build IMDb search headers for a chosen localization."""
    return {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                      '(KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36',
        'Accept-Language': language_header
    }

def build_imdb_suggestion_url(search_term):
    """Build IMDb's suggestion JSON endpoint URL for a search term."""
    cleaned = clean_search_term(search_term or '')
    if not cleaned:
        return None
    slug = re.sub(r'\s+', '_', cleaned.strip().lower())
    slug = quote(slug, safe='_')
    if not slug:
        return None
    return f"https://v2.sg.media-imdb.com/suggestion/{slug[0]}/{slug}.json"

def is_allowed_suggestion_item(item, allow_video=False):
    """Allow only movie-like suggestion items from IMDb's JSON endpoint."""
    item_id = (item.get('id') or '').lower()
    qid = item.get('qid')
    q_value = (item.get('q') or '').lower()
    if not item_id.startswith('tt'):
        return False
    if not item.get('y') or not item.get('l'):
        return False
    if qid in {'movie', 'tvMovie', 'tvMiniSeries', 'tvSpecial'}:
        return True
    if allow_video and (qid == 'video' or q_value == 'video'):
        return True
    if q_value in {'feature', 'tv movie', 'tv mini-series', 'tv special'}:
        return True
    return False

def extract_imdb_suggestion_candidates(search_term, data, prefer_search_title=False, allow_video=False):
    """Convert IMDb suggestion JSON data into normalized candidate objects."""
    candidates = []
    preferred_title = clean_search_term(search_term or '')
    for item in data.get('d', []):
        if not is_allowed_suggestion_item(item, allow_video=allow_video):
            continue
        english_title = item.get('l')
        if is_generic_imdb_title(english_title):
            continue
        display_title = preferred_title if prefer_search_title and preferred_title else english_title
        candidates.append({
            'title': display_title,
            'title_de': display_title,
            'title_en': english_title,
            'year': str(item.get('y')),
            'imdb_id': item.get('id'),
            'imdb_rank': item.get('rank', 10**9)
        })
    return candidates

def fetch_imdb_suggestion_candidates(search_term, debug=False, prefer_search_title=False, allow_video=False):
    """Search IMDb's suggestion JSON endpoint and return normalized candidates."""
    url = build_imdb_suggestion_url(search_term)
    if not url:
        return []
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        data = response.json()
    except Exception as exc:
        if debug:
            print(f"  Suggestion search failed for '{search_term}': {exc}")
        return []
    candidates = extract_imdb_suggestion_candidates(
        search_term,
        data,
        prefer_search_title=prefer_search_title,
        allow_video=allow_video
    )
    if debug:
        print(f"DEBUG: Suggestion candidates for '{search_term}': {len(candidates)}")
    return candidates

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
        'kurzfilm', 'short', 'video game', 'videospiel', 'making of'
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
        target_year = extract_year_from_folder_name(folder_name_for_year)
        requested_number = extract_franchise_number(search_base)
        clue_year_set = set(clue_years or [])
        base_title_for_match = search_base
        title_candidates = []
        raw_title_variants = build_raw_search_variants(original_folder_name)
        if raw_title_variants:
            raw_prefix = raw_title_variants[0]
            raw_prefix_words = raw_prefix.split()
            if raw_prefix != search_base and len(raw_prefix_words) >= 2:
                base_title_for_match = raw_prefix

        for title in [search_base] + (authoritative_titles or []):
            if title and title not in title_candidates:
                title_candidates.append(title)

        search_terms = []
        search_terms.extend(raw_title_variants)
        for title in title_candidates:
            search_terms.append(title)

            split_title = title.split(' - ')[0].strip()
            if split_title and split_title != title:
                search_terms.append(split_title)

            prefix_words = title.split()
            if len(prefix_words) >= 3:
                short_prefix = ' '.join(prefix_words[:3]).strip()
                bad_terminal_words = {
                    'a', 'an', 'das', 'de', 'del', 'dem', 'den', 'der', 'des',
                    'die', 'du', 'ein', 'eine', 'einer', 'eines', 'la', 'le',
                    'les', 'of', 'the', 'und', 'von'
                }
                if (
                    len(prefix_words) <= 4 and
                    prefix_words[2].lower() not in bad_terminal_words and
                    short_prefix != title and
                    short_prefix != split_title
                ):
                    search_terms.append(short_prefix)
            if len(prefix_words) >= 2:
                short_two_word_prefix = ' '.join(prefix_words[:2]).strip()
                romanish = {'i', 'ii', 'iii', 'iv', 'v', 'vi', 'vii', 'viii', 'ix', 'x', 'xi', 'xii', 'xl'}
                if (
                    prefix_words[1].lower() in romanish or
                    prefix_words[1].isdigit() or
                    len(prefix_words[1]) <= 2
                ) and short_two_word_prefix not in search_terms:
                    search_terms.append(short_two_word_prefix)
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

        def fallback_acceptance_score(details, search_term, suggestion_index, suggestion_candidates=None):
            words = normalize_title_for_match(search_term)
            cleaned_words = clean_search_term(search_term).split()
            candidate_number = candidate_franchise_number(details.get('title_en') or details.get('title'))
            candidate_year = details.get('year')
            suggestion_candidates = suggestion_candidates or []
            top_rank = suggestion_candidates[0].get('imdb_rank', 10**9) if suggestion_candidates else 10**9
            second_rank = suggestion_candidates[1].get('imdb_rank', 10**9) if len(suggestion_candidates) > 1 else None
            strong_rank_gap = (
                second_rank is None or
                second_rank >= top_rank * 3 or
                (second_rank - top_rank) >= 20000
            )
            score = 0

            if target_year and details['year'] == target_year and len(words) >= 2 and search_term == search_base:
                score = max(score, 160)
            elif clue_year_set and details['year'] in clue_year_set and len(words) >= 2:
                score = max(score, 135)

            strong_title_score = max(
                title_match_score(search_term, details.get('title')),
                title_match_score(search_term, details.get('title_en'))
            )
            raw_prefix_score = 0
            if raw_title_variants:
                raw_prefix = raw_title_variants[0]
                raw_prefix_score = max(
                    title_match_score(raw_prefix, details.get('title')),
                    title_match_score(raw_prefix, details.get('title_en'))
                )
            if (
                target_year and candidate_year and search_term == search_base and
                abs(int(candidate_year) - int(target_year)) <= 1 and strong_title_score >= 235
            ):
                score = max(score, 150)

            if requested_number is not None:
                if requested_number == 1:
                    if candidate_number is None and search_term == search_base:
                        base_score = 105 - (suggestion_index * 5)
                        title_text = details.get('title_en') or details.get('title') or ''
                        try:
                            base_score += max(0, 2025 - int(details['year']))
                        except Exception:
                            pass
                        if ':' in title_text:
                            base_score -= 20
                        if re.search(r'\b(homecoming|hidden world|next chapter|chapter)\b', title_text, flags=re.IGNORECASE):
                                base_score -= 25
                        score = max(score, base_score)
                elif candidate_number == requested_number:
                    score = max(score, 125 - (suggestion_index * 5))
                elif search_term == search_base and suggestion_index == 0 and len(words) >= 2:
                    score = max(score, 118)

            if (
                search_term == search_base and raw_title_variants and raw_title_variants[0] != search_base and
                raw_prefix_score <= 0
            ):
                return score

            if suggestion_index == 0 and strong_rank_gap:
                if target_year and candidate_year == target_year:
                    score = max(score, 150 if len(cleaned_words) >= 2 else 140)
                elif search_term == search_base and len(cleaned_words) >= 2:
                    score = max(score, 122)
                elif len(cleaned_words) >= 3:
                    score = max(score, 112)
                elif len(cleaned_words) == 1 and second_rank is None and top_rank <= 8000:
                    score = max(score, 108)

            return score

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

            suggestion_candidates = fetch_imdb_suggestion_candidates(
                search_term,
                debug=debug,
                prefer_search_title=False
            )
            for suggestion_index, details in enumerate(suggestion_candidates[:8]):
                imdb_id = details['imdb_id']
                if imdb_id in results_by_id:
                    details = results_by_id[imdb_id]
                else:
                    title_score = candidate_match_score(details, title_candidates, base_title=base_title_for_match)
                    if title_score <= 0:
                        title_score = fallback_acceptance_score(
                            details,
                            search_term,
                            suggestion_index,
                            suggestion_candidates
                        )
                    if title_score <= 0:
                        if debug:
                            print(f"  Ignoring loosely related result: {details['title']} ({details['year']})")
                        continue
                    results_by_id[imdb_id] = details
                title_score = candidate_match_score(details, title_candidates, base_title=base_title_for_match)
                if title_score <= 0:
                    title_score = fallback_acceptance_score(
                        details,
                        search_term,
                        suggestion_index,
                        suggestion_candidates
                    )
                score = candidate_scores.setdefault(imdb_id, {
                    'score': 0,
                    'hits': 0,
                    'clue_hits': 0,
                    'year_distance': None,
                    'title_score': 0,
                    'rank_score': -(details.get('imdb_rank', 10**9))
                })
                score['score'] += term_weight + title_score
                score['hits'] += 1
                score['title_score'] = max(score['title_score'], title_score)
                score['rank_score'] = max(score['rank_score'], -(details.get('imdb_rank', 10**9)))
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

            if suggestion_candidates:
                continue

            search_url = f"https://www.imdb.com/find?q={quote_plus(search_term)}&s=tt"
            headers = build_imdb_search_headers()
            response = requests.get(search_url, headers=headers, timeout=10)
            response.raise_for_status()
            soup = soup_from_response(response)

            english_soup = None
            try:
                response_en = requests.get(
                    search_url,
                    headers=build_imdb_search_headers('en-US,en;q=0.9'),
                    timeout=10
                )
                response_en.raise_for_status()
                english_soup = soup_from_response(response_en)
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
                        title_score = candidate_match_score(details, title_candidates, base_title=base_title_for_match)
                        if title_score <= 0:
                            if debug:
                                print(f"  Ignoring loosely related result: {details['title']} ({details['year']})")
                            continue
                        results_by_id[imdb_id] = details
                    title_score = candidate_match_score(details, title_candidates, base_title=base_title_for_match)
                    if title_score <= 0:
                        title_score = fallback_acceptance_score(details, search_term, i, [])
                    score = candidate_scores.setdefault(imdb_id, {
                        'score': 0,
                        'hits': 0,
                        'clue_hits': 0,
                        'year_distance': None,
                        'title_score': 0,
                        'rank_score': -(details.get('imdb_rank', 10**9))
                    })
                    score['score'] += term_weight + title_score
                    score['hits'] += 1
                    score['title_score'] = max(score['title_score'], title_score)
                    score['rank_score'] = max(score['rank_score'], -(details.get('imdb_rank', 10**9)))
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
                        title_score = candidate_match_score(details, title_candidates, base_title=base_title_for_match)
                        if title_score <= 0:
                            continue
                        results_by_id[imdb_id] = details
                    title_score = candidate_match_score(details, title_candidates, base_title=base_title_for_match)
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
        clue_year_matches = []
        if clue_years and not target_year:
            clue_year_set = set(clue_years)
            clue_year_matches = [result for result in unique_results if result['year'] in clue_year_set]

        if target_year and not any(result['year'] == target_year for result in unique_results):
            near_year_matches = [
                result for result in unique_results
                if abs(int(result['year']) - int(target_year)) <= 1 and
                candidate_scores.get(result['imdb_id'], {}).get('title_score', 0) >= 235
            ]
            if len(near_year_matches) == 1:
                result = near_year_matches[0]
                if debug:
                    print(
                        f"  Selected near-year exact-title IMDB result: "
                        f"{result['title']} ({result['year']})"
                    )
                return result
            if debug:
                print(
                    f"  No IMDB result matches the year in folder name ({target_year}). Skipping."
                )
            return None

        if target_year and len(search_base.split()) >= 3:
            year_matches = [result for result in unique_results if result['year'] == target_year]
            if len(year_matches) == 1:
                result = year_matches[0]
                if debug:
                    print(
                        f"  Selected unique year-matching IMDB result despite title translation: "
                        f"{result['title']} ({result['year']})"
                    )
                return result

        if clue_year_matches and len(search_base.split()) >= 2:
            exact_clue_matches = [result for result in unique_results if result['year'] in clue_year_set]
            if len(exact_clue_matches) == 1:
                result = exact_clue_matches[0]
                if debug:
                    print(
                        f"  Selected unique clue-year IMDB result: "
                        f"{result['title']} ({result['year']})"
                    )
                return result

        if len(unique_results) > 1:
            if target_year:
                near_year_matches = [
                    result for result in unique_results
                    if abs(int(result['year']) - int(target_year)) <= 1 and
                    candidate_scores.get(result['imdb_id'], {}).get('title_score', 0) >= 235
                ]
                if len(near_year_matches) == 1:
                    result = near_year_matches[0]
                    if debug:
                        print(
                            f"  Selected near-year exact-title IMDB result: "
                            f"{result['title']} ({result['year']})"
                        )
                    return result
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

            # Same title, different years, no folder year → skip.
            # IMDb popularity must not pick e.g. old vs new "Der Andere".
            if len(years) > 1:
                if debug:
                    print(
                        f"  Multiple versions found with different years {sorted(years)}, "
                        f"but no year in folder name. Skipping to avoid ambiguity."
                    )
                return None

            ranked_results = sorted(
                unique_results,
                key=lambda result: (
                    candidate_scores.get(result['imdb_id'], {}).get('title_score', 0),
                    candidate_scores.get(result['imdb_id'], {}).get('clue_hits', 0),
                    candidate_scores.get(result['imdb_id'], {}).get('score', 0),
                    candidate_scores.get(result['imdb_id'], {}).get('hits', 0),
                    candidate_scores.get(result['imdb_id'], {}).get('rank_score', -(10**9)),
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
                    candidate_scores.get(result['imdb_id'], {}).get('rank_score', -(10**9)),
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
                    best_metrics.get('rank_score', -(10**9)),
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
        
        soup = soup_from_response(response)
        
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

def normalize_extra_marker(value):
    """Normalize folder or filename fragments to Jellyfin extra-type tokens."""
    normalized = transliterate_german((value or '').lower())
    normalized = re.sub(r'[^a-z0-9]+', '', normalized)
    return EXTRA_TYPE_ALIASES.get(normalized)

def detect_extra_type(rel_path, filename):
    """Detect whether a video file should be treated as a Jellyfin extra."""
    path_parts = [part for part in (rel_path or '').split(os.sep) if part and part != '.']
    for part in reversed(path_parts):
        extra_type = normalize_extra_marker(part)
        if extra_type:
            return extra_type

    stem = os.path.splitext(filename)[0]
    lowered_stem = transliterate_german(stem.lower())
    pattern_map = [
        ('behindthescenes', r'(?:(?:^|[\s._-])behind[\s._-]*the[\s._-]*scenes(?:$|[\s._-]))'),
        ('deletedscene', r'(?:(?:^|[\s._-])deleted[\s._-]*scenes?(?:$|[\s._-]))'),
        ('featurette', r'(?:(?:^|[\s._-])featurettes?(?:$|[\s._-]))'),
        ('interview', r'(?:(?:^|[\s._-])interviews?(?:$|[\s._-]))'),
        ('trailer', r'(?:(?:^|[\s._-])trailer(?:$|[\s._-])| trailer$)'),
        ('sample', r'(?:(?:^|[\s._-])sample(?:$|[\s._-])| sample$)'),
        ('scene', r'(?:(?:^|[\s._-])scene(?:$|[\s._-]))'),
        ('clip', r'(?:(?:^|[\s._-])clip(?:$|[\s._-]))'),
        ('short', r'(?:(?:^|[\s._-])short(?:$|[\s._-]))'),
        ('other', r'(?:(?:^|[\s._-])other(?:$|[\s._-]))'),
        ('extra', r'(?:(?:^|[\s._-])extra(?:$|[\s._-])|(?:^|[\s._-])extras(?:$|[\s._-]))'),
    ]
    for extra_type, pattern in pattern_map:
        if re.search(pattern, lowered_stem):
            return extra_type
    return None

def classify_video_files(folder_path, video_files):
    """Split discovered video files into main movie files and extras."""
    main_files = []
    extra_files = []
    ignored_files = []

    for rel_path, filename in video_files:
        file_path = os.path.join(folder_path, rel_path, filename) if rel_path else os.path.join(folder_path, filename)
        try:
            file_size = os.path.getsize(file_path)
        except OSError:
            file_size = 0

        extra_type = detect_extra_type(rel_path, filename)
        file_info = {
            'rel_path': rel_path,
            'filename': filename,
            'path': file_path,
            'size': file_size,
            'extra_type': extra_type,
        }
        if extra_type:
            extra_files.append(file_info)
        elif file_size < MIN_MAIN_VIDEO_BYTES:
            ignored_files.append(file_info)
        else:
            main_files.append(file_info)

    return main_files, extra_files, ignored_files

def has_dvd_structure(folder_path):
    """Detect a classic DVD folder that should keep its VIDEO_TS contents untouched."""
    dvd_marker_pattern = re.compile(r'.+\.(?:ifo|vob|bup)$', flags=re.IGNORECASE)

    try:
        for item in os.listdir(folder_path):
            item_path = os.path.join(folder_path, item)
            if os.path.isfile(item_path) and dvd_marker_pattern.match(item):
                return True
    except OSError:
        return False

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

def has_nested_dvd_structure(folder_path):
    """Detect DVD structures inside nested subfolders below the movie root."""
    try:
        for root, dirs, files in os.walk(folder_path):
            dirs[:] = [d for d in dirs if not d.startswith('.') and not d.startswith('@')]
            if os.path.abspath(root) == os.path.abspath(folder_path):
                continue

            if os.path.basename(root).upper() == 'VIDEO_TS':
                for item in files:
                    upper_name = item.upper()
                    if upper_name.endswith('.IFO') or upper_name.endswith('.VOB') or upper_name.endswith('.BUP'):
                        return True
    except OSError:
        return False
    return False

def detect_special_skip_reason(folder_path, clue_years=None):
    """Return a clearer skip reason for known unsafe folder shapes."""
    years = sorted(set(clue_years or []))
    if has_nested_dvd_structure(folder_path) and len(years) >= 2:
        return "Likely multi-movie DVD set with multiple years; skipping single-title rename"
    return None

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
        filename = item['filename'] if isinstance(item, dict) else item[1]
        rel_path = item['rel_path'] if isinstance(item, dict) else item[0]
        part = extract_part_number(filename)
        has_part = 0 if part is not None else 1
        part_value = part if part is not None else 999
        return (has_part, part_value, rel_path.lower(), filename.lower())
    return sorted(video_files, key=key)

def build_extra_video_name(base_name, extra_type, ext, used_names):
    """Build a Jellyfin extra filename and avoid collisions if several extras share a type."""
    suffix = f"-{extra_type}"
    candidate = f"{base_name}{suffix}{ext.lower()}"
    counter = 2
    while candidate in used_names:
        candidate = f"{base_name}{suffix}{counter}{ext.lower()}"
        counter += 1
    used_names.add(candidate)
    return candidate

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
    special_skip_reason = detect_special_skip_reason(folder_path, clue_years=clue_years)
    if special_skip_reason:
        if debug:
            print(f"  Skipping: {special_skip_reason}")
        return {
            'status': 'skipped',
            'folder': folder_name,
            'reason': special_skip_reason,
        }
    
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

    movie_info = apply_preferred_output_title(
        movie_info,
        folder_name,
        metadata_titles=metadata_titles,
        clue_terms=clue_terms,
        debug=debug
    )
    
    # Create new folder name strictly from lookup title.
    new_folder_name = create_folder_name(movie_info)
    new_folder_path = os.path.join(base_path, new_folder_name)
    
    print(f"  New folder name: {new_folder_name}")
    
    # Get video files (now returns list of (rel_path, filename) tuples)
    video_files = get_video_files(folder_path)
    main_video_files, extra_video_files, ignored_video_files = classify_video_files(folder_path, video_files)
    dvd_structure = has_dvd_structure(folder_path)
    iso_image = has_iso_image(folder_path)
    print(f"  Found {len(main_video_files) + len(extra_video_files)} usable video file(s)")
    for file_info in main_video_files:
        rel_path = file_info['rel_path']
        filename = file_info['filename']
        if rel_path:
            print(f"    {rel_path}/{filename}")
        else:
            print(f"    {filename}")
    for file_info in extra_video_files:
        rel_path = file_info['rel_path']
        filename = file_info['filename']
        extra_type = file_info['extra_type']
        if rel_path:
            print(f"    {rel_path}/{filename} [{extra_type}]")
        else:
            print(f"    {filename} [{extra_type}]")
    if ignored_video_files:
        print(f"  Ignoring {len(ignored_video_files)} small video file(s) under 100 MB")
        for file_info in ignored_video_files:
            rel_path = file_info['rel_path']
            filename = file_info['filename']
            if rel_path:
                print(f"    {rel_path}/{filename}")
            else:
                print(f"    {filename}")
    if dvd_structure and not main_video_files and not extra_video_files:
        print("  Detected DVD VIDEO_TS structure; folder will be renamed but DVD files kept unchanged")
    if iso_image and not main_video_files and not extra_video_files:
        print("  Detected ISO image; folder will be renamed but ISO files kept unchanged")

    result_info = {
        'status': 'success',
        'original_folder': folder_name,
        'new_folder': new_folder_name,
        'has_videos': len(main_video_files) > 0 or dvd_structure or iso_image,
        'dvd_structure': dvd_structure,
        'iso_image': iso_image,
        'video_moves': []
    }
    
    # Always rename folder if we have movie info
    if DRY_RUN:
        print("  [DRY RUN] Would rename folder and video files")
        print(f"    Folder: {folder_name} -> {new_folder_name}")
        
        all_video_files = sort_video_files(main_video_files)
        used_names = set()

        for idx, file_info in enumerate(all_video_files):
            rel_path = file_info['rel_path']
            filename = file_info['filename']
            _, ext = os.path.splitext(filename)
            base_name = create_video_base_name(new_folder_name, movie_info)
            cd_suffix = f"-cd{idx + 1}" if len(all_video_files) > 1 else ""
            new_video_name = f"{base_name}{cd_suffix}{ext.lower()}"
            used_names.add(new_video_name)
            source_display = f"{rel_path}/{filename}" if rel_path else filename
            result_info['video_moves'].append((source_display, new_video_name))
            if rel_path:
                print(f"    Move and rename: {rel_path}/{filename} -> {new_video_name}")
            else:
                print(f"    Rename: {filename} -> {new_video_name}")

        for file_info in sort_video_files(extra_video_files):
            rel_path = file_info['rel_path']
            filename = file_info['filename']
            _, ext = os.path.splitext(filename)
            base_name = create_video_base_name(new_folder_name, movie_info)
            new_video_name = build_extra_video_name(base_name, file_info['extra_type'], ext, used_names)
            source_display = f"{rel_path}/{filename}" if rel_path else filename
            result_info['video_moves'].append((source_display, new_video_name))
            if rel_path:
                print(f"    Move and rename extra: {rel_path}/{filename} -> {new_video_name}")
            else:
                print(f"    Rename extra: {filename} -> {new_video_name}")
        if dvd_structure and not all_video_files and not extra_video_files:
            print("    DVD structure preserved: VIDEO_TS contents stay unchanged")
        if iso_image and not all_video_files and not extra_video_files:
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

            all_video_files = sort_video_files(main_video_files)
            all_extra_files = sort_video_files(extra_video_files)
            subfolders_to_remove = set()
            used_names = set()
            
            for idx, file_info in enumerate(all_video_files):
                rel_path = file_info['rel_path']
                filename = file_info['filename']
                _, ext = os.path.splitext(filename)
                base_name = create_video_base_name(new_folder_name, movie_info)
                cd_suffix = f"-cd{idx + 1}" if len(all_video_files) > 1 else ""
                new_video_name = f"{base_name}{cd_suffix}{ext.lower()}"
                used_names.add(new_video_name)
                
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

            for file_info in all_extra_files:
                rel_path = file_info['rel_path']
                filename = file_info['filename']
                _, ext = os.path.splitext(filename)
                base_name = create_video_base_name(new_folder_name, movie_info)
                new_video_name = build_extra_video_name(base_name, file_info['extra_type'], ext, used_names)

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
                    print(f"    Renamed extra: {filename} -> {new_video_name}")

            if dvd_structure and not all_video_files and not all_extra_files:
                print("    Preserved DVD structure inside VIDEO_TS")
            if iso_image and not all_video_files and not all_extra_files:
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

    total_loaded = len(movies_to_process)
    if args.offset:
        movies_to_process = movies_to_process[args.offset:]
    if args.limit is not None:
        movies_to_process = movies_to_process[:args.limit]
    if args.offset or args.limit is not None:
        print(
            f"Batch: offset={args.offset}, limit={args.limit} "
            f"→ {len(movies_to_process)} of {total_loaded} movies"
        )

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
        if isinstance(result, dict) and result.get('status') == 'skipped':
            skipped_count += 1
            skipped_movies.append({
                'folder': result.get('folder', movie_folder),
                'reason': result.get('reason', 'No confident IMDB match found from folder, file, and metadata clues')
            })
        elif result is False:
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
