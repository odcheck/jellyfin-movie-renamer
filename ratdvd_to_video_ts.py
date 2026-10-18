#!/usr/bin/env python3
"""Unpack .ratDVD into a classic VIDEO_TS folder (7z + path rebuild)."""
import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def find_ratdvds(target: Path):
    if target.is_file() and target.suffix.lower() == ".ratdvd":
        return [target]
    if target.is_dir():
        return sorted(target.glob("*.ratDVD")) + sorted(target.glob("*.ratdvd"))
    return []


def seven_zip_bin():
    for name in ("7z", "7za"):
        if shutil.which(name):
            return name
    return None


def extract_ratdvd(ratdvd: Path, extract_dir: Path):
    bin_name = seven_zip_bin()
    if not bin_name:
        raise RuntimeError("7z/7za not found")
    extract_dir.mkdir(parents=True, exist_ok=True)
    cmd = [bin_name, "e", "-y", f"-o{extract_dir}", str(ratdvd)]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(
            f"{bin_name} failed ({proc.returncode}):\n{proc.stderr or proc.stdout}"
        )


def rebuild_video_ts(extract_dir: Path, out_dir: Path, rename_xvo: bool):
    """Map AV_TS\\foo (literal backslash names or nested) → out_dir/VIDEO_TS/foo."""
    video_ts = out_dir / "VIDEO_TS"
    video_ts.mkdir(parents=True, exist_ok=True)
    sep = "\\"
    moved = 0

    for path in extract_dir.rglob("*"):
        if not path.is_file():
            continue
        # Flat extract: "AV_TS\VIDEO_TS.IFO"
        if sep in path.name:
            parts = path.name.split(sep)
            if parts[0] in ("AV_TS", "VIDEO_TS") and len(parts) > 1:
                rel_parts = parts[1:]
            else:
                rel_parts = parts
            dest = video_ts.joinpath(*rel_parts)
        elif path.name == "Version.xml":
            dest = out_dir / "Version.xml"
        else:
            # Nested extract: extract_dir/AV_TS/VIDEO_TS.IFO
            try:
                rel = path.relative_to(extract_dir)
            except ValueError:
                continue
            parts = list(rel.parts)
            if parts and parts[0] in ("AV_TS", "VIDEO_TS"):
                parts = parts[1:]
            if not parts:
                continue
            dest = video_ts.joinpath(*parts)

        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists():
            dest.unlink()
        path.rename(dest)
        moved += 1

        if rename_xvo and dest.suffix.upper() == ".XVO":
            vob = dest.with_suffix(".VOB")
            if vob.exists():
                vob.unlink()
            dest.rename(vob)
            dest = vob

        # Classic DVD players expect .BUP beside .IFO
        if dest.suffix.upper() == ".IFO":
            bup = dest.with_suffix(".BUP")
            if not bup.exists():
                shutil.copy2(dest, bup)

    return moved


def convert(target: Path, keep_ratdvd: bool, rename_xvo: bool, dry_run: bool):
    ratdvds = find_ratdvds(target)
    if not ratdvds:
        raise SystemExit(f"No .ratDVD found in {target}")

    for ratdvd in ratdvds:
        folder = ratdvd.parent if target.is_dir() or target.suffix.lower() == ".ratdvd" else target
        if target.is_file():
            folder = ratdvd.parent
        print(f"Input:  {ratdvd}")
        print(f"Output: {folder / 'VIDEO_TS'}")
        if dry_run:
            print("  [dry-run] would extract + rebuild VIDEO_TS")
            continue

        if (folder / "VIDEO_TS").exists():
            raise SystemExit(f"VIDEO_TS already exists: {folder / 'VIDEO_TS'}")

        with tempfile.TemporaryDirectory(prefix="ratdvd_") as tmp:
            extract_dir = Path(tmp) / "extract"
            print("  Extracting with 7z…")
            extract_ratdvd(ratdvd, extract_dir)
            moved = rebuild_video_ts(extract_dir, folder, rename_xvo=rename_xvo)
            print(f"  Wrote {moved} files into VIDEO_TS/")

        if not keep_ratdvd:
            print(f"  Removing {ratdvd.name}")
            ratdvd.unlink()
        print("  Done.")


def main():
    parser = argparse.ArgumentParser(
        description="Convert .ratDVD to VIDEO_TS (for Jellyfin DVD folders)"
    )
    parser.add_argument(
        "path",
        help="Movie folder containing .ratDVD, or path to a .ratDVD file",
    )
    parser.add_argument(
        "--keep-ratdvd",
        action="store_true",
        help="Do not delete the .ratDVD after successful convert",
    )
    parser.add_argument(
        "--no-rename-xvo",
        action="store_true",
        help="Keep .XVO names (default: rename .XVO → .VOB and copy .IFO → .BUP)",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if seven_zip_bin() is None:
        raise SystemExit("7z not found (install p7zip / 7zip)")

    convert(
        Path(args.path).expanduser().resolve(),
        keep_ratdvd=args.keep_ratdvd,
        rename_xvo=not args.no_rename_xvo,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()
