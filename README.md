# Jellyfin Movie Renamer

A Python script to rename movie folders and video files to Jellyfin-compatible naming conventions using IMDB data.

## Features

- **Automatic IMDB Lookup**: Searches IMDB for movie information and extracts title, year, and IMDB ID.
- **German Title Preference**: Prefers the German movie title from IMDB when one exists.
- **Ambiguity Handling**: Skips renaming if multiple movie versions exist with different years and no year is specified in the folder name.
- **Batch Processing**: Process multiple movies from an input file.
- **Input File Generation**: Scan a directory to generate a list of movie folders.
- **Nested Video Handling**: Finds video files in subfolders, moves them to the movie folder, and removes empty subfolders.
- **Backlog Management**: Writes backlog, skipped, and successful results to text files.
- **Dry Run Mode**: Test renaming without actually moving files.

## Requirements

- **Python 3.6+**
- **Dependencies**:
  - `requests` (for HTTP requests)
  - `beautifulsoup4` (for HTML parsing)

## Installation (Debian/Ubuntu)

1. Install Python 3 and pip:
   ```bash
   sudo apt update
   sudo apt install python3 python3-pip
   ```

2. Install required Python packages:
   ```bash
   sudo apt install python3-requests python3-bs4
   ```
   
   Or using pip:
   ```bash
   pip3 install requests beautifulsoup4
   ```

## Usage

**Important**: Use `python3` instead of `python` on Debian/Ubuntu systems.

### Generate Input File from Directory

Scan a movie directory and generate a list of folders:

```bash
python3 movie_renamer.py --generate-inputfile-from=/path/to/movies --fileoutput=/output/directory
```

Example:
```bash
python3 movie_renamer.py --generate-inputfile-from=/mnt/nfs/huddle/media/Filme --fileoutput=/home/ben
```

This creates `/home/ben/movielist.txt` with one movie folder name per line.

### Process Movies from Input File

Rename movies listed in an input file:

```bash
python3 movie_renamer.py --input=/path/to/movielist.txt --execute
```

Options:
- `--backlog-file=/path/to/backlog.txt`: Specify custom backlog file (default: `~/backlog.txt`)
- `--success-file=/path/to/success.txt`: Specify custom success list file (default: `~/success.txt`)
- `--execute`: Actually rename files (default is dry-run mode)

By default the script writes:
- `~/backlog.txt`: Movies matched on IMDB but with no video files found
- `~/backlog_skipped.txt`: Movies skipped because IMDB lookup failed or was ambiguous
- `~/success.txt`: Movies successfully processed

### Dry Run (Default)

To test without renaming files:

```bash
python3 movie_renamer.py --input=/path/to/movielist.txt
```

## Naming Convention

- **Folder**: `Movie_Name (year)`
- **Video File**: `Movie_Name (year) [imdbid-ttXXXXXX].ext`
- **Multipart Video File**: `Movie_Name (year) [imdbid-ttXXXXXX]-cd1.ext`

## Examples

1. **Generate input file**:
   ```bash
   python3 movie_renamer.py --generate-inputfile-from=/mnt/nfs/huddle/media/Filme --fileoutput=/home/ben
   ```

2. **Process with dry run**:
   ```bash
   python3 movie_renamer.py --input=/home/ben/movielist.txt
   ```

3. **Process and rename files**:
   ```bash
   python3 movie_renamer.py --input=/home/ben/movielist.txt --execute --backlog-file=/home/ben/unmatched.txt
   ```

4. **Process and write all result lists to custom files**:
   ```bash
   python3 movie_renamer.py --input=/home/ben/movielist.txt --execute --backlog-file=/home/ben/unmatched.txt --success-file=/home/ben/processed.txt
   ```

## Result Files

- **Backlog**: Movies that matched on IMDB but had no video files found. Default: `~/backlog.txt`
- **Skipped**: Movies skipped because IMDB lookup failed or remained ambiguous. Default: `~/backlog_skipped.txt`
- **Success**: Movies successfully processed. Default: `~/success.txt`

## Configuration

Edit the script to change:
- `BASE_PATH`: Root directory containing movie folders
- `WAIT_TIME`: Delay between IMDB requests (be respectful)

## Troubleshooting

- **Command not found**: Use `python3` instead of `python`
- **Permission errors**: Ensure write access to output directories
- **IMDB blocking**: The script includes delays; if blocked, wait and retry
- **Missing dependencies**: Install with `pip3 install requests beautifulsoup4`

## Jellyfin Naming Convention

The script follows the Jellyfin naming convention:
- **Folder format:** `Movie_Name (year)`
- **Video format:** `Movie_Name (year) [imdbid-ttXXXXXX].extension`
- **Multipart format:** `Movie_Name (year) [imdbid-ttXXXXXX]-cd1.extension`

This ensures optimal metadata matching and organization in Jellyfin.

## Safety Features

- Dry run mode prevents accidental changes
- Skips movies that can't be matched on IMDB
- Prefers German titles when IMDB provides them
- Rate limiting to respect IMDB servers

## License

MIT License
