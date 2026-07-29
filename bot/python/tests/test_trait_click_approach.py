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
        assert move.call_args_list == [call(960, 540), call(1500, 900)]

    def test_uses_the_monitor_origin_not_the_screen_origin(self):
        # On a second monitor the centre is offset, and a move to 960,540 would
        # leave the game window entirely.
        second = {"left": 1920, "top": 0, "width": 1920, "height": 1080}
        with patch.object(core, "robust_move") as move, \
             patch.object(core, "safe_sleep"):
            core.approach_cursor(2500, 300, second)
        assert move.call_args_list[0] == call(2880, 540)

    def test_settles_after_each_step(self):
        # Without a pause both moves can land inside one frame, which is the same
        # as not moving through the centre at all.
        with patch.object(core, "robust_move"), patch.object(core, "safe_sleep") as sleep:
            core.approach_cursor(100, 100, MONITOR)
        assert sleep.call_count == 2
        assert sleep.call_args_list[0] == call(core.TRAIT_CLICK_APPROACH_PAUSE_SEC)


class TestRobustMoveNudge:
    def test_sends_a_relative_nudge_so_raw_input_notices(self):
        user32 = MagicMock()
        with patch.object(core, "_user32", user32), \
             patch.object(core, "check_exit"), \
             patch.object(core, "_USER_STOP_LATCHED", False), \
             patch("time.sleep"):
            core.robust_move(400, 300)
        # SetCursorPos, then nudge out, nudge back, then the absolute move.
        user32.SetCursorPos.assert_called_once_with(400, 300)
        assert user32.SendInput.call_count == 3
