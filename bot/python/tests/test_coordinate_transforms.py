import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np


PYTHON_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PYTHON_DIR))

import xmacro_core as core


class FakeCapture:
    def __init__(self, pixel=(0, 255, 255, 255)):
        self.pixel = np.array(pixel, dtype=np.uint8)
        self.grabs = []

    def grab(self, box):
        captured = {key: int(value) for key, value in box.items()}
        self.grabs.append(captured)
        return np.tile(self.pixel, (captured["height"], captured["width"], 1))


class FakeUser32:
    def __init__(self):
        self.cursor_positions = []
        self.sent_inputs = []

    def SetCursorPos(self, x, y):
        self.cursor_positions.append((x, y))
        return 1

    def SendInput(self, count, inputs, size):
        self.sent_inputs.append(
            [(inputs[index].mi.dwFlags, inputs[index].mi.dx, inputs[index].mi.dy)
             for index in range(count)]
        )
        return count


class CoordinateTransformTests(unittest.TestCase):
    def test_reference_point_on_negative_origin_secondary_monitor(self):
        geometry = {"left": -1920, "top": 0, "width": 1920, "height": 1080}
        self.assertEqual(core._reference_point(327, 425, geometry), (-1593, 425))
        self.assertEqual(core._reference_point(1920, 1080, geometry), (0, 1080))

    def test_reference_point_scales_for_different_client_resolution(self):
        geometry = {"left": -1280, "top": 100, "width": 1280, "height": 720}
        self.assertEqual(core._reference_point(960, 540, geometry), (-640, 460))
        self.assertEqual(core._reference_point(1920, 1080, geometry), (0, 820))

    def test_reference_box_scales_and_keeps_negative_origin(self):
        geometry = {"left": -1280, "top": -720, "width": 1280, "height": 720}
        box = {"left": 960, "top": 540, "width": 300, "height": 150}
        self.assertEqual(
            core._reference_box(box, geometry),
            {"left": -640, "top": -360, "width": 200, "height": 100},
        )

    def test_screen_calibration_round_trips_through_reference_space(self):
        geometry = {"left": -1600, "top": 80, "width": 1600, "height": 900}
        screen_box = {"left": -1200, "top": 305, "width": 800, "height": 450}
        reference_box = core._screen_box_to_reference(screen_box, geometry)
        self.assertEqual(
            reference_box,
            {"left": 480, "top": 270, "width": 960, "height": 540},
        )
        self.assertEqual(core._reference_box(reference_box, geometry), screen_box)

    def test_reference_grab_uses_live_box_then_normalizes(self):
        capture = FakeCapture()
        geometry = {"left": -1280, "top": 50, "width": 1280, "height": 720}
        frame = core._grab_reference_box(
            capture,
            {"left": 300, "top": 150, "width": 150, "height": 90},
            geometry,
        )
        self.assertEqual(
            capture.grabs,
            [{"left": -1080, "top": 150, "width": 100, "height": 60}],
        )
        self.assertEqual(frame.shape, (90, 150, 3))

    def test_apply_game_offset_cannot_double_offset_reference_globals(self):
        before = dict(core.BUTTONS)
        core.apply_game_offset()
        core.apply_game_offset()
        self.assertEqual(core.BUTTONS, before)
        first = core._button_screen_point(
            "Health", {"left": -1920, "top": 0, "width": 1920, "height": 1080}
        )
        moved = core._button_screen_point(
            "Health", {"left": 100, "top": 200, "width": 1280, "height": 720}
        )
        self.assertEqual(first, (-1593, 425))
        self.assertEqual(moved, (318, 483))
        self.assertEqual(core.BUTTONS, before)

    def test_missing_roblox_never_falls_back_to_primary_monitor(self):
        with (
            patch.object(core, "update_game_window", return_value=False),
            patch.object(core, "GAME_HWND", None),
        ):
            with self.assertRaisesRegex(RuntimeError, "Roblox client window was not found"):
                core._confirmed_game_capture_rect()

    def test_virtual_desktop_coordinates_include_negative_monitor_origin(self):
        bounds = (-1920, 0, 4480, 1600)
        self.assertEqual(core._absolute_virtual_coords(-1920, 0, bounds), (0, 0))
        self.assertEqual(core._absolute_virtual_coords(2559, 1599, bounds), (65535, 65535))
        x, y = core._absolute_virtual_coords(-1531, 751, bounds)
        self.assertGreater(x, 0)
        self.assertLess(x, 65535)
        self.assertGreater(y, 0)
        self.assertLess(y, 65535)

    def test_absolute_click_uses_coordinates_only_for_move_packets(self):
        fake_user32 = FakeUser32()
        with (
            patch.object(core, "_user32", fake_user32),
            patch.object(core, "_absolute_virtual_coords", return_value=(1234, 5678)),
            patch.object(core.time, "sleep"),
        ):
            core._click_sendinput_abs(-1531, 751)

        self.assertEqual(fake_user32.cursor_positions, [(-1531, 751)])
        self.assertEqual(len(fake_user32.sent_inputs), 3)
        absolute_flags = core._MOUSEEVENTF_ABSOLUTE | core._MOUSEEVENTF_VIRTUALDESK
        self.assertEqual(
            fake_user32.sent_inputs[:2],
            [[(core._MOUSEEVENTF_MOVE | absolute_flags, 1234, 5678)]] * 2,
        )
        self.assertEqual(
            fake_user32.sent_inputs[2],
            [
                (core._MOUSEEVENTF_LEFTDOWN, 0, 0),
                (core._MOUSEEVENTF_LEFTUP, 0, 0),
            ],
        )


if __name__ == "__main__":
    unittest.main()
