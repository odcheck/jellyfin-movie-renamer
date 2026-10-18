#!/usr/bin/env python3
"""Minimal GUI to triage permanent IMDb skips: preview, set year, unskip."""
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
        self.geometry("980x640")
        self.reasons = load_reasons()
        self.skips = load_skips()
        self.filtered = list(self.skips)
        self._build()
        self.refresh_list()

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
        self.status_var = tk.StringVar()

        ttk.Label(detail, textvariable=self.name_var, font=("", 12, "bold")).pack(anchor="w")
        ttk.Label(detail, textvariable=self.reason_var, wraplength=560).pack(anchor="w", pady=(4, 8))
        ttk.Label(detail, textvariable=self.path_var, wraplength=560).pack(anchor="w")

        year_row = ttk.Frame(detail)
        year_row.pack(fill="x", pady=8)
        ttk.Label(year_row, text="Jahr:").pack(side="left")
        self.year_var = tk.StringVar()
        ttk.Entry(year_row, textvariable=self.year_var, width=8).pack(side="left", padx=4)
        ttk.Button(year_row, text="Ordner → Titel (Jahr)", command=self.apply_year).pack(side="left", padx=4)

        btns = ttk.Frame(detail)
        btns.pack(fill="x", pady=4)
        ttk.Button(btns, text="Ordner öffnen", command=self.open_folder).pack(side="left", padx=2)
        ttk.Button(btns, text="Video abspielen", command=self.play_video).pack(side="left", padx=2)
        ttk.Button(btns, text="Aus Skip-Liste nehmen", command=self.unskip).pack(side="left", padx=2)
        ttk.Button(btns, text="Nächster", command=self.next_item).pack(side="left", padx=2)

        ttk.Label(detail, text="Videos im Ordner:").pack(anchor="w", pady=(12, 2))
        self.files = tk.Listbox(detail, height=12)
        self.files.pack(fill="both", expand=True)
        self.files.bind("<Double-Button-1>", lambda e: self.play_selected_file())

        ttk.Label(detail, textvariable=self.status_var).pack(anchor="w", pady=6)

    def refresh_list(self):
        needle = self.filter_var.get().strip().lower()
        self.filtered = [
            n for n in self.skips
            if not needle or needle in n.lower() or needle in self.reasons.get(n, "").lower()
        ]
        self.listbox.delete(0, "end")
        for name in self.filtered:
            tag = ""
            if "multi-movie" in self.reasons.get(name, "").lower():
                tag = " [DVD-SET]"
            self.listbox.insert("end", name + tag)
        self.count_var.set(f"{len(self.filtered)} / {len(self.skips)} skips")
        if self.filtered:
            self.listbox.selection_set(0)
            self.on_select()

    def selected_name(self):
        sel = self.listbox.curselection()
        if not sel:
            return None
        return self.filtered[sel[0]]

    def on_select(self, _event=None):
        name = self.selected_name()
        if not name:
            return
        folder = FILME / name
        self.name_var.set(name)
        self.reason_var.set(self.reasons.get(name, "(kein Reason-Eintrag)"))
        self.path_var.set(str(folder))
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
        videos = find_videos(folder)
        for path in videos:
            try:
                rel = path.relative_to(folder)
            except ValueError:
                rel = path.name
            self.files.insert("end", str(rel))
        self.status_var.set(f"{len(videos)} Video(s)" if videos else "Keine Videos gefunden")

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
        videos = find_videos(FILME / name, limit=1)
        if not videos:
            messagebox.showinfo("Info", "Kein Video gefunden")
            return
        subprocess.Popen(["xdg-open", str(videos[0])])

    def play_selected_file(self):
        name = self.selected_name()
        sel = self.files.curselection()
        if not name or not sel:
            return
        rel = self.files.get(sel[0])
        path = FILME / name / rel
        if path.is_file():
            subprocess.Popen(["xdg-open", str(path)])

    def apply_year(self):
        name = self.selected_name()
        if not name:
            return
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
        folder.rename(dest)
        # Year fix → drop from skip list so the next batch can pick it up.
        self.skips = [n for n in self.skips if n != name]
        save_skips(self.skips)
        self.reasons.pop(name, None)
        self.status_var.set(f"Umbenannt → {new_name} (aus Skip-Liste entfernt)")
        self.refresh_list()

    def unskip(self):
        name = self.selected_name()
        if not name:
            return
        if not messagebox.askyesno("Unskip", f"Aus permanent_skipped entfernen?\n\n{name}"):
            return
        self.skips = [n for n in self.skips if n != name]
        save_skips(self.skips)
        self.status_var.set(f"Entfernt aus Skip-Liste: {name}")
        self.refresh_list()

    def next_item(self):
        sel = self.listbox.curselection()
        if not sel:
            return
        nxt = sel[0] + 1
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
