#!/usr/bin/env python3
import importlib.util
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock


def load_mod():
    path = Path(__file__).resolve().parents[1] / "media_to_mp4.py"
    spec = importlib.util.spec_from_file_location("media_to_mp4", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class MediaToMp4Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = load_mod()

    def test_parse_handbrake_scan_picks_longest_german(self):
        text = """
+ title 1:
  + duration: 00:01:20
  + audio tracks:
    + 1, English (AC3) (iso639-2: eng)
+ title 2:
  + duration: 01:32:31
  + audio tracks:
    + 1, English (AC3) (iso639-2: eng)
    + 2, Deutsch (AC3) (iso639-2: deu)
  + subtitle tracks:
"""
        titles = self.mod.parse_handbrake_scan(text)
        self.assertEqual(len(titles), 2)
        title, audio = self.mod.pick_handbrake_title_audio(titles)
        self.assertEqual(title, 2)
        self.assertEqual(audio, 2)

    def test_handbrake_cmd_prefers_flatpak_shape(self):
        cmd = self.mod.handbrake_cmd()
        if cmd is None:
            self.skipTest("HandBrake not installed")
        self.assertIn("HandBrakeCLI", " ".join(cmd) if cmd[0] == "flatpak" else cmd[0])

    def test_flat_dvd_find_and_safe_cleanup(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder / "VIDEO_TS.IFO").write_bytes(b"x")
            (folder / "VTS_01_1.VOB").write_bytes(b"y")
            (folder / "keep.nfo").write_text("nfo")
            self.assertTrue(self.mod.is_flat_dvd_dir(folder))
            self.assertEqual(self.mod.find_video_ts(folder), folder)
            dest = folder / f"{folder.name}.mp4"
            dest.write_bytes(b"mp4")
            self.mod.remove_dvd_source(folder, dest)
            self.assertTrue(folder.is_dir())
            self.assertTrue((folder / "keep.nfo").exists())
            self.assertTrue(dest.exists())
            self.assertFalse((folder / "VTS_01_1.VOB").exists())

    def test_empty_video_ts_stub_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            stub = folder / "VIDEO_TS"
            stub.mkdir()
            (stub / ".@__thumb").mkdir()
            self.assertIsNone(self.mod.find_video_ts(folder))
            real = folder / "real" / "VIDEO_TS"
            real.mkdir(parents=True)
            (real / "VIDEO_TS.IFO").write_bytes(b"x")
            (real / "VTS_01_1.VOB").write_bytes(b"y")
            self.assertEqual(self.mod.find_video_ts(folder / "real"), real)

    def test_ratdvd_xc_detected(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder / "Version.xml").write_text(
                "<versioninfo><videoformat>XC</videoformat></versioninfo>"
            )
            video_ts = folder / "VIDEO_TS"
            video_ts.mkdir()
            (video_ts / "VIDEO_TS.IFO").write_bytes(b"x")
            (video_ts / "VTS_01_1.VOB").write_bytes(b"y")
            self.assertTrue(self.mod.is_ratdvd_xc(folder))
            dest = folder / "out.mp4"
            with self.assertRaises(RuntimeError) as ctx:
                self.mod.convert_video_ts(video_ts, dest, dry_run=False, keep_source=True)
            self.assertIn("ratDVD XC", str(ctx.exception))

    def test_ordered_m2ts_from_mpls(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            stream = folder / "STREAM"
            stream.mkdir()
            playlist = folder / "PLAYLIST"
            playlist.mkdir()
            for name in ("00002.m2ts", "00000.m2ts", "00001.m2ts"):
                (stream / name).write_bytes(b"x")
            # Minimal MPLS body with clip ids in order 00000,00001,00002
            (playlist / "00000.mpls").write_bytes(
                b"MPLS0200xxxx00000M2TSyyyy00001M2TSzzzz00002M2TS"
            )
            ordered = self.mod.ordered_m2ts(stream)
            self.assertEqual([p.name for p in ordered], ["00000.m2ts", "00001.m2ts", "00002.m2ts"])
            self.assertEqual(self.mod.find_bluray_stream(folder), stream)

    def test_handbrake_scan_tolerates_non_utf8(self):
        """Regression: Geheimnis ISO — HB dump had 0xff at pos 758, text=True crashed."""
        bad = (
            b"x" * 758
            + b"\xff"
            + b"\n+ title 1:\n  + duration: 01:00:00\n"
            + b"  + audio tracks:\n    + 1, Deutsch (AC3) (iso639-2: deu)\n"
        )

        def fake_run(cmd, **kwargs):
            if kwargs.get("capture_output"):
                enc = kwargs.get("encoding") or "utf-8"
                err = kwargs.get("errors") or "strict"
                text = bad.decode(enc, errors=err)
                return subprocess.CompletedProcess(cmd, 0, stdout=text, stderr="")
            return subprocess.CompletedProcess(cmd, 0)

        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "x.iso"
            dest = Path(tmp) / "out.mp4"
            src.write_bytes(b"iso")
            with mock.patch.object(self.mod, "handbrake_cmd", return_value=["HandBrakeCLI"]):
                with mock.patch.object(self.mod, "run", side_effect=fake_run):
                    ok = self.mod.convert_with_handbrake(src, dest, dry_run=False)
            self.assertTrue(ok)

    def test_iso_falls_back_when_handbrake_returns_false(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            iso = folder / "movie.iso"
            dest = folder / "movie.mp4"
            iso.write_bytes(b"iso")
            calls = []

            def fake_extract(*_a, **_k):
                calls.append("7z")
                dest.write_bytes(b"mp4")

            with mock.patch.object(self.mod, "convert_with_handbrake", return_value=False):
                with mock.patch.object(self.mod.ratdvd, "seven_zip_bin", return_value="7z"):
                    with mock.patch.object(self.mod, "find_video_ts", return_value=folder / "VIDEO_TS"):
                        with mock.patch.object(
                            self.mod, "convert_video_ts", side_effect=fake_extract
                        ):
                            with mock.patch("subprocess.run") as sp:
                                sp.return_value = subprocess.CompletedProcess(
                                    ["7z"], 0, "", ""
                                )
                                self.mod.convert_iso(
                                    iso, dest, dry_run=False, keep_source=True
                                )
            self.assertEqual(calls, ["7z"])
            self.assertTrue(dest.exists())


if __name__ == "__main__":
    unittest.main()
