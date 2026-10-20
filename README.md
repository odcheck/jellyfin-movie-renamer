# Jellyfin Movie Renamer

[![Python](https://img.shields.io/badge/Python-3.6%2B-blue?logo=python&logoColor=white)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Jellyfin](https://img.shields.io/badge/Jellyfin-compatible-00A4DC?logo=jellyfin&logoColor=white)](https://jellyfin.org/)
[![ffmpeg](https://img.shields.io/badge/ffmpeg-required-green?logo=ffmpeg&logoColor=white)](https://ffmpeg.org/)
[![HandBrake](https://img.shields.io/badge/HandBrake-optional-darkred?logo=handbrake&logoColor=white)](https://handbrake.fr/)

Rename movie folders and files into a Jellyfin-friendly format using IMDb lookup, local metadata, and safe ambiguity handling. Optional tools convert legacy disc/container media to MP4 and triage skipped folders in a GUI.

## Table of Contents

- [What It Does](#what-it-does)
- [Current Behavior](#current-behavior)
- [Requirements](#requirements)
- [Installation](#installation)
- [Library Paths](#library-paths)
- [Usage](#usage)
- [Command-Line Options](#command-line-options)
- [Default Files](#default-files)
- [Output Files](#output-files)
- [Lookup Rules](#lookup-rules)
- [DVD / ISO Handling](#dvd--iso-handling-rename)
- [Media → MP4](#media--mp4-media_to_mp4py)
- [Skip Triage GUI](#skip-triage-gui-skip_triage_guipy)
- [Nested Video Handling](#nested-video-handling)
- [Example Workflows](#example-workflows)
- [Troubleshooting](#troubleshooting)
- [Safety](#safety)
- [Credits](#credits)
- [License](#license)

## What It Does

- Renames movie folders to:
  - `Movie_Name (year)`
- Renames movie files to:
  - `Movie_Name (year) [imdbid-ttXXXXXX].ext`
- Renames multipart movie files to:
  - `Movie_Name (year) [imdbid-ttXXXXXX]-cd1.ext`
  - `Movie_Name (year) [imdbid-ttXXXXXX]-cd2.ext`
- Renames extras to Jellyfin-style suffixes, for example:
  - `Movie_Name (year) [imdbid-ttXXXXXX]-sample.ext`
  - `Movie_Name (year) [imdbid-ttXXXXXX]-trailer.ext`
- Converts DivX / mpg / mpeg / DVD (`VIDEO_TS`) / ISO / [ratDVD](https://www.ratdvd.ca/) / Blu-ray `STREAM/*.m2ts` → MP4 (`media_to_mp4.py`)
- Triages skipped folders in a GUI: set year/IMDb, queue pending renames, batch convert to MP4 (`skip_triage_gui.py`)

## Current Behavior

- Uses IMDb lookup for title, year, and IMDb ID
- A single IMDb ID in local metadata / `movie.nfo` (e.g. from the triage GUI) is authoritative; if online lookup fails, folder title + year are used with that ID
- Never uses a bare `tt#######` string as the display title
- Prefers German/localized titles when strong local evidence points to them
- Reads local metadata from `.nfo`, `.txt`, and `.xml`
- Extracts title clues, year clues, and direct `tt...` IMDb IDs from metadata files
- Cleans noisy release tags from folder and file names:
  - `DVDRip`, `XviD`, `German`, `R5`, `SD`, `LD`, `PROPER`, and similar
- Finds video files in subfolders
- Moves nested video files into the movie folder root
- Removes empty subfolders after flattening
- Treats `VIDEO_TS` / `.iso` like discs for rename (structure kept); convert to MP4 separately if desired
- Detects extras (trailer, sample, interview, …); ambiguous tokens like `other` / `short` only count as extras when they are the whole name or a trailing marker (avoids `love.other.drugs`)
- Ignores tiny main-video candidates under `100 MB`
- Verifies real video files instead of trusting file extensions alone
- Skips ambiguous matches instead of forcing bad renames

## Requirements

- Python 3.6+
- `requests`
- `beautifulsoup4`
- `file` command available on the host for MIME-type verification
- For `media_to_mp4.py`: [ffmpeg](https://ffmpeg.org/) / `ffprobe`; [HandBrakeCLI](https://handbrake.fr/) (Flatpak `fr.handbrake.ghb` preferred, with `libdvdcss` for CSS DVDs); `7z`/`7za` as ISO extract fallback

## Installation

Debian/Ubuntu:

```bash
sudo apt update
sudo apt install python3 python3-requests python3-bs4 file
```

Fedora:

```bash
sudo dnf install python3 python3-requests python3-beautifulsoup4 file
```

Homebrew (macOS / Linux):

```bash
brew install python
pip3 install requests beautifulsoup4
```

Or with `pip` only:

```bash
pip3 install requests beautifulsoup4
```

Optional tools for `media_to_mp4.py`:

```bash
# Debian/Ubuntu
sudo apt install ffmpeg handbrake-cli p7zip-full

# Fedora
sudo dnf install ffmpeg HandBrake-cli p7zip p7zip-plugins

# Homebrew
brew install ffmpeg handbrake p7zip
```

## Library Paths

Triage GUI and batch scripts share one path config.

| Path | Description | Default |
| --- | --- | --- |
| **Source** | dirty source folder | `~/Source` |
| **Destination** | cleaned Jellyfin folder | `~/Destination` |
| **Work** | scratch / archives | `~/renamer_work` |

**Priority:** environment variables → config file → defaults above.

| Variable | Sets |
| --- | --- |
| `SOURCE` | Source (`FILME` still accepted) |
| `DESTINATION` | Destination (`MOVIES` still accepted) |
| `WORK_DIR` | Work |

Config file (written by the GUI **Save paths** button):

```text
~/.config/jellyfin-movie-renamer/paths.json
```

Example:

```json
{
  "source": "/path/to/Source",
  "destination": "/path/to/Destination",
  "work": "/path/to/renamer_work"
}
```

CLI overrides (same paths as the GUI):

```bash
./batch_filme_to_movies.sh --source /path/to/Source --destination /path/to/Destination --work /path/to/work
./retry_skips.sh --source /path/to/Source --work /path/to/work
```

Inspect resolved paths:

```bash
python3 paths_config.py --print
```

## Usage

### 1. Generate an Input File

Scan a movie directory and create `movielist.txt`.

```bash
python3 movie_renamer.py --generate-inputfile-from=/path/to/Source --fileoutput=/home/your_username
```

This creates:

```text
/home/your_username/movielist.txt
```

The generated file also stores the source folder in a header:

```text
# BASE_PATH=/path/to/Source
```

That means a later `--input` run automatically knows where to look.

### 2. Dry Run

Dry run is the default. No files are changed.

```bash
python3 movie_renamer.py --input=/home/your_username/movielist.txt
```

Useful for checking:

- proposed folder names
- proposed video names
- skipped titles
- backlog items

### 3. Execute Real Renaming

```bash
python3 movie_renamer.py --input=/home/your_username/movielist.txt --execute
```

### 4. Verbose Debugging

```bash
python3 movie_renamer.py --input=/home/your_username/movielist.txt --debug
```

This prints:

- metadata clues found
- IMDb IDs found in local files
- search attempts
- candidate selection decisions
- skip reasons

## Command-Line Options

```text
--input INPUT
    Read movie folder names from an input file

--generate-inputfile-from PATH
    Scan a directory and generate movielist.txt

--base-path PATH
    Override the movie base directory manually
    Default: the # BASE_PATH header from the input file, otherwise the current user's home directory

--fileoutput PATH
    Directory where movielist.txt should be written
    Default: current directory

--backlog-file PATH
    File for titles that matched IMDb but had no usable video files
    Default: ~/backlog.txt

--success-file PATH
    File for successful rename operations
    Default: ~/success.txt

--execute
    Perform the actual renaming
    Without this flag, the script runs in dry-run mode

--debug
    Enable verbose debugging output

--limit N
    Process at most N movies from the input file

--offset N
    Skip the first N movies (use with --limit for batches)
```

## Default Files

If you do not override them:

- backlog:
  - `~/backlog.txt`
- skipped:
  - `~/backlog_skipped.txt`
- success:
  - `~/success.txt`

## Output Files

### Success File

Lists successful folder and video rename mappings, for example:

```text
Folder: OldName -> New_Name (2012)
Video: oldfile.avi -> New_Name (2012) [imdbid-tt1234567].avi
```

### Backlog File

Used when a movie matched IMDb but no usable normal video file was found.

Typical examples:

- empty folder
- folder with no real movie file

DVD and ISO cases are not treated as backlog if they are valid disc structures.

### Skipped File

Used when the script could not make a safe IMDb decision.

Example:

```text
Serenity | No confident IMDB match found from folder, file, and metadata clues
```

## Lookup Rules

The script considers:

- folder name
- video filenames
- metadata files inside the folder
- direct IMDb IDs found in metadata
- local years
- alternate titles
- German or localized titles

If metadata strongly identifies the movie, that evidence is used.

A single local `tt...` IMDb ID is treated as authoritative: online details are preferred, but rename can still proceed from folder title/year if IMDb is unreachable.

If multiple plausible IMDb matches remain and there is not enough evidence to decide safely, the movie is skipped.

## DVD / ISO Handling (rename)

### VIDEO_TS

If a folder contains a valid `VIDEO_TS` structure:

- the movie folder can be renamed
- `VIDEO_TS` contents are left unchanged by the renamer

### ISO

If a folder contains one or more `.iso` files:

- the movie folder can be renamed
- ISO files are left unchanged by the renamer

To turn discs/containers into a single MP4 first, use Media → MP4 below.

## Media → MP4 (`media_to_mp4.py`)

Converts legacy media in a movie folder to H.264/AAC MP4 (German audio preferred, no subtitles):

| Source | Tool |
| --- | --- |
| `.divx` / `.mpg` / `.mpeg` | [ffmpeg](https://ffmpeg.org/) |
| `VIDEO_TS` / flat IFO+VOB | [HandBrake](https://handbrake.fr/) (+libdvdcss); ffmpeg VOB-concat fallback |
| `.iso` / `.ISO` | HandBrake; on failure extract via 7z then DVD path |
| [`.ratDVD`](https://www.ratdvd.ca/) | unpack → `VIDEO_TS` → same as DVD ([XC format](https://www.ratdvd.ca/) cannot convert — needs the original [ratDVD](https://www.ratdvd.ca/) app) |
| Blu-ray `STREAM/*.m2ts` | ffmpeg concat (playlist order when `.mpls` present) |

```bash
# one folder
python3 media_to_mp4.py "/path/to/Movie (1965)"

# dry-run / keep sources after convert
python3 media_to_mp4.py --dry-run --keep-source "/path/to/Movie (1965)"

# paths listed one per line
python3 media_to_mp4.py --from-list ~/to_convert.txt
```

Output file: `Movie (1965)/Movie (1965).mp4`. HandBrake progress can contain non-UTF-8 bytes; the CLI and triage GUI decode with replacement so converts are not aborted mid-encode.

> **Note:** [ratDVD](https://www.ratdvd.ca/) XC unpacks (fake VOBs from XVO) cannot be converted by HandBrake/ffmpeg. You need the original [ratDVD](https://www.ratdvd.ca/) application to expand them to a real DVD, or keep the `.ratDVD` source.

## Skip Triage GUI (`skip_triage_gui.py`)

Interactive triage: **Source** (= dirty source folder) → convert / rename → **Destination** (= cleaned Jellyfin folder):

```bash
python3 skip_triage_gui.py
```

Set **Source** / **Destination** / **Work** in the top row and click **Save paths** (writes `~/.config/jellyfin-movie-renamer/paths.json`). Batch scripts pick up the same file. See [Library Paths](#library-paths).

Typical flow:

1. Open a folder — set year and/or IMDb ID → **Pending**
2. If the folder still has ISO / DVD / DivX / mpg / [ratDVD](https://www.ratdvd.ca/) / m2ts → **→ mp4** (or **Batch media→mp4**)
3. When format is OK (`mkv`/`mp4`/`avi`) → **Batch ×N** runs the renamer on pending titles

Closing the GUI does not kill an in-flight HandBrake encode; stop leftover jobs with `pkill -f HandBrakeCLI` / `pkill -f media_to_mp4` if needed.

## Nested Video Handling

If a movie folder contains:

- one video file: it is renamed
- multiple video files: they are renamed as multipart files
- a subfolder with video files: they are moved to the movie root and renamed
- extras such as samples or trailers: they are renamed with Jellyfin extra suffixes instead of multipart `-cdN`
- tiny files under `100 MB`: ignored unless recognized as extras

Example:

```text
Incorrect:
super_movie (1968)[imdbid-123456].avi-cd1
super_movie (1968)[imdbid-123456].avi-cd2

Correct:
super_movie (1968)[imdbid-123456]-cd1.avi
super_movie (1968)[imdbid-123456]-cd2.avi
```

## Example Workflows

### Typical Safe Workflow

1. Generate a list:

```bash
python3 movie_renamer.py --generate-inputfile-from=/path/to/Source --fileoutput=/home/your_username
```

2. Inspect with dry-run:

```bash
python3 movie_renamer.py --input=/home/your_username/movielist.txt --debug
```

3. If the output looks correct, execute:

```bash
python3 movie_renamer.py --input=/home/your_username/movielist.txt --execute
```

### Custom Output Files

```bash
python3 movie_renamer.py \
  --input=/home/your_username/movielist.txt \
  --execute \
  --backlog-file=/home/your_username/custom_backlog.txt \
  --success-file=/home/your_username/custom_success.txt
```

Skipped entries will be written automatically to:

```text
/home/your_username/custom_backlog_skipped.txt
```

## Troubleshooting

### Dry-run says everything is skipped

Possible causes:

- IMDb lookup source is temporarily blocked or challenged
- folder names are too ambiguous
- not enough metadata is present

Use:

```bash
python3 movie_renamer.py --input=/home/your_username/movielist.txt --debug
```

### A title was skipped even though a metadata file exists

Check whether the folder contains:

- `.nfo`
- `.txt`
- `.xml`
- a direct `tt...` IMDb ID

The script reads those files and uses them as lookup clues, but it still skips if the final match is not safe.

### A movie has the wrong local year but the right IMDb result

IMDb is treated as the final source of truth for the target year once the title match is confidently resolved.

### A title is still ambiguous

Add one of:

- the year in the folder name
- a metadata file with title/year
- a direct IMDb ID

## Safety

- dry-run by default
- ambiguity skips instead of blind guessing
- preserves DVD structures and ISO images
- records successful renames
- records skipped items with reasons

## Credits

This project builds on and gratefully uses:

| | Project | Role |
| --- | --- | --- |
| ![Jellyfin](https://cdn.simpleicons.org/jellyfin/00A4DC) | [Jellyfin](https://jellyfin.org/) | Naming conventions this renamer targets |
| ![ffmpeg](https://cdn.simpleicons.org/ffmpeg/007808) | [ffmpeg](https://ffmpeg.org/) | Container convert (DivX/mpg/mpeg, Blu-ray m2ts, DVD fallback) |
| ![HandBrake](https://cdn.simpleicons.org/handbrake/D2BD1A) | [HandBrake](https://handbrake.fr/) | DVD / ISO encode via HandBrakeCLI |
| | [ratDVD](https://www.ratdvd.ca/) | Legacy `.ratDVD` container format (unpack / XC note) |
| ![Python](https://cdn.simpleicons.org/python/3776AB) | [Python](https://www.python.org/) | Runtime |
| | [IMDb](https://www.imdb.com/) | Title / year / ID lookup |
| | [Beautiful Soup](https://www.crummy.com/software/BeautifulSoup/) | HTML parsing for lookups |
| | [Requests](https://requests.readthedocs.io/) | HTTP client |
| | [7-Zip](https://www.7-zip.org/) | ISO / archive extract fallback |
| | [libdvdcss](https://www.videolan.org/developers/libdvdcss.html) | CSS-encrypted DVD support with HandBrake |

Repository: [odcheck/jellyfin-movie-renamer](https://github.com/odcheck/jellyfin-movie-renamer)

## License

MIT License
