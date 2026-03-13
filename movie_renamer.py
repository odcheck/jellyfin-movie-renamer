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
from bs4 import BeautifulSoup
from urllib.parse import quote_plus
import shutil

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
parser.add_argument('--backlog-file', type=str, default='backlog.txt', help='File to write unmatched movies to (default: backlog.txt)')
parser.add_argument('--execute', action='store_true', help='Actually rename files (disable dry-run)')
args = parser.parse_args()

# Configuration
BASE_PATH = "/mnt/nfs/huddle/media/Filme"
DRY_RUN = not args.execute  # Set to False if --execute is used
WAIT_TIME = 1  # Seconds between IMDB requests to be respectful

def clean_search_term(folder_name):
    """Clean folder name for IMDB search"""
    # Remove common indicators but keep German names
    clean_name = folder_name
    
    # Remove year if already present
    clean_name = re.sub(r'\s*\(\d{4}\)\s*', ' ', clean_name)
    
    # Remove common German suffixes and indicators
    clean_name = re.sub(r'\s*-.*German.*$', '', clean_name, flags=re.IGNORECASE)
    clean_name = re.sub(r'\s*-.*Dubbed.*$', '', clean_name, flags=re.IGNORECASE)
    clean_name = re.sub(r'\s*-.*Uncut.*$', '', clean_name, flags=re.IGNORECASE)
    clean_name = re.sub(r'\s*-.*Director.*$', '', clean_name, flags=re.IGNORECASE)
    clean_name = re.sub(r'\s*\[.*\]', '', clean_name)
    
    # Clean up dots and special characters
    clean_name = clean_name.replace('.', ' ')
    clean_name = clean_name.replace('-', ' ')
    clean_name = clean_name.replace('_', ' ')
    clean_name = re.sub(r'\s+', ' ', clean_name.strip())
    
    return clean_name

def has_year_in_folder_name(folder_name):
    """Check if folder name contains a year in parentheses"""
    return bool(re.search(r'\(\d{4}\)', folder_name))

def search_imdb(original_folder_name, folder_name_for_year):
    """Search IMDB for movie and return result with year and ID, or None if ambiguous"""
    try:
        # Clean the search term for IMDB lookup
        search_base = clean_search_term(original_folder_name)
        
        # Try multiple search strategies
        search_terms = [
            search_base,  # Cleaned title
            search_base.split(' - ')[0].strip(),  # First part before dash
            ' '.join(search_base.split()[:3]),  # First 3 words
        ]
        
        all_results = []
        
        for search_term in search_terms:
            if not search_term.strip():
                continue
                
            print(f"  Trying search: '{search_term}'")
            
            # Search IMDB
            search_url = f"https://www.imdb.com/find?q={quote_plus(search_term)}&s=tt&ttype=ft"
            
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
            }
            
            response = requests.get(search_url, headers=headers, timeout=10)
            response.raise_for_status()
            
            soup = BeautifulSoup(response.content, 'html.parser')
            
            # Try the new IMDB structure first (2024)
            title_links = soup.find_all('a', href=re.compile(r'/title/tt\d+/'))
            print(f"DEBUG: Processing {len(title_links)} title links")
            
            for i, link in enumerate(title_links[:5]):  # Check first 5 results
                href = link.get('href', '')
                print(f"DEBUG: Link {i}: {href}")
                title_match = re.search(r'/title/(tt\d+)/', href)
                
                if title_match:
                    imdb_id = title_match.group(1)
                    print(f"DEBUG: Found IMDB ID: {imdb_id}")
                    
                    # Get the movie details from the actual movie page
                    movie_url = f"https://www.imdb.com/title/{imdb_id}/"
                    print(f"DEBUG: Fetching movie page: {movie_url}")
                    try:
                        movie_response = requests.get(movie_url, headers=headers, timeout=10)
                        movie_soup = BeautifulSoup(movie_response.content, 'html.parser')
                        
                        # Try to find the title and year
                        title_elem = movie_soup.find('h1')
                        if title_elem:
                            title_text = title_elem.get_text().strip()
                            print(f"DEBUG: Found title: {title_text}")
                            # Remove "Original title:" prefix if present
                            title_text = re.sub(r'^Original title:\s*', '', title_text)
                            
                            # Look for year in various places
                            year = None
                            
                            # Try different selectors for year
                            year_selectors = [
                                ('span', {'class': re.compile(r'year')}),
                                ('span', {'id': re.compile(r'titleYear')}),
                                ('a', {'href': re.compile(r'/year/\d+')}),
                                ('li', {'data-testid': 'title-details-releasedate'}),
                            ]
                            
                            for tag, attrs in year_selectors:
                                elements = movie_soup.find_all(tag, attrs)
                                for elem in elements:
                                    year_text = elem.get_text().strip()
                                    print(f"DEBUG: Found year element ({tag}, {attrs}): {year_text}")
                                    year_match = re.search(r'(\d{4})', year_text)
                                    if year_match:
                                        year = year_match.group(1)
                                        break
                                if year:
                                    break
                            
                            # Also try looking in the title itself for year
                            if not year:
                                title_year_match = re.search(r'\((\d{4})\)', title_text)
                                if title_year_match:
                                    year = title_year_match.group(1)
                                    print(f"DEBUG: Found year in title: {year}")
                            
                            # Last resort: search the entire page content for 4-digit years
                            if not year:
                                page_text = movie_soup.get_text()
                                years = re.findall(r'\b(19|20)\d{2}\b', page_text)
                                if years:
                                    # Take the first reasonable year (prefer 1990s-2020s)
                                    for y in years:
                                        if 1990 <= int(y) <= 2030:
                                            year = y
                                            print(f"DEBUG: Found year in page content: {year}")
                                            break
                            
                            if year:
                                result = {
                                    'title': title_text,
                                    'year': year,
                                    'imdb_id': imdb_id
                                }
                                all_results.append(result)
                                print(f"  ✅ Found candidate: {title_text} ({year}) [imdbid-{imdb_id}]")
                            else:
                                print(f"DEBUG: No year found for {title_text}")
                        else:
                            print("DEBUG: No h1 title found")
                    except Exception as e:
                        print(f"  Error fetching movie page: {e}")
                        continue
            
            # Fallback to old structure
            results = soup.find_all('td', class_='result_text')
            
            for result in results[:5]:  # Check first 5
                link = result.find('a')
                if not link:
                    continue
                    
                href = link.get('href', '')
                title_match = re.search(r'/title/(tt\d+)/', href)
                
                if title_match:
                    imdb_id = title_match.group(1)
                    title_text = result.get_text()
                    
                    # Extract year
                    year_match = re.search(r'\((\d{4})\)', title_text)
                    if year_match:
                        year = year_match.group(1)
                        title_clean = re.sub(r'\s*\(\d{4}\).*$', '', link.get_text()).strip()
                        
                        result = {
                            'title': title_clean,
                            'year': year,
                            'imdb_id': imdb_id
                        }
                        all_results.append(result)
                        print(f"  ✅ Found candidate: {title_clean} ({year}) [imdbid-{imdb_id}]")
        
        # Now decide what to return
        if not all_results:
            print(f"  ❌ No suitable matches found on IMDB for any search term")
            return None
        
        # Check for ambiguity
        years = set(r['year'] for r in all_results)
        has_year_in_name = has_year_in_folder_name(folder_name_for_year)
        
        if len(all_results) > 1 and len(years) > 1:
            if has_year_in_name:
                # Extract year from folder name
                year_match = re.search(r'\((\d{4})\)', folder_name_for_year)
                if year_match:
                    target_year = year_match.group(1)
                    # Find matching result
                    for result in all_results:
                        if result['year'] == target_year:
                            print(f"  Selected version matching folder year ({target_year}): {result['title']} ({result['year']})")
                            return result
                    print(f"  No IMDB result matches the year in folder name ({target_year}). Skipping.")
                    return None
            else:
                print(f"  Multiple versions found with different years {sorted(years)}, but no year in folder name. Skipping to avoid ambiguity.")
                return None
        else:
            # Single result or all same year - return first
            result = all_results[0]
            print(f"  Selected: {result['title']} ({result['year']}) [imdbid-{result['imdb_id']}]")
            return result
        
    except Exception as e:
        print(f"  Error searching IMDB: {e}")
        return None

def debug_imdb_search(movie_title):
    """Debug IMDB search to see what we get"""
    try:
        search_url = f"https://www.imdb.com/find?q={quote_plus(movie_title)}&s=tt&ttype=ft"
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
    video_extensions = {'.mkv', '.mp4', '.avi', '.mov', '.wmv', '.flv', '.webm', '.m4v'}
    video_files = []
    
    try:
        for root, dirs, files in os.walk(folder_path):
            for file in files:
                if any(file.lower().endswith(ext) for ext in video_extensions):
                    # Get relative path from folder_path
                    rel_path = os.path.relpath(root, folder_path)
                    if rel_path == '.':
                        rel_path = ''
                    video_files.append((rel_path, file))
    except OSError:
        pass
        
    return video_files

def create_folder_name(original_name, movie_info):
    """Create Jellyfin-compliant folder name (without IMDB ID)"""
    # Use original name to preserve German titles, but clean it up
    title = original_name
    
    # Remove existing year if present
    title = re.sub(r'\s*\(\d{4}\)\s*', '', title).strip()
    
    # Remove extra tags like [SD] [PG] [voted_*] [genre]
    title = re.sub(r'\s*\[.*?\]', '', title)
    
    # Apply German transliteration
    title = transliterate_german(title)
    
    # Replace spaces with underscores in title part only
    title = title.replace(' ', '_')
    
    # Folder name format: Title (year)
    return f"{title} ({movie_info['year']})"

def create_video_name(folder_name, movie_info, extension):
    """Create video file name with IMDB ID"""
    # Video file format: Title (year) [imdbid-ttXXXXXX].ext
    return f"{folder_name} [imdbid-{movie_info['imdb_id']}]{extension}"

def rename_movie_folder(folder_name):
    """Process and rename a single movie folder"""
    print(f"\nProcessing: {folder_name}")
    
    folder_path = os.path.join(BASE_PATH, folder_name)
    
    # Check if folder exists
    if not os.path.isdir(folder_path):
        print(f"  Folder not found: {folder_path}")
        return False
    
    # Use original folder name for IMDB search
    print(f"  Searching IMDB for: '{folder_name}'")
    
    # Search IMDB
    movie_info = search_imdb(folder_name, folder_name)
    if not movie_info:
        print(f"  Skipping: Could not find match on IMDB")
        return False
    
    # Create new folder name (without IMDB ID)
    new_folder_name = create_folder_name(folder_name, movie_info)
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
    
    # Always rename folder if we have movie info
    if DRY_RUN:
        print("  [DRY RUN] Would rename folder and video files")
        print(f"    Folder: {folder_name} -> {new_folder_name}")
        
        # Sort all video files and assign cd numbers sequentially
        all_video_files = sorted(video_files, key=lambda x: (x[0], x[1]))  # Sort by path, then filename
        
        for idx, (rel_path, filename) in enumerate(all_video_files):
            _, ext = os.path.splitext(filename)
            suffix = f" - cd{idx + 1}" if len(all_video_files) > 1 else ""
            new_video_name = f"{create_video_name(new_folder_name, movie_info, ext)}{suffix}"
            if rel_path:
                print(f"    Move and rename: {rel_path}/{filename} -> {new_video_name}")
            else:
                print(f"    Rename: {filename} -> {new_video_name}")
    else:
        try:
            # Rename folder
            shutil.move(folder_path, new_folder_path)
            print(f"  Renamed folder: {folder_name} -> {new_folder_name}")
            
            # Sort all video files and assign cd numbers sequentially
            all_video_files = sorted(video_files, key=lambda x: (x[0], x[1]))  # Sort by path, then filename
            
            subfolders_to_remove = set()
            
            for idx, (rel_path, filename) in enumerate(all_video_files):
                _, ext = os.path.splitext(filename)
                suffix = f" - cd{idx + 1}" if len(all_video_files) > 1 else ""
                new_video_name = f"{create_video_name(new_folder_name, movie_info, ext)}{suffix}"
                
                # Source path
                if rel_path:
                    old_video_path = os.path.join(new_folder_path, rel_path, filename)
                    subfolders_to_remove.add(os.path.join(new_folder_path, rel_path))
                else:
                    old_video_path = os.path.join(new_folder_path, filename)
                
                new_video_path = os.path.join(new_folder_path, new_video_name)
                
                if os.path.exists(old_video_path):
                    shutil.move(old_video_path, new_video_path)
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
    return True, len(video_files) > 0

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
    skipped_movies = []
    backlog_movies = []
    
    for i, movie_folder in enumerate(movies_to_process, 1):
        print(f"\n[{i}/{len(movies_to_process)}]", end="")
        
        result = rename_movie_folder(movie_folder)
        if result is False:
            # IMDB lookup failed
            skipped_count += 1
            skipped_movies.append(movie_folder)
        else:
            success_count += 1
            folder_renamed, has_videos = result
            if not has_videos:
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
            skipped_file = args.backlog_file.replace('.txt', '_skipped.txt')
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