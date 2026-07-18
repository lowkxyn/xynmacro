import sys
import unittest
from pathlib import Path
from unittest.mock import call, patch


PYTHON_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PYTHON_DIR))

import xmacro_core as core


class PowerLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.prevent_sleep = core.PREVENT_SLEEP_WHILE_RUNNING

    def tearDown(self):
        core.PREVENT_SLEEP_WHILE_RUNNING = self.prevent_sleep

    def test_sleep_hold_releases_when_controller_errors(self):
        core.PREVENT_SLEEP_WHILE_RUNNING = True
        with (
            patch.object(core, "_set_thread_sleep_hold") as sleep_hold,
            patch.object(core, "run_master_controller", side_effect=RuntimeError("test")),
            patch.object(core, "_stop_background_game_monitor"),
            patch.object(core, "_record_run_outcome"),
            patch.object(core, "_finalize_run_result"),
        ):
            core._run_macro_safe()

        self.assertEqual(sleep_hold.call_args_list, [call(True), call(False)])

    def test_disabled_setting_never_requests_a_sleep_hold(self):
        core.PREVENT_SLEEP_WHILE_RUNNING = False
        with (
            patch.object(core, "_set_thread_sleep_hold") as sleep_hold,
            patch.object(core, "run_master_controller"),
            patch.object(core, "_stop_background_game_monitor"),
            patch.object(core, "_finalize_run_result"),
        ):
            core._run_macro_safe()

        sleep_hold.assert_not_called()


if __name__ == "__main__":
    unittest.main()
