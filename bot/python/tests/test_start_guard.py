import sys
import unittest
from pathlib import Path
from unittest.mock import patch


PYTHON_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PYTHON_DIR))

import xmacro_core as core


class StartGuardTests(unittest.TestCase):
    def setUp(self):
        self.macro_thread = core.MACRO_THREAD
        self.game_hwnd = core.GAME_HWND
        core.MACRO_THREAD = None
        core.GAME_HWND = None

    def tearDown(self):
        core.MACRO_THREAD = self.macro_thread
        core.GAME_HWND = self.game_hwnd

    def test_start_refuses_when_roblox_is_not_found(self):
        with (
            patch.object(core, "update_game_window", return_value=False),
            patch.object(core.threading.Thread, "start") as start_thread,
        ):
            ok, message = core._ui_start_macro()

        self.assertFalse(ok)
        self.assertEqual(message, "Open Roblox before starting XynMacro.")
        start_thread.assert_not_called()

    def test_window_owner_must_be_a_roblox_executable(self):
        for executable in ("chrome.exe", "robloxstudiobeta.exe", "roblox-lookalike.exe"):
            with self.subTest(executable=executable):
                with patch.object(
                    core, "_window_process_executable", return_value=executable
                ):
                    self.assertFalse(core._is_supported_roblox_window(123))
        with patch.object(
            core, "_window_process_executable", return_value="robloxplayerbeta.exe"
        ):
            self.assertTrue(core._is_supported_roblox_window(123))

    def test_close_game_revalidates_window_owner_and_never_clicks(self):
        previous_hwnd = core.GAME_HWND
        core.GAME_HWND = 123
        try:
            with (
                patch.object(core, "update_game_window", return_value=True),
                patch.object(core, "_is_supported_roblox_window", return_value=False),
                patch.object(core, "_after_run_click_reference") as click,
                patch.object(core._user32, "PostMessageW") as post_message,
            ):
                self.assertFalse(core._after_run_close_game())
            post_message.assert_not_called()
            click.assert_not_called()
        finally:
            core.GAME_HWND = previous_hwnd

    def test_start_refuses_while_previous_monitor_is_still_stopping(self):
        class StuckThread:
            def is_alive(self):
                return True

        previous_event = core._background_monitor_stop
        previous_thread = core._background_monitor_thread
        try:
            core._background_monitor_stop = core.threading.Event()
            core._background_monitor_stop.set()
            core._background_monitor_thread = StuckThread()
            with (
                patch.object(core, "update_game_window", return_value=True),
                patch.object(core.threading.Thread, "start") as start_thread,
            ):
                core.GAME_HWND = 123
                ok, message = core._ui_start_macro()

            self.assertFalse(ok)
            self.assertIn("still stopping", message)
            start_thread.assert_not_called()
        finally:
            core._background_monitor_stop = previous_event
            core._background_monitor_thread = previous_thread


if __name__ == "__main__":
    unittest.main()
