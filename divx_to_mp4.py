#!/usr/bin/env python3
"""Convert .divx files in a movie folder to .mp4 (ffmpeg)."""
import argparse
import shutil
import subprocess
import sys
from pathlib import Path


def find_divx(target: Path):
    if target.is_file() and target.suffix.lower() == ".divx":
        return [target]
    if target.is_dir():
        found = []
        for path in sorted(target.rglob("*")):
            if path.is_file() and path.suffix.lower() == ".divx":
                # skip Synology thumbs etc.
                if any(p.startswith(".") or p.startswith("@") for p in path.parts):
                    continue
                found.append(path)
        return found
    return []


def convert_one(src: Path, keep_divx: bool, dry_run: bool, reencode: bool):
    dest = src.with_suffix(".mp4")
    print(f"Input:  {src}")
    print(f"Output: {dest}")
    if dest.exists():
        raise SystemExit(f"Target already exists: {dest}")
    if dry_run:
        print("  [dry-run] would convert")
        return

    if reencode:
        cmd = [
            "ffmpeg", "-hide_banner", "-y",
            "-i", str(src),
            "-c:v", "libx264", "-crf", "20", "-preset", "medium",
            "-c:a", "aac", "-b:a", "192k",
            "-movflags", "+faststart",
            str(dest),
        ]
        print("  Encoding H.264/AAC mp4…")
        proc = subprocess.run(cmd)
        if proc.returncode != 0:
            if dest.exists():
                dest.unlink()
            raise SystemExit(f"ffmpeg encode failed ({proc.returncode})")
    else:
        # Try remux first (fast); fall back to encode.
        cmd_copy = [
            "ffmpeg", "-hide_banner", "-y",
            "-i", str(src),
            "-c", "copy",
            "-movflags", "+faststart",
            str(dest),
        ]
        print("  Trying stream copy…")
        proc = subprocess.run(cmd_copy, capture_output=True, text=True)
        if proc.returncode != 0 or not dest.exists() or dest.stat().st_size < 1000:
            if dest.exists():
                dest.unlink()
            print("  Copy failed — encoding H.264/AAC…")
            cmd = [
                "ffmpeg", "-hide_banner", "-y",
                "-i", str(src),
                "-c:v", "libx264", "-crf", "20", "-preset", "medium",
                "-c:a", "aac", "-b:a", "192k",
                "-movflags", "+faststart",
                str(dest),
            ]
            proc = subprocess.run(cmd)
            if proc.returncode != 0:
                if dest.exists():
                    dest.unlink()
                raise SystemExit(f"ffmpeg encode failed ({proc.returncode})")

    if not keep_divx:
        print(f"  Removing {src.name}")
        src.unlink()
    print("  Done.")


def main():
    parser = argparse.ArgumentParser(description="Convert .divx → .mp4 via ffmpeg")
    parser.add_argument("path", help="Movie folder or a .divx file")
    parser.add_argument("--keep-divx", action="store_true")
    parser.add_argument(
        "--reencode",
        action="store_true",
        help="Always re-encode (skip stream-copy attempt)",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if shutil.which("ffmpeg") is None:
        raise SystemExit("ffmpeg not found")

    target = Path(args.path).expanduser().resolve()
    files = find_divx(target)
    if not files:
        raise SystemExit(f"No .divx found in {target}")

    for src in files:
        convert_one(src, keep_divx=args.keep_divx, dry_run=args.dry_run, reencode=args.reencode)


if __name__ == "__main__":
    main()
