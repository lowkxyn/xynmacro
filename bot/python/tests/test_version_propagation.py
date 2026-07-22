import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import xynmacro_core as core


class VersionPropagationTests(unittest.TestCase):
    def setUp(self):
        self.original_version = core.APP_VERSION

    def tearDown(self):
        core.APP_VERSION = self.original_version

    def test_tauri_version_is_used_in_state_snapshot(self):
        core.set_app_version("2.4.1")
        with (
            patch.object(core, "_ui_is_running", return_value=False),
            patch.object(core, "update_game_window"),
            patch.object(core, "_get_screen_info", return_value={"source": "unavailable"}),
        ):
            snapshot = core._ui_state_snapshot()

        self.assertEqual(snapshot["version"], "2.4.1")

    def test_missing_tauri_version_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "package version is required"):
            core.set_app_version("  ")

    def test_tauri_version_is_used_in_health(self):
        core.set_app_version("3.2.0")
        self.assertEqual(core._health_snapshot()["version"], "3.2.0")


if __name__ == "__main__":
    unittest.main()
