import sys
import unittest
from pathlib import Path
from unittest import mock

import numpy as np


PYTHON_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PYTHON_DIR))

import xynmacro_core as core


def solid_bgr(color, width=72, height=16):
    return np.full((height, width, 3), color, dtype=np.uint8)


class SenzuHpDetectionTests(unittest.TestCase):
    def test_red_is_critical_but_orange_is_not(self):
        self.assertTrue(core._hp_fill_is_critical(solid_bgr((0, 0, 255))))
        self.assertFalse(core._hp_fill_is_critical(solid_bgr((0, 165, 255))))

    def test_dark_hud_background_is_not_health_fill(self):
        background = solid_bgr((45, 45, 45))
        self.assertFalse(core._hp_fill_is_critical(background))
        self.assertFalse(core._hp_fill_is_green(background))

    def test_background_does_not_change_the_colour_vote(self):
        frame = solid_bgr((45, 45, 45))
        frame[:, :44] = (0, 0, 255)
        self.assertTrue(core._hp_fill_is_critical(frame))

    def test_green_is_required_before_recovery_resumes(self):
        self.assertTrue(core._hp_fill_is_green(solid_bgr((0, 255, 0))))
        self.assertFalse(core._hp_fill_is_green(solid_bgr((0, 165, 255))))
        self.assertFalse(core._hp_fill_is_green(solid_bgr((0, 0, 255))))

    def test_half_bean_wait_accepts_orange_and_rejects_red(self):
        # Half beans only need HP to leave critical red. This path used to call an
        # undefined helper and crashed the Senzu thread with NameError.
        with mock.patch.object(core, "_senzu_abort_requested", return_value=False), \
             mock.patch.object(core.time, "sleep"):
            with mock.patch.object(core, "_grab_reference_box",
                                   return_value=solid_bgr((0, 165, 255))):
                self.assertTrue(core._wait_for_green_hp(None, 1.0, "half"))
                self.assertFalse(core._wait_for_green_hp(None, 0.0, "full"))
            with mock.patch.object(core, "_grab_reference_box",
                                    return_value=solid_bgr((0, 0, 255))):
                self.assertFalse(core._wait_for_green_hp(None, 0.05, "half"))
            with mock.patch.object(core, "_grab_reference_box",
                                   return_value=solid_bgr((45, 45, 45))):
                self.assertFalse(core._wait_for_green_hp(None, 0.05, "half"))


if __name__ == "__main__":
    unittest.main()
