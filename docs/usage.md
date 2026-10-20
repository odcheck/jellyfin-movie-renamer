# Usage

## 1. Generate an input file

Scan a movie directory and create `movielist.txt`.

```bash
python3 movie_renamer.py --generate-inputfile-from=/path/to/Source --fileoutput=/home/your_username
```

Creates `/home/your_username/movielist.txt` with a header so later `--input` runs know the base path:

```text
# BASE_PATH=/path/to/Source
```

## 2. Dry run

Default mode — no files are changed.

```bash
python3 movie_renamer.py --input=/home/your_username/movielist.txt
```

Useful for checking proposed folder/video names, skipped titles, and backlog items.

## 3. Execute

```bash
python3 movie_renamer.py --input=/home/your_username/movielist.txt --execute
```

## 4. Verbose debugging

```bash
python3 movie_renamer.py --input=/home/your_username/movielist.txt --debug
```

Prints metadata clues, IMDb IDs from local files, search attempts, candidate selection, and skip reasons.

## Command-line options

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

## Default files

If you do not override them:

- backlog: `~/backlog.txt`
- skipped: `~/backlog_skipped.txt`
- success: `~/success.txt`

## Output files

### Success

```text
Folder: OldName -> New_Name (2012)
Video: oldfile.avi -> New_Name (2012) [imdbid-tt1234567].avi
```

### Backlog

IMDb matched but no usable normal video file (empty folder, no real movie file). Valid DVD/ISO structures are not treated as backlog.

### Skipped

No safe IMDb decision:

```text
Serenity | No confident IMDB match found from folder, file, and metadata clues
```

## Example workflows

### Typical safe workflow

```bash
python3 movie_renamer.py --generate-inputfile-from=/path/to/Source --fileoutput=/home/your_username
python3 movie_renamer.py --input=/home/your_username/movielist.txt --debug
python3 movie_renamer.py --input=/home/your_username/movielist.txt --execute
```

### Custom output files

```bash
python3 movie_renamer.py \
  --input=/home/your_username/movielist.txt \
  --execute \
  --backlog-file=/home/your_username/custom_backlog.txt \
  --success-file=/home/your_username/custom_success.txt
```

Skipped entries go to `/home/your_username/custom_backlog_skipped.txt`.
