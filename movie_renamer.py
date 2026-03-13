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
from bs4 import BeautifulSoup
from urllib.parse import quote_plus
import shutil

# Configuration
BASE_PATH = "/mnt/nfs/huddle/media/Filme"
DRY_RUN = True  # Set to False to actually rename files
WAIT_TIME = 1  # Seconds between IMDB requests to be respectful

# List of 20 movies starting from Body Snatchers
MOVIES_TO_PROCESS = [
    "Body Snatchers",
    "Bodyguard", 
    "Bread Crumbs",
    "Breaking - Dawn - Biss zum Ende der Nacht - Teil 1",
    "Brendan - und das Geheimnis von Kells",
    "Buck Rogers (1979)",
    "Bud, Der Ganovenschreck (1983)",
    "Buddy fängt nur große Fische (1973)",
    "Buddy Haut Den Lukas (1980)",
    "Buddy in HongKong (1975)",
    "Bunker-Es gibt kein Entkommen",
    "Buried - Lebend begraben",
    "Byzantium",
    "Cabin Fever -The New Outbreak",
    "Cabin Fever 2 - Spring Fever", 
    "Cabin Fever-1",
    "Cabin.Fever.3.Patient.Zero",
    "Caged (2010)",
    "Captivity",
    "Carlos der Schakal"
]

def clean_search_term(folder_name):
    """Clean folder name for IMDB search"""
    # Remove common indicators but keep German names
    clean_name = folder_name
    
    # Remove year if already present
    clean_name = re.sub(r'\s*\(\d{4}\)\s*', ' ', clean_name)
    
    # Remove common suffixes but preserve German content
    clean_name = re.sub(r'\s*-.*German.*$', '', clean_name, flags=re.IGNORECASE)
    clean_name = re.sub(r'\s*\[.*\]', '', clean_name)
    
    # Clean up dots and special characters
    clean_name = clean_name.replace('.', ' ')
    clean_name = clean_name.replace('-', ' ')
    clean_name = re.sub(r'\s+', ' ', clean_name.strip())
    
    return clean_name

def search_imdb(movie_title):
    """Search IMDB for movie and return first result with year and ID"""
    try:
        # Search IMDB
        search_url = f"https://www.imdb.com/find?q={quote_plus(movie_title)}&s=tt&ttype=ft"
        
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
        
        response = requests.get(search_url, headers=headers, timeout=10)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.content, 'html.parser')
        
        # Look for movie results
        results = soup.find_all('td', class_='result_text')
        
        for result in results:
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
                    
                    print(f"  Found: {title_clean} ({year}) [imdbid-{imdb_id}]")
                    return {
                        'title': title_clean,
                        'year': year,
                        'imdb_id': imdb_id
                    }
        
        print(f"  No suitable match found on IMDB")
        return None
        
    except Exception as e:
        print(f"  Error searching IMDB: {e}")
        return None

def get_video_files(folder_path):
    """Get video files from folder"""
    video_extensions = {'.mkv', '.mp4', '.avi', '.mov', '.wmv', '.flv', '.webm', '.m4v'}
    video_files = []
    
    try:
        for file in os.listdir(folder_path):
            if any(file.lower().endswith(ext) for ext in video_extensions):
                video_files.append(file)
    except OSError:
        pass
        
    return video_files

def create_folder_name(original_name, movie_info):
    """Create Jellyfin-compliant folder name (without IMDB ID)"""
    # Use original name to preserve German titles, but clean it up
    title = original_name
    
    # Remove existing year if present
    title = re.sub(r'\s*\(\d{4}\)\s*', '', title).strip()
    
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
    
    # Clean search term
    search_term = clean_search_term(folder_name)
    print(f"  Searching IMDB for: '{search_term}'")
    
    # Search IMDB
    movie_info = search_imdb(search_term)
    if not movie_info:
        print(f"  Skipping: Could not find match on IMDB")
        return False
    
    # Create new folder name (without IMDB ID)
    new_folder_name = create_folder_name(folder_name, movie_info)
    new_folder_path = os.path.join(BASE_PATH, new_folder_name)
    
    print(f"  New folder name: {new_folder_name}")
    
    # Get video files
    video_files = get_video_files(folder_path)
    print(f"  Found {len(video_files)} video file(s): {video_files}")
    
    if DRY_RUN:
        print("  [DRY RUN] Would rename folder and video files")
        print(f"    Folder: {folder_name} -> {new_folder_name}")
        for video_file in video_files:
            # Get file extension
            _, ext = os.path.splitext(video_file)
            new_video_name = create_video_name(new_folder_name, movie_info, ext)
            print(f"    Video: {video_file} -> {new_video_name}")
    else:
        try:
            # Rename folder
            shutil.move(folder_path, new_folder_path)
            print(f"  Renamed folder: {folder_name} -> {new_folder_name}")
            
            # Rename video files
            for video_file in video_files:
                old_video_path = os.path.join(new_folder_path, video_file)
                _, ext = os.path.splitext(video_file)
                new_video_name = create_video_name(new_folder_name, movie_info, ext)
                new_video_path = os.path.join(new_folder_path, new_video_name)
                
                if os.path.exists(old_video_path):
                    shutil.move(old_video_path, new_video_path)
                    print(f"    Renamed video: {video_file} -> {new_video_name}")
                    
        except Exception as e:
            print(f"  Error renaming: {e}")
            return False
    
    return True

def main():
    """Main function"""
    print("Movie Folder Renamer for Jellyfin")
    print("=" * 50)
    print(f"Base path: {BASE_PATH}")
    print(f"Dry run mode: {DRY_RUN}")
    print(f"Processing {len(MOVIES_TO_PROCESS)} movies")
    
    if DRY_RUN:
        print("\n⚠️  DRY RUN MODE - No files will be actually renamed")
        print("Set DRY_RUN = False in the script to perform actual renaming")
    
    print("\nNaming convention:")
    print("  Folder: Movie_Name (year)")
    print("  Video:  Movie_Name (year) [imdbid-ttXXXXXX].ext")
    
    print("\n" + "=" * 50)
    
    success_count = 0
    skipped_count = 0
    
    for i, movie_folder in enumerate(MOVIES_TO_PROCESS, 1):
        print(f"\n[{i}/{len(MOVIES_TO_PROCESS)}]", end="")
        
        if rename_movie_folder(movie_folder):
            success_count += 1
        else:
            skipped_count += 1
            
        # Be respectful to IMDB
        if i < len(MOVIES_TO_PROCESS):
            print(f"  Waiting {WAIT_TIME}s before next request...")
            time.sleep(WAIT_TIME)
    
    print("\n" + "=" * 50)
    print("SUMMARY")
    print("=" * 50)
    print(f"Successfully processed: {success_count}")
    print(f"Skipped: {skipped_count}")
    print(f"Total: {len(MOVIES_TO_PROCESS)}")
    
    if DRY_RUN:
        print(f"\n⚠️  This was a DRY RUN - no files were actually renamed")
        print("Review the output above, then set DRY_RUN = False to perform actual renaming")

if __name__ == "__main__":
    main()