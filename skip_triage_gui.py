#!/usr/bin/env python3
"""GUI to triage Source/: format gate, convert, pending rename batch, manual-later queue."""
import os
import queue
import re
import shutil
import subprocess
import threading
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

import paths_config

SCRIPT_DIR = Path(__file__).resolve().parent
RENAMER = SCRIPT_DIR / "movie_renamer.py"
MEDIA_TO_MP4 = SCRIPT_DIR / "media_to_mp4.py"

# Set by apply_paths() — env > ~/.config/…/paths.json > ~/Source etc.
SOURCE = DESTINATION = WORK = Path(".")
SKIP_ARCHIVE = REASONS_FILE = MANUAL_LATER = PENDING_FILE = Path(".")


def apply_paths(source=None, destination=None, work=None):
    """Update module path globals (and derived archive files under WORK)."""
    global SOURCE, DESTINATION, WORK, SKIP_ARCHIVE, REASONS_FILE, MANUAL_LATER, PENDING_FILE
    f, m, w = paths_config.resolve()
    if source is not None:
        f = Path(source).expanduser()
    if destination is not None:
        m = Path(destination).expanduser()
    if work is not None:
        w = Path(work).expanduser()
    SOURCE, DESTINATION, WORK = f, m, w
    SKIP_ARCHIVE = Path(os.environ["SKIP_ARCHIVE"]) if os.environ.get("SKIP_ARCHIVE") else WORK / "permanent_skipped.txt"
    REASONS_FILE = Path(os.environ["REASONS_FILE"]) if os.environ.get("REASONS_FILE") else WORK / "skipped_reasons.txt"
    MANUAL_LATER = Path(os.environ["MANUAL_LATER"]) if os.environ.get("MANUAL_LATER") else WORK / "manual_later.txt"
    PENDING_FILE = Path(os.environ["PENDING_FILE"]) if os.environ.get("PENDING_FILE") else WORK / "pending_batch.txt"


apply_paths()
# Jellyfin-ok containers that skip convert and may go to rename/pending.
READY_VIDEO_EXT = {".mkv", ".mp4", ".avi"}
VIDEO_EXT = READY_VIDEO_EXT | {".mov", ".m4v", ".wmv", ".mpg", ".mpeg", ".m2ts", ".ts", ".divx"}
IGNORABLE_NAMES = {".@", "@eaDir", "Thumbs.db", "desktop.ini", ".DS_Store"}


def load_reasons():
    reasons = {}
    if not REASONS_FILE.is_file():
        return reasons
    for line in REASONS_FILE.read_text(encoding="utf-8", errors="ignore").splitlines():
        if " | " not in line:
            continue
        name, reason = line.split(" | ", 1)
        reasons[name.strip()] = reason.strip()
    return reasons


def save_reasons(reasons):
    REASONS_FILE.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"{name} | {reason}\n" for name, reason in sorted(reasons.items())]
    REASONS_FILE.write_text("".join(lines), encoding="utf-8")


def load_skips():
    if not SKIP_ARCHIVE.is_file():
        return []
    return [
        line.strip()
        for line in SKIP_ARCHIVE.read_text(encoding="utf-8", errors="ignore").splitlines()
        if line.strip()
    ]


def save_skips(names):
    SKIP_ARCHIVE.parent.mkdir(parents=True, exist_ok=True)
    SKIP_ARCHIVE.write_text("".join(n + "\n" for n in names), encoding="utf-8")


def load_pending():
    if not PENDING_FILE.is_file():
        return []
    seen = []
    for line in PENDING_FILE.read_text(encoding="utf-8", errors="ignore").splitlines():
        name = line.strip()
        if name and name not in seen:
            seen.append(name)
    return seen


def save_pending(names):
    WORK.mkdir(parents=True, exist_ok=True)
    PENDING_FILE.write_text("".join(n + "\n" for n in names), encoding="utf-8")


def load_manual_later_names():
    if not MANUAL_LATER.is_file():
        return set()
    return {
        line.split(" | ", 1)[0].strip()
        for line in MANUAL_LATER.read_text(encoding="utf-8", errors="ignore").splitlines()
        if line.strip()
    }


def append_manual_later(name, note=""):
    MANUAL_LATER.parent.mkdir(parents=True, exist_ok=True)
    existing = load_manual_later_names()
    if name in existing:
        return
    with MANUAL_LATER.open("a", encoding="utf-8") as handle:
        handle.write(f"{name} | {note}\n" if note else f"{name}\n")


def remove_manual_later(name):
    if not MANUAL_LATER.is_file() or name not in load_manual_later_names():
        return
    kept = []
    for line in MANUAL_LATER.read_text(encoding="utf-8", errors="ignore").splitlines():
        raw = line.strip()
        if not raw:
            continue
        entry = raw.split(" | ", 1)[0].strip()
        if entry != name:
            kept.append(raw)
    MANUAL_LATER.write_text("".join(n + "\n" for n in kept), encoding="utf-8")


# Sidecars next to loose media — skip in scan (NFS: suffix only, never stat).
_SCAN_SKIP_SUFFIXES = {
    ".nfo", ".txt", ".jpg", ".jpeg", ".png", ".srt", ".sfv", ".xml",
    ".metathumb",  # XBMC/Kodi thumbnail sidecar next to loose .avi etc.
}
# Loose media in Source/ root — shown in GUI, wrapped into a folder before rename/move.
LOOSE_MEDIA_EXT = VIDEO_EXT | {".iso", ".img"}
_LOOSE_SIDECAR_EXTS = (
    ".nfo", ".txt", ".jpg", ".jpeg", ".png", ".srt", ".sfv", ".xml", ".metathumb",
)


def is_loose_media_name(name: str) -> bool:
    return Path(name).suffix.lower() in LOOSE_MEDIA_EXT


def scan_source_folders():
    """Movie folders + loose media files still under Source/ (left-list source of truth)."""
    if not SOURCE.is_dir():
        return []
    names = []
    try:
        with os.scandir(SOURCE) as entries:
            for entry in entries:
                if entry.name.startswith(".") or entry.name.startswith("@"):
                    continue
                # Avoid entry.is_dir() — NFS hard-mount can block forever on stale dentries.
                suffix = Path(entry.name).suffix.lower()
                if suffix in LOOSE_MEDIA_EXT:
                    names.append(entry.name)
                    continue
                if suffix in _SCAN_SKIP_SUFFIXES:
                    continue
                names.append(entry.name)
    except OSError:
        return []
    return sorted(names, key=str.lower)


def wrap_loose_media(name: str, source: Path = None) -> str:
    """
    If name is a loose media file under Source/, move it (+ same-stem sidecars)
    into Source/<stem>/. Returns the folder name to use afterwards.
    """
    base = source if source is not None else SOURCE
    if not is_loose_media_name(name):
        return name
    src = base / name
    if src.is_dir():
        return name
    if not src.is_file():
        raise FileNotFoundError(f"Loose media missing: {src}")
    folder_name = src.stem
    dest_dir = base / folder_name
    if dest_dir.exists() and not dest_dir.is_dir():
        raise RuntimeError(f"Cannot wrap into {dest_dir} (exists, not a folder)")
    dest_dir.mkdir(exist_ok=True)
    target = dest_dir / src.name
    if target.exists():
        raise RuntimeError(f"Target already exists: {target}")
    src.rename(target)
    stem = src.stem
    for ext in _LOOSE_SIDECAR_EXTS:
        side = base / f"{stem}{ext}"
        if side.is_file():
            side.rename(dest_dir / side.name)
        # e.g. Movie.avi.metathumb
        side2 = base / f"{name}{ext}"
        if side2.is_file():
            side2.rename(dest_dir / side2.name)
    return folder_name


def find_ratdvds(folder: Path):
    if not folder.is_dir():
        return []
    return sorted(folder.glob("*.ratDVD")) + sorted(folder.glob("*.ratdvd"))


def is_ratdvd_xc(folder: Path) -> bool:
    """ratDVD unpack (.VSI / XC) — fake VOBs, cannot HandBrake/ffmpeg to mp4."""
    for ver in (folder / "Version.xml",):
        try:
            if ver.is_file() and "<videoformat>XC</videoformat>" in ver.read_text(
                encoding="utf-8", errors="ignore"
            ):
                return True
        except OSError:
            pass
    for root in (folder, folder / "VIDEO_TS"):
        if not root.is_dir():
            continue
        try:
            if any(p.suffix.upper() == ".VSI" for p in root.iterdir() if p.is_file()):
                return True
        except OSError:
            pass
    return False


FFMPEG_CONVERT_EXTS = {".divx", ".mpg", ".mpeg"}


def find_ffmpeg_convert_files(folder: Path):
    if not folder.is_dir():
        return []
    found = []
    for path in folder.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in FFMPEG_CONVERT_EXTS:
            continue
        if any(p.startswith(".") or p.startswith("@") for p in path.parts):
            continue
        found.append(path)
    return sorted(found)


def find_divx_files(folder: Path):
    """Back-compat: divx only."""
    return [p for p in find_ffmpeg_convert_files(folder) if p.suffix.lower() == ".divx"]


def _dvd_files_present(dir_path: Path) -> bool:
    """True when dir has VIDEO_TS.IFO plus at least one VTS_*.VOB."""
    try:
        names = {p.name.upper() for p in dir_path.iterdir() if p.is_file()}
    except OSError:
        return False
    if "VIDEO_TS.IFO" not in names:
        return False
    return any(n.startswith("VTS_") and n.endswith(".VOB") for n in names)


def has_dvd_structure(folder: Path) -> bool:
    """True for real DVD layouts (IFO+VOB) or ISO — not empty VIDEO_TS stubs."""
    if not folder.is_dir():
        return False
    try:
        if _dvd_files_present(folder):
            return True
        video_ts = folder / "VIDEO_TS"
        if video_ts.is_dir() and _dvd_files_present(video_ts):
            return True
        for child in folder.iterdir():
            if child.is_file() and child.suffix.lower() == ".iso":
                return True
            if child.is_dir():
                if child.name.upper() == "VIDEO_TS" and _dvd_files_present(child):
                    return True
                nested = child / "VIDEO_TS"
                if nested.is_dir() and _dvd_files_present(nested):
                    return True
    except OSError:
        return False
    return False


def find_videos(folder: Path, limit=20, exts=None):
    if not folder.is_dir():
        return []
    allowed = exts if exts is not None else VIDEO_EXT
    found = []
    for root, dirs, files in os.walk(folder):
        dirs[:] = [d for d in dirs if not d.startswith(".") and not d.startswith("@")]
        for name in files:
            if Path(name).suffix.lower() in allowed:
                found.append(Path(root) / name)
                if len(found) >= limit:
                    return found
    return found


def has_ready_video(folder: Path) -> bool:
    """True when folder already has a Jellyfin-ok container (mkv/mp4/avi)."""
    return bool(find_videos(folder, limit=1, exts=READY_VIDEO_EXT))


def has_bluray_stream(folder: Path) -> bool:
    for candidate in (folder / "STREAM", folder / "BDMV" / "STREAM"):
        if not candidate.is_dir():
            continue
        try:
            if any(p.is_file() and p.suffix.lower() == ".m2ts" for p in candidate.iterdir()):
                return True
        except OSError:
            continue
    return False


def needs_media_convert(folder: Path) -> bool:
    """ISO / VIDEO_TS / ratDVD / DivX / mpg / mpeg / m2ts still need convert before rename is ideal."""
    if is_ratdvd_xc(folder):
        return False  # proprietary VOBs — convert would hang/fail
    return bool(
        find_ffmpeg_convert_files(folder)
        or find_ratdvds(folder)
        or has_dvd_structure(folder)
        or has_bluray_stream(folder)
    )


def peek_folder_media(folder: Path) -> dict:
    """
    One shallow pass for list tags / view filters (NFS-friendly).
    Does not recurse deep trees — detail pane still uses full checks.
    """
    out = {
        "missing": False,
        "empty": True,
        "ready": False,
        "convert": False,
        "ratdvd": False,
        "divx": False,
        "dvd": False,
    }
    # Loose media listed as triage item (before wrap into folder).
    if folder.is_file() and folder.suffix.lower() in LOOSE_MEDIA_EXT:
        out["empty"] = False
        suf = folder.suffix.lower()
        if suf in READY_VIDEO_EXT:
            out["ready"] = True
        elif suf in FFMPEG_CONVERT_EXTS or suf in {".iso", ".img"}:
            out["convert"] = True
            if suf in {".iso", ".img"}:
                out["dvd"] = True
        return out
    if not folder.is_dir():
        out["missing"] = True
        return out
    try:
        with os.scandir(folder) as entries:
            for entry in entries:
                name = entry.name
                if name.startswith(".") or name.startswith("@"):
                    continue
                try:
                    is_dir = entry.is_dir(follow_symlinks=False)
                    is_file = entry.is_file(follow_symlinks=False)
                except OSError:
                    continue
                if is_file:
                    out["empty"] = False
                    suf = Path(name).suffix.lower()
                    if suf in READY_VIDEO_EXT:
                        out["ready"] = True
                    elif suf in FFMPEG_CONVERT_EXTS:
                        out["convert"] = True
                        if suf == ".divx":
                            out["divx"] = True
                    elif suf == ".iso":
                        out["convert"] = True
                        out["dvd"] = True
                    elif suf == ".ratdvd":
                        out["convert"] = True
                        out["ratdvd"] = True
                    elif suf in {".ifo", ".vob", ".bup"} or name.upper() == "VIDEO_TS.IFO":
                        # Flat DVD layout (IFO/VOB in movie folder root)
                        out["convert"] = True
                        out["dvd"] = True
                elif is_dir:
                    out["empty"] = False
                    upper = name.upper()
                    if upper == "VIDEO_TS":
                        out["convert"] = True
                        out["dvd"] = True
                    elif upper == "STREAM":
                        out["convert"] = True
                    elif upper == "BDMV":
                        out["convert"] = True
                    else:
                        # one nested level (release subfolder / VIDEO_TS wrapper)
                        try:
                            nested_ts = Path(entry.path) / "VIDEO_TS"
                            if nested_ts.is_dir():
                                out["convert"] = True
                                out["dvd"] = True
                            with os.scandir(entry.path) as nested:
                                for child in nested:
                                    if child.name.startswith(".") or child.name.startswith("@"):
                                        continue
                                    try:
                                        if not child.is_file(follow_symlinks=False):
                                            continue
                                    except OSError:
                                        continue
                                    suf = Path(child.name).suffix.lower()
                                    if suf in READY_VIDEO_EXT:
                                        out["ready"] = True
                                    elif suf in FFMPEG_CONVERT_EXTS:
                                        out["convert"] = True
                                        if suf == ".divx":
                                            out["divx"] = True
                                    elif suf in {".iso", ".ratdvd"}:
                                        out["convert"] = True
                        except OSError:
                            pass
    except OSError:
        out["missing"] = True
    return out


def is_effectively_empty(folder: Path) -> bool:
    if not folder.is_dir():
        return True
    if has_dvd_structure(folder):
        return False
    if find_videos(folder, limit=1):
        return False
    try:
        for root, dirs, files in os.walk(folder):
            dirs[:] = [d for d in dirs if not d.startswith(".") and not d.startswith("@")]
            for name in files:
                lower = name.lower()
                if lower.endswith((".nfo", ".txt", ".sfv", ".jpg", ".png", ".jpeg", ".xml")):
                    continue
                if name in IGNORABLE_NAMES or name.startswith("."):
                    continue
                return False
    except OSError:
        return False
    return True


def parse_movable(success_file: Path, backlog_file: Path):
    backlog = set()
    if backlog_file.is_file():
        backlog = {
            line.strip()
            for line in backlog_file.read_text(encoding="utf-8", errors="ignore").splitlines()
            if line.strip()
        }
    movable = []
    if not success_file.is_file():
        return movable
    text = success_file.read_text(encoding="utf-8", errors="ignore")
    for block in text.split("\n\n"):
        lines = [ln for ln in block.splitlines() if ln.strip()]
        if not lines or not lines[0].startswith("Folder: "):
            continue
        body = lines[0][len("Folder: "):]
        if " -> " not in body:
            continue
        original, new = body.split(" -> ", 1)
        original, new = original.strip(), new.strip()
        if original in backlog:
            continue
        movable.append((original, new))
    return movable


class BatchProgressWindow(tk.Toplevel):
    def __init__(self, master, titles):
        super().__init__(master)
        self.title(f"Batch ×{len(titles)}")
        self.geometry("780x480")
        self.transient(master)

        self.phase_var = tk.StringVar(value="Start…")
        self.progress = ttk.Progressbar(self, mode="determinate", maximum=max(len(titles), 1))
        ttk.Label(self, textvariable=self.phase_var).pack(anchor="w", padx=8, pady=(8, 2))
        self.progress.pack(fill="x", padx=8, pady=4)

        frame = ttk.Frame(self)
        frame.pack(fill="both", expand=True, padx=8, pady=4)
        self.text = tk.Text(frame, wrap="word", height=24)
        scroll = ttk.Scrollbar(frame, orient="vertical", command=self.text.yview)
        self.text.configure(yscrollcommand=scroll.set)
        self.text.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

        self.close_btn = ttk.Button(self, text="Close", command=self.destroy, state="disabled")
        self.close_btn.pack(pady=8)

        self._q = queue.Queue()
        self.after(100, self._drain)

    def log(self, line):
        self._q.put(("log", line))

    def set_phase(self, text, value=None):
        self._q.put(("phase", text, value))

    def done(self, ok, summary):
        self._q.put(("done", ok, summary))

    def _drain(self):
        try:
            while True:
                item = self._q.get_nowait()
                kind = item[0]
                if kind == "log":
                    self.text.insert("end", item[1] + "\n")
                    self.text.see("end")
                elif kind == "phase":
                    self.phase_var.set(item[1])
                    if item[2] is not None:
                        self.progress["value"] = item[2]
                elif kind == "done":
                    self.phase_var.set(item[2])
                    self.close_btn.configure(state="normal")
                    if item[1]:
                        self.progress["value"] = self.progress["maximum"]
        except queue.Empty:
            pass
        self.after(100, self._drain)


class TriageApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Source triage")
        self.geometry("1000x720")
        self.reasons = load_reasons()
        self.skips = load_skips()
        self.skip_set = set(self.skips)
        self.pending = load_pending()
        self.manual_later = load_manual_later_names()
        self.folders = []
        self.filtered = []
        self.view_mode = tk.StringVar(value="All")
        self.batch_running = False
        self._build()
        # Fast scandir (no per-row NFS walks). Deep media checks only on select / filtered views.
        self.rescan_source(select_index=0)
        if not SOURCE.is_dir():
            self.status_var.set(f"Source path missing — set paths above and Save paths: {SOURCE}")

    def save_paths(self):
        source = self.source_var.get().strip()
        destination = self.destination_var.get().strip()
        work = self.work_var.get().strip()
        if not source or not destination or not work:
            messagebox.showerror("Paths", "Source, Destination, and Work are required.")
            return
        paths_config.save_config(source, destination, work)
        # Drop env overrides for the three roots so the saved file wins this session.
        for key in ("SOURCE", "DESTINATION", "FILME", "MOVIES", "WORK_DIR"):
            os.environ.pop(key, None)
        apply_paths(source, destination, work)
        self.source_var.set(str(SOURCE))
        self.destination_var.set(str(DESTINATION))
        self.work_var.set(str(WORK))
        self.reasons = load_reasons()
        self.skips = load_skips()
        self.skip_set = set(self.skips)
        self.pending = load_pending()
        self.manual_later = load_manual_later_names()
        self.rescan_source(select_index=0)
        self.status_var.set(f"Paths saved → {paths_config.CONFIG_PATH}")
        if not SOURCE.is_dir():
            messagebox.showwarning("Paths", f"Source does not exist yet:\n{SOURCE}")

    def _build(self):
        paths = ttk.Frame(self, padding=(8, 8, 8, 0))
        paths.pack(fill="x")
        self.source_var = tk.StringVar(value=str(SOURCE))
        self.destination_var = tk.StringVar(value=str(DESTINATION))
        self.work_var = tk.StringVar(value=str(WORK))
        # Labels: Name = English description (path entries follow).
        ttk.Label(paths, text="Source = dirty source folder").pack(side="left")
        ttk.Entry(paths, textvariable=self.source_var, width=22).pack(side="left", padx=(4, 10))
        ttk.Label(paths, text="Destination = cleaned Jellyfin folder").pack(side="left")
        ttk.Entry(paths, textvariable=self.destination_var, width=22).pack(side="left", padx=(4, 10))
        ttk.Label(paths, text="Work = scratch / archives").pack(side="left")
        ttk.Entry(paths, textvariable=self.work_var, width=16).pack(side="left", padx=(4, 8))
        ttk.Button(paths, text="Save paths", command=self.save_paths).pack(side="left")

        top = ttk.Frame(self, padding=8)
        top.pack(fill="x")
        ttk.Label(top, text="Filter:").pack(side="left")
        self.filter_var = tk.StringVar()
        self.filter_var.trace_add("write", lambda *_: self.refresh_list())
        ttk.Entry(top, textvariable=self.filter_var, width=22).pack(side="left", padx=4)
        ttk.Label(top, text="View:").pack(side="left")
        view = ttk.Combobox(
            top,
            textvariable=self.view_mode,
            values=(
                "All", "Skip", "Ready", "Convert", "Pending",
                "Later", "Empty",
            ),
            width=11,
            state="readonly",
        )
        view.pack(side="left", padx=4)
        view.bind("<<ComboboxSelected>>", lambda _e: self.refresh_list(select_index=0))
        ttk.Button(top, text="Rescan Source", command=self.rescan_source).pack(side="left", padx=4)
        self.batch_btn = ttk.Button(top, text="Batch ×0", command=self.run_batch)
        self.batch_btn.pack(side="left", padx=4)
        ttk.Button(top, text="Batch media→mp4", command=self.run_media_convert_batch).pack(
            side="left", padx=4
        )
        ttk.Button(top, text="Clear pending", command=self.clear_pending).pack(side="left", padx=2)
        self.count_var = tk.StringVar()
        ttk.Label(top, textvariable=self.count_var).pack(side="right")

        body = ttk.Panedwindow(self, orient="horizontal")
        body.pack(fill="both", expand=True, padx=8, pady=4)

        left = ttk.Frame(body)
        right = ttk.Frame(body)
        body.add(left, weight=1)
        body.add(right, weight=2)

        self.listbox = tk.Listbox(left, exportselection=False)
        scroll = ttk.Scrollbar(left, orient="vertical", command=self.listbox.yview)
        self.listbox.configure(yscrollcommand=scroll.set)
        self.listbox.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.listbox.bind("<<ListboxSelect>>", self.on_select)

        detail = ttk.Frame(right, padding=8)
        detail.pack(fill="both", expand=True)

        self.name_var = tk.StringVar()
        self.reason_var = tk.StringVar()
        self.path_var = tk.StringVar()
        self.dvd_var = tk.StringVar()
        self.status_var = tk.StringVar()

        # Entry (not Label) so the title can be selected/copied.
        name_entry = ttk.Entry(
            detail, textvariable=self.name_var, font=("", 12, "bold"), state="readonly"
        )
        name_entry.pack(anchor="w", fill="x")
        ttk.Label(detail, textvariable=self.dvd_var, foreground="#884400").pack(anchor="w")
        ttk.Label(detail, textvariable=self.reason_var, wraplength=560).pack(anchor="w", pady=(4, 8))
        ttk.Label(detail, textvariable=self.path_var, wraplength=560).pack(anchor="w")

        meta_row = ttk.Frame(detail)
        meta_row.pack(fill="x", pady=8)
        ttk.Label(meta_row, text="Year:").pack(side="left")
        self.year_var = tk.StringVar()
        year_entry = ttk.Entry(meta_row, textvariable=self.year_var, width=8)
        year_entry.pack(side="left", padx=4)
        ttk.Label(meta_row, text="IMDb:").pack(side="left", padx=(8, 0))
        self.imdb_var = tk.StringVar()
        id_entry = ttk.Entry(meta_row, textvariable=self.imdb_var, width=14)
        id_entry.pack(side="left", padx=4)
        year_entry.bind("<Return>", lambda _e: self.apply_year_and_or_imdb())
        id_entry.bind("<Return>", lambda _e: self.apply_year_and_or_imdb())
        ttk.Button(
            meta_row,
            text="Apply → Pending",
            command=self.apply_year_and_or_imdb,
        ).pack(side="left", padx=4)

        btns = ttk.Frame(detail)
        btns.pack(fill="x", pady=4)
        ttk.Button(btns, text="Open folder", command=self.open_folder).pack(side="left", padx=2)
        ttk.Button(btns, text="Play video", command=self.play_video).pack(side="left", padx=2)
        ttk.Button(btns, text="→ Pending", command=self.add_to_pending).pack(side="left", padx=2)
        ttk.Button(btns, text="Review later", command=self.mark_later).pack(side="left", padx=2)
        ttk.Button(btns, text="→ mp4", command=self.convert_media_one).pack(side="left", padx=2)
        ttk.Button(btns, text="Delete folder", command=self.delete_folder).pack(side="left", padx=2)
        ttk.Button(btns, text="Unskip", command=self.unskip).pack(side="left", padx=2)
        ttk.Button(btns, text="Next", command=self.next_item).pack(side="left", padx=2)

        ttk.Label(
            detail,
            text="mkv/avi/mp4 = ready → Year/IMDb → Pending → Batch (rename→Destination). "
                 "Loose files in Source/ are wrapped into a folder on Apply/Pending. "
                 "ISO/DVD/ratDVD/DivX/mpg/mpeg = needs convert → “→ mp4”. "
                 "Review later = manual.",
            wraplength=560,
        ).pack(anchor="w", pady=(2, 8))

        ttk.Label(detail, text="Videos / DVD files:").pack(anchor="w", pady=(4, 2))
        self.files = tk.Listbox(detail, height=12)
        self.files.pack(fill="both", expand=True)
        self.files.bind("<Double-Button-1>", lambda e: self.play_selected_file())

        ttk.Label(detail, textvariable=self.status_var).pack(anchor="w", pady=6)

    def queue_for_batch(self, folder_name):
        """Remember a Source folder for the next exact-size batch."""
        if not folder_name:
            return
        remove_manual_later(folder_name)
        self.manual_later = load_manual_later_names()
        if folder_name not in self.pending:
            self.pending.append(folder_name)
            save_pending(self.pending)
        self.update_batch_button()

    def clear_pending(self):
        if not self.pending:
            return
        if not messagebox.askyesno("Clear pending", f"Discard {len(self.pending)} entries?"):
            return
        self.pending = []
        save_pending(self.pending)
        self.update_batch_button()
        self.status_var.set("Pending cleared")

    def update_batch_button(self):
        n = len(self.pending)
        self.batch_btn.configure(
            text=f"Batch ×{n}",
            state=("normal" if n and not self.batch_running else "disabled"),
        )
        skip_n = sum(1 for n in self.folders if n in self.skip_set)
        self.count_var.set(
            f"{len(self.filtered)}/{len(self.folders)} folders · "
            f"{skip_n} skip · pending {n} · later {len(self.manual_later)}"
        )

    def list_tag(self, name):
        # Memory-only tags here — filesystem peeks on every row freeze NFS startups.
        tags = []
        if is_loose_media_name(name):
            tags.append("FILE")
        if name in self.pending:
            tags.append("PENDING")
        if name in self.manual_later:
            tags.append("LATER")
        if name in self.skip_set:
            tags.append("SKIP")
        reason = self.reasons.get(name, "").lower()
        if "multi-movie" in reason:
            tags.append("DVD-SET")
        return " [" + ", ".join(tags) + "]" if tags else ""

    def retarget_tracked(self, old: str, new: str):
        """Rename old→new in pending / skip / reasons / manual-later lists."""
        if not old or not new or old == new:
            return
        if old in self.pending:
            self.pending = [new if n == old else n for n in self.pending]
            # dedupe preserve order
            seen = set()
            self.pending = [n for n in self.pending if not (n in seen or seen.add(n))]
            save_pending(self.pending)
        if old in self.skip_set:
            self.skips = [new if n == old else n for n in self.skips]
            if new not in self.skips:
                self.skips.append(new)
            seen = set()
            self.skips = [n for n in self.skips if not (n in seen or seen.add(n))]
            self.skip_set = set(self.skips)
            save_skips(self.skips)
        if old in self.reasons:
            self.reasons[new] = self.reasons.pop(old)
            save_reasons(self.reasons)
        if old in self.manual_later:
            remove_manual_later(old)
            append_manual_later(new, "manual")
            self.manual_later = load_manual_later_names()
        self.update_batch_button()

    def ensure_movie_folder(self, name):
        """Wrap loose media into a folder; update tracking. None on failure."""
        if not name:
            return None
        if not is_loose_media_name(name):
            return name
        try:
            new_name = wrap_loose_media(name)
        except (OSError, RuntimeError, FileNotFoundError) as exc:
            messagebox.showerror("Error", f"File→folder wrap failed:\n{exc}")
            return None
        if new_name != name:
            self.retarget_tracked(name, new_name)
            self.folders = scan_source_folders()
        return new_name

    def matches_view(self, name):
        mode = self.view_mode.get()
        if mode == "All":
            return True
        if mode == "Skip":
            return name in self.skip_set
        if mode == "Pending":
            return name in self.pending
        if mode == "Later":
            return name in self.manual_later
        if mode in {"Ready", "Convert", "Empty"}:
            peek = peek_folder_media(SOURCE / name)
            if mode == "Ready":
                return peek["ready"] and name not in self.manual_later
            if mode == "Convert":
                return peek["convert"]
            if mode == "Empty":
                return peek["empty"] and not peek["missing"]
        return True

    def prune_stale_skips(self, present_names=None):
        """Remove skip/pending entries whose folders are gone (use scan set, no NFS is_dir)."""
        present = set(present_names if present_names is not None else self.folders)
        stale = [n for n in self.skips if n not in present]
        if stale:
            self.skips = [n for n in self.skips if n not in stale]
            self.skip_set = set(self.skips)
            save_skips(self.skips)
            for name in stale:
                self.reasons.pop(name, None)
            save_reasons(self.reasons)
        stale_pending = [n for n in self.pending if n not in present]
        if stale_pending:
            self.pending = [n for n in self.pending if n not in stale_pending]
            save_pending(self.pending)
        stale_later = [n for n in self.manual_later if n not in present]
        if stale_later:
            for name in stale_later:
                remove_manual_later(name)
            self.manual_later = load_manual_later_names()
        return stale, stale_pending

    def rescan_source(self, select_index=0):
        """Rescan Source/ + reload archives — left list shows all folders."""
        self.reasons = load_reasons()
        self.skips = load_skips()
        self.skip_set = set(self.skips)
        self.pending = load_pending()
        self.manual_later = load_manual_later_names()
        self.folders = scan_source_folders()
        stale, stale_pending = self.prune_stale_skips(present_names=self.folders)
        self.refresh_list(select_index=select_index)
        msg = f"Rescan: {len(self.folders)} folders in Source/"
        if stale:
            msg += f" · removed {len(stale)} stale skips"
        if stale_pending:
            msg += f" · removed {len(stale_pending)} pending"
        self.status_var.set(msg)

    def reload_from_disk(self):
        self.rescan_source(select_index=0)

    def refresh_list(self, select_index=None):
        needle = self.filter_var.get().strip().lower()
        self.filtered = []
        for name in self.folders:
            if not self.matches_view(name):
                continue
            if needle and needle not in name.lower() and needle not in self.reasons.get(name, "").lower():
                continue
            self.filtered.append(name)
        self.listbox.delete(0, "end")
        for name in self.filtered:
            self.listbox.insert("end", name + self.list_tag(name))
        self.update_batch_button()
        if not self.filtered:
            self.name_var.set("")
            self.reason_var.set("")
            self.path_var.set("")
            self.dvd_var.set("")
            self.files.delete(0, "end")
            self.status_var.set("No folders in this view")
            return
        if select_index is None:
            return
        idx = max(0, min(select_index, len(self.filtered) - 1))
        self.listbox.selection_clear(0, "end")
        self.listbox.selection_set(idx)
        self.listbox.see(idx)
        self.on_select()

    def selected_index(self):
        sel = self.listbox.curselection()
        return sel[0] if sel else None

    def selected_name(self):
        idx = self.selected_index()
        if idx is None:
            return None
        return self.filtered[idx]

    def remove_from_skip_archive(self, name):
        if name in self.skip_set:
            self.skips = [n for n in self.skips if n != name]
            self.skip_set = set(self.skips)
            save_skips(self.skips)
        if name in self.reasons:
            self.reasons.pop(name, None)
            save_reasons(self.reasons)

    def remove_from_triage(self, name, select_next_from=None, rescan=True):
        """Drop from skip archive; optionally rescan Source list."""
        idx = select_next_from
        if idx is None:
            idx = self.selected_index() or 0
        self.remove_from_skip_archive(name)
        if rescan:
            self.folders = scan_source_folders()
        self.refresh_list(select_index=idx)

    def on_select(self, _event=None):
        name = self.selected_name()
        if not name:
            return
        folder = SOURCE / name
        self.name_var.set(name)
        if name in self.skip_set:
            self.reason_var.set(self.reasons.get(name, "(in skip archive, no reason text)"))
        elif name in self.pending:
            self.reason_var.set("In Pending — ready for Batch ×N (rename title/year/IMDb)")
        elif name in self.manual_later:
            self.reason_var.set("Review later — manual follow-up")
        else:
            self.reason_var.set("In Source/ — check format, then Pending or Convert")
        self.path_var.set(str(folder))

        # Loose file: show status without wrapping (wrap happens on Apply/Pending/…).
        if is_loose_media_name(name):
            suf = Path(name).suffix.lower()
            self.files.delete(0, "end")
            self.files.insert("end", name + " [loose file]")
            m = re.search(r"\((\d{4})\)", Path(name).stem)
            if not m:
                m = re.search(r"(?<!\d)(19\d{2}|20\d{2})(?!\d)", Path(name).stem)
            self.year_var.set(m.group(1) if m else "")
            self.imdb_var.set("")
            if suf in READY_VIDEO_EXT:
                self.dvd_var.set(
                    "Loose file (ready) — Year/IMDb or → Pending wraps into a folder"
                )
                self.status_var.set("FILE · READY")
            elif suf in FFMPEG_CONVERT_EXTS or suf in {".iso", ".img"}:
                self.dvd_var.set(
                    "Loose file — “→ mp4” / Pending wraps into a folder first"
                )
                self.status_var.set("FILE · →mp4")
            else:
                self.dvd_var.set("Loose media file")
                self.status_var.set("FILE")
            return

        is_dvd = has_dvd_structure(folder)
        is_xc = is_ratdvd_xc(folder)
        multi = "multi-movie" in self.reasons.get(name, "").lower()
        rats = find_ratdvds(folder)
        ffmpeg_srcs = find_ffmpeg_convert_files(folder)
        ready = has_ready_video(folder)
        convert = needs_media_convert(folder)
        status_bits = []
        if ready:
            status_bits.append("Ready (mkv/avi/mp4) — rename/Pending still needed")
        if is_xc:
            status_bits.append(
                "ratDVD (.VSI/XC, not a real MPEG-DVD) — “→ mp4” cannot work; "
                "Need original .ratDVD + ratDVD app"
            )
        elif convert:
            bits = []
            if rats:
                bits.append("ratDVD")
            exts = sorted({p.suffix.lower().lstrip(".") for p in ffmpeg_srcs})
            bits.extend(exts)
            if is_dvd:
                bits.append("DVD/ISO")
            status_bits.append(
                "Needs convert (" + "+".join(bits) + ") — “→ mp4” / Batch media→mp4"
            )
        elif multi:
            status_bits.append("Reported as multi-movie DVD set")
        elif not ready and folder.is_dir() and not is_effectively_empty(folder):
            status_bits.append("No mkv/avi/mp4 — neither ready nor a known convert source")
        self.dvd_var.set(" · ".join(status_bits))
        m = re.search(r"\((\d{4})\)", name)
        if m:
            year_guess = m.group(1)
        else:
            m = re.search(r"(?<!\d)(19\d{2}|20\d{2})(?!\d)", name)
            year_guess = m.group(1) if m else ""
        self.year_var.set(year_guess)
        # Prefill IMDb ID from existing local metadata if present.
        existing_id = ""
        try:
            for path in folder.rglob("*"):
                if not path.is_file():
                    continue
                if path.suffix.lower() not in {".nfo", ".txt", ".xml"}:
                    continue
                if any(p.startswith(".") or p.startswith("@") for p in path.parts):
                    continue
                text = path.read_text(encoding="utf-8", errors="ignore")
                m = re.search(r"\b(tt\d{7,9})\b", text, flags=re.IGNORECASE)
                if m:
                    existing_id = m.group(1).lower()
                    break
        except OSError:
            pass
        self.imdb_var.set(existing_id)
        self.files.delete(0, "end")
        if not folder.is_dir():
            self.status_var.set("Folder missing under Source/")
            return
        for rat in find_ratdvds(folder):
            self.files.insert("end", rat.name + " [ratDVD]")
        for src in ffmpeg_srcs:
            tag = src.suffix.lower().lstrip(".") or "convert"
            try:
                self.files.insert("end", str(src.relative_to(folder)) + f" [{tag}]")
            except ValueError:
                self.files.insert("end", src.name + f" [{tag}]")
        if is_dvd:
            self.files.insert("end", "(DVD structure detected)")
            for marker in ("VIDEO_TS", "AUDIO_TS"):
                if (folder / marker).is_dir():
                    self.files.insert("end", marker + "/")
            for iso in sorted(folder.glob("*.iso")) + sorted(folder.glob("*.ISO")):
                self.files.insert("end", iso.name)
        videos = find_videos(folder)
        ready_videos = find_videos(folder, exts=READY_VIDEO_EXT)
        for path in videos:
            try:
                rel = path.relative_to(folder)
            except ValueError:
                rel = path.name
            tag = ""
            if path.suffix.lower() in READY_VIDEO_EXT:
                tag = " [OK]"
            self.files.insert("end", str(rel) + tag)
        parts = []
        if is_effectively_empty(folder):
            parts.append("EMPTY")
        if ready:
            parts.append(f"READY ({len(ready_videos)})")
        if convert:
            parts.append("→mp4")
        if is_dvd:
            parts.append("DVD/ISO")
        parts.append(f"{len(videos)} video(s)" if videos else "no normal videos")
        self.status_var.set(" · ".join(parts))

    def open_folder(self):
        name = self.selected_name()
        if not name:
            return
        if is_loose_media_name(name):
            path = SOURCE / name
            if path.is_file():
                subprocess.Popen(["xdg-open", str(path.parent)])
                return
        name = self.ensure_movie_folder(name)
        if not name:
            return
        folder = SOURCE / name
        if not folder.is_dir():
            messagebox.showerror("Error", f"Folder missing:\n{folder}")
            return
        subprocess.Popen(["xdg-open", str(folder)])

    def play_video(self):
        name = self.selected_name()
        if not name:
            return
        if is_loose_media_name(name):
            path = SOURCE / name
            if path.is_file():
                subprocess.Popen(["xdg-open", str(path)])
                return
        folder = SOURCE / name
        videos = find_videos(folder, limit=1)
        if videos:
            subprocess.Popen(["xdg-open", str(videos[0])])
            return
        isos = list(folder.glob("*.iso")) + list(folder.glob("*.ISO")) if folder.is_dir() else []
        if isos:
            subprocess.Popen(["xdg-open", str(isos[0])])
            return
        messagebox.showinfo("Info", "No video/ISO found")

    def play_selected_file(self):
        name = self.selected_name()
        sel = self.files.curselection()
        if not name or not sel:
            return
        rel = self.files.get(sel[0])
        if rel.startswith("(") or rel.endswith("/"):
            return
        path = SOURCE / name / rel
        if path.is_file():
            subprocess.Popen(["xdg-open", str(path)])

    def apply_year_and_or_imdb(self):
        """Apply year and/or IMDb ID (either or both), then queue for batch."""
        name = self.selected_name()
        if not name:
            return
        # Read fields before wrap — wrap/rescan must not clobber user input.
        year = self.year_var.get().strip()
        imdb_raw = self.imdb_var.get().strip()
        imdb_id = None
        if imdb_raw:
            m = re.search(r"(tt\d{7,9})", imdb_raw, flags=re.IGNORECASE)
            if not m:
                messagebox.showerror("Error", "IMDb ID expected, e.g. tt0089563")
                return
            imdb_id = m.group(1).lower()

        if year and not re.fullmatch(r"19\d{2}|20\d{2}", year):
            messagebox.showerror("Error", "Year must be 19xx/20xx (or leave empty)")
            return
        if not year and not imdb_id:
            messagebox.showerror("Error", "Enter year and/or IMDb ID")
            return

        name = self.ensure_movie_folder(name)
        if not name:
            return
        folder = SOURCE / name
        if not folder.is_dir():
            messagebox.showerror("Error", f"Folder missing:\n{folder}")
            return

        new_name = name
        notes = []

        if year:
            base = re.sub(r"\s*\(\d{4}\)\s*$", "", name).strip()
            base = re.sub(r"[_\s.-](19\d{2}|20\d{2})$", "", base).strip()
            new_name = f"{base} ({year})"
            dest = SOURCE / new_name
            if dest.resolve() != folder.resolve():
                if dest.exists():
                    messagebox.showerror("Error", f"Target already exists:\n{dest}")
                    return
                try:
                    folder.rename(dest)
                except OSError as exc:
                    messagebox.showerror("Error", f"Rename failed:\n{exc}")
                    return
                folder = dest
                notes.append(f"Year→{new_name}")
            else:
                notes.append(f"Year ok ({year})")

        if imdb_id:
            nfo = folder / "movie.nfo"
            try:
                nfo.write_text(
                    f"<movie>\n  <title>{new_name}</title>\n  <imdbid>{imdb_id}</imdbid>\n"
                    f"  <uniqueid type=\"imdb\">{imdb_id}</uniqueid>\n"
                    + (f"  <year>{year}</year>\n" if year else "")
                    + "</movie>\n",
                    encoding="utf-8",
                )
            except OSError as exc:
                messagebox.showerror("Error", f"Could not write movie.nfo:\n{exc}")
                return
            notes.append(f"IMDb {imdb_id}")

        self.remove_from_skip_archive(name)
        if new_name != name:
            self.remove_from_skip_archive(new_name)
        self.queue_for_batch(new_name)
        self.folders = scan_source_folders()
        self.refresh_list(select_index=0)
        if new_name in self.filtered:
            idx = self.filtered.index(new_name)
            self.listbox.selection_clear(0, "end")
            self.listbox.selection_set(idx)
            self.listbox.see(idx)
            self.on_select()
        self.status_var.set(" · ".join(notes) + f" · Pending {len(self.pending)}")

    def add_to_pending(self):
        name = self.selected_name()
        if not name:
            return
        name = self.ensure_movie_folder(name)
        if not name:
            return
        folder = SOURCE / name
        if not folder.is_dir():
            messagebox.showerror("Error", "Folder missing")
            return
        if needs_media_convert(folder) and not has_ready_video(folder):
            if not messagebox.askyesno(
                "Needs convert",
                f"No mkv/avi/mp4 — convert ISO/DVD/ratDVD/DivX/mpg/mpeg first?\n"
                f"Add to Pending anyway?\n\n{name}",
            ):
                return
        self.remove_from_skip_archive(name)
        self.queue_for_batch(name)
        self.refresh_list(select_index=0)
        if name in self.filtered:
            idx = self.filtered.index(name)
            self.listbox.selection_clear(0, "end")
            self.listbox.selection_set(idx)
            self.listbox.see(idx)
            self.on_select()
        self.status_var.set(f"→ Pending: {name} ({len(self.pending)})")

    def mark_later(self):
        """Park folder for manual review later (not pending, not convert batch)."""
        name = self.selected_name()
        if not name:
            return
        idx = self.selected_index() or 0
        if not messagebox.askyesno(
            "Review later",
            f"Park for manual review?\n"
            f"(removed from Pending, View “Later”)\n\n{name}",
        ):
            return
        append_manual_later(name, "manual")
        self.manual_later = load_manual_later_names()
        if name in self.pending:
            self.pending = [n for n in self.pending if n != name]
            save_pending(self.pending)
            self.update_batch_button()
        self.remove_from_skip_archive(name)
        self.refresh_list(select_index=idx)
        self.status_var.set(f"Later: {name}")

    def folder_needs_media_convert(self, folder: Path) -> bool:
        return needs_media_convert(folder)

    def convert_media_one(self):
        name = self.selected_name()
        if not name:
            return
        name = self.ensure_movie_folder(name)
        if not name:
            return
        folder = SOURCE / name
        if not self.folder_needs_media_convert(folder):
            messagebox.showinfo(
                "mp4",
                "No divx/mpg/mpeg / ratDVD / VIDEO_TS / ISO in this folder.",
            )
            return
        if not MEDIA_TO_MP4.is_file():
            messagebox.showerror("Error", f"Missing: {MEDIA_TO_MP4}")
            return
        if not messagebox.askyesno(
            "→ mp4",
            f"Convert to MP4 (German audio, no subtitles)?\n"
            f"Sources (divx/mpg/mpeg/DVD/ratDVD/ISO) are removed afterwards.\n\n{name}",
        ):
            return
        self._start_media_convert([folder], title=f"mp4: {name}")

    def run_media_convert_batch(self):
        if not MEDIA_TO_MP4.is_file():
            messagebox.showerror("Error", f"Missing: {MEDIA_TO_MP4}")
            return
        targets = [
            SOURCE / name
            for name in self.folders
            if self.folder_needs_media_convert(SOURCE / name)
        ]
        if not targets:
            messagebox.showinfo("Batch media→mp4", "Nothing to convert.")
            return
        preview = "\n".join(p.name for p in targets[:20])
        if len(targets) > 20:
            preview += f"\n… +{len(targets) - 20} more"
        if not messagebox.askyesno(
            "Batch media→mp4",
            f"Convert {len(targets)} folders to MP4 (German audio, no subs)?\n\n{preview}",
        ):
            return
        self._start_media_convert(targets, title=f"media→mp4 ×{len(targets)}")

    def _start_media_convert(self, folders: list, title: str):
        win = BatchProgressWindow(self, [p.name for p in folders])
        win.title(title)
        self.status_var.set(f"{title} gestartet…")

        def worker():
            ok = True
            summary = "Done"
            try:
                for i, folder in enumerate(folders, 1):
                    win.set_phase(f"[{i}/{len(folders)}] {folder.name}", i - 1)
                    win.log(f"\n=== {folder.name} ===")
                    env = {**os.environ, "PYTHONUNBUFFERED": "1"}
                    proc = subprocess.Popen(
                        ["python3", "-u", str(MEDIA_TO_MP4), str(folder)],
                        cwd=str(SCRIPT_DIR),
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        env=env,
                    )
                    assert proc.stdout is not None
                    # HandBrake progress can emit non-UTF-8; never use text=True here.
                    for raw in proc.stdout:
                        win.log(raw.decode("utf-8", errors="replace").rstrip("\n"))
                    rc = proc.wait()
                    if rc != 0:
                        ok = False
                        win.log(f"ERROR exit {rc}")
                win.set_phase("Done", len(folders))
                summary = f"{'OK' if ok else 'With errors'} — {len(folders)} folders"
            except Exception as exc:
                ok = False
                win.log(f"ERROR: {exc}")
                summary = str(exc)
            win.done(ok, summary)
            self.after(0, self._media_convert_finished)

        threading.Thread(target=worker, daemon=True).start()

    def _media_convert_finished(self):
        self.folders = scan_source_folders()
        self.refresh_list(select_index=0)
        self.status_var.set("media→mp4 batch done — rescan ok")

    def delete_folder(self):
        name = self.selected_name()
        if not name:
            return
        idx = self.selected_index() or 0
        folder = SOURCE / name
        if is_loose_media_name(name) and folder.is_file():
            if not messagebox.askyesno("Delete file?", f"DELETE loose file?\n\n{folder}"):
                return
            try:
                folder.unlink()
            except OSError as exc:
                messagebox.showerror("Error", f"Delete failed:\n{exc}")
                return
            self.remove_from_skip_archive(name)
            if name in self.pending:
                self.pending = [n for n in self.pending if n != name]
                save_pending(self.pending)
            if name in self.manual_later:
                remove_manual_later(name)
                self.manual_later = load_manual_later_names()
            self.folders = scan_source_folders()
            self.refresh_list(select_index=idx)
            self.status_var.set(f"Deleted: {name}")
            return
        if not folder.is_dir():
            if messagebox.askyesno("List only", f"Folder missing. Remove from skip list only?\n\n{name}"):
                self.remove_from_triage(name, select_next_from=idx)
            return
        empty = is_effectively_empty(folder)
        warn = (
            f"DELETE folder?\n\n{folder}\n\n"
            + ("Looks empty." if empty else "WARNING: Still contains files!")
        )
        if not messagebox.askyesno("Delete folder?", warn):
            return
        try:
            shutil.rmtree(folder)
        except OSError as exc:
            messagebox.showerror("Error", f"Delete failed:\n{exc}")
            return
        self.remove_from_skip_archive(name)
        if name in self.pending:
            self.pending = [n for n in self.pending if n != name]
            save_pending(self.pending)
        if name in self.manual_later:
            remove_manual_later(name)
            self.manual_later = load_manual_later_names()
        self.folders = scan_source_folders()
        self.refresh_list(select_index=idx)
        self.status_var.set(f"Deleted: {name}")

    def unskip(self):
        name = self.selected_name()
        if not name:
            return
        idx = self.selected_index() or 0
        if name not in self.skip_set:
            messagebox.showinfo("Unskip", "Not in the skip list — use “→ Pending”.")
            return
        if not messagebox.askyesno(
            "Unskip",
            f"Remove from permanent_skipped and add to Pending?\n\n{name}",
        ):
            return
        self.remove_from_skip_archive(name)
        name = self.ensure_movie_folder(name) or name
        if (SOURCE / name).is_dir():
            self.queue_for_batch(name)
        self.refresh_list(select_index=idx)
        self.status_var.set(f"Unskip → pending {len(self.pending)}")

    def run_batch(self):
        if self.batch_running:
            messagebox.showinfo("Batch", "Already running.")
            return
        # Wrap any loose pending files into folders first.
        wrapped = []
        for n in list(self.pending):
            if is_loose_media_name(n):
                try:
                    new = wrap_loose_media(n)
                except (OSError, RuntimeError, FileNotFoundError) as exc:
                    messagebox.showerror("Pending", f"Wrap failed for {n}:\n{exc}")
                    return
                if new != n:
                    self.retarget_tracked(n, new)
                wrapped.append(new)
            else:
                wrapped.append(n)
        self.pending = list(dict.fromkeys(wrapped))
        save_pending(self.pending)
        # Drop pending entries whose folders vanished
        alive = [n for n in self.pending if (SOURCE / n).is_dir()]
        missing = [n for n in self.pending if n not in alive]
        self.pending = alive
        save_pending(self.pending)
        self.update_batch_button()
        if missing:
            messagebox.showwarning("Pending", "No longer in Source (skipped):\n" + "\n".join(missing[:12]))
        if not self.pending:
            messagebox.showinfo("Batch", "Pending is empty.\nSet year first or Unskip.")
            return
        titles = list(self.pending)
        if not messagebox.askyesno(
            "Start batch",
            f"Process exactly {len(titles)} folders?\n\n" + "\n".join(titles[:15])
            + ("\n…" if len(titles) > 15 else ""),
        ):
            return
        if not RENAMER.is_file():
            messagebox.showerror("Error", f"Missing: {RENAMER}")
            return
        if not DESTINATION.is_dir():
            messagebox.showerror("Error", f"Destination missing: {DESTINATION}")
            return

        win = BatchProgressWindow(self, titles)
        self.batch_running = True
        self.update_batch_button()

        def worker():
            ok = False
            summary = "Cancelled"
            try:
                WORK.mkdir(parents=True, exist_ok=True)
                batch_list = WORK / "gui_pending_list.txt"
                batch_list.write_text(
                    "# BASE_PATH=%s\n%s\n" % (SOURCE, "\n".join(titles)),
                    encoding="utf-8",
                )
                win.set_phase(f"Dry-run ({len(titles)})…", 0)
                win.log("=== DRY RUN ===")
                success = WORK / "gui_batch_success.txt"
                backlog = WORK / "gui_batch_backlog.txt"
                # Clear stale outputs from previous batches before parsing.
                for path in (success, backlog, WORK / "gui_batch_backlog_skipped.txt"):
                    path.write_text("", encoding="utf-8")
                self._run_renamer(
                    ["--input", str(batch_list),
                     "--success-file", str(success),
                     "--backlog-file", str(backlog)],
                    win,
                )
                movable = parse_movable(success, backlog)
                win.log(f"Movable: {len(movable)}")
                if not movable:
                    summary = "No movable successes"
                    win.done(False, summary)
                    return

                exec_list = WORK / "gui_pending_execute.txt"
                exec_list.write_text(
                    "# BASE_PATH=%s\n%s\n" % (
                        SOURCE,
                        "\n".join(orig for orig, _new in movable),
                    ),
                    encoding="utf-8",
                )
                win.set_phase(f"Execute ({len(movable)})…", 0)
                win.log("=== EXECUTE ===")
                success_ex = WORK / "gui_batch_success_exec.txt"
                backlog_ex = WORK / "gui_batch_backlog_exec.txt"
                self._run_renamer(
                    ["--input", str(exec_list), "--execute",
                     "--success-file", str(success_ex),
                     "--backlog-file", str(backlog_ex)],
                    win,
                )

                win.set_phase("Move to Destination…", 0)
                win.log("=== MOVE ===")
                moved = 0
                for i, (_orig, new) in enumerate(movable, 1):
                    src = SOURCE / new
                    dst = DESTINATION / new
                    if src.exists():
                        if dst.exists():
                            win.log(f"ALREADY in Destination — remove Source leftover: {new}")
                            shutil.rmtree(src)
                        else:
                            shutil.move(str(src), str(DESTINATION))
                            win.log(f"MOVED: {new}")
                            moved += 1
                    else:
                        win.log(f"MISSING: {src}")
                    win.set_phase(f"Move {i}/{len(movable)}", i)

                # Drop this batch's titles from pending (persisted for resume).
                self.pending = [n for n in load_pending() if n not in titles]
                save_pending(self.pending)
                ok = True
                summary = f"Done — {moved} moved, {len(movable)} execute"
            except Exception as exc:
                win.log(f"ERROR: {exc}")
                summary = f"Error: {exc}"
            finally:
                win.done(ok, summary)
                self.after(0, self._batch_finished)

        threading.Thread(target=worker, daemon=True).start()

    def _run_renamer(self, extra_args, win: BatchProgressWindow):
        cmd = ["python3", str(RENAMER), *extra_args]
        win.log("$ " + " ".join(cmd))
        proc = subprocess.Popen(
            cmd,
            cwd=str(SCRIPT_DIR),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        assert proc.stdout is not None
        step = 0
        for raw in proc.stdout:
            line = raw.decode("utf-8", errors="replace").rstrip("\n")
            win.log(line)
            if line.startswith("Processing:") or line.startswith("[") and "]Processing:" in line.replace(" ", ""):
                step += 1
                win.set_phase(line.strip(), step)
            elif "Processing:" in line:
                step += 1
                win.set_phase(line.strip()[:80], step)
        rc = proc.wait()
        if rc != 0:
            raise RuntimeError(f"movie_renamer exit {rc}")

    def _batch_finished(self):
        self.batch_running = False
        self.pending = load_pending()
        self.folders = scan_source_folders()
        self.refresh_list(select_index=0)
        self.update_batch_button()
        self.status_var.set(f"Batch done · pending {len(self.pending)}")

    def next_item(self):
        sel = self.selected_index()
        if sel is None:
            return
        nxt = sel + 1
        if nxt >= self.listbox.size():
            self.status_var.set("End of list")
            return
        self.listbox.selection_clear(0, "end")
        self.listbox.selection_set(nxt)
        self.listbox.see(nxt)
        self.on_select()


def main():
    app = TriageApp()
    app.mainloop()


if __name__ == "__main__":
    main()
