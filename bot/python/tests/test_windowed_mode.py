"""Windowed Mode sizes Roblox to a 1920x1080 client without touching the display
resolution. The scan regions are authored against exactly that client size, so
anything else has to be refused rather than approximated."""
from unittest.mock import patch

import xynmacro_core as core


def _monitor(width=2560, height=1600, taskbar=48):
    return {"device": r"\.\DISPLAY1", "left": 0, "top": 0,
            "right": width, "bottom": height,
            "width": width, "height": height,
            "work_left": 0, "work_top": 0,
            "work_width": width, "work_height": height - taskbar,
            "primary": True}


class TestTargetBounds:
    def test_centres_the_client_inside_the_work_area(self):
        # 1920x1080 client + a 16x39 frame on a 2560x1600 panel with a 48px taskbar.
        bounds = core._windowed_target_bounds(_monitor(), 16, 39)
        assert bounds == ((2560 - 1936) // 2, (1552 - 1119) // 2, 1936, 1119)

    def test_never_places_the_window_under_the_taskbar(self):
        x, y, width, height = core._windowed_target_bounds(_monitor(), 16, 39)
        monitor = _monitor()
        assert y >= monitor["work_top"]
        assert y + height <= monitor["work_top"] + monitor["work_height"]
        assert x >= monitor["work_left"]
        assert x + width <= monitor["work_left"] + monitor["work_width"]

    def test_exactly_1080p_display_cannot_fit_a_titled_window(self):
        # The frame makes the window taller than the screen, so there is no way to
        # show a full 1920x1080 client. These users want fullscreen or Set 1080p.
        assert core._windowed_target_bounds(_monitor(1920, 1080), 16, 39) is None

    def test_smaller_display_is_refused_rather_than_scaled(self):
        assert core._windowed_target_bounds(_monitor(1366, 768), 16, 39) is None

    def test_borderless_frame_still_fits_an_exact_1080p_display(self):
        # No frame at all (borderless) is the one way 1920x1080 fits a 1080p panel,
        # but only when the taskbar is hidden too.
        assert core._windowed_target_bounds(
            _monitor(1920, 1080, taskbar=0), 0, 0
        ) == (0, 0, 1920, 1080)

    def test_secondary_monitor_with_negative_origin_keeps_its_offset(self):
        monitor = {"device": r"\.\DISPLAY2", "left": -2560, "top": -200,
                   "right": 0, "bottom": 1240,
                   "width": 2560, "height": 1440,
                   "work_left": -2560, "work_top": -200,
                   "work_width": 2560, "work_height": 1440, "primary": False}
        x, y, width, height = core._windowed_target_bounds(monitor, 16, 39)
        assert (x, y) == (-2560 + (2560 - 1936) // 2, -200 + (1440 - 1119) // 2)
        assert (width, height) == (1936, 1119)

    def test_missing_work_area_falls_back_to_the_monitor_rect(self):
        monitor = {"left": 0, "top": 0, "right": 2560, "bottom": 1600,
                   "width": 2560, "height": 1600}
        assert core._windowed_target_bounds(monitor, 16, 39) == (312, 240, 1936, 1119)

    def test_no_monitor_means_no_guess(self):
        assert core._windowed_target_bounds(None, 16, 39) is None


class TestEnsureGameWindowed:
    def test_reports_why_a_small_display_cannot_be_used(self):
        with patch.object(core, "update_game_window", return_value=True), \
                patch.object(core, "GAME_HWND", 1), \
                patch.object(core, "_monitor_info_for_window", return_value=_monitor(1920, 1080)), \
                patch.object(core, "game_window_is_fullscreen", return_value=False), \
                patch.object(core, "_game_window_frame_padding", return_value=(16, 39)), \
                patch.object(core, "_move_game_window") as move:
            ok, message = core.ensure_game_windowed(wait=0.1)
        assert ok is False
        assert "too small" in message
        move.assert_not_called()

    def test_leaves_fullscreen_before_sizing(self):
        # F11 is a toggle, so it may only be pressed when really fullscreen.
        states = iter([True, False, False, False])

        with patch.object(core, "update_game_window", return_value=True), \
                patch.object(core, "GAME_HWND", 1), \
                patch.object(core, "GAME_WIDTH", 1920), patch.object(core, "GAME_HEIGHT", 1080), \
                patch.object(core, "_monitor_info_for_window", return_value=_monitor()), \
                patch.object(core, "game_window_is_fullscreen", side_effect=lambda: next(states, False)), \
                patch.object(core, "focus_game_window", return_value=True), \
                patch.object(core, "_game_window_frame_padding", return_value=(16, 39)), \
                patch.object(core, "_move_game_window", return_value=True), \
                patch.object(core, "pydirectinput") as keys:
            ok, _ = core.ensure_game_windowed(wait=1.0)
        assert ok is True
        keys.keyDown.assert_called_once_with("f11")

    def test_already_windowed_is_not_sent_f11(self):
        with patch.object(core, "update_game_window", return_value=True), \
                patch.object(core, "GAME_HWND", 1), \
                patch.object(core, "GAME_WIDTH", 1920), patch.object(core, "GAME_HEIGHT", 1080), \
                patch.object(core, "_monitor_info_for_window", return_value=_monitor()), \
                patch.object(core, "game_window_is_fullscreen", return_value=False), \
                patch.object(core, "_game_window_frame_padding", return_value=(16, 39)), \
                patch.object(core, "_move_game_window", return_value=True), \
                patch.object(core, "pydirectinput") as keys:
            ok, _ = core.ensure_game_windowed(wait=1.0)
        assert ok is True
        keys.keyDown.assert_not_called()

    def test_wrong_final_size_is_reported_not_assumed(self):
        with patch.object(core, "update_game_window", return_value=True), \
                patch.object(core, "GAME_HWND", 1), \
                patch.object(core, "GAME_WIDTH", 1600), patch.object(core, "GAME_HEIGHT", 900), \
                patch.object(core, "_monitor_info_for_window", return_value=_monitor()), \
                patch.object(core, "game_window_is_fullscreen", return_value=False), \
                patch.object(core, "_game_window_frame_padding", return_value=(16, 39)), \
                patch.object(core, "_move_game_window", return_value=True):
            ok, message = core.ensure_game_windowed(wait=0.3)
        assert ok is False
        assert "1600x900" in message

    def test_closed_game_is_reported_before_any_input(self):
        with patch.object(core, "update_game_window", return_value=False), \
                patch.object(core, "pydirectinput") as keys:
            ok, message = core.ensure_game_windowed(wait=0.1)
        assert ok is False
        assert "Open Roblox" in message
        keys.keyDown.assert_not_called()

    def test_unmeasurable_frame_stops_before_moving_the_window(self):
        with patch.object(core, "update_game_window", return_value=True), \
                patch.object(core, "GAME_HWND", 1), \
                patch.object(core, "_monitor_info_for_window", return_value=_monitor()), \
                patch.object(core, "game_window_is_fullscreen", return_value=False), \
                patch.object(core, "_game_window_frame_padding", return_value=None), \
                patch.object(core, "_move_game_window") as move:
            ok, message = core.ensure_game_windowed(wait=0.1)
        assert ok is False
        assert "frame" in message
        move.assert_not_called()


def test_windowed_mode_is_off_by_default():
    # It only fits on displays larger than 1080p, so it must be opt-in.
    assert core.DEFAULT_USER_SETTINGS["windowed_mode_on_start"] is False
