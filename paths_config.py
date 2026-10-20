#!/usr/bin/env python3
"""Shared library paths: Filme (dirty source) → Movies (clean Jellyfin).

Priority: env > ~/.config/…/paths.json > ~/Filme ~/Movies ~/filme_batch_work.
"""
from __future__ import annotations

import json
import os
import shlex
import sys
from pathlib import Path

CONFIG_PATH = Path.home() / ".config" / "jellyfin-movie-renamer" / "paths.json"

DEFAULTS = {
    "filme": str(Path.home() / "Filme"),
    "movies": str(Path.home() / "Movies"),
    "work": str(Path.home() / "filme_batch_work"),
}


def load_config() -> dict:
    if not CONFIG_PATH.is_file():
        return dict(DEFAULTS)
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return dict(DEFAULTS)
    out = dict(DEFAULTS)
    for key in ("filme", "movies", "work"):
        val = data.get(key)
        if isinstance(val, str) and val.strip():
            out[key] = val.strip()
    return out


def save_config(filme: str | Path, movies: str | Path, work: str | Path) -> Path:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "filme": str(Path(filme).expanduser()),
        "movies": str(Path(movies).expanduser()),
        "work": str(Path(work).expanduser()),
    }
    CONFIG_PATH.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return CONFIG_PATH


def resolve() -> tuple[Path, Path, Path]:
    cfg = load_config()
    filme = Path(os.environ["FILME"]).expanduser() if os.environ.get("FILME") else Path(cfg["filme"]).expanduser()
    movies = (
        Path(os.environ["MOVIES"]).expanduser()
        if os.environ.get("MOVIES")
        else Path(cfg["movies"]).expanduser()
    )
    work = (
        Path(os.environ["WORK_DIR"]).expanduser()
        if os.environ.get("WORK_DIR")
        else Path(cfg["work"]).expanduser()
    )
    return filme, movies, work


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv in ([], ["--help"], ["-h"]):
        print(
            "Usage: paths_config.py --shell | --print\n"
            f"Config: {CONFIG_PATH}\n"
            "Priority: env FILME/MOVIES/WORK_DIR > config file > ~/Filme ~/Movies ~/filme_batch_work",
            file=sys.stderr if argv else sys.stdout,
        )
        return 0 if argv else 0
    filme, movies, work = resolve()
    if argv == ["--shell"]:
        print(f"FILME={shlex.quote(str(filme))}")
        print(f"MOVIES={shlex.quote(str(movies))}")
        print(f"WORK_DIR={shlex.quote(str(work))}")
        return 0
    if argv == ["--print"]:
        print(filme)
        print(movies)
        print(work)
        return 0
    print(f"Unknown args: {argv}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
