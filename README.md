# Jellyfin Movie Renamer

A Python script to rename movie folders and files to follow Jellyfin naming conventions.

## Features

- Searches IMDB for movie information (year and ID)
- Preserves German movie titles
- Renames folders to: `Movie_Name (year)`
- Renames video files to: `Movie_Name (year) [imdbid-ttXXXXXX].ext`
- Dry run mode for safe testing
- Respectful IMDB request rate limiting

## Installation

```bash
pip install -r requirements.txt
```

## Usage

1. Edit the `BASE_PATH` in the script to point to your movie directory
2. Customize the `MOVIES_TO_PROCESS` list with your movies
3. Run in dry-run mode first: `python movie_renamer.py`
4. Review the proposed changes
5. Set `DRY_RUN = False` when ready to rename files

## Configuration

- `BASE_PATH`: Path to your movie collection
- `DRY_RUN`: Set to False to actually rename files (default: True)
- `WAIT_TIME`: Delay between IMDB requests in seconds (default: 1)
- `MOVIES_TO_PROCESS`: List of movie folders to process

## Example

**Before:**
```
Body Snatchers/
├── snatchers.ac3.x264-JPL.mkv
└── snatchers.ac3.x264-JPL.nfo.txt
```

**After:**
```
Body_Snatchers (1993)/
├── Body_Snatchers (1993) [imdbid-tt0106452].mkv
└── snatchers.ac3.x264-JPL.nfo.txt
```

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