#!/usr/bin/env python3
import importlib.util
import tempfile
import unittest
from pathlib import Path


def load_gui():
    path = Path(__file__).resolve().parents[1] / "skip_triage_gui.py"
    spec = importlib.util.spec_from_file_location("skip_triage_gui", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class LooseMediaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = load_gui()

    def test_wrap_moves_file_and_sidecars(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            avi = base / "Platoon.avi"
            avi.write_bytes(b"video")
            (base / "Platoon.nfo").write_text("nfo")
            (base / "Platoon.avi.metathumb").write_bytes(b"thumb")
            (base / "other.txt").write_text("keep")

            folder_name = self.mod.wrap_loose_media("Platoon.avi", filme=base)
            self.assertEqual(folder_name, "Platoon")
            dest = base / "Platoon"
            self.assertTrue((dest / "Platoon.avi").is_file())
            self.assertTrue((dest / "Platoon.nfo").is_file())
            self.assertTrue((dest / "Platoon.avi.metathumb").is_file())
            self.assertFalse(avi.exists())
            self.assertTrue((base / "other.txt").is_file())

    def test_wrap_noop_for_folder_name(self):
        self.assertEqual(self.mod.wrap_loose_media("Already A Folder"), "Already A Folder")

    def test_scan_includes_loose_media(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / "Movie Dir").mkdir()
            (base / "Loose.avi").write_bytes(b"x")
            (base / "Loose.nfo").write_text("nfo")
            (base / "skip.jpg").write_bytes(b"j")
            old = self.mod.FILME
            try:
                self.mod.FILME = base
                names = self.mod.scan_filme_folders()
            finally:
                self.mod.FILME = old
            self.assertIn("Movie Dir", names)
            self.assertIn("Loose.avi", names)
            self.assertNotIn("Loose.nfo", names)
            self.assertNotIn("skip.jpg", names)

    def test_peek_loose_ready(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "X.mkv"
            path.write_bytes(b"x")
            peek = self.mod.peek_folder_media(path)
            self.assertTrue(peek["ready"])
            self.assertFalse(peek["missing"])


if __name__ == "__main__":
    unittest.main()
