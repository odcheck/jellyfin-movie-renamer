#!/usr/bin/env python3
"""Shared library paths: Source (dirty) → Destination (clean Jellyfin).

Priority: env > ~/.config/…/paths.json > ~/Source ~/Destination ~/renamer_work.
"""
from __future__ import annotations

import json
import os
import shlex
import sys
from pathlib import Path

CONFIG_PATH = Path.home() / ".config" / "jellyfin-movie-renamer" / "paths.json"

DEFAULTS = {
    "source": str(Path.home() / "Source"),
    "destination": str(Path.home() / "Destination"),
    "work": str(Path.home() / "renamer_work"),
}

# Older config / env names still accepted when reading.
_LEGACY_KEYS = {"filme": "source", "movies": "destination"}
_ENV = {
    "source": ("SOURCE", "FILME"),
    "destination": ("DESTINATION", "MOVIES"),
    "work": ("WORK_DIR",),
}


def load_config() -> dict:
    out = dict(DEFAULTS)
    if not CONFIG_PATH.is_file():
        return out
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return out
    if not isinstance(data, dict):
        return out
    for old, new in _LEGACY_KEYS.items():
        if old in data and new not in data:
            data[new] = data[old]
    for key in ("source", "destination", "work"):
        val = data.get(key)
        if isinstance(val, str) and val.strip():
            out[key] = val.strip()
    return out


def save_config(source: str | Path, destination: str | Path, work: str | Path) -> Path:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "source": str(Path(source).expanduser()),
        "destination": str(Path(destination).expanduser()),
        "work": str(Path(work).expanduser()),
    }
    CONFIG_PATH.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return CONFIG_PATH


def _env_path(names: tuple[str, ...]) -> Path | None:
    for name in names:
        raw = os.environ.get(name)
        if raw:
            return Path(raw).expanduser()
    return None


def resolve() -> tuple[Path, Path, Path]:
    cfg = load_config()
    source = _env_path(_ENV["source"]) or Path(cfg["source"]).expanduser()
    destination = _env_path(_ENV["destination"]) or Path(cfg["destination"]).expanduser()
    work = _env_path(_ENV["work"]) or Path(cfg["work"]).expanduser()
    return source, destination, work


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv in (["--help"], ["-h"]):
        print(
            "Usage: paths_config.py --shell | --print\n"
            f"Config: {CONFIG_PATH}\n"
            "Priority: env SOURCE/DESTINATION/WORK_DIR > config > ~/Source ~/Destination ~/renamer_work\n"
            "Source = dirty inbox folder; Destination = cleaned Jellyfin library folder.",
        )
        return 0
    source, destination, work = resolve()
    if argv == ["--shell"]:
        print(f"SOURCE={shlex.quote(str(source))}")
        print(f"DESTINATION={shlex.quote(str(destination))}")
        print(f"WORK_DIR={shlex.quote(str(work))}")
        return 0
    if argv == ["--print"]:
        print(source)
        print(destination)
        print(work)
        return 0
    print(f"Unknown args: {argv}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
