import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


PYTHON_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PYTHON_DIR))

import xynmacro_core as core


class SessionLogTests(unittest.TestCase):
    def test_opening_new_session_caps_total_logs_at_25(self):
        with tempfile.TemporaryDirectory() as temp:
            json_dir = Path(temp) / "json"
            logs_dir = json_dir / "logs"
            logs_dir.mkdir(parents=True)
            for index in range(30):
                path = logs_dir / f"session_old_{index:02}.log"
                path.write_text(str(index), encoding="utf-8")
                os.utime(path, (index + 1, index + 1))

            with patch.object(core, "JSON_DIR", str(json_dir)):
                handle = core._open_session_log_file()
                self.assertIsNotNone(handle)
                handle.close()

            logs = list(logs_dir.glob("session_*"))
            self.assertEqual(len(logs), 25)
            self.assertFalse((logs_dir / "session_old_00.log").exists())
            self.assertTrue((logs_dir / "session_old_29.log").exists())

    def test_session_file_lines_are_redacted_as_they_are_written(self):
        # Open logs folder hands out these files directly, unlike Copy and Save Log.
        import collections
        import io

        session_file = io.StringIO()
        ring = collections.deque()
        tee = core._TeeBuffer(None, ring, file_handle=session_file)
        with patch.dict(os.environ, {"USERPROFILE": r"C:\Users\somebody", "USERNAME": "somebody"}):
            tee.write(r"[sidecar] Data folder: C:\Users\somebody\AppData\Roaming\xyn" + "\n")
        written = session_file.getvalue()
        self.assertNotIn("somebody", written)
        self.assertIn(r"%USERPROFILE%\AppData\Roaming\xyn", written)
        self.assertTrue(written.endswith("\n"))
        self.assertIn("somebody", ring[0]["msg"])   # the in-app viewer keeps the raw line

    def test_logs_from_older_builds_are_redacted_once(self):
        # Builds before 2.0.6 wrote these files unredacted.
        with tempfile.TemporaryDirectory() as logs_dir:
            logs = Path(logs_dir)
            old = logs / "session_2026-10-01_09-00-00_pid111.log"
            old.write_text(r"[sidecar] Data folder: C:\Users\somebody\AppData\Roaming\xyn" + "\n",
                           encoding="utf-8")
            os.utime(old, (1_700_000_000, 1_700_000_000))
            live = logs / "session_2026-11-16_09-00-00_pid222.log"
            live.write_text(r"C:\Users\somebody\still-open" + "\n", encoding="utf-8")
            other = logs / "notes.txt"
            other.write_text(r"C:\Users\somebody\not-a-session-log" + "\n", encoding="utf-8")
            with patch.dict(os.environ, {"USERPROFILE": r"C:\Users\somebody", "USERNAME": "somebody"}), \
                    patch.object(core, "_pid_alive", side_effect=lambda pid: pid == 222):
                core._scrub_older_session_logs(logs_dir)
                self.assertNotIn("somebody", old.read_text(encoding="utf-8"))
                self.assertEqual(int(old.stat().st_mtime), 1_700_000_000)   # rotation order kept
                self.assertIn("somebody", live.read_text(encoding="utf-8"))   # its sidecar is running
                self.assertIn("somebody", other.read_text(encoding="utf-8"))
                # Only once: a log written unredacted after the marker is not touched again.
                old.write_text(r"C:\Users\somebody\again" + "\n", encoding="utf-8")
                core._scrub_older_session_logs(logs_dir)
                self.assertIn("somebody", old.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
