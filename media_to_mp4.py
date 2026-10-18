#!/usr/bin/env python3
"""
Convert movie-folder media to MP4:
  - .divx → mp4
  - .ratDVD → VIDEO_TS → mp4
  - VIDEO_TS → mp4

Always: prefer German audio (deu/ger/de), no subtitles (-sn), H.264/AAC mp4.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# Reuse ratDVD unpacker
sys.path.insert(0, str(Path(__file__).resolve().parent))
import ratdvd_to_video_ts as ratdvd  # noqa: E402


GERMAN_LANGS = {"deu", "ger", "de", "german", "deutsch"}


def run(cmd, **kwargs):
    print("$ " + " ".join(str(c) for c in cmd))
    return subprocess.run(cmd, **kwargs)


def find_divx(folder: Path):
    out = []
    for path in folder.rglob("*"):
        if not path.is_file() or path.suffix.lower() != ".divx":
            continue
        if any(p.startswith(".") or p.startswith("@") for p in path.parts):
            continue
        out.append(path)
    return sorted(out)


def find_ratdvds(folder: Path):
    return ratdvd.find_ratdvds(folder)


def find_video_ts(folder: Path) -> Path | None:
    direct = folder / "VIDEO_TS"
    if direct.is_dir():
        return direct
    for child in folder.iterdir():
        if child.is_dir() and (child / "VIDEO_TS").is_dir():
            return child / "VIDEO_TS"
        if child.is_dir() and child.name.upper() == "VIDEO_TS":
            return child
    return None


def needs_convert(folder: Path) -> bool:
    return bool(find_divx(folder) or find_ratdvds(folder) or find_video_ts(folder))


def ffprobe_streams(source: str | Path):
    proc = subprocess.run(
        [
            "ffprobe", "-hide_banner", "-v", "quiet",
            "-print_format", "json", "-show_streams",
            str(source),
        ],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        return []
    try:
        return json.loads(proc.stdout).get("streams") or []
    except json.JSONDecodeError:
        return []


def pick_german_audio_index(streams) -> int | None:
    """Return 0-based index among audio streams (for -map 0:a:N)."""
    audios = [s for s in streams if s.get("codec_type") == "audio"]
    if not audios:
        return None
    for i, stream in enumerate(audios):
        tags = stream.get("tags") or {}
        lang = str(tags.get("language") or tags.get("LANGUAGE") or "").lower()
        title = str(tags.get("title") or "").lower()
        if lang in GERMAN_LANGS or any(g in title for g in ("deutsch", "german", "ger ", " deu")):
            print(f"  Audio: German track a:{i} (lang={lang or '?'})")
            return i
    print("  Audio: no German tag — using first audio track a:0")
    return 0


def encode_to_mp4(input_args: list, dest: Path, audio_index: int | None, dry_run: bool):
    """input_args: ffmpeg input options including -i …"""
    if dest.exists():
        raise RuntimeError(f"Target exists: {dest}")
    map_args = ["-map", "0:v:0"]
    if audio_index is not None:
        map_args += ["-map", f"0:a:{audio_index}"]
    else:
        print("  WARNING: no audio stream found")
    cmd = [
        "ffmpeg", "-hide_banner", "-y",
        *input_args,
        *map_args,
        "-sn",  # no subtitles
        "-c:v", "libx264", "-crf", "20", "-preset", "medium",
        "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart",
        str(dest),
    ]
    if dry_run:
        print("  [dry-run]", " ".join(cmd))
        return
    proc = run(cmd)
    if proc.returncode != 0:
        if dest.exists():
            dest.unlink()
        raise RuntimeError(f"ffmpeg failed ({proc.returncode})")


def convert_divx_file(src: Path, dry_run: bool, keep_source: bool):
    dest = src.with_suffix(".mp4")
    print(f"divx: {src.name} → {dest.name}")
    streams = [] if dry_run else ffprobe_streams(src)
    audio_i = None if dry_run else pick_german_audio_index(streams)
    encode_to_mp4(["-i", str(src)], dest, audio_i if audio_i is not None else 0, dry_run)
    if not dry_run and not keep_source:
        src.unlink()
        print(f"  Removed {src.name}")


def largest_vts_vobs(video_ts: Path) -> list[Path]:
    """Pick the VTS_## set with the largest total size of VTS_##_1+.VOB files."""
    groups: dict[str, list[Path]] = {}
    for path in video_ts.iterdir():
        if not path.is_file():
            continue
        m = re.match(r"^(VTS_(\d+)_(\d+))\.(VOB|vob)$", path.name)
        if not m:
            continue
        part = int(m.group(3))
        if part < 1:
            continue  # skip menu _0
        key = m.group(2)
        groups.setdefault(key, []).append(path)
    if not groups:
        # fallback: any .VOB except VIDEO_TS.VOB
        vobs = [
            p for p in video_ts.iterdir()
            if p.suffix.lower() == ".vob" and p.name.upper() != "VIDEO_TS.VOB"
        ]
        return sorted(vobs, key=lambda p: p.name)
    best_key = max(groups, key=lambda k: sum(p.stat().st_size for p in groups[k]))
    return sorted(groups[best_key], key=lambda p: p.name)


def convert_video_ts(video_ts: Path, dest_mp4: Path, dry_run: bool, keep_source: bool):
    vobs = largest_vts_vobs(video_ts)
    if not vobs:
        raise RuntimeError(f"No VOB files in {video_ts}")
    print(f"DVD: {video_ts} ({len(vobs)} VOB(s) → {dest_mp4.name})")
    # concat demuxer list file (safer than concat: protocol with spaces)
    if dry_run:
        print("  [dry-run] would encode", [p.name for p in vobs])
        return

    with tempfile.NamedTemporaryFile("w", suffix=".ffconcat", delete=False) as handle:
        handle.write("ffconcat version 1.0\n")
        for vob in vobs:
            # ffmpeg concat demuxer needs escaped paths
            escaped = str(vob).replace("'", "'\\''")
            handle.write(f"file '{escaped}'\n")
        list_path = Path(handle.name)

    try:
        # Probe first vob / concat for streams
        streams = ffprobe_streams(vobs[0])
        audio_i = pick_german_audio_index(streams)
        encode_to_mp4(
            ["-f", "concat", "-safe", "0", "-i", str(list_path)],
            dest_mp4,
            audio_i if audio_i is not None else 0,
            dry_run=False,
        )
    finally:
        list_path.unlink(missing_ok=True)

    if not keep_source:
        # remove VIDEO_TS folder (and parent wrapper if only that)
        shutil.rmtree(video_ts)
        print(f"  Removed {video_ts}")


def unpack_ratdvd(folder: Path, dry_run: bool):
    rats = find_ratdvds(folder)
    if not rats:
        return
    for rat in rats:
        print(f"ratDVD: {rat.name}")
        if dry_run:
            print("  [dry-run] would unpack to VIDEO_TS")
            continue
        if (folder / "VIDEO_TS").exists():
            print("  VIDEO_TS already present — skipping unpack")
        else:
            with tempfile.TemporaryDirectory(prefix="ratdvd_") as tmp:
                extract_dir = Path(tmp) / "extract"
                ratdvd.extract_ratdvd(rat, extract_dir)
                moved = ratdvd.rebuild_video_ts(extract_dir, folder, rename_xvo=True)
                print(f"  Unpacked {moved} files")
        rat.unlink()
        print(f"  Removed {rat.name}")


def default_mp4_name(folder: Path) -> Path:
    return folder / f"{folder.name}.mp4"


def convert_folder(folder: Path, dry_run: bool, keep_source: bool):
    folder = folder.resolve()
    if not folder.is_dir():
        raise RuntimeError(f"Not a folder: {folder}")

    print(f"\n=== {folder.name} ===")
    did = False

    # 1) ratDVD → VIDEO_TS first
    if find_ratdvds(folder):
        unpack_ratdvd(folder, dry_run=dry_run)
        did = True

    # 2) divx → mp4
    for divx in find_divx(folder):
        convert_divx_file(divx, dry_run=dry_run, keep_source=keep_source)
        did = True

    # 3) VIDEO_TS → mp4
    video_ts = find_video_ts(folder)
    if video_ts:
        dest = default_mp4_name(folder)
        if dest.exists() and not dry_run:
            raise RuntimeError(f"MP4 already exists: {dest}")
        convert_video_ts(video_ts, dest, dry_run=dry_run, keep_source=keep_source)
        did = True

    if not did:
        print("  Nothing to convert (no divx/ratDVD/VIDEO_TS)")
    return did


def main():
    parser = argparse.ArgumentParser(
        description="Convert divx / DVD / ratDVD → MP4 (German audio, no subs)"
    )
    parser.add_argument(
        "paths",
        nargs="+",
        help="Movie folder(s), or a file listing folder paths",
    )
    parser.add_argument("--keep-source", action="store_true", help="Keep divx/VIDEO_TS after convert")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--from-list",
        action="store_true",
        help="Treat the single path argument as a text file of folder paths",
    )
    args = parser.parse_args()

    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        raise SystemExit("ffmpeg/ffprobe required")

    folders: list[Path] = []
    if args.from_list:
        list_file = Path(args.paths[0]).expanduser()
        for line in list_file.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            folders.append(Path(line))
    else:
        for raw in args.paths:
            folders.append(Path(raw).expanduser().resolve())

    errors = 0
    for folder in folders:
        try:
            convert_folder(folder, dry_run=args.dry_run, keep_source=args.keep_source)
        except Exception as exc:
            errors += 1
            print(f"ERROR: {folder}: {exc}", file=sys.stderr)

    if errors:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
