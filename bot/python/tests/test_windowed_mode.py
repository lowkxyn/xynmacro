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


class TestFallbackBounds:
    """Accepting an undersized window is opt-in, and only after a warning: the
    client ends up scaled, and usually skewed, which breaks every template."""

    def test_fills_the_work_area_when_1080p_will_not_fit(self):
        # 1920x1080 panel, 48px taskbar, 16x39 frame: the client loses the taskbar
        # and the title bar, so it is 1904x993 rather than 1920x1080.
        x, y, width, height = core._windowed_fallback_bounds(_monitor(1920, 1080), 16, 39)
        assert (width, height) == (1920, 1032)
        assert (width - 16, height - 39) == (1904, 993)
        assert (x, y) == (0, 0)

    def test_never_grows_past_the_reference_size(self):
        # A big display fits a real 1920x1080 window, so the fallback must not
        # stretch the client — the regions are authored for exactly that size.
        assert core._windowed_fallback_bounds(_monitor(), 16, 39) == (
            (2560 - 1936) // 2, (1552 - 1119) // 2, 1936, 1119
        )

    def test_refuses_when_the_frame_alone_fills_the_display(self):
        assert core._windowed_fallback_bounds(_monitor(40, 40, taskbar=0), 60, 60) is None
        assert core._windowed_fallback_bounds(None, 16, 39) is None


class TestUndersizedFallback:
    """The refusal has to be distinguishable from every other one, because it is
    the only one the UI can offer a way past."""

    def test_the_refusal_is_tagged_so_the_ui_can_offer_the_warning(self):
        core.WINDOWED_BLOCK_CODE = None
        with patch.object(core, "update_game_window", return_value=True), \
                patch.object(core, "GAME_HWND", 1), \
                patch.object(core, "_monitor_info_for_window", return_value=_monitor(1920, 1080)), \
                patch.object(core, "game_window_is_fullscreen", return_value=False), \
                patch.object(core, "_game_window_frame_padding", return_value=(16, 39)), \
                patch.object(core, "_move_game_window"):
            ok, _ = core.ensure_game_windowed(wait=0.1)
        assert ok is False
        assert core.WINDOWED_BLOCK_CODE == "windowed_too_small"

    def test_an_unrelated_refusal_is_not_tagged(self):
        core.WINDOWED_BLOCK_CODE = "windowed_too_small"   # stale from a previous call
        with patch.object(core, "update_game_window", return_value=False):
            ok, _ = core.ensure_game_windowed(wait=0.1)
        assert ok is False
        assert core.WINDOWED_BLOCK_CODE is None

    def test_with_consent_an_undersized_client_is_accepted(self):
        # 1920x1080 panel, 48px taskbar, 16x39 frame -> a 1904x993 client.
        with patch.object(core, "update_game_window", return_value=True), \
                patch.object(core, "GAME_HWND", 1), \
                patch.object(core, "GAME_WIDTH", 1904), patch.object(core, "GAME_HEIGHT", 993), \
                patch.object(core, "_monitor_info_for_window", return_value=_monitor(1920, 1080)), \
                patch.object(core, "game_window_is_fullscreen", return_value=False), \
                patch.object(core, "_game_window_frame_padding", return_value=(16, 39)), \
                patch.object(core, "_move_game_window", return_value=True) as move:
            ok, message = core.ensure_game_windowed(wait=0.3, allow_fallback=True)
        assert ok is True
        assert "1904x993" in message
        assert "scaled" in message
        move.assert_called_once()

    def test_consent_does_not_excuse_a_size_nobody_asked_for(self):
        # Windows put the window somewhere else entirely — that is a failure even
        # when an undersized result was allowed.
        with patch.object(core, "update_game_window", return_value=True), \
                patch.object(core, "GAME_HWND", 1), \
                patch.object(core, "GAME_WIDTH", 800), patch.object(core, "GAME_HEIGHT", 600), \
                patch.object(core, "_monitor_info_for_window", return_value=_monitor(1920, 1080)), \
                patch.object(core, "game_window_is_fullscreen", return_value=False), \
                patch.object(core, "_game_window_frame_padding", return_value=(16, 39)), \
                patch.object(core, "_move_game_window", return_value=True):
            ok, message = core.ensure_game_windowed(wait=0.3, allow_fallback=True)
        assert ok is False
        assert "800x600" in message


class TestFindDbogWindow:
    def test_find_dbog_window_accepts_standard_sub_800x600_window(self):
        """Standard windowed Roblox at 800x600 outer size gives ~800x599 or 784x561 client."""
        import ctypes

        class _MockUser32:
            def IsWindowVisible(self, hwnd):
                return True
            def GetWindowTextLengthW(self, hwnd):
                return 6
            def GetWindowTextW(self, hwnd, buf, maxlen):
                buf.value = "Roblox"
                return 6
            def GetClientRect(self, hwnd, rect_ref):
                rect_ref._obj.left = 0
                rect_ref._obj.top = 0
                rect_ref._obj.right = 800
                rect_ref._obj.bottom = 599
                return 1
            def ClientToScreen(self, hwnd, pt_ref):
                pt_ref._obj.x = 100
                pt_ref._obj.y = 100
                return 1
            def EnumWindows(self, cb, lparam):
                cb(1234, 0)
                return 1

        with patch("ctypes.windll.user32", _MockUser32()), \
                patch.object(core, "_is_supported_roblox_window", return_value=True):
            hwnd, rect = core.find_dbog_window()
        assert hwnd == 1234
        assert rect == (100, 100, 800, 599)

