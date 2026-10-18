#!/usr/bin/env python3
"""GUI to triage permanent IMDb skips: year, DVD, delete, pending batch with live progress."""
import os
import queue
import re
import shutil
import subprocess
import threading
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

FILME = Path(os.environ.get("FILME", "/path/to/Source"))
MOVIES = Path(os.environ.get("MOVIES", "/path/to/Destination"))
WORK = Path(os.environ.get("WORK_DIR", Path.home() / "filme_batch_work"))
SKIP_ARCHIVE = Path(os.environ.get("SKIP_ARCHIVE", WORK / "permanent_skipped.txt"))
REASONS_FILE = Path(os.environ.get("REASONS_FILE", WORK / "skipped_reasons.txt"))
DVD_QUEUE = Path(os.environ.get("DVD_QUEUE", WORK / "dvd_manual.txt"))
PENDING_FILE = Path(os.environ.get("PENDING_FILE", WORK / "pending_batch.txt"))
SCRIPT_DIR = Path(__file__).resolve().parent
RENAMER = SCRIPT_DIR / "movie_renamer.py"
RATDVD_TOOL = SCRIPT_DIR / "ratdvd_to_video_ts.py"
VIDEO_EXT = {".mkv", ".mp4", ".avi", ".mov", ".m4v", ".wmv", ".mpg", ".mpeg", ".m2ts", ".ts"}
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


def append_dvd_queue(name, note=""):
    DVD_QUEUE.parent.mkdir(parents=True, exist_ok=True)
    existing = set()
    if DVD_QUEUE.is_file():
        existing = {
            line.split(" | ", 1)[0].strip()
            for line in DVD_QUEUE.read_text(encoding="utf-8", errors="ignore").splitlines()
            if line.strip()
        }
    if name in existing:
        return
    with DVD_QUEUE.open("a", encoding="utf-8") as handle:
        handle.write(f"{name} | {note}\n" if note else f"{name}\n")


def find_ratdvds(folder: Path):
    if not folder.is_dir():
        return []
    return sorted(folder.glob("*.ratDVD")) + sorted(folder.glob("*.ratdvd"))


def has_dvd_structure(folder: Path) -> bool:
    if not folder.is_dir():
        return False
    try:
        if (folder / "VIDEO_TS").is_dir():
            return True
        for child in folder.iterdir():
            if child.is_file() and child.name.upper() == "VIDEO_TS.IFO":
                return True
            if child.is_dir() and child.name.upper() == "VIDEO_TS":
                return True
            if child.is_dir() and (child / "VIDEO_TS").is_dir():
                return True
            if child.is_file() and child.suffix.lower() == ".iso":
                return True
    except OSError:
        return False
    return False


def find_videos(folder: Path, limit=20):
    if not folder.is_dir():
        return []
    found = []
    for root, dirs, files in os.walk(folder):
        dirs[:] = [d for d in dirs if not d.startswith(".") and not d.startswith("@")]
        for name in files:
            if Path(name).suffix.lower() in VIDEO_EXT:
                found.append(Path(root) / name)
                if len(found) >= limit:
                    return found
    return found


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

        self.close_btn = ttk.Button(self, text="Schließen", command=self.destroy, state="disabled")
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
        self.title("Filme skip triage")
        self.geometry("1000x720")
        self.reasons = load_reasons()
        self.skips = load_skips()
        self.pending = load_pending()
        self.filtered = list(self.skips)
        self.batch_running = False
        self._build()
        self.prune_stale_skips()
        self.refresh_list(select_index=0)
        self.update_batch_button()

    def _build(self):
        top = ttk.Frame(self, padding=8)
        top.pack(fill="x")
        ttk.Label(top, text="Filter:").pack(side="left")
        self.filter_var = tk.StringVar()
        self.filter_var.trace_add("write", lambda *_: self.refresh_list())
        ttk.Entry(top, textvariable=self.filter_var, width=28).pack(side="left", padx=4)
        ttk.Button(top, text="Reload", command=self.reload_from_disk).pack(side="left", padx=4)
        self.batch_btn = ttk.Button(top, text="Batch ×0", command=self.run_batch)
        self.batch_btn.pack(side="left", padx=4)
        ttk.Button(top, text="Pending leeren", command=self.clear_pending).pack(side="left", padx=2)
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

        ttk.Label(detail, textvariable=self.name_var, font=("", 12, "bold")).pack(anchor="w")
        ttk.Label(detail, textvariable=self.dvd_var, foreground="#884400").pack(anchor="w")
        ttk.Label(detail, textvariable=self.reason_var, wraplength=560).pack(anchor="w", pady=(4, 8))
        ttk.Label(detail, textvariable=self.path_var, wraplength=560).pack(anchor="w")

        year_row = ttk.Frame(detail)
        year_row.pack(fill="x", pady=8)
        ttk.Label(year_row, text="Jahr:").pack(side="left")
        self.year_var = tk.StringVar()
        year_entry = ttk.Entry(year_row, textvariable=self.year_var, width=8)
        year_entry.pack(side="left", padx=4)
        year_entry.bind("<Return>", lambda _e: self.apply_year())
        ttk.Button(year_row, text="Jahr setzen & aus Liste", command=self.apply_year).pack(
            side="left", padx=4
        )

        btns = ttk.Frame(detail)
        btns.pack(fill="x", pady=4)
        ttk.Button(btns, text="Ordner öffnen", command=self.open_folder).pack(side="left", padx=2)
        ttk.Button(btns, text="Video abspielen", command=self.play_video).pack(side="left", padx=2)
        ttk.Button(btns, text="Als DVD ablegen", command=self.mark_dvd).pack(side="left", padx=2)
        ttk.Button(btns, text="ratDVD → VIDEO_TS", command=self.convert_ratdvd).pack(side="left", padx=2)
        ttk.Button(btns, text="Ordner löschen", command=self.delete_folder).pack(side="left", padx=2)
        ttk.Button(btns, text="Aus Skip-Liste nehmen", command=self.unskip).pack(side="left", padx=2)
        ttk.Button(btns, text="Nächster", command=self.next_item).pack(side="left", padx=2)

        ttk.Label(
            detail,
            text="Jahr setzen / Unskip → Pending-Batch. "
                 "DVD ablegen / Löschen → kein Batch. "
                 "„Batch ×N“ verarbeitet genau diese N Ordner (Live-Fenster).",
            wraplength=560,
        ).pack(anchor="w", pady=(2, 8))

        ttk.Label(detail, text="Videos / DVD-Dateien:").pack(anchor="w", pady=(4, 2))
        self.files = tk.Listbox(detail, height=12)
        self.files.pack(fill="both", expand=True)
        self.files.bind("<Double-Button-1>", lambda e: self.play_selected_file())

        ttk.Label(detail, textvariable=self.status_var).pack(anchor="w", pady=6)

    def queue_for_batch(self, folder_name):
        """Remember a Filme folder for the next exact-size batch."""
        if not folder_name:
            return
        if folder_name not in self.pending:
            self.pending.append(folder_name)
            save_pending(self.pending)
        self.update_batch_button()

    def clear_pending(self):
        if not self.pending:
            return
        if not messagebox.askyesno("Pending leeren", f"{len(self.pending)} Einträge verwerfen?"):
            return
        self.pending = []
        save_pending(self.pending)
        self.update_batch_button()
        self.status_var.set("Pending geleert")

    def update_batch_button(self):
        n = len(self.pending)
        self.batch_btn.configure(text=f"Batch ×{n}", state=("normal" if n and not self.batch_running else "disabled"))
        self.count_var.set(f"{len(self.filtered)}/{len(self.skips)} skips · pending {n}")

    def list_tag(self, name):
        folder = FILME / name
        tags = []
        if not folder.is_dir():
            tags.append("FEHLT")
        elif is_effectively_empty(folder):
            tags.append("LEER")
        if find_ratdvds(folder):
            tags.append("ratDVD")
        if has_dvd_structure(folder):
            tags.append("DVD")
        reason = self.reasons.get(name, "").lower()
        if "multi-movie" in reason:
            tags.append("DVD-SET")
        if not tags:
            return ""
        return " [" + ", ".join(tags) + "]"

    def prune_stale_skips(self):
        """Remove skip/pending entries whose folders no longer exist under Filme/."""
        stale = [n for n in self.skips if not (FILME / n).is_dir()]
        if stale:
            self.skips = [n for n in self.skips if n not in stale]
            save_skips(self.skips)
            for name in stale:
                self.reasons.pop(name, None)
            save_reasons(self.reasons)
        stale_pending = [n for n in self.pending if not (FILME / n).is_dir()]
        if stale_pending:
            self.pending = [n for n in self.pending if n not in stale_pending]
            save_pending(self.pending)
        return stale, stale_pending

    def reload_from_disk(self):
        self.reasons = load_reasons()
        self.skips = load_skips()
        self.pending = load_pending()
        stale, stale_pending = self.prune_stale_skips()
        self.refresh_list(select_index=0)
        self.update_batch_button()
        msg = "Neu geladen"
        if stale:
            msg += f" · {len(stale)} Skip(s) entfernt (Ordner weg): " + ", ".join(stale[:5])
            if len(stale) > 5:
                msg += "…"
        if stale_pending:
            msg += f" · {len(stale_pending)} Pending entfernt"
        self.status_var.set(msg)

    def refresh_list(self, select_index=None):
        needle = self.filter_var.get().strip().lower()
        self.filtered = [
            n for n in self.skips
            if not needle or needle in n.lower() or needle in self.reasons.get(n, "").lower()
        ]
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
            self.status_var.set("Keine Skips mehr in der Liste")
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

    def remove_from_triage(self, name, select_next_from=None):
        idx = select_next_from
        if idx is None:
            idx = self.selected_index() or 0
        self.skips = [n for n in self.skips if n != name]
        save_skips(self.skips)
        if name in self.reasons:
            self.reasons.pop(name, None)
            save_reasons(self.reasons)
        self.refresh_list(select_index=idx)

    def on_select(self, _event=None):
        name = self.selected_name()
        if not name:
            return
        folder = FILME / name
        self.name_var.set(name)
        self.reason_var.set(self.reasons.get(name, "(kein Reason-Eintrag)"))
        self.path_var.set(str(folder))
        is_dvd = has_dvd_structure(folder)
        multi = "multi-movie" in self.reasons.get(name, "").lower()
        rats = find_ratdvds(folder)
        if rats:
            self.dvd_var.set(f"ratDVD gefunden ({rats[0].name}) — „ratDVD → VIDEO_TS“")
        elif is_dvd and multi:
            self.dvd_var.set("DVD erkannt — eher Multi-Movie-Set → „Als DVD ablegen“")
        elif is_dvd:
            self.dvd_var.set("DVD erkannt — Jahr setzen (→ Batch) oder „Als DVD ablegen“")
        elif multi:
            self.dvd_var.set("Als Multi-Movie-DVD-Set gemeldet")
        else:
            self.dvd_var.set("")
        m = re.search(r"\((\d{4})\)", name)
        if m:
            year_guess = m.group(1)
        else:
            m = re.search(r"(?<!\d)(19\d{2}|20\d{2})(?!\d)", name)
            year_guess = m.group(1) if m else ""
        self.year_var.set(year_guess)
        self.files.delete(0, "end")
        if not folder.is_dir():
            self.status_var.set("Ordner fehlt unter Filme/")
            return
        for rat in find_ratdvds(folder):
            self.files.insert("end", rat.name + " [ratDVD]")
        if is_dvd:
            self.files.insert("end", "(DVD-Struktur erkannt)")
            for marker in ("VIDEO_TS", "AUDIO_TS"):
                if (folder / marker).is_dir():
                    self.files.insert("end", marker + "/")
            for iso in sorted(folder.glob("*.iso")) + sorted(folder.glob("*.ISO")):
                self.files.insert("end", iso.name)
        videos = find_videos(folder)
        for path in videos:
            try:
                rel = path.relative_to(folder)
            except ValueError:
                rel = path.name
            self.files.insert("end", str(rel))
        parts = []
        if is_effectively_empty(folder):
            parts.append("LEER")
        if is_dvd:
            parts.append("DVD")
        parts.append(f"{len(videos)} Video(s)" if videos else "keine normalen Videos")
        self.status_var.set(" · ".join(parts))

    def open_folder(self):
        name = self.selected_name()
        if not name:
            return
        folder = FILME / name
        if not folder.is_dir():
            messagebox.showerror("Fehler", f"Ordner fehlt:\n{folder}")
            return
        subprocess.Popen(["xdg-open", str(folder)])

    def play_video(self):
        name = self.selected_name()
        if not name:
            return
        folder = FILME / name
        videos = find_videos(folder, limit=1)
        if videos:
            subprocess.Popen(["xdg-open", str(videos[0])])
            return
        isos = list(folder.glob("*.iso")) + list(folder.glob("*.ISO")) if folder.is_dir() else []
        if isos:
            subprocess.Popen(["xdg-open", str(isos[0])])
            return
        messagebox.showinfo("Info", "Kein Video/ISO gefunden")

    def play_selected_file(self):
        name = self.selected_name()
        sel = self.files.curselection()
        if not name or not sel:
            return
        rel = self.files.get(sel[0])
        if rel.startswith("(") or rel.endswith("/"):
            return
        path = FILME / name / rel
        if path.is_file():
            subprocess.Popen(["xdg-open", str(path)])

    def apply_year(self):
        name = self.selected_name()
        if not name:
            return
        idx = self.selected_index() or 0
        year = self.year_var.get().strip()
        if not re.fullmatch(r"19\d{2}|20\d{2}", year):
            messagebox.showerror("Fehler", "Jahr muss 19xx/20xx sein")
            return
        folder = FILME / name
        if not folder.is_dir():
            messagebox.showerror("Fehler", f"Ordner fehlt:\n{folder}")
            return
        base = re.sub(r"\s*\(\d{4}\)\s*$", "", name).strip()
        new_name = f"{base} ({year})"
        dest = FILME / new_name
        if dest.exists():
            messagebox.showerror("Fehler", f"Ziel existiert schon:\n{dest}")
            return
        try:
            folder.rename(dest)
        except OSError as exc:
            messagebox.showerror("Fehler", f"Rename fehlgeschlagen:\n{exc}")
            return
        self.remove_from_triage(name, select_next_from=idx)
        self.queue_for_batch(new_name)
        self.status_var.set(f"→ {new_name} · pending {len(self.pending)}")

    def convert_ratdvd(self):
        name = self.selected_name()
        if not name:
            return
        folder = FILME / name
        rats = find_ratdvds(folder)
        if not rats:
            messagebox.showinfo("ratDVD", "Keine .ratDVD-Datei in diesem Ordner.")
            return
        if not RATDVD_TOOL.is_file():
            messagebox.showerror("Fehler", f"Fehlt: {RATDVD_TOOL}")
            return
        if not messagebox.askyesno(
            "ratDVD → VIDEO_TS",
            f"Extrahieren nach VIDEO_TS/ und .ratDVD danach löschen?\n\n"
            f"{rats[0].name}\n\n"
            f"(XVO→VOB, IFO→BUP; dauert bei großen Dateien)",
        ):
            return
        self.status_var.set("ratDVD-Konvertierung läuft…")
        self.update_idletasks()

        def worker():
            try:
                proc = subprocess.run(
                    ["python3", str(RATDVD_TOOL), str(folder)],
                    cwd=str(SCRIPT_DIR),
                    capture_output=True,
                    text=True,
                )
                out = (proc.stdout or "") + (proc.stderr or "")
                self.after(0, lambda: self._ratdvd_done(proc.returncode, out, name))
            except Exception as exc:
                self.after(0, lambda: self._ratdvd_done(1, str(exc), name))

        threading.Thread(target=worker, daemon=True).start()

    def _ratdvd_done(self, rc, out, name):
        if rc != 0:
            messagebox.showerror("ratDVD fehlgeschlagen", out[-2000:] or f"exit {rc}")
            self.status_var.set("ratDVD fehlgeschlagen")
            return
        messagebox.showinfo("ratDVD", f"Fertig: {name}/VIDEO_TS\n\n" + (out[-800:] if out else ""))
        self.on_select()
        self.status_var.set(f"ratDVD → VIDEO_TS ok: {name}")

    def mark_dvd(self):
        name = self.selected_name()
        if not name:
            return
        idx = self.selected_index() or 0
        folder = FILME / name
        note = "dvd"
        if has_dvd_structure(folder):
            note = "VIDEO_TS/ISO erkannt"
        if "multi-movie" in self.reasons.get(name, "").lower():
            note = "multi-movie DVD set"
        if not messagebox.askyesno(
            "Als DVD ablegen",
            f"Nach dvd_manual.txt (kein Batch)?\n\n{name}\n({note})",
        ):
            return
        append_dvd_queue(name, note)
        self.remove_from_triage(name, select_next_from=idx)
        self.status_var.set(f"DVD abgelegt: {name}")

    def delete_folder(self):
        name = self.selected_name()
        if not name:
            return
        idx = self.selected_index() or 0
        folder = FILME / name
        if not folder.is_dir():
            if messagebox.askyesno("Nur Liste", f"Ordner fehlt. Nur aus Skip-Liste?\n\n{name}"):
                self.remove_from_triage(name, select_next_from=idx)
            return
        empty = is_effectively_empty(folder)
        warn = (
            f"Ordner LÖSCHEN?\n\n{folder}\n\n"
            + ("Sieht leer aus." if empty else "WARNUNG: Enthält noch Dateien!")
        )
        if not messagebox.askyesno("Ordner löschen?", warn):
            return
        try:
            shutil.rmtree(folder)
        except OSError as exc:
            messagebox.showerror("Fehler", f"Löschen fehlgeschlagen:\n{exc}")
            return
        self.remove_from_triage(name, select_next_from=idx)
        self.status_var.set(f"Gelöscht: {name}")

    def unskip(self):
        name = self.selected_name()
        if not name:
            return
        idx = self.selected_index() or 0
        if not messagebox.askyesno(
            "Unskip",
            f"Aus permanent_skipped nehmen und in Pending-Batch?\n\n{name}",
        ):
            return
        self.remove_from_triage(name, select_next_from=idx)
        if (FILME / name).is_dir():
            self.queue_for_batch(name)
        self.status_var.set(f"Unskip → pending {len(self.pending)}")

    def run_batch(self):
        if self.batch_running:
            messagebox.showinfo("Batch", "Läuft bereits.")
            return
        # Drop pending entries whose folders vanished
        alive = [n for n in self.pending if (FILME / n).is_dir()]
        missing = [n for n in self.pending if n not in alive]
        self.pending = alive
        save_pending(self.pending)
        self.update_batch_button()
        if missing:
            messagebox.showwarning("Pending", "Nicht mehr in Filme (übersprungen):\n" + "\n".join(missing[:12]))
        if not self.pending:
            messagebox.showinfo("Batch", "Pending ist leer.\nErst Jahr setzen oder Unskip.")
            return
        titles = list(self.pending)
        if not messagebox.askyesno(
            "Batch starten",
            f"Genau {len(titles)} Ordner verarbeiten?\n\n" + "\n".join(titles[:15])
            + ("\n…" if len(titles) > 15 else ""),
        ):
            return
        if not RENAMER.is_file():
            messagebox.showerror("Fehler", f"Fehlt: {RENAMER}")
            return
        if not MOVIES.is_dir():
            messagebox.showerror("Fehler", f"Movies fehlt: {MOVIES}")
            return

        win = BatchProgressWindow(self, titles)
        self.batch_running = True
        self.update_batch_button()

        def worker():
            ok = False
            summary = "Abbruch"
            try:
                WORK.mkdir(parents=True, exist_ok=True)
                batch_list = WORK / "gui_pending_list.txt"
                batch_list.write_text(
                    "# BASE_PATH=%s\n%s\n" % (FILME, "\n".join(titles)),
                    encoding="utf-8",
                )
                win.set_phase(f"Dry-run ({len(titles)})…", 0)
                win.log("=== DRY RUN ===")
                success = WORK / "gui_batch_success.txt"
                backlog = WORK / "gui_batch_backlog.txt"
                self._run_renamer(
                    ["--input", str(batch_list),
                     "--success-file", str(success),
                     "--backlog-file", str(backlog)],
                    win,
                )
                movable = parse_movable(success, backlog)
                win.log(f"Movable: {len(movable)}")
                if not movable:
                    summary = "Keine movable Successes"
                    win.done(False, summary)
                    return

                exec_list = WORK / "gui_pending_execute.txt"
                exec_list.write_text(
                    "# BASE_PATH=%s\n%s\n" % (
                        FILME,
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

                win.set_phase("Move nach Movies…", 0)
                win.log("=== MOVE ===")
                moved = 0
                for i, (_orig, new) in enumerate(movable, 1):
                    src = FILME / new
                    dst = MOVIES / new
                    if src.exists():
                        if dst.exists():
                            win.log(f"ALREADY in Movies — remove Filme leftover: {new}")
                            shutil.rmtree(src)
                        else:
                            shutil.move(str(src), str(MOVIES))
                            win.log(f"MOVED: {new}")
                            moved += 1
                    else:
                        win.log(f"MISSING: {src}")
                    win.set_phase(f"Move {i}/{len(movable)}", i)

                # Drop this batch's titles from pending (persisted for resume).
                self.pending = [n for n in load_pending() if n not in titles]
                save_pending(self.pending)
                ok = True
                summary = f"Fertig — {moved} verschoben, {len(movable)} execute"
            except Exception as exc:
                win.log(f"ERROR: {exc}")
                summary = f"Fehler: {exc}"
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
            text=True,
            bufsize=1,
        )
        assert proc.stdout is not None
        step = 0
        for line in proc.stdout:
            line = line.rstrip("\n")
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
        self.update_batch_button()
        self.status_var.set(f"Batch fertig · pending {len(self.pending)}")

    def next_item(self):
        sel = self.selected_index()
        if sel is None:
            return
        nxt = sel + 1
        if nxt >= self.listbox.size():
            self.status_var.set("Ende der Liste")
            return
        self.listbox.selection_clear(0, "end")
        self.listbox.selection_set(nxt)
        self.listbox.see(nxt)
        self.on_select()


def main():
    if not FILME.is_dir():
        raise SystemExit(f"Filme path missing: {FILME}")
    app = TriageApp()
    app.mainloop()


if __name__ == "__main__":
    main()
