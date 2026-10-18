#!/usr/bin/env python3
"""Minimal GUI to triage permanent IMDb skips: preview, set year, mark DVD, unskip."""
import os
import re
import subprocess
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

FILME = Path(os.environ.get("FILME", "/mnt/nfs/huddle/media/Filme"))
WORK = Path(os.environ.get("WORK_DIR", Path.home() / "filme_batch_work"))
SKIP_ARCHIVE = Path(os.environ.get("SKIP_ARCHIVE", WORK / "permanent_skipped.txt"))
REASONS_FILE = Path(os.environ.get("REASONS_FILE", WORK / "skipped_reasons.txt"))
DVD_QUEUE = Path(os.environ.get("DVD_QUEUE", WORK / "dvd_manual.txt"))
VIDEO_EXT = {".mkv", ".mp4", ".avi", ".mov", ".m4v", ".wmv", ".mpg", ".mpeg", ".m2ts", ".ts"}


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


class TriageApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Filme skip triage")
        self.geometry("980x680")
        self.reasons = load_reasons()
        self.skips = load_skips()
        self.filtered = list(self.skips)
        self._build()
        self.refresh_list(select_index=0)

    def _build(self):
        top = ttk.Frame(self, padding=8)
        top.pack(fill="x")
        ttk.Label(top, text="Filter:").pack(side="left")
        self.filter_var = tk.StringVar()
        self.filter_var.trace_add("write", lambda *_: self.refresh_list())
        ttk.Entry(top, textvariable=self.filter_var, width=40).pack(side="left", padx=4)
        ttk.Label(top, text=f"Filme: {FILME}").pack(side="left", padx=12)
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
        ttk.Button(btns, text="Aus Skip-Liste nehmen", command=self.unskip).pack(side="left", padx=2)
        ttk.Button(btns, text="Nächster", command=self.next_item).pack(side="left", padx=2)

        ttk.Label(
            detail,
            text="„Als DVD ablegen“ → dvd_manual.txt, weg aus dieser Liste (manuell später).",
            wraplength=560,
        ).pack(anchor="w", pady=(2, 8))

        ttk.Label(detail, text="Videos / DVD-Dateien:").pack(anchor="w", pady=(4, 2))
        self.files = tk.Listbox(detail, height=12)
        self.files.pack(fill="both", expand=True)
        self.files.bind("<Double-Button-1>", lambda e: self.play_selected_file())

        ttk.Label(detail, textvariable=self.status_var).pack(anchor="w", pady=6)

    def list_tag(self, name):
        folder = FILME / name
        tags = []
        if has_dvd_structure(folder):
            tags.append("DVD")
        reason = self.reasons.get(name, "").lower()
        if "multi-movie" in reason:
            tags.append("DVD-SET")
        if not tags:
            return ""
        return " [" + ", ".join(tags) + "]"

    def refresh_list(self, select_index=None):
        needle = self.filter_var.get().strip().lower()
        self.filtered = [
            n for n in self.skips
            if not needle or needle in n.lower() or needle in self.reasons.get(n, "").lower()
        ]
        self.listbox.delete(0, "end")
        for name in self.filtered:
            self.listbox.insert("end", name + self.list_tag(name))
        self.count_var.set(f"{len(self.filtered)} / {len(self.skips)} skips")
        if not self.filtered:
            self.name_var.set("")
            self.reason_var.set("")
            self.path_var.set("")
            self.dvd_var.set("")
            self.files.delete(0, "end")
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
        """Drop from skip archive + reasons; refresh list and keep position."""
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
        if is_dvd and multi:
            self.dvd_var.set("DVD erkannt (VIDEO_TS/ISO) — laut Reason eher Multi-Movie-Set")
        elif is_dvd:
            self.dvd_var.set("DVD erkannt (VIDEO_TS/ISO) — Jahr setzen oder „Als DVD ablegen“")
        elif multi:
            self.dvd_var.set("Als Multi-Movie-DVD-Set gemeldet (Struktur ggf. prüfen)")
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
        self.status_var.set(f"Umbenannt → {new_name} — aus Liste entfernt (bereit für Batch)")

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
            f"Aus Skip-Triage nehmen und nach dvd_manual.txt?\n\n{name}\n({note})",
        ):
            return
        append_dvd_queue(name, note)
        self.remove_from_triage(name, select_next_from=idx)
        self.status_var.set(f"DVD abgelegt: {name} → {DVD_QUEUE.name}")

    def unskip(self):
        name = self.selected_name()
        if not name:
            return
        idx = self.selected_index() or 0
        if not messagebox.askyesno("Unskip", f"Aus permanent_skipped entfernen?\n\n{name}"):
            return
        self.remove_from_triage(name, select_next_from=idx)
        self.status_var.set(f"Entfernt aus Skip-Liste: {name}")

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
