# Library paths and triage GUI

## Library paths

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

```json
{
  "source": "/path/to/Source",
  "destination": "/path/to/Destination",
  "work": "/path/to/renamer_work"
}
```

CLI overrides:

```bash
./batch_filme_to_movies.sh --source /path/to/Source --destination /path/to/Destination --work /path/to/work
./retry_skips.sh --source /path/to/Source --work /path/to/work
python3 paths_config.py --print
```

## Skip triage GUI (`skip_triage_gui.py`)

Interactive triage: **Source** (= dirty source folder) → convert / rename → **Destination** (= cleaned Jellyfin folder):

```bash
python3 skip_triage_gui.py
```

Set **Source** / **Destination** / **Work** in the top row and click **Save paths**.

Typical flow:

1. Open a folder — set year and/or IMDb ID → **Pending**
2. If the folder still has ISO / DVD / DivX / mpg / [ratDVD](https://www.ratdvd.ca/) / m2ts → **→ mp4** (or **Batch media→mp4**)
3. When format is OK (`mkv`/`mp4`/`avi`) → **Batch ×N** runs the renamer on pending titles

Closing the GUI does not kill an in-flight HandBrake encode; stop leftover jobs with `pkill -f HandBrakeCLI` / `pkill -f media_to_mp4` if needed.
