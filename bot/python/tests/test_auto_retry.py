import sys
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np


PYTHON_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PYTHON_DIR))

import xmacro_core as core


class AutoRetryTests(unittest.TestCase):
    def setUp(self):
        self.settings = {
            "AUTO_RETRY_ON_FAILURE": core.AUTO_RETRY_ON_FAILURE,
            "AUTO_RETRY_MAX_ATTEMPTS": core.AUTO_RETRY_MAX_ATTEMPTS,
            "PREVENT_SLEEP_WHILE_RUNNING": core.PREVENT_SLEEP_WHILE_RUNNING,
            "_USER_STOP_LATCHED": core._USER_STOP_LATCHED,
            "_AFTER_ACTIONS_BLOCKED": core._AFTER_ACTIONS_BLOCKED,
            "UI_STOP_REQUESTED": core.UI_STOP_REQUESTED,
        }
        core.AUTO_RETRY_ON_FAILURE = True
        core.AUTO_RETRY_MAX_ATTEMPTS = 3
        core.PREVENT_SLEEP_WHILE_RUNNING = False
        core._USER_STOP_LATCHED = False
        core._AFTER_ACTIONS_BLOCKED = False
        core._telemetry_reset()
        core._begin_run_result()

    def tearDown(self):
        for name, value in self.settings.items():
            setattr(core, name, value)
        core._begin_run_result()

    def test_only_errors_with_remaining_attempts_can_retry(self):
        core._record_run_outcome("error", "failed", retryable=True)
        self.assertTrue(core._auto_retry_can_run(0))
        self.assertFalse(core._auto_retry_can_run(3))

        core._USER_STOP_LATCHED = True
        self.assertFalse(core._auto_retry_can_run(0))

    def test_controller_restarts_once_and_finalizes_only_after_success(self):
        calls = []

        def controller():
            calls.append(len(calls) + 1)
            if len(calls) == 1:
                core._record_run_outcome("error", "temporary failure", retryable=True)
            else:
                core._record_run_outcome("completed", "done")

        with (
            patch.object(core, "run_master_controller", side_effect=controller),
            patch.object(core, "_stop_background_game_monitor", return_value=True),
            patch.object(core, "_auto_retry_recover", return_value=True) as recover,
            patch.object(core, "_finalize_run_result") as finalize,
        ):
            core._run_macro_safe()

        self.assertEqual(calls, [1, 2])
        recover.assert_called_once_with()
        finalize.assert_called_once_with()
        self.assertEqual(core.TELEMETRY["recovery_attempts"], 1)

    def test_failed_recovery_keeps_the_original_error_and_stops(self):
        def controller():
            core._record_run_outcome("error", "temporary failure", retryable=True)

        with (
            patch.object(core, "run_master_controller", side_effect=controller),
            patch.object(core, "_stop_background_game_monitor", return_value=True),
            patch.object(core, "_auto_retry_recover", return_value=False),
            patch.object(core, "_finalize_run_result") as finalize,
        ):
            core._run_macro_safe()

        self.assertEqual(core._CURRENT_RUN_OUTCOME, "error")
        self.assertEqual(core._CURRENT_RUN_REASON, "temporary failure")
        self.assertEqual(core.TELEMETRY["recovery_attempts"], 1)
        finalize.assert_called_once_with()

    def test_gc_death_dialog_requires_all_three_blue_text_bands(self):
        blue_band = np.full((45, 850, 4), (70, 45, 20, 255), dtype=np.uint8)
        blue_band[:5, :100, :3] = 255

        with patch.object(core, "_grab_reference_box", return_value=blue_band):
            self.assertTrue(core._gc_death_dialog_visible(object(), {}))

        red_world = np.full((45, 850, 4), (0, 0, 220, 255), dtype=np.uint8)
        with patch.object(core, "_grab_reference_box", return_value=red_world):
            self.assertFalse(core._gc_death_dialog_visible(object(), {}))

    def test_confirmed_death_stops_even_when_retry_is_disabled(self):
        core.AUTO_RETRY_ON_FAILURE = False
        core.UI_STOP_REQUESTED = False

        core._stop_for_game_death()

        self.assertTrue(core.UI_STOP_REQUESTED)
        self.assertEqual(core._CURRENT_RUN_OUTCOME, "error")
        self.assertEqual(core._CURRENT_RUN_REASON, "Character death was confirmed")

    def test_non_retryable_error_never_enters_recovery(self):
        def controller():
            core._record_run_outcome("error", "missing recognition asset")

        with (
            patch.object(core, "run_master_controller", side_effect=controller),
            patch.object(core, "_stop_background_game_monitor", return_value=True),
            patch.object(core, "_auto_retry_recover") as recover,
            patch.object(core, "_finalize_run_result"),
        ):
            core._run_macro_safe()

        recover.assert_not_called()

    def test_later_unexpected_error_revokes_retryability(self):
        core._record_run_outcome("error", "confirmed death", retryable=True)
        core._record_run_outcome("error", "unexpected worker bug")

        self.assertFalse(core._auto_retry_can_run(0))

    def test_unexpected_exception_never_enters_recovery(self):
        with (
            patch.object(core, "run_master_controller", side_effect=ValueError("bug")),
            patch.object(core, "_stop_background_game_monitor", return_value=True),
            patch.object(core, "_auto_retry_recover") as recover,
            patch.object(core, "_finalize_run_result"),
        ):
            core._run_macro_safe()

        recover.assert_not_called()

    def test_transient_navigation_runtime_error_can_retry(self):
        calls = []

        def controller():
            calls.append(1)
            if len(calls) == 1:
                raise RuntimeError(
                    "Training Mode menu was not confirmed after Tab; switch cancelled"
                )
            core._record_run_outcome("completed", "done")

        with (
            patch.object(core, "run_master_controller", side_effect=controller),
            patch.object(core, "_stop_background_game_monitor", return_value=True),
            patch.object(core, "_auto_retry_recover", return_value=True) as recover,
            patch.object(core, "_finalize_run_result"),
        ):
            core._run_macro_safe()

        self.assertEqual(calls, [1, 1])
        recover.assert_called_once_with()

    def test_latched_stop_prevents_attempt_preparation(self):
        core._USER_STOP_LATCHED = True
        core.UI_STOP_REQUESTED = True

        self.assertFalse(core._prepare_controller_attempt())
        self.assertTrue(core.UI_STOP_REQUESTED)

    def test_stop_after_recovery_never_restarts_controller(self):
        calls = []

        def controller():
            calls.append(1)
            core._record_run_outcome("error", "temporary failure", retryable=True)

        def recover_then_stop():
            core._USER_STOP_LATCHED = True
            core.UI_STOP_REQUESTED = True
            return True

        with (
            patch.object(core, "run_master_controller", side_effect=controller),
            patch.object(core, "_stop_background_game_monitor", return_value=True),
            patch.object(core, "_auto_retry_recover", side_effect=recover_then_stop),
            patch.object(core, "_finalize_run_result"),
        ):
            core._run_macro_safe()

        self.assertEqual(calls, [1])
        self.assertEqual(core._CURRENT_RUN_OUTCOME, "stopped")
        self.assertTrue(core.UI_STOP_REQUESTED)

    def test_reset_sends_no_input_when_stop_arrives_during_focus(self):
        def focus_then_stop():
            core._USER_STOP_LATCHED = True
            return True

        with (
            patch.object(core, "focus_game_window", side_effect=focus_then_stop),
            patch.object(core, "_tap_key_unchecked") as tap,
        ):
            self.assertFalse(core._auto_retry_reset_character())

        tap.assert_not_called()

    def test_reset_sends_no_later_keys_after_stop(self):
        keys = []

        def stop_after_first_key(key):
            keys.append(key)
            core._USER_STOP_LATCHED = True

        with (
            patch.object(core, "focus_game_window", return_value=True),
            patch.object(core, "_tap_key_unchecked", side_effect=stop_after_first_key),
        ):
            self.assertFalse(core._auto_retry_reset_character())

        self.assertEqual(keys, ["tab"])

    def test_respawn_visible_to_hidden_race_sends_no_click(self):
        context = unittest.mock.MagicMock()
        context.__enter__.return_value = object()
        context.__exit__.return_value = False
        with (
            patch.object(core, "focus_game_window", return_value=True),
            patch.object(
                core,
                "_confirmed_game_capture_rect",
                return_value={"left": 0, "top": 0, "width": 1920, "height": 1080},
            ),
            patch.object(core.mss, "MSS", return_value=context),
            patch.object(
                core, "_gc_death_dialog_visible", side_effect=[True, False]
            ),
            patch.object(core, "_click_sendinput_abs") as click,
        ):
            self.assertFalse(core._auto_retry_click_respawn())

        click.assert_not_called()

    def test_respawn_stop_during_final_confirmation_sends_no_click(self):
        context = unittest.mock.MagicMock()
        context.__enter__.return_value = object()
        context.__exit__.return_value = False

        def confirm_then_stop(_sct, _geometry):
            if confirm_then_stop.calls == 1:
                core._USER_STOP_LATCHED = True
            confirm_then_stop.calls += 1
            return True

        confirm_then_stop.calls = 0
        geometry = {"left": 0, "top": 0, "width": 1920, "height": 1080}
        with (
            patch.object(core, "focus_game_window", return_value=True),
            patch.object(core, "_confirmed_game_capture_rect", return_value=geometry),
            patch.object(core.mss, "MSS", return_value=context),
            patch.object(core, "_gc_death_dialog_visible", side_effect=confirm_then_stop),
            patch.object(core, "_click_sendinput_abs") as click,
        ):
            self.assertFalse(core._auto_retry_click_respawn())

        click.assert_not_called()

    def test_walk_releases_w_when_stop_arrives_after_key_down(self):
        def key_down_then_stop(_key):
            core._USER_STOP_LATCHED = True

        with (
            patch.object(core, "focus_game_window", return_value=True),
            patch.object(core.pydirectinput, "keyDown", side_effect=key_down_then_stop),
            patch.object(core.pydirectinput, "keyUp") as key_up,
        ):
            self.assertFalse(core._auto_retry_walk_forward())

        key_up.assert_called_once_with("w")

    def test_stop_linearizes_with_inflight_key_and_blocks_later_input(self):
        key_down_entered = threading.Event()
        release_key_down = threading.Event()
        stop_finished = threading.Event()
        downs = []
        ups = []

        def blocked_key_down(key):
            downs.append(key)
            key_down_entered.set()
            release_key_down.wait(1.0)

        def request_stop():
            core._ui_stop_macro()
            stop_finished.set()

        with (
            patch.object(core.pydirectinput, "keyDown", side_effect=blocked_key_down),
            patch.object(core.pydirectinput, "keyUp", side_effect=ups.append),
            patch.object(core, "_ui_is_running", return_value=False),
        ):
            input_thread = threading.Thread(
                target=core._tap_key_unchecked, args=("tab",)
            )
            input_thread.start()
            self.assertTrue(key_down_entered.wait(1.0))

            stop_thread = threading.Thread(target=request_stop)
            stop_thread.start()
            time.sleep(0.02)
            self.assertFalse(stop_finished.is_set())

            release_key_down.set()
            input_thread.join(1.0)
            stop_thread.join(1.0)
            self.assertTrue(stop_finished.is_set())
            self.assertFalse(core._tap_key_unchecked("r"))

        self.assertEqual(downs, ["tab"])
        self.assertEqual(ups, ["tab"])

    def test_click_is_blocked_after_stop_returns(self):
        with (
            patch.object(core, "_ui_is_running", return_value=False),
            patch.object(core, "_click_sendinput_abs_unchecked") as click,
        ):
            core._ui_stop_macro()
            self.assertFalse(core._click_sendinput_abs(960, 610))

        click.assert_not_called()


if __name__ == "__main__":
    unittest.main()
