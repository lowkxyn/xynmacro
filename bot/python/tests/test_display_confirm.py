"""Auto-revert is the safety net for a mode the monitor cannot display.

If it only lived in the WebView, an unreadable screen would leave nothing to
click and no way back, so these tests pin the timer to the backend.
"""
import time
from unittest.mock import patch

import xynmacro_core as core


def _armed():
    return core._display_confirm_state()["pending"]


def test_confirming_cancels_the_revert():
    with patch.object(core, "display_revert_resolution") as revert:
        core._arm_display_confirm(1.5)
        assert _armed()
        ok, _ = core.display_keep_resolution()
        assert ok
        assert not _armed()
        time.sleep(2.0)
        revert.assert_not_called()


def test_silence_reverts():
    with patch.object(core, "display_revert_resolution", return_value=(True, 0)) as revert, \
            patch.object(core, "update_game_window", return_value=True):
        core._arm_display_confirm(0.4)
        time.sleep(1.0)
        revert.assert_called_once()
        assert not _armed()


def test_confirming_nothing_is_not_an_error():
    core._cancel_display_confirm()
    ok, msg = core.display_keep_resolution()
    assert ok is False
    assert "Nothing waiting" in msg


def test_arming_twice_leaves_one_timer():
    with patch.object(core, "display_revert_resolution", return_value=(True, 0)) as revert, \
            patch.object(core, "update_game_window", return_value=True):
        core._arm_display_confirm(0.4)
        core._arm_display_confirm(0.4)
        time.sleep(1.0)
        assert revert.call_count == 1


def test_remaining_seconds_count_down():
    core._arm_display_confirm(5)
    first = core._display_confirm_state()["seconds_remaining"]
    time.sleep(0.5)
    second = core._display_confirm_state()["seconds_remaining"]
    core._cancel_display_confirm()
    assert 0 < second < first <= 5
