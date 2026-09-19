import sys
import unittest
from pathlib import Path
from unittest.mock import patch, Mock


PYTHON_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PYTHON_DIR))

import xynmacro_core as core


class ShutdownPolicyTests(unittest.TestCase):
    def setUp(self):
        self.timer_patch = patch.object(core.threading, "Timer")
        self.timer = self.timer_patch.start()
        self.addCleanup(self.timer_patch.stop)
        core._shutdown_status = "idle"
        core._shutdown_timer = None
        self.shutdown_finished = core.SHUTDOWN_PC_WHEN_FINISHED
        self.game_action = core.AFTER_RUN_GAME_ACTION
        self.on_failure = core.AFTER_RUN_ON_FAILURE
        self.user_stop_latched = core._USER_STOP_LATCHED
        self.after_actions_blocked = core._AFTER_ACTIONS_BLOCKED
        core._USER_STOP_LATCHED = False
        core._AFTER_ACTIONS_BLOCKED = False

    def tearDown(self):
        core._shutdown_status = "idle"
        core._shutdown_timer = None
        core.SHUTDOWN_PC_WHEN_FINISHED = self.shutdown_finished
        core.AFTER_RUN_GAME_ACTION = self.game_action
        core.AFTER_RUN_ON_FAILURE = self.on_failure
        core._USER_STOP_LATCHED = self.user_stop_latched
        core._AFTER_ACTIONS_BLOCKED = self.after_actions_blocked

    def test_shutdown_policy_distinguishes_finish_failure_and_manual_stop(self):
        core.SHUTDOWN_PC_WHEN_FINISHED = True
        core.AFTER_RUN_ON_FAILURE = False

        self.assertTrue(core._should_run_after_actions("completed"))
        self.assertTrue(core._should_shutdown_pc("completed"))
        self.assertFalse(core._should_shutdown_pc("error"))
        self.assertFalse(core._should_shutdown_pc("stopped"))

        core.AFTER_RUN_ON_FAILURE = True
        self.assertTrue(core._should_run_after_actions("error"))
        self.assertTrue(core._should_shutdown_pc("error"))
        self.assertFalse(core._should_run_after_actions("stopped"))
        self.assertFalse(core._should_shutdown_pc("stopped"))
        self.assertFalse(core._should_run_after_actions("incomplete"))
        self.assertFalse(core._should_shutdown_pc("incomplete"))

    @patch("subprocess.run", return_value=Mock(returncode=0))
    def test_eligible_shutdown_waits_in_app_before_requesting_windows(self, run):
        core.SHUTDOWN_PC_WHEN_FINISHED = True
        core.AFTER_RUN_ON_FAILURE = False

        with patch.object(core.os, "name", "nt"):
            scheduled = core._schedule_pc_shutdown("completed", "Training order completed")

        self.assertTrue(scheduled)
        run.assert_not_called()
        self.assertTrue(core._shutdown_snapshot()["pending"])
        self.assertEqual(self.timer.call_args.args[0], 60)
        self.timer.return_value.start.assert_called_once()
        core._dispatch_pc_shutdown(core._shutdown_generation)
        command = run.call_args.args[0]
        self.assertTrue(command[0].lower().endswith("system32\\shutdown.exe"))
        self.assertEqual(command[1:4], ["/s", "/t", "0"])
        self.assertNotIn("/f", command)
        self.assertEqual(core._shutdown_snapshot()["status"], "requested")

    @patch("subprocess.run")
    def test_cancel_prevents_a_stale_timer_from_shutting_down(self, run):
        core.SHUTDOWN_PC_WHEN_FINISHED = True
        with patch.object(core.os, "name", "nt"):
            core._schedule_pc_shutdown("completed", "done")
        generation = core._shutdown_generation
        self.assertTrue(core._cancel_pc_shutdown()[0])
        core._dispatch_pc_shutdown(generation)
        run.assert_not_called()
        self.assertEqual(core._shutdown_snapshot()["status"], "cancelled")

    @patch("subprocess.run")
    def test_stop_cancels_pending_countdown_and_blocks_rescheduling(self, run):
        core.SHUTDOWN_PC_WHEN_FINISHED = True
        with patch.object(core.os, "name", "nt"), patch.object(core, "_ui_is_running", return_value=False):
            core._schedule_pc_shutdown("completed", "done")
            generation = core._shutdown_generation
            core._ui_stop_macro()
            self.assertFalse(core._schedule_pc_shutdown("completed", "late result"))
            core._dispatch_pc_shutdown(generation)
        run.assert_not_called()

    @patch("subprocess.run")
    def test_duplicate_countdown_and_unowned_cancel_are_noops(self, run):
        self.assertFalse(core._cancel_pc_shutdown()[0])
        core.SHUTDOWN_PC_WHEN_FINISHED = True
        with patch.object(core.os, "name", "nt"):
            self.assertTrue(core._schedule_pc_shutdown("completed", "done"))
            self.assertFalse(core._schedule_pc_shutdown("completed", "duplicate"))
        self.timer.assert_called_once()
        run.assert_not_called()

    def test_failed_and_uncertain_windows_requests_are_not_reported_as_success(self):
        for result, expected in [(Mock(returncode=5), "failed"), (OSError(), "failed"),
                                 (core.subprocess.TimeoutExpired("shutdown", 5), "unknown")]:
            with self.subTest(expected=expected):
                core._shutdown_status = "pending"
                with patch("subprocess.run") as run:
                    if isinstance(result, Exception):
                        run.side_effect = result
                    else:
                        run.return_value = result
                    core._dispatch_pc_shutdown(core._shutdown_generation)
                self.assertEqual(core._shutdown_snapshot()["status"], expected)

    @patch("subprocess.run")
    def test_start_cancels_countdown_even_if_game_is_missing(self, run):
        core.SHUTDOWN_PC_WHEN_FINISHED = True
        with patch.object(core.os, "name", "nt"):
            core._schedule_pc_shutdown("completed", "done")
        with patch.object(core, "_ui_is_running", return_value=False), patch.object(core, "update_game_window", return_value=False), patch.object(core, "restore_game_window", return_value=False):
            core._ui_start_macro()
        self.assertEqual(core._shutdown_snapshot()["status"], "cancelled")
        run.assert_not_called()

    @patch("subprocess.Popen")
    def test_ineligible_outcomes_never_launch_shutdown(self, popen):
        core.SHUTDOWN_PC_WHEN_FINISHED = True
        core.AFTER_RUN_ON_FAILURE = False

        self.assertFalse(core._schedule_pc_shutdown("error", "Detector failed"))
        self.assertFalse(core._schedule_pc_shutdown("stopped", "User requested stop"))
        popen.assert_not_called()

    def test_game_action_dispatch_is_independent_from_pc_shutdown(self):
        core.AFTER_RUN_GAME_ACTION = "main_menu"
        core.SHUTDOWN_PC_WHEN_FINISHED = False
        core.AFTER_RUN_ON_FAILURE = False

        with (
            patch.object(core, "_perform_after_run_game_action") as game_action,
            patch.object(core, "_schedule_pc_shutdown") as shutdown,
        ):
            self.assertTrue(core._run_after_actions("completed", "done"))

        game_action.assert_called_once_with("main_menu")
        shutdown.assert_called_once_with("completed", "done")

    def test_manual_stop_never_dispatches_after_actions(self):
        core.AFTER_RUN_GAME_ACTION = "close_game"
        core.SHUTDOWN_PC_WHEN_FINISHED = True
        core.AFTER_RUN_ON_FAILURE = True

        with (
            patch.object(core, "_perform_after_run_game_action") as game_action,
            patch.object(core, "_schedule_pc_shutdown") as shutdown,
        ):
            self.assertFalse(core._run_after_actions("stopped", "Run stopped"))

        game_action.assert_not_called()
        shutdown.assert_not_called()

    def test_game_action_dispatch_calls_each_real_handler(self):
        handlers = {
            "main_menu": "_after_run_go_to_main_menu",
            "close_game": "_after_run_close_game",
            "zero_gravity": "_after_run_set_zero_gravity",
        }
        for action, handler_name in handlers.items():
            with self.subTest(action=action):
                with patch.object(core, handler_name, return_value=True) as handler:
                    self.assertTrue(core._perform_after_run_game_action(action))
                handler.assert_called_once_with()

    def test_game_action_dispatch_handles_none_unknown_and_handler_error(self):
        self.assertTrue(core._perform_after_run_game_action("none"))
        self.assertFalse(core._perform_after_run_game_action("unknown"))
        with patch.object(
            core, "_after_run_close_game", side_effect=RuntimeError("close failed")
        ):
            self.assertFalse(core._perform_after_run_game_action("close_game"))

    def test_game_action_dispatch_restores_cursor_after_uncertain_failure(self):
        with (
            patch.object(core.win32api, "GetCursorPos", return_value=(321, 654)),
            patch.object(core, "_after_run_set_zero_gravity", return_value=False),
            patch.object(core._user32, "SetCursorPos") as restore_cursor,
        ):
            self.assertFalse(core._perform_after_run_game_action("zero_gravity"))

        restore_cursor.assert_called_once_with(321, 654)

    def test_close_game_does_not_move_a_cursor_it_never_used(self):
        with (
            patch.object(core.win32api, "GetCursorPos") as get_cursor,
            patch.object(core, "_after_run_close_game", return_value=True),
            patch.object(core._user32, "SetCursorPos") as move_cursor,
        ):
            self.assertTrue(core._perform_after_run_game_action("close_game"))

        get_cursor.assert_not_called()
        move_cursor.assert_not_called()

    def test_user_stop_latch_wins_over_late_error(self):
        core.AFTER_RUN_GAME_ACTION = "close_game"
        core.SHUTDOWN_PC_WHEN_FINISHED = True
        core.AFTER_RUN_ON_FAILURE = True
        core._begin_run_result()

        with patch.object(core, "_ui_is_running", return_value=True):
            core._ui_stop_macro()
        core._record_run_outcome("error", "late background failure")

        self.assertEqual(core._CURRENT_RUN_OUTCOME, "stopped")
        self.assertFalse(core._should_run_after_actions("error"))
        with (
            patch.object(core, "_perform_after_run_game_action") as game_action,
            patch.object(core, "_schedule_pc_shutdown") as shutdown,
        ):
            self.assertFalse(core._run_after_actions("error", "late background failure"))
        game_action.assert_not_called()
        shutdown.assert_not_called()

    def test_unstopped_background_monitor_blocks_after_actions(self):
        class StuckThread:
            def is_alive(self):
                return True

            def join(self, timeout):
                self.timeout = timeout

        previous_event = core._background_monitor_stop
        previous_thread = core._background_monitor_thread
        try:
            core._background_monitor_stop = core.threading.Event()
            core._background_monitor_thread = StuckThread()
            self.assertFalse(core._stop_background_game_monitor())
            self.assertTrue(core._AFTER_ACTIONS_BLOCKED)
            self.assertFalse(core._should_run_after_actions("completed"))

            core._begin_run_result()
            self.assertFalse(core._start_background_game_monitor())
            self.assertTrue(core._AFTER_ACTIONS_BLOCKED)
        finally:
            core._background_monitor_stop = previous_event
            core._background_monitor_thread = previous_thread

    def test_manual_stop_cancels_cleanup_input_already_in_progress(self):
        core._USER_STOP_LATCHED = True
        with (
            patch.object(core, "_tap_key_unchecked") as tap,
            patch.object(core, "_click_sendinput_abs") as click,
        ):
            with self.assertRaises(core.AfterRunCancelled):
                core._after_run_tap("m")
            with self.assertRaises(core.AfterRunCancelled):
                core._after_run_click_reference(100, 100, (0, 0, 1920, 1080))
        tap.assert_not_called()
        click.assert_not_called()

    def test_manual_stop_blocks_handler_focus_and_direct_gravity_input(self):
        core._USER_STOP_LATCHED = True
        with patch.object(core, "focus_game_window") as focus:
            with self.assertRaises(core.AfterRunCancelled):
                core._after_run_go_to_main_menu()
            with self.assertRaises(core.AfterRunCancelled):
                core._after_run_set_zero_gravity()
        focus.assert_not_called()

        with (
            patch.object(core, "_load_gravity_templates", return_value=[object()] * 11),
            patch.object(core._user32, "SetCursorPos") as move,
            patch.object(core, "_click_sendinput_abs") as click,
        ):
            with self.assertRaises(core.AfterRunCancelled):
                core._cycle_gc_gravity_to_zero(None, (0, 0, 1920, 1080), after_run=True)
        move.assert_not_called()
        click.assert_not_called()

    def test_manual_stop_mid_gravity_cycle_prevents_later_input(self):
        core._USER_STOP_LATCHED = False

        def latch_stop(*_args):
            core._USER_STOP_LATCHED = True

        with (
            patch.object(core, "_load_gravity_templates", return_value=[object()] * 11),
            patch.object(core, "_read_gc_gravity", return_value=(100, 1.0, 0.0, True)),
            patch.object(core._user32, "SetCursorPos") as move,
            patch.object(core, "_click_sendinput_abs", side_effect=latch_stop) as click,
        ):
            with self.assertRaises(core.AfterRunCancelled):
                core._cycle_gc_gravity_to_zero(None, (0, 0, 1920, 1080), after_run=True)

        click.assert_called_once()
        self.assertEqual(move.call_count, 1)


if __name__ == "__main__":
    unittest.main()
