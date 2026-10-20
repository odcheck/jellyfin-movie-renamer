#!/usr/bin/env python3
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import paths_config


class PathsConfigTests(unittest.TestCase):
    def test_resolve_prefers_env_over_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Path(tmp) / "paths.json"
            cfg.write_text(json.dumps({
                "filme": "/from/config/Filme",
                "movies": "/from/config/Movies",
                "work": "/from/config/work",
            }))
            env = {
                "FILME": "/from/env/Filme",
                "MOVIES": "/from/env/Movies",
                "WORK_DIR": "/from/env/work",
            }
            with mock.patch.object(paths_config, "CONFIG_PATH", cfg), \
                    mock.patch.dict(os.environ, env, clear=False):
                for key in ("FILME", "MOVIES", "WORK_DIR"):
                    self.assertEqual(os.environ[key], env[key])
                filme, movies, work = paths_config.resolve()
            self.assertEqual(filme, Path("/from/env/Filme"))
            self.assertEqual(movies, Path("/from/env/Movies"))
            self.assertEqual(work, Path("/from/env/work"))

    def test_resolve_uses_config_when_env_absent(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Path(tmp) / "paths.json"
            cfg.write_text(json.dumps({
                "filme": "/cfg/Filme",
                "movies": "/cfg/Movies",
                "work": "/cfg/work",
            }))
            clean = {k: v for k, v in os.environ.items()
                     if k not in ("FILME", "MOVIES", "WORK_DIR")}
            with mock.patch.object(paths_config, "CONFIG_PATH", cfg), \
                    mock.patch.dict(os.environ, clean, clear=True):
                filme, movies, work = paths_config.resolve()
            self.assertEqual(filme, Path("/cfg/Filme"))
            self.assertEqual(movies, Path("/cfg/Movies"))
            self.assertEqual(work, Path("/cfg/work"))

    def test_save_config_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Path(tmp) / "paths.json"
            with mock.patch.object(paths_config, "CONFIG_PATH", cfg):
                paths_config.save_config("~/Filme", "/path/to/Movies", "/path/to/work")
                data = json.loads(cfg.read_text())
            self.assertEqual(data["filme"], str(Path("~/Filme").expanduser()))
            self.assertEqual(data["movies"], "/path/to/Movies")
            self.assertEqual(data["work"], "/path/to/work")


if __name__ == "__main__":
    unittest.main()
