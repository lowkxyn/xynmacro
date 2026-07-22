import sys
import unittest
from pathlib import Path
from unittest.mock import ANY, Mock, call, patch

import numpy as np


PYTHON_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PYTHON_DIR))

import xynmacro_core as core


class SenzuControlFlowTests(unittest.TestCase):
    def test_live_full_bean_slot_score_keeps_type_and_stability_guard(self):
        self.assertTrue(core._senzu_slot_match_is_reliable(0.762, 42, "full"))
        self.assertFalse(core._senzu_slot_match_is_reliable(0.739, 42, "full"))
        self.assertFalse(core._senzu_slot_match_is_reliable(0.900, 80, "full"))
        self.assertTrue(core._senzu_slot_match_is_reliable(0.900, 80, "half"))

    def setUp(self):
        # Tests that exercise the real _find_senzu_row populate the row cache;
        # every test must start without a remembered Items position.
        core._invalidate_senzu_row_cache()

    def test_slot_detector_rejects_half_senzu_suffix(self):
        template = np.zeros((1, 1), dtype=np.uint8)
        frame = np.zeros((42, 260, 3), dtype=np.uint8)
        full_scores = np.zeros((1, 120), dtype=np.float32)
        full_scores[0, 42] = 0.95
        half_scores = np.zeros((1, 120), dtype=np.float32)
        half_scores[0, 92] = 0.99

        with (
            patch.object(core, "_grab_reference_box", return_value=frame),
            patch.object(core.cv2, "matchTemplate", side_effect=[full_scores, half_scores]),
        ):
            full_loaded, full_score = core._senzu_slot_has_bean(Mock(), 1, template)
            half_loaded, half_score = core._senzu_slot_has_bean(Mock(), 1, template)

        self.assertTrue(full_loaded)
        self.assertAlmostEqual(full_score, 0.95, places=5)
        self.assertFalse(half_loaded)
        self.assertAlmostEqual(half_score, 0.99, places=5)

    def test_slot_detector_accepts_half_senzu_label_when_requested(self):
        template = np.zeros((1, 1), dtype=np.uint8)
        frame = np.zeros((42, 260, 3), dtype=np.uint8)
        scores = np.zeros((1, 120), dtype=np.float32)
        scores[0, 92] = 0.99

        with (
            patch.object(core, "_grab_reference_box", return_value=frame),
            patch.object(core.cv2, "matchTemplate", return_value=scores),
        ):
            loaded, score = core._senzu_slot_has_bean(
                Mock(), 1, template, bean_type="half"
            )

        self.assertTrue(loaded)
        self.assertAlmostEqual(score, 0.99, places=5)

    def test_slot_detector_crops_the_actual_first_hotbar_row(self):
        template = np.zeros((1, 1), dtype=np.uint8)
        frame = np.zeros((42, 260, 3), dtype=np.uint8)
        scores = np.zeros((1, 120), dtype=np.float32)
        scores[0, 42] = 0.95

        with (
            patch.object(core, "_grab_reference_box", return_value=frame) as grab,
            patch.object(core.cv2, "matchTemplate", return_value=scores),
        ):
            loaded, _ = core._senzu_slot_has_bean(
                Mock(), 1, template, hotbar=True
            )

        self.assertTrue(loaded)
        grab.assert_called_once_with(ANY, (1510, 920, 260, 42))

        with (
            patch.object(core, "_grab_reference_box", return_value=frame) as inventory_grab,
            patch.object(core.cv2, "matchTemplate", return_value=scores),
        ):
            core._senzu_slot_has_bean(Mock(), 1, template)
        inventory_grab.assert_called_once_with(ANY, (1510, 946, 260, 42))

    def test_stop_prevents_inventory_cleanup_inputs(self):
        with (
            patch.object(core, "_senzu_abort_requested", return_value=True),
            patch.object(core, "_training_menu_visible_for_senzu") as training_visible,
            patch.object(core, "_focus_game_for_senzu") as focus_game,
            patch.object(core, "_tap_key_unchecked") as tap_key,
            patch.object(core, "click_at") as click_at,
        ):
            self.assertFalse(
                core._close_inventory_to_training(
                    Mock(), {"training": object(), "inventory": object(), "game_menu": object()}
                )
            )

        training_visible.assert_not_called()
        focus_game.assert_not_called()
        tap_key.assert_not_called()
        click_at.assert_not_called()

    def test_full_senzu_match_is_preferred_over_half_senzu_substring(self):
        assets = {
            "bean": np.zeros((1, 1), dtype=np.uint8),
            "digits": object(),
        }
        scores = np.zeros((1, 70), dtype=np.float32)
        scores[0, 8] = 0.91   # Full "Senzu Bean" is left-aligned.
        scores[0, 50] = 0.99  # "Senzu Bean" substring inside "Half Senzu Bean".

        with (
            patch.object(core, "_reference_point", return_value=(0, 0)),
            patch.object(core, "robust_move"),
            patch.object(core._user32, "SendInput"),
            patch.object(core, "_senzu_abort_requested", return_value=False),
            patch.object(core, "_grab_reference_box", return_value=np.zeros((1, 1, 3), dtype=np.uint8)),
            patch.object(core.cv2, "matchTemplate", return_value=scores),
            patch.object(core, "_read_inventory_count", return_value=12),
            patch.object(core.time, "sleep"),
        ):
            row_y, count, score = core._find_senzu_row(Mock(), assets)

        self.assertEqual(row_y, 395)
        self.assertEqual(count, 12)
        self.assertAlmostEqual(score, 0.91, places=5)

    def test_empty_preflight_disables_auto_senzu_for_run_and_continues(self):
        old_status = core.SENZU_STATUS
        old_remaining = core.SENZU_REMAINING
        old_disabled = core.SENZU_DISABLED_FOR_RUN
        assets = {name: object() for name in ("training", "game_menu", "inventory", "bean", "slot", "digits")}
        try:
            core.SENZU_DISABLED_FOR_RUN = False

            def empty_refill(*_args, **_kwargs):
                core.SENZU_STATUS = "empty"
                return False

            with (
                patch.object(core, "_senzu_assets", return_value=assets),
                patch.object(core, "_senzu_slot_has_bean", return_value=(False, 0.0)),
                patch.object(core, "_refill_senzu_slot", side_effect=empty_refill),
                patch.object(core, "_lower_gc_gravity_for_empty_senzu", return_value=True),
            ):
                self.assertTrue(core.ensure_senzu_ready(Mock()))

            self.assertTrue(core.SENZU_DISABLED_FOR_RUN)
            self.assertEqual(core.SENZU_STATUS, "empty")
            self.assertEqual(core.SENZU_REMAINING, 0)
        finally:
            core.SENZU_STATUS = old_status
            core.SENZU_REMAINING = old_remaining
            core.SENZU_DISABLED_FOR_RUN = old_disabled

    def test_visible_ghost_slot_still_requires_real_inventory_stock(self):
        old_status = core.SENZU_STATUS
        old_disabled = core.SENZU_DISABLED_FOR_RUN
        assets = {
            name: object()
            for name in ("training", "game_menu", "inventory", "bean", "slot", "digits")
        }

        def no_inventory_stock(*_args, **_kwargs):
            core.SENZU_STATUS = "empty"
            return False

        try:
            core.SENZU_DISABLED_FOR_RUN = False
            with (
                patch.object(core, "_senzu_assets", return_value=assets),
                patch.object(core, "_senzu_type_priority", return_value=["full"]),
                patch.object(core, "_senzu_slot_has_bean", return_value=(True, 0.99)),
                patch.object(
                    core,
                    "_refill_senzu_slot",
                    side_effect=no_inventory_stock,
                ) as refill,
                patch.object(core, "_lower_gc_gravity_for_empty_senzu", return_value=True),
            ):
                self.assertTrue(core.ensure_senzu_ready(Mock()))

            refill.assert_called_once()
            self.assertTrue(core.SENZU_DISABLED_FOR_RUN)
            self.assertEqual(core.SENZU_STATUS, "empty")
        finally:
            core.SENZU_STATUS = old_status
            core.SENZU_DISABLED_FOR_RUN = old_disabled

    def test_last_full_bean_resumes_and_disables_future_attempts(self):
        old_status = core.SENZU_STATUS
        old_remaining = core.SENZU_REMAINING
        old_disabled = core.SENZU_DISABLED_FOR_RUN
        old_eaten = core.TELEMETRY["senzu_eaten"]
        try:
            core.SENZU_DISABLED_FOR_RUN = False
            with (
                patch.object(core, "_wait_for_green_hp", return_value=True),
                patch.object(core, "_lower_gc_gravity_for_empty_senzu", return_value=True),
                patch.object(core, "_resume_training_after_senzu", return_value=True),
            ):
                self.assertTrue(
                    core._resume_after_senzu_stock_empty(
                        Mock(), {"training": object()}, bean_was_used=True
                    )
                )

            self.assertTrue(core.SENZU_DISABLED_FOR_RUN)
            self.assertEqual(core.SENZU_STATUS, "empty")
            self.assertEqual(core.SENZU_REMAINING, 0)
            self.assertEqual(core.TELEMETRY["senzu_eaten"], old_eaten + 1)
        finally:
            core.SENZU_STATUS = old_status
            core.SENZU_REMAINING = old_remaining
            core.SENZU_DISABLED_FOR_RUN = old_disabled
            core.TELEMETRY["senzu_eaten"] = old_eaten

    def test_missed_eat_waits_then_retries_once(self):
        old_status = core.SENZU_STATUS
        old_eaten = core.TELEMETRY["senzu_eaten"]
        assets = {name: object() for name in ("training", "game_menu", "inventory", "bean", "slot", "digits")}
        refill_results = iter([False, True])

        def refill(*_args, **_kwargs):
            result = next(refill_results)
            if not result:
                core.SENZU_STATUS = "not_consumed"
            return result

        try:
            with (
                patch.object(core, "_senzu_assets", return_value=assets),
                patch.object(core, "_ensure_training_menu_for_senzu", return_value=True),
                patch.object(core, "_focus_game_for_senzu", return_value=True),
                patch.object(core, "_stable_senzu_slot_state", return_value=(True, 0.99)),
                patch.object(core, "_senzu_abort_requested", return_value=False),
                patch.object(core, "_refill_senzu_slot", side_effect=refill) as refill_slot,
                patch.object(core, "_wait_for_hotbar_slot_clear", return_value=(True, 0.99)),
                patch.object(core, "_recover_after_unconfirmed_senzu", return_value=None),
                patch.object(core, "_wait_for_green_hp", return_value=True),
                patch.object(core, "_resume_training_after_senzu", return_value=True),
                patch.object(core, "_stop_for_senzu_failure") as stop_failure,
                patch.object(core, "_tap_key_unchecked") as tap_key,
                patch.object(core.time, "sleep"),
            ):
                self.assertTrue(core.eat_senzu(Mock()))

            self.assertEqual(refill_slot.call_count, 2)
            self.assertEqual(tap_key.call_args_list.count(call(str(core.SENZU_SLOT))), 2)
            stop_failure.assert_not_called()
        finally:
            core.SENZU_STATUS = old_status
            core.TELEMETRY["senzu_eaten"] = old_eaten

    def test_ki_senzu_uses_tab_h_slot_h_sequence(self):
        assets = {
            name: object()
            for name in ("training", "game_menu", "inventory", "bean", "slot", "digits")
        }
        old_status = core.SENZU_STATUS
        old_eaten = core.TELEMETRY["senzu_eaten"]
        try:
            with (
                patch.object(core, "CURRENT_TRAINING_STATE", "Ki Control"),
                patch.object(core, "SENZU_ACTIVE_TYPE", "full"),
                patch.object(core, "SENZU_SLOT", 1),
                patch.object(core, "_senzu_assets", return_value=assets),
                patch.object(core, "_senzu_type_priority", return_value=["full"]),
                patch.object(core, "_ensure_training_menu_for_senzu", return_value=True),
                patch.object(core, "_focus_game_for_senzu", return_value=True),
                patch.object(core, "_stable_senzu_slot_state", return_value=(True, 0.99)),
                patch.object(core, "_senzu_abort_requested", return_value=False),
                patch.object(core, "_wait_for_hotbar_slot_clear", return_value=(True, 0.99)),
                patch.object(core, "_refill_senzu_slot", return_value=True),
                patch.object(core, "_wait_for_green_hp", return_value=True),
                patch.object(core, "_resume_training_after_senzu", return_value=True),
                patch.object(core, "_tap_key_unchecked") as tap_key,
                patch.object(core.time, "sleep"),
            ):
                self.assertTrue(core.eat_senzu(Mock()))

            self.assertEqual(
                tap_key.call_args_list,
                [call("tab"), call("h"), call("1"), call("h")],
            )
        finally:
            core.SENZU_STATUS = old_status
            core.TELEMETRY["senzu_eaten"] = old_eaten

    def test_delayed_green_recovery_refreshes_slot_and_resumes(self):
        old_status = core.SENZU_STATUS
        old_eaten = core.TELEMETRY["senzu_eaten"]
        try:
            with (
                patch.object(core, "_wait_for_green_hp", return_value=True),
                patch.object(core, "_refill_senzu_slot", return_value=True) as refill_slot,
                patch.object(core, "_resume_training_after_senzu", return_value=True) as resume,
            ):
                self.assertTrue(
                    core._recover_after_unconfirmed_senzu(
                        Mock(), {"training": object()}
                    )
                )

            refill_slot.assert_called_once()
            resume.assert_called_once()
            self.assertEqual(core.TELEMETRY["senzu_eaten"], old_eaten + 1)
            self.assertEqual(core.SENZU_STATUS, "ready")
        finally:
            core.SENZU_STATUS = old_status
            core.TELEMETRY["senzu_eaten"] = old_eaten

    def test_resume_clicks_the_active_training_category(self):
        assets = {"training": object()}
        visible_states = [(True, 0.99), (False, 0.1), (False, 0.1)]
        with (
            patch.object(core, "CURRENT_TRAINING_STATE", "Ki Control"),
            patch.object(core, "_senzu_abort_requested", return_value=False),
            patch.object(
                core,
                "_training_menu_visible_for_senzu",
                side_effect=visible_states,
            ),
            patch.object(core, "_button_screen_point", return_value=(420, 360)) as point,
            patch.object(core, "click_at") as click,
            patch.object(core.time, "sleep"),
        ):
            self.assertTrue(core._resume_training_after_senzu(Mock(), assets))

        point.assert_called_once_with("Ki Control")
        click.assert_called_once_with(420, 360)

    def test_session_senzu_disable_resets_on_next_run(self):
        old_disabled = core.SENZU_DISABLED_FOR_RUN
        try:
            core.SENZU_DISABLED_FOR_RUN = True
            with (
                patch.object(core, "run_master_controller"),
                patch.object(core, "_stop_background_game_monitor"),
                patch.object(core, "_finalize_run_result"),
            ):
                core._run_macro_safe()
            self.assertFalse(core.SENZU_DISABLED_FOR_RUN)
        finally:
            core.SENZU_DISABLED_FOR_RUN = old_disabled

    def test_game_menu_close_reopens_training_with_tab(self):
        assets = {
            "training": object(),
            "inventory": object(),
            "game_menu": object(),
        }
        screen_waits = iter([
            (None, 0.1),        # Inventory is not open
            ((10, 10), 0.99),  # Game Menu is open
        ])
        training_waits = iter([
            (False, 0.1),  # M closes Game Menu to gameplay
            (True, 0.99),  # Tab reopens Training Mode
        ])

        with (
            patch.object(core, "_training_menu_visible_for_senzu", return_value=(False, 0.0)),
            patch.object(core, "_wait_for_senzu_screen", side_effect=lambda *a, **k: next(screen_waits)),
            patch.object(core, "_wait_for_training_menu_for_senzu", side_effect=lambda *a, **k: next(training_waits)),
            patch.object(core, "_focus_game_for_senzu", return_value=True),
            patch.object(core, "_senzu_abort_requested", return_value=False),
            patch.object(core, "_tap_key_unchecked") as tap_key,
        ):
            self.assertTrue(core._close_inventory_to_training(Mock(), assets))

        self.assertEqual(tap_key.call_args_list, [call("m"), call("tab")])

    def test_existing_inventory_routes_back_without_pressing_tab(self):
        assets = {
            "training": object(),
            "inventory": object(),
            "game_menu": object(),
        }
        wait_results = [((10, 10), 0.99)]

        with (
            patch.object(core, "_senzu_abort_requested", return_value=False),
            patch.object(core, "_training_menu_visible_for_senzu", return_value=(False, 0.0)),
            patch.object(core, "_wait_for_senzu_screen", side_effect=wait_results),
            patch.object(core, "_close_inventory_to_training", return_value=True) as close_menu,
            patch.object(core, "_focus_game_for_senzu") as focus_game,
            patch.object(core, "_tap_key_unchecked") as tap_key,
        ):
            self.assertTrue(core._ensure_training_menu_for_senzu(Mock(), assets))

        close_menu.assert_called_once()
        focus_game.assert_not_called()
        tap_key.assert_not_called()

    def test_user_stop_during_senzu_route_is_not_reported_as_failure(self):
        status = core.SENZU_STATUS
        try:
            with (
                patch.object(core, "_senzu_assets", return_value={"training": object()}),
                patch.object(core, "_ensure_training_menu_for_senzu", return_value=False),
                patch.object(core, "_senzu_abort_requested", return_value=True),
                patch.object(core, "_stop_for_senzu_failure") as stop_failure,
            ):
                self.assertFalse(core.eat_senzu(Mock()))
            stop_failure.assert_not_called()
        finally:
            core.SENZU_STATUS = status

    def test_stop_flag_is_not_consumed_by_first_worker(self):
        old_stop = core.UI_STOP_REQUESTED
        try:
            core.UI_STOP_REQUESTED = True
            with self.assertRaises(core.QuitException):
                core.check_exit()
            self.assertTrue(core.UI_STOP_REQUESTED)
        finally:
            core.UI_STOP_REQUESTED = old_stop

    def test_monitor_treats_senzu_quit_exception_as_user_stop(self):
        stop_event = Mock()
        stop_event.is_set.return_value = False
        capture = Mock()
        capture.__enter__ = Mock(return_value=Mock())
        capture.__exit__ = Mock(return_value=False)

        with (
            patch.object(core.mss, "MSS", return_value=capture),
            patch.object(core, "CURRENT_TRAINING_STATE", "Ki Control"),
            # Category started long ago (0.0) so senzu's post-switch grace window has
            # elapsed and the check can fire; the progression path is muted by mocking
            # its completion read to None rather than by an infinite start time (which
            # would now also suppress senzu, which shares that grace timer).
            patch.object(core, "PROGRESSION_STATE_STARTED_AT", 0.0),
            patch.object(core, "read_progression_completion", return_value=None),
            patch.object(core, "SENZU_ENABLED", True),
            patch.object(core, "SENZU_DISABLED_FOR_RUN", False),
            patch.object(core, "SENZU_DELAY_SEC", 0.0),
            patch.object(core, "update_game_window", return_value=True),
            patch.object(core, "_hp_bar_is_red", return_value=True),
            patch.object(
                core,
                "eat_senzu",
                side_effect=core.QuitException(),
            ),
            patch.object(core, "_stop_for_senzu_failure") as stop_failure,
        ):
            core._background_game_monitor(stop_event)

        stop_failure.assert_not_called()

    def test_inventory_click_retries_while_game_menu_stays_confirmed(self):
        assets = {
            "training": object(),
            "inventory": object(),
            "game_menu": object(),
            "bean": object(),
            "slot": object(),
            "digits": object(),
        }
        waits = iter([
            ((10, 10), 0.99),  # Game Menu after M
            (None, 0.229),     # first Inventory click stayed on Game Menu
            ((10, 10), 0.99),  # Game Menu still confirmed, retry is safe
            ((10, 10), 0.99),  # Inventory opens after retry
            ((10, 10), 0.99),  # Inventory remains open for Items retry
        ])
        status = core.SENZU_STATUS
        remaining = core.SENZU_REMAINING
        try:
            with (
                patch.object(core, "_focus_game_for_senzu", return_value=True),
                patch.object(core, "_senzu_abort_requested", return_value=False),
                patch.object(core, "_wait_for_senzu_screen", side_effect=lambda *a, **k: next(waits)),
                patch.object(core, "_reference_point", side_effect=lambda x, y: (x, y)),
                patch.object(core, "_find_senzu_row", return_value=(None, None, 0.1)),
                patch.object(core, "_close_inventory_to_training", return_value=True),
                patch.object(core, "_tap_key_unchecked"),
                patch.object(core, "click_at") as click_at,
                patch.object(core.time, "sleep"),
            ):
                self.assertFalse(
                    core._refill_senzu_slot(
                        Mock(), assets, _inventory_reopen_attempted=True
                    )
                )

            self.assertEqual(
                click_at.call_args_list[:4],
                [call(389, 568), call(389, 568), call(414, 758), call(414, 758)],
            )
        finally:
            core.SENZU_STATUS = status
            core.SENZU_REMAINING = remaining

    def test_empty_stock_is_error_when_training_menu_cannot_be_restored(self):
        assets = {
            "training": object(),
            "inventory": object(),
            "game_menu": object(),
            "bean": object(),
            "slot": object(),
            "digits": object(),
        }
        waits = iter([
            ((10, 10), 0.99),  # Game Menu after M
            ((10, 10), 0.99),  # Inventory opens
            ((10, 10), 0.99),  # Inventory remains open for Items retry
        ])
        old_status = core.SENZU_STATUS
        old_remaining = core.SENZU_REMAINING
        try:
            with (
                patch.object(core, "_focus_game_for_senzu", return_value=True),
                patch.object(core, "_senzu_abort_requested", return_value=False),
                patch.object(core, "_wait_for_senzu_screen", side_effect=lambda *a, **k: next(waits)),
                patch.object(core, "_reference_point", side_effect=lambda x, y: (x, y)),
                patch.object(core, "_find_senzu_row", return_value=(None, None, 0.1)),
                patch.object(core, "_close_inventory_to_training", return_value=False),
                patch.object(core, "_tap_key_unchecked"),
                patch.object(core, "click_at"),
                patch.object(core.time, "sleep"),
            ):
                self.assertFalse(
                    core._refill_senzu_slot(
                        Mock(), assets, _inventory_reopen_attempted=True
                    )
                )

            self.assertEqual(core.SENZU_STATUS, "error")
        finally:
            core.SENZU_STATUS = old_status
            core.SENZU_REMAINING = old_remaining


if __name__ == "__main__":
    unittest.main()
