# Jellyfin Movie Renamer

A Python script to rename movie folders and video files to Jellyfin-compatible naming conventions using IMDB data.

## Features

- **Automatic IMDB Lookup**: Searches IMDB for movie information and extracts title, year, and IMDB ID.
- **Ambiguity Handling**: Skips renaming if multiple movie versions exist with different years and no year is specified in the folder name.
- **Batch Processing**: Process multiple movies from an input file.
- **Input File Generation**: Scan a directory to generate a list of movie folders.
- **Backlog Management**: Writes unmatched movies to a backlog file for manual review.
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
python3 movie_renamer.py --generate-inputfile-from=/path/to/Source --fileoutput=/home/your_username
```

This creates `/home/your_username/movielist.txt` with one movie folder name per line.

### Process Movies from Input File

Rename movies listed in an input file:

```bash
python3 movie_renamer.py --input=/path/to/movielist.txt --execute
```

Options:
- `--backlog-file=/path/to/backlog.txt`: Specify custom backlog file (default: `backlog.txt`)
- `--execute`: Actually rename files (default is dry-run mode)

### Dry Run (Default)

To test without renaming files:

```bash
python3 movie_renamer.py --input=/path/to/movielist.txt
```

## Naming Convention

- **Folder**: `Movie_Name_(year)`
- **Video File**: `Movie_Name_(year)_[imdbid-ttXXXXXX].ext`

## Examples

1. **Generate input file**:
   ```bash
   python3 movie_renamer.py --generate-inputfile-from=/path/to/Source --fileoutput=/home/your_username
   ```

2. **Process with dry run**:
   ```bash
   python3 movie_renamer.py --input=/home/your_username/movielist.txt
   ```

3. **Process and rename files**:
   ```bash
   python3 movie_renamer.py --input=/home/your_username/movielist.txt --execute --backlog-file=/home/your_username/unmatched.txt
   ```

## Backlog File

Movies that cannot be matched on IMDB are written to a backlog file (default: `backlog.txt`) for manual review. This includes:
- Movies with ambiguous versions (multiple years, no year in folder name)
- Movies not found on IMDB

## Configuration

Edit the script to change:
- `BASE_PATH`: Root directory containing movie folders
- `WAIT_TIME`: Delay between IMDB requests (be respectful)

## Troubleshooting

- **Command not found**: Use `python3` instead of `python`
- **Permission errors**: Ensure write access to output directories
- **IMDB blocking**: The script includes delays; if blocked, wait and retry
- **Missing dependencies**: Install with `pip3 install requests beautifulsoup4`

## License

[Add your license here]

## Jellyfin Naming Convention

The script follows the Jellyfin naming convention:
- **Folder format:** `Movie_Name (year)`
- **Video format:** `Movie_Name (year) [imdbid-ttXXXXXX].extension`

This ensures optimal metadata matching and organization in Jellyfin.

## Safety Features

- Dry run mode prevents accidental changes
- Skips movies that can't be matched on IMDB
- Preserves original titles (especially German ones)
- Rate limiting to respect IMDB servers

## License

MIT License