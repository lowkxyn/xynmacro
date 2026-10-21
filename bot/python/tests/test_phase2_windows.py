from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import win32gui

import xynmacro_core as core
from test_progression_stall import _run_monitor


@pytest.mark.parametrize("name", ["HEALTH_BOX", "AGILITY_BOX", "PROG_SEARCH_BOX",
                                  "TRAINING_POINTS_BOX", "SENZU_HP_FILL_BOX",
                                  "GRAVITY_LABEL_BOX", "majin_vision.PANEL_BOX"])
def test_covering_rectangle_detects_each_reader_at_scaled_offset(monkeypatch, name):
    # Looked up at run time: the layout fixture sets these per test.
    owner, attribute = (core.majin_vision, "PANEL_BOX") if "." in name else (core, name)
    reference = getattr(owner, attribute)
    for name, value in dict(GAME_HWND=42, GAME_OFFSET_X=-1920, GAME_OFFSET_Y=100,
                            GAME_WIDTH=1280, GAME_HEIGHT=720).items():
        monkeypatch.setattr(core, name, value)
    if isinstance(reference, dict):
        x, y, w, h = (reference[key] for key in ("left", "top", "width", "height"))
    else:
        x, y, w, h = reference
    rect = (-1920 + (x + 1) * 2 / 3, 100 + (y + 1) * 2 / 3,
            -1920 + (x + w - 1) * 2 / 3, 100 + (y + h - 1) * 2 / 3)
    monkeypatch.setattr(core, "_windows_above_game", lambda: [(rect, "PiP")])
    assert core._game_window_covering_title() == "PiP"


def test_own_overlay_above_readers_does_not_block(monkeypatch):
    for name, value in dict(GAME_HWND=42, GAME_OFFSET_X=0, GAME_OFFSET_Y=0,
                            GAME_WIDTH=1920, GAME_HEIGHT=1080).items():
        monkeypatch.setattr(core, name, value)
    monkeypatch.setattr(core, "_windows_above_game", lambda: [((600, 0, 900, 32), "XynMacro")])
    assert core._game_window_covering_title() is None


def test_z_order_skips_fully_transparent_overlays_but_keeps_opaque_ones(monkeypatch):
    monkeypatch.setattr(core, "GAME_HWND", 42)
    monkeypatch.setattr(win32gui, "GetTopWindow", lambda _: 1)
    monkeypatch.setattr(win32gui, "GetWindow", lambda hwnd, _: {1: 2, 2: 42}[hwnd])
    monkeypatch.setattr(win32gui, "IsWindowVisible", lambda _: True)
    monkeypatch.setattr(win32gui, "IsIconic", lambda _: False)
    monkeypatch.setattr(win32gui, "GetWindowLong", lambda *_: core.win32con.WS_EX_LAYERED)
    monkeypatch.setattr(win32gui, "GetLayeredWindowAttributes",
                        lambda hwnd: (0, 0 if hwnd == 1 else 200, core.win32con.LWA_ALPHA))
    monkeypatch.setattr(win32gui, "GetWindowRect", lambda _: (1230, 820, 1920, 940))
    monkeypatch.setattr(win32gui, "GetWindowText", lambda _: "opaque overlay")
    monkeypatch.setattr(core._ctypes.windll, "dwmapi", SimpleNamespace(DwmGetWindowAttribute=lambda *_: 0))
    assert list(core._windows_above_game()) == [((1230, 820, 1920, 940), "opaque overlay")]


def test_overlay_appearing_during_run_stops_and_blocks_after_run_input(monkeypatch):
    monkeypatch.setattr(core, "_AFTER_ACTIONS_BLOCKED", False)
    cover = Mock(side_effect=[None, "PiP"])
    monkeypatch.setattr(core, "_game_window_covering_title", cover)
    _run_monitor(monkeypatch, [(10, {}), (10.25, {}), (10.5, {}), (11, {})])
    assert cover.call_count == 2
    assert core.UI_STOP_REQUESTED
    assert core._AFTER_ACTIONS_BLOCKED
    core._release_macro_input.assert_called_once()
    assert not core.PROGRESSION_COMPLETE_REQUESTED.is_set()


def test_periodic_cover_check_preserves_start_countdown_and_alt_tab_refocus(monkeypatch):
    cover = Mock(return_value=None)
    monkeypatch.setattr(core, "_game_window_covering_title", cover)
    _run_monitor(monkeypatch, [
        (10, {"CURRENT_TRAINING_STATE": None}),
        (11, {"CURRENT_TRAINING_STATE": "Health", "focused": False}),
        (12, {"focused": True}),
    ])
    cover.assert_called_once()
    assert not core.UI_STOP_REQUESTED
