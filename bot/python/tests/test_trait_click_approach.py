"""The trait click lands on the right pixel and still does nothing on some setups.
An external auto clicker at that same pixel works, so the coordinates are fine and
the problem is that the game does not agree where the cursor is."""
import sys
from pathlib import Path
from unittest.mock import MagicMock, call, patch

PYTHON_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PYTHON_DIR))

import xynmacro_core as core


MONITOR = {"left": 0, "top": 0, "width": 1920, "height": 1080}


class TestApproachCursor:
    def test_passes_through_the_window_centre_before_the_target(self):
        with patch.object(core, "robust_move") as move, \
             patch.object(core, "safe_sleep"):
            core.approach_cursor(1500, 900, MONITOR)
        assert move.call_args_list == [
            call(960, 540, nudge=True),
            call(1500, 900, nudge=True),
        ]

    def test_uses_the_monitor_origin_not_the_screen_origin(self):
        # On a second monitor the centre is offset, and a move to 960,540 would
        # leave the game window entirely.
        second = {"left": 1920, "top": 0, "width": 1920, "height": 1080}
        with patch.object(core, "robust_move") as move, \
             patch.object(core, "safe_sleep"):
            core.approach_cursor(2500, 300, second)
        assert move.call_args_list[0] == call(2880, 540, nudge=True)

    def test_settles_after_each_step(self):
        # Without a pause both moves can land inside one frame, which is the same
        # as not moving through the centre at all.
        with patch.object(core, "robust_move"), patch.object(core, "safe_sleep") as sleep:
            core.approach_cursor(100, 100, MONITOR)
        assert sleep.call_count == 2
        assert sleep.call_args_list[0] == call(core.TRAIT_CLICK_APPROACH_PAUSE_SEC)


class TestApproachSetting:
    def setup_method(self):
        self.enabled = core.TRAIT_CLICK_APPROACH_ENABLED

    def teardown_method(self):
        core.TRAIT_CLICK_APPROACH_ENABLED = self.enabled

    def test_default_is_off(self):
        assert core.DEFAULT_USER_SETTINGS["trait_click_approach"] is False

    def test_toggle_is_a_user_setting(self):
        with patch.object(core, "save_master_config"):
            core._ui_apply_setting("trait_click_approach", True)
            assert core.TRAIT_CLICK_APPROACH_ENABLED is True
            core._ui_apply_setting("trait_click_approach", False)
            assert core.TRAIT_CLICK_APPROACH_ENABLED is False

    def test_snapshot_exposes_the_toggle(self):
        core.TRAIT_CLICK_APPROACH_ENABLED = True
        assert core._ui_config_snapshot()["trait_click_approach"] is True

    def test_reset_restores_the_default(self):
        core.TRAIT_CLICK_APPROACH_ENABLED = True
        core.reset_user_settings_to_defaults()
        assert core.TRAIT_CLICK_APPROACH_ENABLED is False


class TestRobustMoveNudge:
    def _move(self, **kwargs):
        user32 = MagicMock()
        with patch.object(core, "_user32", user32), \
             patch.object(core, "check_exit"), \
             patch.object(core, "_USER_STOP_LATCHED", False), \
             patch("time.sleep"):
            core.robust_move(400, 300, **kwargs)
        return user32

    def test_sends_a_relative_nudge_when_asked(self):
        user32 = self._move(nudge=True)
        # SetCursorPos, then nudge out, nudge back, then the absolute move.
        user32.SetCursorPos.assert_called_once_with(400, 300)
        assert user32.SendInput.call_count == 3

    def test_leaves_existing_callers_alone_by_default(self):
        # Gravity parking has always moved without a nudge. Changing that would
        # reach everyone, including people with the toggle off.
        assert self._move().SendInput.call_count == 1
