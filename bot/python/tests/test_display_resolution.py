import sys
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import call, patch


PYTHON_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PYTHON_DIR))

import xmacro_core as core


def monitor(device, width, height, left=0, top=0):
    return {
        "device": device,
        "width": width,
        "height": height,
        "left": left,
        "top": top,
        "right": left + width,
        "bottom": top + height,
        "primary": left == 0 and top == 0,
    }


def mode(width, height, hz=60):
    return SimpleNamespace(
        PelsWidth=width,
        PelsHeight=height,
        DisplayFrequency=hz,
        Fields=0,
    )


class DisplayStateTests(unittest.TestCase):
    def test_screen_info_unavailable_then_found(self):
        display_a = monitor("DISPLAY_A", 2560, 1440)
        with (
            patch.object(core, "GAME_HWND", 10),
            patch.object(core, "_current_game_monitor_info", side_effect=[None, display_a]),
            patch.object(core.win32api, "EnumDisplaySettings", return_value=mode(2560, 1440)),
        ):
            unavailable = core._get_screen_info()
            found = core._get_screen_info()

        self.assertEqual(unavailable["source"], "unavailable")
        self.assertEqual(found["device"], "DISPLAY_A")
        self.assertEqual((found["width"], found["height"]), (2560, 1440))

    def test_screen_info_tracks_external_resolution_changes(self):
        display_a = monitor("DISPLAY_A", 2560, 1440)
        with (
            patch.object(core, "GAME_HWND", 10),
            patch.object(core, "_current_game_monitor_info", return_value=display_a),
            patch.object(
                core.win32api,
                "EnumDisplaySettings",
                side_effect=[mode(2560, 1440), mode(1920, 1080)],
            ),
        ):
            first = core._get_screen_info()
            second = core._get_screen_info()

        self.assertEqual((first["width"], first["height"]), (2560, 1440))
        self.assertEqual((second["width"], second["height"]), (1920, 1080))

    def test_screen_info_tracks_monitor_moves(self):
        display_a = monitor("DISPLAY_A", 1920, 1080)
        display_b = monitor("DISPLAY_B", 2560, 1440, left=-2560)
        with (
            patch.object(core, "GAME_HWND", 10),
            patch.object(core, "_current_game_monitor_info", side_effect=[display_a, display_b]),
            patch.object(
                core.win32api,
                "EnumDisplaySettings",
                side_effect=[mode(1920, 1080), mode(2560, 1440)],
            ),
        ):
            first = core._get_screen_info()
            second = core._get_screen_info()

        self.assertEqual(first["device"], "DISPLAY_A")
        self.assertEqual(second["device"], "DISPLAY_B")
        self.assertEqual(second["left"], -2560)


class DisplaySetRevertTests(unittest.TestCase):
    def setUp(self):
        self._temp_dir = tempfile.TemporaryDirectory()
        self._data_dir_patch = patch.object(core, "DATA_DIR", self._temp_dir.name)
        self._data_dir_patch.start()

    def tearDown(self):
        self._data_dir_patch.stop()
        self._temp_dir.cleanup()

    def test_set_on_a_move_to_b_revert_still_restores_a(self):
        display_a = monitor("DISPLAY_A", 2560, 1440)
        display_b = monitor("DISPLAY_B", 1920, 1080)
        original_a = mode(2560, 1440, 144)
        target_a = mode(2560, 1440, 144)
        current_monitor = {"value": None}

        def refresh_game_window():
            current_monitor["value"] = (
                display_a if current_monitor["value"] is None else display_b
            )

        with (
            patch.object(core, "GAME_HWND", 10),
            patch.object(core, "_DISPLAY_RESTORE", None),
            patch.object(core, "update_game_window", side_effect=refresh_game_window),
            patch.object(
                core,
                "_current_game_monitor_info",
                side_effect=lambda: current_monitor["value"],
            ) as game_monitor,
            patch.object(core.win32api, "EnumDisplaySettings", side_effect=[original_a, target_a]),
            patch.object(
                core.win32api,
                "ChangeDisplaySettingsEx",
                return_value=core.win32con.DISP_CHANGE_SUCCESSFUL,
            ) as change,
        ):
            set_ok, _ = core.display_set_resolution(1920, 1080)
            revert_ok, _ = core.display_revert_resolution()
            restore_after_revert = core._DISPLAY_RESTORE

        self.assertTrue(set_ok)
        self.assertTrue(revert_ok)
        self.assertIsNone(restore_after_revert)
        self.assertIs(current_monitor["value"], display_b)
        game_monitor.assert_called_once()
        self.assertEqual((target_a.PelsWidth, target_a.PelsHeight), (1920, 1080))
        self.assertEqual(
            change.call_args_list,
            [call("DISPLAY_A", target_a, 0), call("DISPLAY_A", original_a, 0)],
        )

    def test_failed_revert_keeps_original_mode_for_retry(self):
        restore = {"device": "DISPLAY_A", "mode": mode(2560, 1440)}
        with (
            patch.object(core, "_DISPLAY_RESTORE", restore),
            patch.object(core, "update_game_window"),
            patch.object(core.win32api, "ChangeDisplaySettingsEx", return_value=-1),
        ):
            ok, _ = core.display_revert_resolution()
            self.assertFalse(ok)
            self.assertIs(core._DISPLAY_RESTORE, restore)

    def test_repeated_set_on_same_display_keeps_first_original_mode(self):
        display_a = monitor("DISPLAY_A", 2560, 1440)
        first_original = mode(2560, 1440, 144)
        modes = [first_original, mode(2560, 1440), mode(1920, 1080), mode(1920, 1080)]
        with (
            patch.object(core, "GAME_HWND", 10),
            patch.object(core, "_DISPLAY_RESTORE", None),
            patch.object(core, "update_game_window"),
            patch.object(core, "_current_game_monitor_info", return_value=display_a),
            patch.object(core.win32api, "EnumDisplaySettings", side_effect=modes),
            patch.object(
                core.win32api,
                "ChangeDisplaySettingsEx",
                return_value=core.win32con.DISP_CHANGE_SUCCESSFUL,
            ),
        ):
            core.display_set_resolution(1920, 1080)
            core.display_set_resolution(1600, 900)
            self.assertIs(core._DISPLAY_RESTORE["mode"], first_original)

    def test_set_on_second_display_is_rejected_until_first_is_reverted(self):
        display_b = monitor("DISPLAY_B", 1920, 1080)
        restore = {"device": "DISPLAY_A", "mode": mode(2560, 1440)}
        with (
            patch.object(core, "GAME_HWND", 10),
            patch.object(core, "_DISPLAY_RESTORE", restore),
            patch.object(core, "update_game_window"),
            patch.object(core, "_current_game_monitor_info", return_value=display_b),
            patch.object(core.win32api, "ChangeDisplaySettingsEx") as change,
        ):
            ok, message = core.display_set_resolution(1920, 1080)

        self.assertFalse(ok)
        self.assertIn("DISPLAY_A", message)
        change.assert_not_called()

    def test_saved_original_display_survives_sidecar_restart(self):
        original = mode(2560, 1440, 144)
        current = mode(1920, 1080, 60)
        with patch.object(core, "_DISPLAY_RESTORE", None):
            core._persist_display_restore({"device": "DISPLAY_A", "mode": original})
            with patch.object(
                core.win32api, "EnumDisplaySettings", return_value=current
            ):
                restored = core._load_display_restore()

        self.assertEqual(restored["device"], "DISPLAY_A")
        self.assertEqual(
            (restored["mode"].PelsWidth, restored["mode"].PelsHeight),
            (2560, 1440),
        )
        self.assertEqual(restored["mode"].DisplayFrequency, 144)

    def test_successful_fallback_revert_clears_malformed_restore_file(self):
        restore_path = Path(self._temp_dir.name) / "display_restore.json"
        restore_path.write_text("{broken", encoding="utf-8")
        display_a = monitor("DISPLAY_A", 1920, 1080)

        with (
            patch.object(core, "GAME_HWND", 10),
            patch.object(core, "_DISPLAY_RESTORE", None),
            patch.object(core, "update_game_window"),
            patch.object(core, "_current_game_monitor_info", return_value=display_a),
            patch.object(
                core.win32api,
                "ChangeDisplaySettingsEx",
                return_value=core.win32con.DISP_CHANGE_SUCCESSFUL,
            ) as change,
        ):
            ok, _ = core.display_revert_resolution()

        self.assertTrue(ok)
        self.assertFalse(restore_path.exists())
        change.assert_called_once_with("DISPLAY_A", None, 0)

    def test_successful_fallback_revert_clears_disconnected_display_restore(self):
        restore_path = Path(self._temp_dir.name) / "display_restore.json"
        restore_path.write_text(
            json.dumps({
                "device": "DISCONNECTED",
                "width": 2560,
                "height": 1440,
                "hz": 144,
            }),
            encoding="utf-8",
        )
        display_a = monitor("DISPLAY_A", 1920, 1080)

        with (
            patch.object(core, "GAME_HWND", 10),
            patch.object(core, "_DISPLAY_RESTORE", None),
            patch.object(core, "update_game_window"),
            patch.object(core, "_current_game_monitor_info", return_value=display_a),
            patch.object(core.win32api, "EnumDisplaySettings", side_effect=OSError("gone")),
            patch.object(
                core.win32api,
                "ChangeDisplaySettingsEx",
                return_value=core.win32con.DISP_CHANGE_SUCCESSFUL,
            ),
        ):
            ok, _ = core.display_revert_resolution()

        self.assertTrue(ok)
        self.assertFalse(restore_path.exists())


if __name__ == "__main__":
    unittest.main()
