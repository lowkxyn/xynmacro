import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

PYTHON_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PYTHON_DIR))

import xynmacro_core as core


DOT_RADIUS = 20


def render_ki_dot(dot_value, stroke_value):
    """A stand-in Ki dot: an orange disc with the black "1" stroke down the middle.

    Both are passed in as brightness values so the same dot can be rendered as a
    display pipeline would show it at different brightness levels — the stroke
    stays proportionally dark, which is what the adaptive check relies on.
    """
    image = np.zeros((200, 200, 3), dtype=np.uint8)
    center = 100
    ys, xs = np.mgrid[0:200, 0:200]
    disc = (xs - center) ** 2 + (ys - center) ** 2 <= DOT_RADIUS ** 2
    # Orange: blue channel stays low, so max(B,G,R) is the value we set.
    image[disc] = (0, int(dot_value * 0.55), dot_value)
    stroke_h = int(DOT_RADIUS * 1.3)
    image[center - stroke_h // 2:center + stroke_h // 2, center - 1:center + 2] = stroke_value
    return image


class KiAdaptiveBrightnessDetectionTests(unittest.TestCase):
    """The digit check reads the dark "1" inside the dot. With a fixed threshold
    it only passes at one brightness level; adaptive mode tracks the dot."""

    def setUp(self):
        self.adaptive = core.KI_V8_ADAPTIVE_BRIGHTNESS

    def tearDown(self):
        core.KI_V8_ADAPTIVE_BRIGHTNESS = self.adaptive

    def check(self, image):
        return core._ki_v8_check_vertical_one(image, 100, 100, DOT_RADIUS)

    def test_fixed_threshold_reads_the_digit_at_stock_brightness(self):
        core.KI_V8_ADAPTIVE_BRIGHTNESS = False
        self.assertTrue(self.check(render_ki_dot(dot_value=180, stroke_value=70)))

    def test_fixed_threshold_loses_the_digit_when_brightness_is_lifted(self):
        core.KI_V8_ADAPTIVE_BRIGHTNESS = False
        self.assertFalse(self.check(render_ki_dot(dot_value=210, stroke_value=100)))

    def test_adaptive_reads_the_digit_at_both_brightness_levels(self):
        core.KI_V8_ADAPTIVE_BRIGHTNESS = True
        self.assertTrue(self.check(render_ki_dot(dot_value=180, stroke_value=70)))
        self.assertTrue(self.check(render_ki_dot(dot_value=210, stroke_value=100)))

    def test_adaptive_keeps_the_fixed_threshold_as_a_floor(self):
        """A dim dot must not drag the threshold below KI_V8_DARK_THRESH, or an
        already-dark scene would start qualifying on noise."""
        core.KI_V8_ADAPTIVE_BRIGHTNESS = True
        dim = render_ki_dot(dot_value=100, stroke_value=80)
        # 100 * 0.55 = 55, below the 90 floor; the floor still admits the stroke.
        self.assertTrue(self.check(dim))


class KiDigitRejectionDiagnosticTests(unittest.TestCase):
    """A rejection has to record why. Without it the only evidence is a saved
    image, and the image looks fine to whoever is asked for it."""

    def setUp(self):
        self.adaptive = core.KI_V8_ADAPTIVE_BRIGHTNESS
        self.rejection = core._ki_v8_last_digit_rejection
        core.KI_V8_ADAPTIVE_BRIGHTNESS = False
        core._ki_v8_last_digit_rejection = None

    def tearDown(self):
        core.KI_V8_ADAPTIVE_BRIGHTNESS = self.adaptive
        core._ki_v8_last_digit_rejection = self.rejection

    def test_a_passing_check_records_nothing(self):
        core._ki_v8_check_vertical_one(
            render_ki_dot(dot_value=180, stroke_value=70), 100, 100, DOT_RADIUS
        )
        self.assertIsNone(core._ki_v8_last_digit_rejection)

    def test_a_lifted_capture_records_the_threshold_it_needed(self):
        core._ki_v8_check_vertical_one(
            render_ki_dot(dot_value=210, stroke_value=100), 100, 100, DOT_RADIUS
        )
        rejection = core._ki_v8_last_digit_rejection
        self.assertIsNotNone(rejection)
        self.assertEqual(rejection["threshold"], core.KI_V8_DARK_THRESH)
        # The gap between these two is the diagnosis: the stroke is there, it is
        # just brighter than the fixed threshold can see.
        self.assertGreater(rejection["needed"], rejection["threshold"])
        self.assertEqual(rejection["columns"], 0)
        self.assertFalse(rejection["adaptive"])


class KiAdaptiveBrightnessSettingTests(unittest.TestCase):
    def setUp(self):
        self.adaptive = core.KI_V8_ADAPTIVE_BRIGHTNESS

    def tearDown(self):
        core.KI_V8_ADAPTIVE_BRIGHTNESS = self.adaptive

    def test_default_is_off(self):
        self.assertFalse(core.DEFAULT_USER_SETTINGS["ki_adaptive_brightness"])

    @patch.object(core, "save_master_config")
    def test_toggle_is_a_user_setting(self, save_config):
        core._ui_apply_setting("ki_adaptive_brightness", True)
        self.assertTrue(core.KI_V8_ADAPTIVE_BRIGHTNESS)
        core._ui_apply_setting("ki_adaptive_brightness", False)
        self.assertFalse(core.KI_V8_ADAPTIVE_BRIGHTNESS)

    def test_settings_snapshot_exposes_the_toggle(self):
        core.KI_V8_ADAPTIVE_BRIGHTNESS = True
        self.assertTrue(core._ui_config_snapshot()["ki_adaptive_brightness"])

    def test_reset_restores_the_default(self):
        core.KI_V8_ADAPTIVE_BRIGHTNESS = True
        core.reset_user_settings_to_defaults()
        self.assertFalse(core.KI_V8_ADAPTIVE_BRIGHTNESS)


if __name__ == "__main__":
    unittest.main()
