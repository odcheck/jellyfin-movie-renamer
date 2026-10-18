#!/usr/bin/env python3
"""Back-compat wrapper — use media_to_mp4.py (divx/DVD/ratDVD → mp4)."""
import subprocess
import sys
from pathlib import Path

tool = Path(__file__).resolve().parent / "media_to_mp4.py"
sys.exit(subprocess.call([sys.executable, str(tool), *sys.argv[1:]]))
