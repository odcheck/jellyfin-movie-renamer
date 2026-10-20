#!/usr/bin/env python3
"""
Convert movie-folder media to MP4:
  - .divx / .mpg / .mpeg → mp4 (ffmpeg)
  - .ratDVD → VIDEO_TS → mp4
  - VIDEO_TS / .iso → mp4 via HandBrakeCLI (flatpak + libdvdcss), ffmpeg VOB-concat fallback
  - Blu-ray STREAM/*.m2ts (playlist order) → one mp4 (ffmpeg concat)

Always: prefer German audio (deu/ger/de), no subtitles, H.264/AAC mp4.
"""
from __future__ import annotations

import argparse
import json
import os
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
HANDBRAKE_FLATPAK = "fr.handbrake.ghb"
def libdvdcss_dir() -> Path | None:
    """Prefer env override, then ~/.local, then /usr/local."""
    candidates = []
    env = os.environ.get("LIBDVDCSS_DIR")
    if env:
        candidates.append(Path(env))
    candidates += [
        Path.home() / ".local" / "lib",
        Path("/usr/local/lib"),
    ]
    for path in candidates:
        if (path / "libdvdcss.so.2").exists() or (path / "libdvdcss.so").exists():
            return path
    return None


def run(cmd, **kwargs):
    print("$ " + " ".join(str(c) for c in cmd))
    return subprocess.run(cmd, **kwargs)


def handbrake_cmd() -> list[str] | None:
    """Build HandBrakeCLI argv (flatpak preferred, else native)."""
    css_dir = libdvdcss_dir()
    if shutil.which("flatpak"):
        cmd = ["flatpak", "run"]
        if css_dir:
            cmd += [
                f"--env=LD_LIBRARY_PATH={css_dir}",
                f"--filesystem={css_dir}:ro",
            ]
        # host: NFS movie paths (same idea as manual Halloween encode)
        cmd += ["--filesystem=host", "--command=HandBrakeCLI", HANDBRAKE_FLATPAK]
        return cmd
    if shutil.which("HandBrakeCLI"):
        return ["HandBrakeCLI"]
    return None


def parse_handbrake_scan(text: str) -> list[dict]:
    """Parse HandBrakeCLI -t 0 listing into [{num, duration_sec, audios}]."""
    titles: list[dict] = []
    current = None
    in_audio = False
    for line in text.splitlines():
        m = re.match(r"\+ title (\d+):", line)
        if m:
            current = {"num": int(m.group(1)), "duration_sec": 0, "audios": []}
            titles.append(current)
            in_audio = False
            continue
        if current is None:
            continue
        if "+ audio tracks:" in line:
            in_audio = True
            continue
        if in_audio and re.match(r"\s+\+ (?:subtitle|chapter)", line):
            in_audio = False
        if "+ subtitle tracks:" in line or "+ chapters:" in line:
            in_audio = False
            continue
        m = re.match(r"\s+\+ duration: (\d+):(\d+):(\d+)", line)
        if m:
            h, mi, s = (int(m.group(1)), int(m.group(2)), int(m.group(3)))
            current["duration_sec"] = h * 3600 + mi * 60 + s
            continue
        if not in_audio:
            continue
        m = re.match(r"\s+\+ (\d+), (.+) \(iso639-2: (\w+)\)", line)
        if m:
            current["audios"].append(
                {
                    "num": int(m.group(1)),
                    "name": m.group(2).strip(),
                    "lang": m.group(3).lower(),
                }
            )
    return titles


def pick_handbrake_title_audio(titles: list[dict]) -> tuple[int, int]:
    """Longest title + German audio track (else first audio / 1)."""
    if not titles:
        return 1, 1
    best = max(titles, key=lambda t: (t.get("duration_sec") or 0, t["num"]))
    audio = 1
    for track in best.get("audios") or []:
        name = (track.get("name") or "").lower()
        lang = (track.get("lang") or "").lower()
        if lang in GERMAN_LANGS or any(g in name for g in ("deutsch", "german")):
            return best["num"], track["num"]
    if best.get("audios"):
        audio = best["audios"][0]["num"]
    return best["num"], audio


def convert_with_handbrake(source: Path, dest_mp4: Path, dry_run: bool) -> bool:
    """
    Encode DVD folder / VIDEO_TS / ISO with HandBrakeCLI.
    Returns False if HandBrake is not installed (caller may fallback).
    """
    base = handbrake_cmd()
    if not base:
        return False

    print(f"HandBrake: {source} → {dest_mp4.name}")
    scan_argv = [*base, "-i", str(source), "-t", "0"]
    if dry_run:
        print("  [dry-run]", " ".join(scan_argv))
        print("  [dry-run] encode x264 q20, German audio, -s none, decomb")
        return True

    if dest_mp4.exists():
        raise RuntimeError(f"Target exists: {dest_mp4}")

    env = os.environ.copy()
    css_dir = libdvdcss_dir()
    if base[0] == "HandBrakeCLI" and css_dir:
        env["LD_LIBRARY_PATH"] = f"{css_dir}:{env.get('LD_LIBRARY_PATH', '')}"

    # DVD titles often aren't UTF-8; replace so scan output can't crash the convert.
    # ponytail: 3min scan cap — HandBrake -t 0 can spin forever on weird multi-title VIDEO_TS (NFS).
    try:
        scan = run(
            scan_argv,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            timeout=180,
        )
    except subprocess.TimeoutExpired:
        print(f"  HandBrake scan timed out on {source} — caller may fallback")
        return False
    text = (scan.stdout or "") + (scan.stderr or "")
    titles = parse_handbrake_scan(text)
    if not titles:
        # Flatpak HandBrake often sees IFO structure but rejects encrypted VOB video
        # (demux_dvd_ps / 0 previews) even with Plugin.dvdcss on folder rips.
        print(
            f"  HandBrake found no titles in {source} "
            "(CSS/unreadable — caller may fallback)"
        )
        return False
    title, audio = pick_handbrake_title_audio(titles)
    print(f"  Selected title {title}, audio {audio} ({len(titles)} title(s) scanned)")

    enc_argv = [
        *base,
        "-i", str(source),
        "-t", str(title),
        "-o", str(dest_mp4),
        "-e", "x264", "-q", "20", "--encoder-preset", "medium",
        "-a", str(audio), "-E", "av_aac", "-B", "192", "-A", "Deutsch",
        "-s", "none",
        "--comb-detect", "--decomb",
    ]
    proc = run(enc_argv, env=env)
    if proc.returncode != 0:
        if dest_mp4.exists():
            dest_mp4.unlink()
        print(f"  HandBrakeCLI encode failed ({proc.returncode})")
        return False
    return True


# Single-file containers converted with ffmpeg (same pipeline as divx).
FFMPEG_CONVERT_EXTS = {".divx", ".mpg", ".mpeg"}


def find_ffmpeg_sources(folder: Path):
    out = []
    for path in folder.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in FFMPEG_CONVERT_EXTS:
            continue
        if any(p.startswith(".") or p.startswith("@") for p in path.parts):
            continue
        out.append(path)
    return sorted(out)


def find_divx(folder: Path):
    """Back-compat alias."""
    return [p for p in find_ffmpeg_sources(folder) if p.suffix.lower() == ".divx"]


def find_ratdvds(folder: Path):
    return ratdvd.find_ratdvds(folder)


def is_ratdvd_xc(folder: Path) -> bool:
    """True when folder is a ratDVD unpack (XC / .VSI) — not real MPEG-DVD VOBs."""
    for ver in (folder / "Version.xml", folder.parent / "Version.xml"):
        try:
            if ver.is_file() and "<videoformat>XC</videoformat>" in ver.read_text(
                encoding="utf-8", errors="ignore"
            ):
                return True
        except OSError:
            pass
    # Unpacked ratDVD leaves .VSI (+ StreamData.xml); video PID is proprietary, not MPEG.
    for root in (folder, folder / "VIDEO_TS"):
        if not root.is_dir():
            continue
        try:
            if any(p.suffix.upper() == ".VSI" for p in root.iterdir() if p.is_file()):
                return True
        except OSError:
            pass
    return False


def is_flat_dvd_dir(folder: Path) -> bool:
    """True when IFO/VOB sit in the movie folder root (no VIDEO_TS/ subdir)."""
    if not folder.is_dir():
        return False
    try:
        names = {p.name.upper() for p in folder.iterdir() if p.is_file()}
    except OSError:
        return False
    if "VIDEO_TS.IFO" not in names:
        return False
    return any(n.startswith("VTS_") and n.endswith(".VOB") for n in names)


def find_video_ts(folder: Path) -> Path | None:
    """Return a VIDEO_TS path that actually has IFO+VOB (ignore empty stubs)."""
    if not folder.is_dir():
        return None
    candidates: list[Path] = []
    direct = folder / "VIDEO_TS"
    if direct.is_dir():
        candidates.append(direct)
    for child in folder.iterdir():
        if not child.is_dir():
            continue
        nested = child / "VIDEO_TS"
        if nested.is_dir():
            candidates.append(nested)
        if child.name.upper() == "VIDEO_TS":
            candidates.append(child)
    for cand in candidates:
        if is_flat_dvd_dir(cand):
            return cand
    # Some rips dump VIDEO_TS contents into the movie folder itself.
    if is_flat_dvd_dir(folder):
        return folder
    return None


def find_isos(folder: Path) -> list[Path]:
    if not folder.is_dir():
        return []
    seen = set()
    out = []
    for path in folder.iterdir():
        if not path.is_file() or path.suffix.lower() != ".iso":
            continue
        key = path.name.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(path)
    return sorted(out, key=lambda p: p.name.lower())


def find_bluray_stream(folder: Path) -> Path | None:
    """STREAM/ with .m2ts — naked BDMV layout or BDMV/STREAM."""
    if not folder.is_dir():
        return None
    for candidate in (folder / "STREAM", folder / "BDMV" / "STREAM"):
        if not candidate.is_dir():
            continue
        try:
            if any(p.is_file() and p.suffix.lower() == ".m2ts" for p in candidate.iterdir()):
                return candidate
        except OSError:
            continue
    return None


def mpls_clip_ids(playlist_dir: Path) -> list[str]:
    """Clip ids (e.g. 00000) in playlist order from the largest .mpls."""
    try:
        playlists = sorted(
            [p for p in playlist_dir.iterdir() if p.is_file() and p.suffix.lower() == ".mpls"],
            key=lambda p: p.stat().st_size,
            reverse=True,
        )
    except OSError:
        return []
    if not playlists:
        return []
    data = playlists[0].read_bytes()
    # MPLS stores clip names as 5 digits + "M2TS"
    return [m.decode() for m in re.findall(rb"(\d{5})M2TS", data)]


def ordered_m2ts(stream_dir: Path) -> list[Path]:
    """m2ts files in playlist order, else numeric name order."""
    # Naked layout: …/STREAM + …/PLAYLIST; standard: …/BDMV/STREAM + …/BDMV/PLAYLIST
    playlist_dir = stream_dir.parent / "PLAYLIST"
    by_stem = {
        p.stem: p
        for p in stream_dir.iterdir()
        if p.is_file() and p.suffix.lower() == ".m2ts"
    }
    ordered: list[Path] = []
    seen = set()
    for clip_id in mpls_clip_ids(playlist_dir):
        path = by_stem.get(clip_id)
        if path and path not in seen:
            ordered.append(path)
            seen.add(path)
    if ordered:
        return ordered
    return sorted(by_stem.values(), key=lambda p: p.name)


def needs_convert(folder: Path) -> bool:
    return bool(
        find_ffmpeg_sources(folder)
        or find_ratdvds(folder)
        or find_video_ts(folder)
        or find_isos(folder)
        or find_bluray_stream(folder)
    )


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


def convert_ffmpeg_file(src: Path, dry_run: bool, keep_source: bool):
    dest = src.with_suffix(".mp4")
    kind = src.suffix.lower().lstrip(".") or "file"
    print(f"{kind}: {src.name} → {dest.name}")
    streams = [] if dry_run else ffprobe_streams(src)
    audio_i = None if dry_run else pick_german_audio_index(streams)
    encode_to_mp4(["-i", str(src)], dest, audio_i if audio_i is not None else 0, dry_run)
    if not dry_run and not keep_source:
        src.unlink()
        print(f"  Removed {src.name}")


def convert_divx_file(src: Path, dry_run: bool, keep_source: bool):
    """Back-compat alias."""
    return convert_ffmpeg_file(src, dry_run=dry_run, keep_source=keep_source)


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


def write_ffconcat(paths: list[Path]) -> Path:
    with tempfile.NamedTemporaryFile("w", suffix=".ffconcat", delete=False) as handle:
        handle.write("ffconcat version 1.0\n")
        for path in paths:
            escaped = str(path).replace("'", "'\\''")
            handle.write(f"file '{escaped}'\n")
        return Path(handle.name)


def convert_video_ts_ffmpeg(video_ts: Path, dest_mp4: Path, dry_run: bool):
    """Fallback: concat largest VTS VOB set with ffmpeg (no CSS decrypt)."""
    vobs = largest_vts_vobs(video_ts)
    if not vobs:
        raise RuntimeError(f"No VOB files in {video_ts}")
    print(f"DVD/ffmpeg fallback: {video_ts} ({len(vobs)} VOB(s) → {dest_mp4.name})")
    if dry_run:
        print("  [dry-run] would encode", [p.name for p in vobs])
        return

    list_path = write_ffconcat(vobs)
    try:
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


def remove_bluray_source(movie_folder: Path, stream_dir: Path):
    """Drop STREAM/PLAYLIST/CLIPINF/BACKUP and index files after convert."""
    bdmv = movie_folder / "BDMV"
    roots = [bdmv] if bdmv.is_dir() else [movie_folder]
    for root in roots:
        for name in ("STREAM", "PLAYLIST", "CLIPINF", "BACKUP", "AUXDATA", "META"):
            path = root / name
            if path.is_dir():
                shutil.rmtree(path)
                print(f"  Removed {path.relative_to(movie_folder)}")
        for name in ("index.bdmv", "MovieObject.bdmv"):
            path = root / name
            if path.is_file():
                path.unlink()
                print(f"  Removed {path.name}")
    # If we only had STREAM under movie root, ensure it's gone even if rename differed
    if stream_dir.is_dir() and stream_dir.resolve() != movie_folder.resolve():
        shutil.rmtree(stream_dir, ignore_errors=True)


def convert_bluray_m2ts(stream_dir: Path, dest_mp4: Path, dry_run: bool, keep_source: bool):
    """Concat playlist-ordered .m2ts into one mp4. Copy H.264/HEVC video when possible."""
    clips = ordered_m2ts(stream_dir)
    if not clips:
        raise RuntimeError(f"No .m2ts in {stream_dir}")
    print(f"Blu-ray: {len(clips)} m2ts → {dest_mp4.name} ({', '.join(p.name for p in clips)})")
    if dry_run:
        print("  [dry-run] would concat encode", [p.name for p in clips])
        return

    if dest_mp4.exists():
        raise RuntimeError(f"MP4 already exists: {dest_mp4}")

    streams = ffprobe_streams(clips[0])
    audio_i = pick_german_audio_index(streams)
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    vcodec = (video or {}).get("codec_name", "")
    copy_video = vcodec in {"h264", "hevc", "mpeg4"}

    list_path = write_ffconcat(clips)
    try:
        map_args = ["-map", "0:v:0"]
        if audio_i is not None:
            map_args += ["-map", f"0:a:{audio_i}"]
        if copy_video:
            print(f"  Video: copy {vcodec} (no re-encode)")
            cmd = [
                "ffmpeg", "-hide_banner", "-y",
                "-f", "concat", "-safe", "0", "-i", str(list_path),
                *map_args,
                "-sn",
                "-c:v", "copy",
                "-c:a", "aac", "-b:a", "192k",
                "-movflags", "+faststart",
                str(dest_mp4),
            ]
            proc = run(cmd)
            if proc.returncode != 0:
                if dest_mp4.exists():
                    dest_mp4.unlink()
                raise RuntimeError(f"ffmpeg failed ({proc.returncode})")
        else:
            encode_to_mp4(
                ["-f", "concat", "-safe", "0", "-i", str(list_path)],
                dest_mp4,
                audio_i if audio_i is not None else 0,
                dry_run=False,
            )
    finally:
        list_path.unlink(missing_ok=True)

    if not keep_source:
        remove_bluray_source(dest_mp4.parent, stream_dir)


def remove_dvd_source(video_ts: Path, dest_mp4: Path):
    """Remove DVD files after convert. Never rmtree the movie folder on flat layouts."""
    if video_ts.resolve() == dest_mp4.parent.resolve():
        removed = 0
        for path in list(video_ts.iterdir()):
            if path.is_file() and path.suffix.lower() in {".ifo", ".bup", ".vob"}:
                path.unlink()
                removed += 1
        print(f"  Removed {removed} flat DVD file(s) (IFO/BUP/VOB)")
        return
    shutil.rmtree(video_ts)
    print(f"  Removed {video_ts}")


def vobs_have_video(video_ts: Path) -> bool:
    """True if largest VTS has a real video codec ffmpeg can decode (not unknown/none)."""
    vobs = largest_vts_vobs(video_ts)
    if not vobs:
        return False
    for stream in ffprobe_streams(vobs[0]):
        if stream.get("codec_type") != "video":
            continue
        name = (stream.get("codec_name") or "").lower()
        if name and name not in {"unknown", "none"}:
            return True
    return False


def convert_video_ts(video_ts: Path, dest_mp4: Path, dry_run: bool, keep_source: bool):
    """ffmpeg when VOBs are readable; else HandBrake+libdvdcss; else ffmpeg again."""
    print(f"DVD: {video_ts} → {dest_mp4.name}")
    movie = video_ts.parent if video_ts.name.upper() == "VIDEO_TS" else video_ts
    if is_ratdvd_xc(movie) or is_ratdvd_xc(video_ts):
        raise RuntimeError(
            f"ratDVD proprietary VIDEO_TS in {movie.name} (.VSI/XC) — "
            "HandBrake/ffmpeg cannot decode it. Need the original .ratDVD "
            "expanded with ratDVD to a real MPEG DVD, or another source."
        )
    # Readable VOBs: skip HandBrake -t 0 (can spin forever on multi-title rips over NFS).
    if dry_run or vobs_have_video(video_ts):
        if not dry_run:
            print("  VOBs readable — ffmpeg (skip HandBrake scan)")
        convert_video_ts_ffmpeg(video_ts, dest_mp4, dry_run=dry_run)
    else:
        # Pass VIDEO_TS itself so HandBrake does not pick a sibling .mp4 in the parent folder.
        used_hb = convert_with_handbrake(video_ts, dest_mp4, dry_run=False)
        if not used_hb:
            print("  HandBrake skipped/failed — ffmpeg VOB concat (may fail on CSS DVDs)")
            convert_video_ts_ffmpeg(video_ts, dest_mp4, dry_run=False)

    if not dry_run and not keep_source:
        remove_dvd_source(video_ts, dest_mp4)


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


def convert_iso(iso: Path, dest_mp4: Path, dry_run: bool, keep_source: bool):
    """DVD ISO → mp4 via HandBrake(+libdvdcss); else 7z extract + VIDEO_TS path."""
    print(f"ISO: {iso.name} → {dest_mp4.name}")
    if dest_mp4.exists() and not dry_run:
        raise RuntimeError(f"MP4 already exists: {dest_mp4}")

    used_hb = convert_with_handbrake(iso, dest_mp4, dry_run=dry_run)
    if used_hb:
        if not dry_run and not keep_source:
            iso.unlink()
            print(f"  Removed {iso.name}")
        return

    print("  HandBrake skipped/failed — 7z extract + encode fallback")
    if dry_run:
        print("  [dry-run] would extract VIDEO_TS via 7z and encode")
        return
    bin_name = ratdvd.seven_zip_bin()
    if not bin_name:
        raise RuntimeError("7z/7za required for ISO convert without HandBrake")

    with tempfile.TemporaryDirectory(prefix="iso_") as tmp:
        extract_dir = Path(tmp) / "extract"
        extract_dir.mkdir(parents=True, exist_ok=True)
        proc = subprocess.run(
            [bin_name, "x", "-y", f"-o{extract_dir}", str(iso)],
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            raise RuntimeError(
                f"{bin_name} extract failed ({proc.returncode}):\n"
                f"{proc.stderr or proc.stdout}"
            )
        video_ts = find_video_ts(extract_dir)
        if not video_ts:
            raise RuntimeError(
                f"No VIDEO_TS in ISO (DVD image expected): {iso.name}"
            )
        convert_video_ts(video_ts, dest_mp4, dry_run=False, keep_source=True)

    if not keep_source:
        iso.unlink()
        print(f"  Removed {iso.name}")


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

    # 2) divx / mpg / mpeg → mp4
    for src in find_ffmpeg_sources(folder):
        convert_ffmpeg_file(src, dry_run=dry_run, keep_source=keep_source)
        did = True

    # 3) VIDEO_TS → mp4
    video_ts = find_video_ts(folder)
    if video_ts:
        dest = default_mp4_name(folder)
        if dest.exists() and not dry_run:
            raise RuntimeError(f"MP4 already exists: {dest}")
        convert_video_ts(video_ts, dest, dry_run=dry_run, keep_source=keep_source)
        did = True

    # 4) Blu-ray STREAM/*.m2ts → one mp4
    dest = default_mp4_name(folder)
    stream = find_bluray_stream(folder)
    if stream and (dry_run or not dest.exists()):
        convert_bluray_m2ts(stream, dest, dry_run=dry_run, keep_source=keep_source)
        did = True

    # 5) ISO → mp4 (skip if we already produced the target mp4)
    isos = find_isos(folder)
    if isos and (dry_run or not dest.exists()):
        for i, iso in enumerate(isos):
            out = dest if i == 0 else folder / f"{folder.name}-{i + 1}.mp4"
            if not dry_run and out.exists():
                print(f"  Skip ISO {iso.name}: {out.name} already exists")
                continue
            convert_iso(iso, out, dry_run=dry_run, keep_source=keep_source)
            did = True

    if not did:
        print("  Nothing to convert (no divx/mpg/mpeg/ratDVD/VIDEO_TS/m2ts/ISO)")
    return did


def main():
    parser = argparse.ArgumentParser(
        description="Convert divx/mpg/mpeg / DVD / ratDVD / ISO → MP4 (German audio, no subs)"
    )
    parser.add_argument(
        "paths",
        nargs="+",
        help="Movie folder(s), or a file listing folder paths",
    )
    parser.add_argument(
        "--keep-source",
        action="store_true",
        help="Keep source divx/mpg/mpeg/VIDEO_TS/ISO after convert",
    )
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
