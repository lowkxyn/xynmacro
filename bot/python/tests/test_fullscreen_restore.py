"""Roblox restored from the taskbar comes back windowed even when it was
fullscreen, which shifts every scan region. F11 is a toggle, so the fix must
only fire when the window is measurably smaller than its monitor."""
from unittest.mock import patch

import xynmacro_core as core


def _monitor(width=2560, height=1600):
    return {"device": r"\.\DISPLAY1", "left": 0, "top": 0,
            "right": width, "bottom": height,
            "width": width, "height": height, "primary": True}


def test_already_fullscreen_is_left_alone():
    with patch.object(core, "update_game_window", return_value=True), \
            patch.object(core, "_monitor_info_for_window", return_value=_monitor()), \
            patch.object(core, "GAME_HWND", 1), \
            patch.object(core, "GAME_WIDTH", 2560), patch.object(core, "GAME_HEIGHT", 1600), \
            patch.object(core, "pydirectinput") as keys:
        assert core.ensure_game_fullscreen() is True
        keys.keyDown.assert_not_called()


def test_maximized_window_gets_f11():
    # A maximized window loses the title bar and borders, so it falls short.
    sizes = iter([(2544, 1561), (2560, 1600)])

    def _refresh():
        core.GAME_WIDTH, core.GAME_HEIGHT = next(sizes, (2560, 1600))
        return True

    with patch.object(core, "_monitor_info_for_window", return_value=_monitor()), \
            patch.object(core, "GAME_HWND", 1), \
            patch.object(core, "focus_game_window", return_value=True), \
            patch.object(core, "update_game_window", side_effect=_refresh), \
            patch.object(core, "pydirectinput") as keys:
        assert core.ensure_game_fullscreen(wait=2.0) is True
        keys.keyDown.assert_called_once_with("f11")


def test_unknown_monitor_is_never_guessed_at():
    with patch.object(core, "update_game_window", return_value=True), \
            patch.object(core, "_monitor_info_for_window", return_value=None), \
            patch.object(core, "GAME_HWND", 1), \
            patch.object(core, "pydirectinput") as keys:
        assert core.game_window_is_fullscreen() is None
        assert core.ensure_game_fullscreen() is False
        keys.keyDown.assert_not_called()


def test_no_window_means_no_input():
    with patch.object(core, "update_game_window", return_value=False), \
            patch.object(core, "pydirectinput") as keys:
        assert core.ensure_game_fullscreen() is False
        keys.keyDown.assert_not_called()
