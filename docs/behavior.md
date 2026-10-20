# Behavior

## Current behavior

- Uses IMDb lookup for title, year, and IMDb ID
- A single IMDb ID in local metadata / `movie.nfo` (e.g. from the triage GUI) is authoritative; if online lookup fails, folder title + year are used with that ID
- Never uses a bare `tt#######` string as the display title
- Prefers German/localized titles when strong local evidence points to them
- Reads local metadata from `.nfo`, `.txt`, and `.xml`
- Extracts title clues, year clues, and direct `tt...` IMDb IDs from metadata files
- Cleans noisy release tags from folder and file names (`DVDRip`, `XviD`, `German`, `R5`, `SD`, `LD`, `PROPER`, …)
- Finds video files in subfolders; moves nested videos to the movie folder root; removes empty subfolders
- Treats `VIDEO_TS` / `.iso` like discs for rename (structure kept); convert to MP4 separately if desired
- Detects extras (trailer, sample, interview, …); ambiguous tokens like `other` / `short` only count as extras when they are the whole name or a trailing marker (avoids `love.other.drugs`)
- Ignores tiny main-video candidates under `100 MB`
- Verifies real video files instead of trusting extensions alone
- Skips ambiguous matches instead of forcing bad renames

## Lookup rules

The script considers folder name, video filenames, metadata files, direct IMDb IDs, local years, alternate titles, and German/localized titles.

If metadata strongly identifies the movie, that evidence is used. A single local `tt...` ID is authoritative: online details are preferred, but rename can proceed from folder title/year if IMDb is unreachable.

If multiple plausible matches remain without enough evidence, the movie is skipped.

## DVD / ISO handling (rename)

### VIDEO_TS

Valid `VIDEO_TS` structure: movie folder can be renamed; `VIDEO_TS` contents are left unchanged.

### ISO

One or more `.iso` files: movie folder can be renamed; ISO files are left unchanged.

To turn discs/containers into a single MP4 first, see [media-to-mp4.md](media-to-mp4.md).

## Nested video handling

- one video → renamed
- multiple videos → multipart `-cdN`
- subfolder with videos → moved to movie root and renamed
- extras (samples/trailers) → Jellyfin extra suffixes, not `-cdN`
- tiny files under `100 MB` → ignored unless recognized as extras

```text
Incorrect:
super_movie (1968)[imdbid-123456].avi-cd1
super_movie (1968)[imdbid-123456].avi-cd2

Correct:
super_movie (1968)[imdbid-123456]-cd1.avi
super_movie (1968)[imdbid-123456]-cd2.avi
```

## Safety

- dry-run by default
- ambiguity skips instead of blind guessing
- preserves DVD structures and ISO images
- records successful renames and skipped items with reasons
