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


if __name__ == "__main__":
    unittest.main()
