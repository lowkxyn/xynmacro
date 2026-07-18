import sys
import unittest
from pathlib import Path
from unittest.mock import patch


PYTHON_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PYTHON_DIR))

import xmacro_core as core


class SettingsContractTests(unittest.TestCase):
    def setUp(self):
        self.start_delay = core.START_DELAY
        self.senzu_slot = core.SENZU_SLOT
        self.gravity_target = core.GC_GRAVITY_TARGET_G
        self.prevent_sleep = core.PREVENT_SLEEP_WHILE_RUNNING
        self.no_yellow_fallback = core.NO_YELLOW_FALLBACK_ENABLED

    def tearDown(self):
        core.START_DELAY = self.start_delay
        core.SENZU_SLOT = self.senzu_slot
        core.GC_GRAVITY_TARGET_G = self.gravity_target
        core.PREVENT_SLEEP_WHILE_RUNNING = self.prevent_sleep
        core.NO_YELLOW_FALLBACK_ENABLED = self.no_yellow_fallback

    @patch.object(core, "save_master_config")
    def test_backend_normalizes_numeric_settings(self, save_config):
        core._ui_apply_setting("start_delay_sec", -2)
        core._ui_apply_setting("senzu_slot", 2.9)

        self.assertEqual(core.START_DELAY, 0.0)
        self.assertEqual(core.SENZU_SLOT, 2)
        self.assertEqual(save_config.call_count, 2)

    @patch.object(core, "save_master_config")
    def test_gravity_steps_and_sleep_toggle_are_user_settings(self, save_config):
        core._ui_apply_setting("gc_gravity_target_g", 100)
        core._ui_apply_setting("prevent_sleep_while_running", False)

        self.assertEqual(core.GC_GRAVITY_TARGET_G, 100)
        self.assertFalse(core.PREVENT_SLEEP_WHILE_RUNNING)
        with self.assertRaises(ValueError):
            core._ui_apply_setting("gc_gravity_target_g", 37)

        self.assertEqual(save_config.call_count, 2)

    @patch.object(core, "save_master_config")
    def test_internal_settings_are_not_user_settable(self, save_config):
        for key in (
            "show_debug_hud",
            "yellow_sample_interval_sec",
            "manual_next_debounce_sec",
            "enable_health_minigame",
            "enable_physical_minigame",
            "enable_ki_minigame",
            "agility_per_letter_timeout_sec",
            "agility_fail_backoff_sec",
            "mouse_method",
            "ki_v8_v2_contrast_click",
        ):
            with self.subTest(key=key):
                with self.assertRaisesRegex(ValueError, "Unknown key"):
                    core._ui_apply_setting(key, 1)

        save_config.assert_not_called()

    @patch.object(core, "save_master_config")
    def test_invalid_detector_modes_are_rejected(self, save_config):
        for key in ("health_mode", "agility_mode", "ki_v8_mode"):
            with self.subTest(key=key):
                with self.assertRaises(ValueError):
                    core._ui_apply_setting(key, "broken")

        save_config.assert_not_called()

    @patch.object(core, "save_master_config")
    def test_unshipped_agility_v3_is_not_user_selectable(self, save_config):
        with self.assertRaises(ValueError):
            core._ui_apply_setting("agility_mode", "v3")
        save_config.assert_not_called()

    def test_ui_state_exposes_only_supported_settings(self):
        config = core._ui_config_snapshot()

        self.assertIn("start_delay_sec", config)
        self.assertIn("gc_gravity_target_g", config)
        self.assertIn("prevent_sleep_while_running", config)
        self.assertIn("senzu_recovery_timeout_sec", config)
        self.assertNotIn("mouse_method", config)
        self.assertNotIn("show_debug_hud", config)

    def test_reset_restores_no_yellow_fallback_default(self):
        core.NO_YELLOW_FALLBACK_ENABLED = True
        core.reset_user_settings_to_defaults()

        self.assertFalse(core.NO_YELLOW_FALLBACK_ENABLED)


if __name__ == "__main__":
    unittest.main()
