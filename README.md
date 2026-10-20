# Jellyfin Movie Renamer

[![Python](https://img.shields.io/badge/Python-3.6%2B-blue?logo=python&logoColor=white)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Jellyfin](https://img.shields.io/badge/Jellyfin-compatible-00A4DC?logo=jellyfin&logoColor=white)](https://jellyfin.org/)
[![ffmpeg](https://img.shields.io/badge/ffmpeg-required-green?logo=ffmpeg&logoColor=white)](https://ffmpeg.org/)
[![HandBrake](https://img.shields.io/badge/HandBrake-optional-darkred?logo=handbrake&logoColor=white)](https://handbrake.fr/)

Rename movie folders and files into a Jellyfin-friendly format using IMDb lookup, local metadata, and safe ambiguity handling. Optional tools convert legacy disc/container media to MP4 and triage skipped folders in a GUI.

## What It Does

- Folder: `Movie_Name (year)`
- File: `Movie_Name (year) [imdbid-ttXXXXXX].ext` (multipart `-cdN`, extras `-trailer` / `-sample`, …)
- Convert DivX / mpg / DVD / ISO / [ratDVD](https://www.ratdvd.ca/) / Blu-ray m2ts → MP4 (`media_to_mp4.py`)
- Triage GUI for skips, year/IMDb, pending rename, batch convert (`skip_triage_gui.py`)

Dry-run by default; ambiguous matches are skipped instead of guessed.

## Requirements

- Python 3.6+, `requests`, `beautifulsoup4`, `file`
- For converts: [ffmpeg](https://ffmpeg.org/) / `ffprobe`, [HandBrakeCLI](https://handbrake.fr/) (Flatpak `fr.handbrake.ghb` + `libdvdcss` preferred), `7z`/`7za`

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

| Path | Description | Default |
| --- | --- | --- |
| **Source** | dirty source folder | `~/Source` |
| **Destination** | cleaned Jellyfin folder | `~/Destination` |
| **Work** | scratch / archives | `~/renamer_work` |

Priority: env `SOURCE` / `DESTINATION` / `WORK_DIR` → `~/.config/jellyfin-movie-renamer/paths.json` → defaults.  
Details: [docs/triage-gui.md](docs/triage-gui.md).

## Quick start

```bash
# 1. Scan Source → movielist.txt
python3 movie_renamer.py --generate-inputfile-from=/path/to/Source --fileoutput=/home/your_username

# 2. Dry-run (default)
python3 movie_renamer.py --input=/home/your_username/movielist.txt --debug

# 3. Execute
python3 movie_renamer.py --input=/home/your_username/movielist.txt --execute

# Optional: GUI triage / convert
python3 skip_triage_gui.py
python3 media_to_mp4.py "/path/to/Movie (1965)"
```

## Docs

| Doc | Contents |
| --- | --- |
| [docs/usage.md](docs/usage.md) | CLI options, output files, workflows |
| [docs/behavior.md](docs/behavior.md) | Lookup rules, extras, DVD/ISO rename, safety |
| [docs/media-to-mp4.md](docs/media-to-mp4.md) | Legacy media → MP4 |
| [docs/triage-gui.md](docs/triage-gui.md) | Paths config + triage GUI |
| [docs/troubleshooting.md](docs/troubleshooting.md) | Common skip / match issues |

## Credits

| | Project | Role |
| --- | --- | --- |
| ![Jellyfin](https://cdn.simpleicons.org/jellyfin/00A4DC) | [Jellyfin](https://jellyfin.org/) | Naming conventions this renamer targets |
| ![ffmpeg](https://cdn.simpleicons.org/ffmpeg/007808) | [ffmpeg](https://ffmpeg.org/) | Container convert |
| ![HandBrake](https://cdn.simpleicons.org/handbrake/D2BD1A) | [HandBrake](https://handbrake.fr/) | DVD / ISO encode |
| | [ratDVD](https://www.ratdvd.ca/) | Legacy `.ratDVD` format |
| ![Python](https://cdn.simpleicons.org/python/3776AB) | [Python](https://www.python.org/) | Runtime |
| | [IMDb](https://www.imdb.com/) | Title / year / ID lookup |
| | [Beautiful Soup](https://www.crummy.com/software/BeautifulSoup/) | HTML parsing |
| | [Requests](https://requests.readthedocs.io/) | HTTP client |
| | [7-Zip](https://www.7-zip.org/) | ISO / archive extract fallback |
| | [libdvdcss](https://www.videolan.org/developers/libdvdcss.html) | CSS DVD support with HandBrake |

## License

MIT License
