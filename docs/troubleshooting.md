# Troubleshooting

## Dry-run says everything is skipped

Possible causes:

- IMDb lookup source is temporarily blocked or challenged
- folder names are too ambiguous
- not enough metadata is present

Use:

```bash
python3 movie_renamer.py --input=/home/your_username/movielist.txt --debug
```

## A title was skipped even though a metadata file exists

Check whether the folder contains `.nfo`, `.txt`, `.xml`, or a direct `tt...` IMDb ID.

The script reads those as lookup clues, but still skips if the final match is not safe.

## A movie has the wrong local year but the right IMDb result

IMDb is the final source of truth for the target year once the title match is confidently resolved.

## A title is still ambiguous

Add one of:

- the year in the folder name
- a metadata file with title/year
- a direct IMDb ID
