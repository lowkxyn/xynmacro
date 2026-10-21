import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import xynmacro_core as core


class AgilityNoLettersLogTests(unittest.TestCase):
    def setUp(self):
        core._agility_no_letters_since = None
        core._agility_no_letters_logged_at = 0.0
        core._agility_last_scan_at = 0.0

    def _scan(self, now, found=False):
        with mock.patch.object(core.time, "monotonic", return_value=now), \
                mock.patch.object(core, "_agility_best_letter_score", return_value=0.41), \
                mock.patch("builtins.print") as printed:
            core._note_agility_letter_scan(None, [], "Agility", found)
        return [call.args[0] for call in printed.call_args_list]

    def test_logs_once_after_five_seconds_of_empty_scans(self):
        logs = []
        for step in range(0, 61):  # 6 s of scans every 0.1 s
            logs += self._scan(1000.0 + step * 0.1)
        self.assertEqual(len(logs), 1)
        self.assertIn("no letters found for 5s", logs[0])
        self.assertIn("best match 0.41", logs[0])

    def test_found_letters_reset_the_timer(self):
        logs = []
        for step in range(0, 80):
            logs += self._scan(1000.0 + step * 0.1, found=(step == 40))
        self.assertEqual(logs, [])

    def test_gap_between_scans_does_not_count_as_missing_time(self):
        self._scan(1000.0)
        # Another stat ran for a minute; the first empty scan back must not log.
        self.assertEqual(self._scan(1060.0), [])
        self.assertEqual(self._scan(1060.1), [])


if __name__ == "__main__":
    unittest.main()
