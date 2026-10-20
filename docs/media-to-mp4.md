# Media → MP4 (`media_to_mp4.py`)

Converts legacy media in a movie folder to H.264/AAC MP4 (German audio preferred, no subtitles).

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

Output: `Movie (1965)/Movie (1965).mp4`. HandBrake progress can contain non-UTF-8 bytes; the CLI and triage GUI decode with replacement so converts are not aborted mid-encode.

> **Note:** [ratDVD](https://www.ratdvd.ca/) XC unpacks (fake VOBs from XVO) cannot be converted by HandBrake/ffmpeg. You need the original [ratDVD](https://www.ratdvd.ca/) application to expand them to a real DVD, or keep the `.ratDVD` source.
