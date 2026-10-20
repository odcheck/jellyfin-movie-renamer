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
                "source": "/from/config/Source",
                "destination": "/from/config/Destination",
                "work": "/from/config/work",
            }))
            env = {
                "SOURCE": "/from/env/Source",
                "DESTINATION": "/from/env/Destination",
                "WORK_DIR": "/from/env/work",
            }
            with mock.patch.object(paths_config, "CONFIG_PATH", cfg), \
                    mock.patch.dict(os.environ, env, clear=False):
                source, destination, work = paths_config.resolve()
            self.assertEqual(source, Path("/from/env/Source"))
            self.assertEqual(destination, Path("/from/env/Destination"))
            self.assertEqual(work, Path("/from/env/work"))

    def test_resolve_uses_config_when_env_absent(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Path(tmp) / "paths.json"
            cfg.write_text(json.dumps({
                "source": "/cfg/Source",
                "destination": "/cfg/Destination",
                "work": "/cfg/work",
            }))
            clean = {
                k: v for k, v in os.environ.items()
                if k not in ("SOURCE", "DESTINATION", "FILME", "MOVIES", "WORK_DIR")
            }
            with mock.patch.object(paths_config, "CONFIG_PATH", cfg), \
                    mock.patch.dict(os.environ, clean, clear=True):
                source, destination, work = paths_config.resolve()
            self.assertEqual(source, Path("/cfg/Source"))
            self.assertEqual(destination, Path("/cfg/Destination"))
            self.assertEqual(work, Path("/cfg/work"))

    def test_legacy_filme_movies_config_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Path(tmp) / "paths.json"
            cfg.write_text(json.dumps({
                "filme": "/legacy/Filme",
                "movies": "/legacy/Movies",
                "work": "/legacy/work",
            }))
            clean = {
                k: v for k, v in os.environ.items()
                if k not in ("SOURCE", "DESTINATION", "FILME", "MOVIES", "WORK_DIR")
            }
            with mock.patch.object(paths_config, "CONFIG_PATH", cfg), \
                    mock.patch.dict(os.environ, clean, clear=True):
                source, destination, work = paths_config.resolve()
            self.assertEqual(source, Path("/legacy/Filme"))
            self.assertEqual(destination, Path("/legacy/Movies"))
            self.assertEqual(work, Path("/legacy/work"))

    def test_save_config_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Path(tmp) / "paths.json"
            with mock.patch.object(paths_config, "CONFIG_PATH", cfg):
                paths_config.save_config("~/Source", "/path/to/Destination", "/path/to/work")
                data = json.loads(cfg.read_text())
            self.assertEqual(data["source"], str(Path("~/Source").expanduser()))
            self.assertEqual(data["destination"], "/path/to/Destination")
            self.assertEqual(data["work"], "/path/to/work")


if __name__ == "__main__":
    unittest.main()
