import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


PYTHON_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PYTHON_DIR))

import xynmacro_core as core


class SenzuEmptyGravityTests(unittest.TestCase):
    def setUp(self):
        self.zero_on_empty = core.SENZU_ZERO_GRAVITY_ON_EMPTY
        self.senzu_status = core.SENZU_STATUS
        self.senzu_remaining = core.SENZU_REMAINING
        self.senzu_disabled = core.SENZU_DISABLED_FOR_RUN
        self.geometry = {"left": 0, "top": 0, "width": 1920, "height": 1080}

    def tearDown(self):
        core.SENZU_ZERO_GRAVITY_ON_EMPTY = self.zero_on_empty
        core.SENZU_STATUS = self.senzu_status
        core.SENZU_REMAINING = self.senzu_remaining
        core.SENZU_DISABLED_FOR_RUN = self.senzu_disabled

    @patch.object(core, "save_master_config")
    def test_toggle_is_user_configurable_and_defaults_on(self, save_config):
        self.assertTrue(core.DEFAULT_USER_SETTINGS["senzu_zero_gravity_on_empty"])
        self.assertIn("senzu_zero_gravity_on_empty", core._ui_config_snapshot())

        core._ui_apply_setting("senzu_zero_gravity_on_empty", False)

        self.assertFalse(core.SENZU_ZERO_GRAVITY_ON_EMPTY)
        save_config.assert_called_once_with()

    def test_htc_ignores_zero_gravity_fallback_without_clicking(self):
        with (
            patch.object(core, "_load_gravity_templates", return_value={value: object() for value in range(0, 101, 10)}),
            patch.object(core, "robust_move"),
            patch.object(core, "safe_sleep"),
            patch.object(core, "_read_gc_gravity", return_value=(None, 0.0, 0.0, False)),
            patch.object(core, "click_at") as click,
        ):
            self.assertTrue(core._cycle_gc_gravity_to_zero(object(), self.geometry))

        click.assert_not_called()

    def test_gc_cycles_forward_and_confirms_wrap_to_zero(self):
        reads = [
            (80, 1.0, 0.4, True),
            (90, 1.0, 0.4, True),
            (100, 1.0, 0.4, True),
            (0, 1.0, 0.4, True),
        ]
        with (
            patch.object(core, "_load_gravity_templates", return_value={value: object() for value in range(0, 101, 10)}),
            patch.object(core, "robust_move"),
            patch.object(core, "safe_sleep"),
            patch.object(core, "check_exit"),
            patch.object(core, "_read_gc_gravity", side_effect=reads),
            patch.object(core, "click_at") as click,
        ):
            self.assertTrue(core._cycle_gc_gravity_to_zero(object(), self.geometry))

        self.assertEqual(click.call_count, 3)

    def test_disabled_option_sends_no_gravity_input(self):
        core.SENZU_ZERO_GRAVITY_ON_EMPTY = False
        with (
            patch.object(core, "_focus_game_for_senzu") as focus,
            patch.object(core, "_cycle_gc_gravity_to_zero") as cycle,
        ):
            self.assertTrue(core._lower_gc_gravity_for_empty_senzu(object()))

        focus.assert_not_called()
        cycle.assert_not_called()

    def test_empty_stock_path_applies_fallback_before_resuming(self):
        core.SENZU_DISABLED_FOR_RUN = False
        assets = {"training": object()}
        operations = []
        with (
            patch.object(
                core,
                "_lower_gc_gravity_for_empty_senzu",
                side_effect=lambda *_args: operations.append("lower_gravity") or True,
            ) as lower,
            patch.object(
                core,
                "_resume_training_after_senzu",
                side_effect=lambda *_args: operations.append("resume_training") or True,
            ) as resume,
        ):
            self.assertTrue(
                core._resume_after_senzu_stock_empty(
                    Mock(), assets, bean_was_used=False
                )
            )

        lower.assert_called_once()
        resume.assert_called_once()
        self.assertEqual(operations, ["lower_gravity", "resume_training"])
        self.assertTrue(core.SENZU_DISABLED_FOR_RUN)
        self.assertEqual(core.SENZU_STATUS, "empty")

    def test_empty_preflight_applies_fallback_and_keeps_starting(self):
        core.SENZU_DISABLED_FOR_RUN = False
        assets = {
            name: object()
            for name in ("training", "game_menu", "inventory", "bean", "slot", "digits")
        }

        def empty_refill(*_args, **_kwargs):
            core.SENZU_STATUS = "empty"
            return False

        with (
            patch.object(core, "_senzu_assets", return_value=assets),
            patch.object(core, "_senzu_slot_has_bean", return_value=(False, 0.0)),
            patch.object(core, "_refill_senzu_slot", side_effect=empty_refill),
            patch.object(core, "_lower_gc_gravity_for_empty_senzu", return_value=True) as lower,
        ):
            self.assertTrue(core.ensure_senzu_ready(Mock()))

        lower.assert_called_once()
        self.assertTrue(core.SENZU_DISABLED_FOR_RUN)


if __name__ == "__main__":
    unittest.main()
