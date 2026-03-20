# Jellyfin Movie Renamer

Rename movie folders and movie files into a Jellyfin-friendly format using IMDb lookup, local metadata, and safe ambiguity handling.

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

## Current Behavior

- Uses IMDb lookup for title, year, and IMDb ID
- Prefers German/localized titles when strong local evidence points to them
- Reads local metadata from `.nfo`, `.txt`, and `.xml`
- Extracts title clues, year clues, and direct `tt...` IMDb IDs from metadata files
- Cleans noisy release tags from folder and file names:
  - `DVDRip`, `XviD`, `German`, `R5`, `SD`, `LD`, `PROPER`, and similar
- Finds video files in subfolders
- Moves nested video files into the movie folder root
- Removes empty subfolders after flattening
- Treats `VIDEO_TS` folders like DVDs:
  - rename folder
  - keep DVD structure unchanged
- Treats `.iso` files like DVDs:
  - rename folder
  - keep ISO file unchanged
- Detects extras such as trailers, samples, clips, interviews, deleted scenes, featurettes, and other Jellyfin-supported extras
- Ignores tiny main-video candidates under `100 MB`
- Verifies real video files instead of trusting file extensions alone
- Skips ambiguous matches instead of forcing bad renames

## Requirements

- Python 3.6+
- `requests`
- `beautifulsoup4`
- `file` command available on the host for MIME-type verification

## Installation

Debian/Ubuntu:

```bash
sudo apt update
sudo apt install python3 python3-requests python3-bs4 file
```

Or with `pip`:

```bash
pip3 install requests beautifulsoup4
```

## Usage

### 1. Generate an Input File

Scan a movie directory and create `movielist.txt`.

```bash
python3 movie_renamer.py --generate-inputfile-from=/path/to/movies --fileoutput=/path/to/output
```

Example:

```bash
python3 movie_renamer.py --generate-inputfile-from=/mnt/nfs/huddle/media/Filme/_RUN --fileoutput=/home/ben
```

This creates:

```text
/home/ben/movielist.txt
```

The generated file also stores the source folder in a header:

```text
# BASE_PATH=/mnt/nfs/huddle/media/Filme/_RUN
```

That means a later `--input` run automatically knows where to look.

### 2. Dry Run

Dry run is the default. No files are changed.

```bash
python3 movie_renamer.py --input=/home/ben/movielist.txt
```

Useful for checking:

- proposed folder names
- proposed video names
- skipped titles
- backlog items

### 3. Execute Real Renaming

```bash
python3 movie_renamer.py --input=/home/ben/movielist.txt --execute
```

### 4. Verbose Debugging

```bash
python3 movie_renamer.py --input=/home/ben/movielist.txt --debug
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

If multiple plausible IMDb matches remain and there is not enough evidence to decide safely, the movie is skipped.

## DVD / ISO Handling

### VIDEO_TS

If a folder contains a valid `VIDEO_TS` structure:

- the movie folder can be renamed
- `VIDEO_TS` contents are left unchanged

### ISO

If a folder contains one or more `.iso` files:

- the movie folder can be renamed
- ISO files are left unchanged

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
python3 movie_renamer.py --generate-inputfile-from=/mnt/nfs/huddle/media/Filme/_RUN --fileoutput=/home/ben
```

2. Inspect with dry-run:

```bash
python3 movie_renamer.py --input=/home/ben/movielist.txt --debug
```

3. If the output looks correct, execute:

```bash
python3 movie_renamer.py --input=/home/ben/movielist.txt --execute
```

### Custom Output Files

```bash
python3 movie_renamer.py \
  --input=/home/ben/movielist.txt \
  --execute \
  --backlog-file=/home/ben/custom_backlog.txt \
  --success-file=/home/ben/custom_success.txt
```

Skipped entries will be written automatically to:

```text
/home/ben/custom_backlog_skipped.txt
```

## Troubleshooting

### Dry-run says everything is skipped

Possible causes:

- IMDb lookup source is temporarily blocked or challenged
- folder names are too ambiguous
- not enough metadata is present

Use:

```bash
python3 movie_renamer.py --input=/home/ben/movielist.txt --debug
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

## License

MIT License
