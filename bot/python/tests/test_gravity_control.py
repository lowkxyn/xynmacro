import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np


PYTHON_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PYTHON_DIR))

import xynmacro_core as core


class GravityControlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Class setup runs outside the per-test layout fixture.
        with patch.object(core, "GAME_UI_LAYOUT", core.LEGACY_UI_LAYOUT):
            cls.templates = core._load_gravity_templates()
        cls.geometry = {"left": 0, "top": 0, "width": 1920, "height": 1080}

    def test_every_captured_gravity_label_reads_exactly(self):
        self.assertEqual(set(self.templates), set(range(0, 101, 10)))
        for expected in range(0, 101, 10):
            with self.subTest(expected=expected):
                mask = cv2.imread(
                    str(PYTHON_DIR / "gravity" / f"gravity_{expected}.png"),
                    cv2.IMREAD_GRAYSCALE,
                )
                value, score, second = core._gravity_value_from_mask(
                    mask, self.templates
                )
                self.assertEqual(value, expected)
                self.assertGreaterEqual(score, 0.99)
                self.assertGreater(score, second)

    def test_live_forty_with_touching_digits_reads_exactly(self):
        # Lossless live capture: the 4's crossbar touches the 0, so "40" is one
        # 23 px component that the old 22 px glyph cap rejected as "no control".
        frame = cv2.imread(str(PYTHON_DIR / "tests" / "fixtures" / "gravity_40_live.png"))
        mask = core._gravity_mask_from_frame(frame)
        self.assertGreater(core._gravity_control_glyph_count(mask), 0)
        value, score, second = core._gravity_value_from_mask(mask, self.templates)
        self.assertEqual(value, 40)
        self.assertGreater(score - second, 0.1)

    def test_blank_htc_header_is_not_a_gravity_control(self):
        blank = np.zeros((51, 142), dtype=np.uint8)
        value, score, second = core._gravity_value_from_mask(blank, self.templates)
        self.assertIsNone(value)
        self.assertEqual((score, second), (0.0, 0.0))

    def test_live_quality_match_is_accepted_only_with_a_clear_margin(self):
        self.assertTrue(core._gravity_match_is_reliable(0.850, 0.356))
        self.assertFalse(core._gravity_match_is_reliable(0.799, 0.200))
        self.assertFalse(core._gravity_match_is_reliable(0.850, 0.820))

    def test_delayed_redraw_can_confirm_safe_forward_progress(self):
        self.assertTrue(core._gravity_advance_is_safe(0, 20, 100))
        self.assertTrue(core._gravity_advance_is_safe(70, 100, 100))
        self.assertFalse(core._gravity_advance_is_safe(40, 60, 50))
        self.assertFalse(core._gravity_advance_is_safe(100, 0, 100))
        self.assertFalse(core._gravity_advance_is_safe(40, 30, 100))

    def test_only_real_ten_g_steps_are_accepted(self):
        self.assertEqual(core._normalize_gravity_target(0, strict=True), 0)
        self.assertEqual(core._normalize_gravity_target(100, strict=True), 100)
        with self.assertRaisesRegex(ValueError, "0, 10, 20"):
            core._normalize_gravity_target(37, strict=True)
        self.assertEqual(core._normalize_gravity_target(37), 0)

    def test_off_sends_no_input(self):
        with (
            patch.object(core, "_load_gravity_templates") as load_templates,
            patch.object(core, "click_at") as click,
        ):
            self.assertTrue(core._raise_gc_gravity(object(), {}, 0))
        load_templates.assert_not_called()
        click.assert_not_called()

    def test_htc_sends_no_gravity_clicks(self):
        with (
            patch.object(core, "_load_gravity_templates", return_value=self.templates),
            patch.object(core, "robust_move"),
            patch.object(core, "safe_sleep"),
            patch.object(
                core,
                "_read_gc_gravity",
                return_value=(None, 0.0, 0.0, False),
            ),
            patch.object(core, "click_at") as click,
        ):
            self.assertTrue(core._raise_gc_gravity(object(), self.geometry, 100))
        click.assert_not_called()

    def test_target_never_lowers_or_wraps_current_value(self):
        with (
            patch.object(core, "_load_gravity_templates", return_value=self.templates),
            patch.object(core, "robust_move"),
            patch.object(core, "safe_sleep"),
            patch.object(
                core,
                "_read_gc_gravity",
                return_value=(100, 1.0, 0.4, True),
            ),
            patch.object(core, "click_at") as click,
        ):
            self.assertTrue(core._raise_gc_gravity(object(), self.geometry, 50))
        click.assert_not_called()

    def test_raise_confirms_each_ten_g_step(self):
        reads = [
            (70, 1.0, 0.8, True),
            (80, 1.0, 0.8, True),
            (90, 1.0, 0.8, True),
            (100, 1.0, 0.4, True),
        ]
        with (
            patch.object(core, "_load_gravity_templates", return_value=self.templates),
            patch.object(core, "robust_move"),
            patch.object(core, "safe_sleep"),
            patch.object(core, "check_exit"),
            patch.object(core, "_read_gc_gravity", side_effect=reads),
            patch.object(core, "click_at") as click,
        ):
            self.assertTrue(core._raise_gc_gravity(object(), self.geometry, 100))
        self.assertEqual(click.call_count, 3)

    def test_every_pointer_park_nudges_so_roblox_redraws_its_cursor(self):
        # Live capture: Roblox's own cursor left over the label after an absolute-only
        # park hid the digits, so the read failed as "GC control disappeared".
        reads = [(0, 1.0, 0.4, True), (10, 1.0, 0.8, True), (20, 1.0, 0.8, True)]
        with (
            patch.object(core, "_load_gravity_templates", return_value=self.templates),
            patch.object(core, "robust_move") as move,
            patch.object(core, "safe_sleep"),
            patch.object(core, "check_exit"),
            patch.object(core, "_read_gc_gravity", side_effect=reads),
            patch.object(core, "click_at"),
        ):
            self.assertTrue(core._raise_gc_gravity(object(), self.geometry, 20))
        self.assertEqual(move.call_count, 3)
        for call in move.call_args_list:
            self.assertTrue(call.kwargs.get("nudge"))

        reads = [(90, 1.0, 0.8, True), (100, 1.0, 0.4, True), (0, 1.0, 0.4, True)]
        with (
            patch.object(core, "_load_gravity_templates", return_value=self.templates),
            patch.object(core, "robust_move") as move,
            patch.object(core, "safe_sleep"),
            patch.object(core, "check_exit"),
            patch.object(core, "_read_gc_gravity", side_effect=reads),
            patch.object(core, "click_at"),
        ):
            self.assertTrue(core._cycle_gc_gravity_to_zero(object(), self.geometry))
        self.assertTrue(move.call_args_list)
        for call in move.call_args_list:
            self.assertTrue(call.kwargs.get("nudge"))

class GravityClickGapTests(unittest.TestCase):
    def test_a_click_waits_out_the_rest_of_the_gap_and_never_sleeps_once_it_has_passed(self):
        with (
            patch.object(core, "GRAVITY_CLICK_GAP_SEC", 0.15),
            patch.object(core.time, "monotonic", return_value=10.05),
            patch.object(core, "safe_sleep") as sleep,
        ):
            core._wait_for_gravity_click_gap(10.0)
            self.assertAlmostEqual(sleep.call_args.args[0], 0.10)
            sleep.reset_mock()
            core._wait_for_gravity_click_gap(9.0)
            sleep.assert_not_called()


if __name__ == "__main__":
    unittest.main()
