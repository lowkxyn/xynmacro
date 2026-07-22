import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np


PYTHON_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PYTHON_DIR))

import xynmacro_core as core


def _solid_bgr(blue, green, red, height=30, width=75):
    frame = np.zeros((height, width, 3), dtype=np.uint8)
    frame[:, :] = (blue, green, red)
    return frame


class SenzuPreferenceModeTests(unittest.TestCase):
    def setUp(self):
        self._old_mode = core.SENZU_PREFERENCE_MODE
        core._invalidate_senzu_row_cache()

    def tearDown(self):
        core.SENZU_PREFERENCE_MODE = self._old_mode
        core._invalidate_senzu_row_cache()

    def test_priorities_for_all_four_modes(self):
        expected = {
            "full_only": ("full",),
            "full_then_half": ("full", "half"),
            "half_only": ("half",),
            "half_then_full": ("half", "full"),
        }
        for mode, priority in expected.items():
            with self.subTest(mode=mode):
                core.SENZU_PREFERENCE_MODE = mode
                self.assertEqual(core._senzu_type_priority(), priority)

    def test_normalize_falls_back_to_full_only_and_strict_raises(self):
        self.assertEqual(core._normalize_senzu_preference("bogus"), "full_only")
        self.assertEqual(core._normalize_senzu_preference(None), "full_only")
        self.assertEqual(
            core._normalize_senzu_preference("half_only"), "half_only"
        )
        with self.assertRaises(ValueError):
            core._normalize_senzu_preference("bogus", strict=True)

    def test_preferred_type_falls_back_only_when_stock_absent(self):
        core.SENZU_PREFERENCE_MODE = "full_then_half"
        finds = {
            "full": (None, None, 0.2),   # full stock absent
            "half": (600, 12, 0.9),
        }
        with patch.object(
            core, "_find_senzu_row",
            side_effect=lambda _sct, _assets, bean_type: finds[bean_type],
        ):
            row_y, count, score, bean_type = core._find_preferred_senzu_row(
                Mock(), {"bean": object(), "digits": object()}
            )
        self.assertEqual((row_y, count, bean_type), (600, 12, "half"))
        self.assertAlmostEqual(score, 0.9)

    def test_zero_count_preferred_type_is_treated_as_empty(self):
        core.SENZU_PREFERENCE_MODE = "half_then_full"
        finds = {
            "half": (520, 0, 0.95),      # row visible but stock is zero
            "full": (560, 5, 0.9),
        }
        with patch.object(
            core, "_find_senzu_row",
            side_effect=lambda _sct, _assets, bean_type: finds[bean_type],
        ):
            row_y, count, _score, bean_type = core._find_preferred_senzu_row(
                Mock(), {"bean": object(), "digits": object()}
            )
        self.assertEqual((row_y, count, bean_type), (560, 5, "full"))

    def test_unreadable_preferred_count_uses_confirmed_stock_fallback(self):
        core.SENZU_PREFERENCE_MODE = "full_then_half"
        finds = {
            "full": (520, None, 0.95),
            "half": (560, 4, 0.9),
        }
        with patch.object(
            core, "_find_senzu_row",
            side_effect=lambda _sct, _assets, bean_type: finds[bean_type],
        ):
            row_y, count, _score, bean_type = core._find_preferred_senzu_row(
                Mock(), {"bean": object(), "digits": object()}
            )
        self.assertEqual((row_y, count, bean_type), (560, 4, "half"))

    def test_unreadable_count_remains_usable_without_a_confirmed_fallback(self):
        core.SENZU_PREFERENCE_MODE = "full_only"
        with patch.object(
            core, "_find_senzu_row", return_value=(520, None, 0.95)
        ):
            row_y, count, _score, bean_type = core._find_preferred_senzu_row(
                Mock(), {"bean": object(), "digits": object()}
            )
        self.assertEqual((row_y, count, bean_type), (520, None, "full"))

    def test_full_only_never_falls_back_to_half(self):
        core.SENZU_PREFERENCE_MODE = "full_only"
        with patch.object(
            core, "_find_senzu_row", return_value=(None, None, 0.3)
        ) as find_row:
            row_y, count, _score, bean_type = core._find_preferred_senzu_row(
                Mock(), {"bean": object(), "digits": object()}
            )
        self.assertEqual((row_y, count, bean_type), (None, None, None))
        self.assertEqual(find_row.call_count, 1)
        self.assertEqual(find_row.call_args[0][2], "full")

    def test_half_only_never_looks_for_full(self):
        core.SENZU_PREFERENCE_MODE = "half_only"
        with patch.object(
            core, "_find_senzu_row", return_value=(None, None, 0.3)
        ) as find_row:
            row_y, count, _score, bean_type = core._find_preferred_senzu_row(
                Mock(), {"bean": object(), "digits": object()}
            )
        self.assertEqual((row_y, count, bean_type), (None, None, None))
        find_row.assert_called_once()
        self.assertEqual(find_row.call_args[0][2], "half")


class InventoryQuantityRedTests(unittest.TestCase):
    def test_red_quantity_is_detected(self):
        with patch.object(
            core, "_grab_reference_box", return_value=_solid_bgr(0, 0, 255)
        ):
            self.assertTrue(core._inventory_quantity_is_red(Mock(), 500))

    def test_green_quantity_is_not_red(self):
        with patch.object(
            core, "_grab_reference_box", return_value=_solid_bgr(0, 255, 0)
        ):
            self.assertFalse(core._inventory_quantity_is_red(Mock(), 500))

    def test_mixed_frame_needs_red_to_dominate_green(self):
        frame = _solid_bgr(0, 255, 0)
        frame[0, :5] = (0, 0, 255)  # 5 red pixels: below the 6-pixel floor
        with patch.object(core, "_grab_reference_box", return_value=frame):
            self.assertFalse(core._inventory_quantity_is_red(Mock(), 500))

    def test_wait_requires_two_consecutive_red_frames(self):
        with (
            patch.object(core, "_senzu_abort_requested", return_value=False),
            patch.object(
                core, "_inventory_quantity_is_red",
                side_effect=[False, True, False, True, True],
            ),
            patch.object(core.time, "sleep"),
        ):
            self.assertTrue(core._wait_for_inventory_quantity_red(Mock(), 500))

    def test_wait_gives_up_after_max_samples(self):
        with (
            patch.object(core, "_senzu_abort_requested", return_value=False),
            patch.object(
                core, "_inventory_quantity_is_red", return_value=False
            ) as is_red,
            patch.object(core.time, "sleep"),
        ):
            self.assertFalse(core._wait_for_inventory_quantity_red(Mock(), 500))
        self.assertEqual(is_red.call_count, 8)


class HotbarAcceptanceWaitTests(unittest.TestCase):
    def test_two_clear_frames_confirm_the_consume(self):
        states = iter([(True, 0.9), (True, 0.9), (False, 0.1), (False, 0.1)])
        with (
            patch.object(core, "_senzu_abort_requested", return_value=False),
            patch.object(
                core, "_senzu_slot_has_bean", side_effect=lambda *a, **k: next(states)
            ),
            patch.object(core, "_tap_key_unchecked"),
            patch.object(core.time, "sleep"),
        ):
            accepted, score = core._wait_for_hotbar_slot_clear(
                Mock(), 1, object(), "full"
            )
        self.assertTrue(accepted)
        self.assertAlmostEqual(score, 0.9)

    def test_single_clear_frame_between_loaded_frames_does_not_confirm(self):
        pattern = [(True, 0.9), (False, 0.1)] * 6
        with (
            patch.object(core, "_senzu_abort_requested", return_value=False),
            patch.object(
                core, "_senzu_slot_has_bean", side_effect=pattern
            ) as slot_state,
            patch.object(core, "_tap_key_unchecked"),
            patch.object(core.time, "sleep"),
        ):
            accepted, _score = core._wait_for_hotbar_slot_clear(
                Mock(), 1, object(), "full"
            )
        self.assertFalse(accepted)
        self.assertEqual(slot_state.call_count, 12)

    def test_slot_never_clearing_times_out_within_budget(self):
        # 12 samples * 0.07s pacing keeps the wait at roughly 0.8 seconds.
        with (
            patch.object(core, "_senzu_abort_requested", return_value=False),
            patch.object(
                core, "_senzu_slot_has_bean", return_value=(True, 0.9)
            ) as slot_state,
            patch.object(core, "_tap_key_unchecked") as tap_key,
            patch.object(core, "click_at"),
            patch.object(core.time, "sleep") as sleep,
        ):
            accepted, _score = core._wait_for_hotbar_slot_clear(
                Mock(), 1, object(), "full"
            )
        self.assertFalse(accepted)
        self.assertEqual(slot_state.call_count, 12)
        self.assertEqual(sleep.call_count, 11)
        # The wait never re-presses the digit; it falls back to one row click.
        tap_key.assert_not_called()

    def test_accepted_digit_never_clicks_the_row(self):
        pattern = [(True, 0.9)] * 2 + [(False, 0.1)] * 10
        with (
            patch.object(core, "_senzu_abort_requested", return_value=False),
            patch.object(core, "_senzu_slot_has_bean", side_effect=pattern),
            patch.object(core, "click_at") as row_click,
            patch.object(core.time, "sleep"),
        ):
            accepted, _score = core._wait_for_hotbar_slot_clear(
                Mock(), 1, object(), "full"
            )
        self.assertTrue(accepted)
        row_click.assert_not_called()

    def test_dropped_digit_is_retried_in_the_same_open_h_menu(self):
        with (
            patch.object(core, "_senzu_abort_requested", return_value=False),
            patch.object(core, "_focus_game_for_senzu", return_value=True) as focus,
            patch.object(
                core,
                "_wait_for_hotbar_slot_clear",
                side_effect=[(False, 0.99), (False, 0.99), (True, 0.99)],
            ) as wait_for_clear,
            patch.object(core, "_tap_key_unchecked") as tap_key,
            patch.object(core.time, "sleep"),
        ):
            accepted, score = core._consume_open_senzu_slot(
                Mock(), 1, object(), "full"
            )

        self.assertTrue(accepted)
        self.assertAlmostEqual(score, 0.99)
        self.assertEqual(focus.call_count, 3)
        self.assertEqual(wait_for_clear.call_count, 3)
        self.assertEqual([entry.args for entry in tap_key.call_args_list], [("1",)] * 3)


class SenzuRowCacheTests(unittest.TestCase):
    def setUp(self):
        self._old_mode = core.SENZU_PREFERENCE_MODE
        self._old_remaining = core.SENZU_REMAINING
        core.SENZU_PREFERENCE_MODE = "full_only"
        core.SENZU_REMAINING = None
        core._invalidate_senzu_row_cache()

    def tearDown(self):
        core.SENZU_PREFERENCE_MODE = self._old_mode
        core.SENZU_REMAINING = self._old_remaining
        core._invalidate_senzu_row_cache()

    def _assets(self, template_height=18):
        return {
            "bean": np.zeros((template_height, 150), dtype=np.uint8),
            "digits": object(),
        }

    def test_empty_cache_skips_validation_without_grabbing(self):
        with patch.object(core, "_grab_reference_box") as grab:
            self.assertIsNone(
                core._validate_cached_senzu_row(Mock(), self._assets())
            )
        grab.assert_not_called()

    def test_preference_mode_change_invalidates_cache(self):
        core._remember_senzu_row("full", 500)
        core.SENZU_PREFERENCE_MODE = "half_only"
        with patch.object(core, "_grab_reference_box") as grab:
            self.assertIsNone(
                core._validate_cached_senzu_row(Mock(), self._assets())
            )
        grab.assert_not_called()
        self.assertIsNone(core.SENZU_ROW_CACHE["row_y"])

    def test_confirmed_row_and_matching_count_reuse_the_cache(self):
        core._remember_senzu_row("full", 500)
        core.SENZU_REMAINING = 27
        scores = np.zeros((9, 10), dtype=np.float32)
        scores[4, 8] = 0.91  # x=8 <= 24: a Full-position match
        frame = np.zeros((335, 650, 3), dtype=np.uint8)
        with (
            patch.object(core, "_grab_reference_box", return_value=frame),
            patch.object(core.cv2, "matchTemplate", return_value=scores),
            patch.object(core, "_read_inventory_count", return_value=27),
        ):
            result = core._validate_cached_senzu_row(Mock(), self._assets())
        self.assertIsNotNone(result)
        row_y, count, score, bean_type = result
        self.assertEqual((count, bean_type), (27, "full"))
        self.assertAlmostEqual(score, 0.91, places=5)
        # band_top=92, match_y=4, template//2=9 -> the original row position
        self.assertEqual(row_y, 500)
        self.assertEqual(core.SENZU_ROW_CACHE["row_y"], 500)

    def test_moved_or_missing_row_invalidates_cache(self):
        core._remember_senzu_row("full", 500)
        scores = np.zeros((9, 10), dtype=np.float32)  # nothing matches
        frame = np.zeros((335, 650, 3), dtype=np.uint8)
        with (
            patch.object(core, "_grab_reference_box", return_value=frame),
            patch.object(core.cv2, "matchTemplate", return_value=scores),
        ):
            self.assertIsNone(
                core._validate_cached_senzu_row(Mock(), self._assets())
            )
        self.assertIsNone(core.SENZU_ROW_CACHE["row_y"])

    def test_half_position_match_rejects_a_cached_full_row(self):
        core._remember_senzu_row("full", 500)
        scores = np.zeros((9, 60), dtype=np.float32)
        scores[4, 40] = 0.95  # x=40 > 24: Half-prefixed label, not Full
        frame = np.zeros((335, 650, 3), dtype=np.uint8)
        with (
            patch.object(core, "_grab_reference_box", return_value=frame),
            patch.object(core.cv2, "matchTemplate", return_value=scores),
        ):
            self.assertIsNone(
                core._validate_cached_senzu_row(Mock(), self._assets())
            )
        self.assertIsNone(core.SENZU_ROW_CACHE["row_y"])

    def test_count_mismatch_invalidates_cache(self):
        core._remember_senzu_row("full", 500)
        core.SENZU_REMAINING = 27
        scores = np.zeros((9, 10), dtype=np.float32)
        scores[4, 8] = 0.91
        frame = np.zeros((335, 650, 3), dtype=np.uint8)
        with (
            patch.object(core, "_grab_reference_box", return_value=frame),
            patch.object(core.cv2, "matchTemplate", return_value=scores),
            patch.object(core, "_read_inventory_count", return_value=25),
        ):
            self.assertIsNone(
                core._validate_cached_senzu_row(Mock(), self._assets())
            )
        self.assertIsNone(core.SENZU_ROW_CACHE["row_y"])

    def test_preferred_lookup_uses_cache_before_scanning(self):
        with (
            patch.object(
                core, "_validate_cached_senzu_row",
                return_value=(500, 27, 0.91, "full"),
            ),
            patch.object(core, "_find_senzu_row") as find_row,
        ):
            result = core._find_preferred_senzu_row(Mock(), self._assets())
        self.assertEqual(result, (500, 27, 0.91, "full"))
        find_row.assert_not_called()

    def test_failed_full_scan_invalidates_cache(self):
        core._remember_senzu_row("full", 500)
        with (
            patch.object(core, "_validate_cached_senzu_row", return_value=None),
            patch.object(core, "_find_senzu_row", return_value=(None, None, 0.2)),
        ):
            row_y, _count, _score, bean_type = core._find_preferred_senzu_row(
                Mock(), self._assets()
            )
        self.assertIsNone(row_y)
        self.assertIsNone(bean_type)
        self.assertIsNone(core.SENZU_ROW_CACHE["row_y"])

    def test_successful_scan_remembers_the_row(self):
        template = np.zeros((18, 150), dtype=np.uint8)
        assets = {"bean": template, "digits": object()}
        frame = np.zeros((335, 650, 3), dtype=np.uint8)
        scores = np.zeros((9, 10), dtype=np.float32)
        scores[4, 8] = 0.91
        with (
            patch.object(core, "_senzu_abort_requested", return_value=False),
            patch.object(core, "_reference_point", side_effect=lambda x, y: (x, y)),
            patch.object(core, "robust_move"),
            patch.object(core._user32, "SendInput", create=True),
            patch.object(core, "_grab_reference_box", return_value=frame),
            patch.object(core.cv2, "matchTemplate", return_value=scores),
            patch.object(core, "_read_inventory_count", return_value=27),
            patch.object(core.time, "sleep"),
        ):
            row_y, count, _score = core._find_senzu_row(Mock(), assets, "full")
        self.assertEqual(count, 27)
        self.assertEqual(core.SENZU_ROW_CACHE["row_y"], row_y)
        self.assertEqual(core.SENZU_ROW_CACHE["bean_type"], "full")
        self.assertEqual(core.SENZU_ROW_CACHE["mode"], "full_only")

    def test_senzu_failure_stop_invalidates_cache(self):
        core._remember_senzu_row("full", 500)
        old_stop = core.UI_STOP_REQUESTED
        old_status = core.SENZU_STATUS
        try:
            with patch.object(core, "_record_run_outcome"):
                core._stop_for_senzu_failure("test failure")
            self.assertIsNone(core.SENZU_ROW_CACHE["row_y"])
        finally:
            core.UI_STOP_REQUESTED = old_stop
            core.SENZU_STATUS = old_status

    def test_settings_change_to_new_mode_invalidates_cache(self):
        core._remember_senzu_row("full", 500)
        with patch.object(core, "save_master_config"):
            core._ui_apply_setting("senzu_preference_mode", "half_then_full")
        try:
            self.assertIsNone(core.SENZU_ROW_CACHE["row_y"])
            self.assertEqual(core.SENZU_PREFERENCE_MODE, "half_then_full")
        finally:
            with patch.object(core, "save_master_config"):
                core._ui_apply_setting("senzu_preference_mode", "full_only")

    def test_no_yellow_fallback_toggle_is_a_user_setting(self):
        old_enabled = core.NO_YELLOW_FALLBACK_ENABLED
        try:
            with patch.object(core, "save_master_config"):
                core._ui_apply_setting("no_yellow_fallback_enabled", True)
                self.assertTrue(core.NO_YELLOW_FALLBACK_ENABLED)
                core._ui_apply_setting("no_yellow_fallback_enabled", False)
                self.assertFalse(core.NO_YELLOW_FALLBACK_ENABLED)
        finally:
            core.NO_YELLOW_FALLBACK_ENABLED = old_enabled

    def test_settings_write_of_same_mode_keeps_cache(self):
        core._remember_senzu_row("full", 500)
        with patch.object(core, "save_master_config"):
            core._ui_apply_setting("senzu_preference_mode", "full_only")
        self.assertEqual(core.SENZU_ROW_CACHE["row_y"], 500)


if __name__ == "__main__":
    unittest.main()
