import sys
import unittest
from pathlib import Path
from unittest.mock import patch


PYTHON_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PYTHON_DIR))

import xmacro_core as core


class CalibrationCancelTests(unittest.TestCase):
    def test_button_cancel_invalidates_result_returned_by_open_overlay(self):
        overrides = {}

        def cancel_then_return(_prompt):
            core._ui_calibrate_button_cancel()
            return (123, 456)

        with (
            patch.multiple(
                core,
                BUTTON_CALIBRATION_WAITING="Health",
                BUTTON_CALIBRATION_GENERATION=7,
                USER_BUTTON_OVERRIDES=overrides,
            ),
            patch.object(core, "_capture_click_via_overlay", side_effect=cancel_then_return),
            patch.object(core, "save_button_overrides") as save,
        ):
            core._run_button_calibration("Health", 7)
            waiting = core.BUTTON_CALIBRATION_WAITING

        self.assertEqual(overrides, {})
        self.assertIsNone(waiting)
        save.assert_not_called()

    def test_region_cancel_invalidates_result_returned_by_open_overlay(self):
        overrides = {}

        def cancel_then_return(_prompt):
            core._ui_calibrate_region_cancel()
            return {"top": 1, "left": 2, "width": 30, "height": 40}

        with (
            patch.multiple(
                core,
                REGION_CALIBRATION_WAITING="health_box",
                REGION_CALIBRATION_GENERATION=11,
                USER_REGION_OVERRIDES=overrides,
            ),
            patch.object(core, "_capture_region_via_overlay", side_effect=cancel_then_return),
            patch.object(core, "save_region_overrides") as save,
        ):
            core._run_region_calibration("health_box", 11)
            waiting = core.REGION_CALIBRATION_WAITING

        self.assertEqual(overrides, {})
        self.assertIsNone(waiting)
        save.assert_not_called()

    def test_old_button_worker_cannot_clear_new_calibration(self):
        with (
            patch.multiple(
                core,
                BUTTON_CALIBRATION_WAITING="Health",
                BUTTON_CALIBRATION_GENERATION=8,
                USER_BUTTON_OVERRIDES={},
            ),
            patch.object(core, "_capture_click_via_overlay", return_value=(123, 456)),
            patch.object(core, "save_button_overrides") as save,
        ):
            core._run_button_calibration("Health", 7)
            waiting = core.BUTTON_CALIBRATION_WAITING

        self.assertEqual(waiting, "Health")
        save.assert_not_called()


if __name__ == "__main__":
    unittest.main()
