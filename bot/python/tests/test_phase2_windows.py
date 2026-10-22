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


def test_brief_popup_pauses_input_then_clears_without_stopping(monkeypatch):
    monkeypatch.setattr(core, "_AFTER_ACTIONS_BLOCKED", False)
    monkeypatch.setattr(core, "_GAME_COVERED_SINCE", None)
    monkeypatch.setattr(core, "_set_charge_hold", Mock())
    cover = Mock(side_effect=[None, "Toast", "Toast", None])
    monkeypatch.setattr(core, "_game_window_covering_title", cover)
    # Focus is lost while covered; the check keeps running so the pause can clear.
    _run_monitor(monkeypatch, [(10, {}), (11, {}), (12, {"focused": False}), (13, {})])
    assert cover.call_count == 4
    assert core._GAME_COVERED_SINCE is None
    assert not core.UI_STOP_REQUESTED and not core._AFTER_ACTIONS_BLOCKED
    core._set_charge_hold.assert_called_once_with(False)


def test_window_left_over_roblox_still_stops_after_give_up_time(monkeypatch):
    monkeypatch.setattr(core, "_AFTER_ACTIONS_BLOCKED", False)
    monkeypatch.setattr(core, "_GAME_COVERED_SINCE", None)
    monkeypatch.setattr(core, "_set_charge_hold", Mock())
    monkeypatch.setattr(core, "_game_window_covering_title", Mock(return_value="PiP"))
    give_up = 10 + core.GAME_COVERED_GIVE_UP_SEC
    _run_monitor(monkeypatch, [(10, {}), (give_up - 1, {}), (give_up, {}), (give_up + 5, {})])
    assert core.UI_STOP_REQUESTED
    assert core._AFTER_ACTIONS_BLOCKED
    core._release_macro_input.assert_called_once()


def test_minigame_input_waits_out_popup_then_is_dropped(monkeypatch):
    clock = SimpleNamespace(now=0.0)
    monkeypatch.setattr(core.time, "monotonic", lambda: clock.now)
    monkeypatch.setattr(core.time, "sleep", lambda sec: setattr(clock, "now", clock.now + sec))
    monkeypatch.setattr(core, "_GAME_COVERED_SINCE", 0.0)
    monkeypatch.setattr(core, "_USER_STOP_LATCHED", False)
    monkeypatch.setattr(core, "UI_STOP_REQUESTED", False)
    monkeypatch.setattr(core, "CURRENT_TRAINING_STATE", "Agility")
    monkeypatch.setattr(core, "_background_monitor_thread", object())
    monkeypatch.setattr(core, "_game_window_covering_title",
                        Mock(side_effect=["Toast", "Toast", None]))
    with pytest.raises(core.SkipMinigameException):
        core._wait_while_game_covered()
    assert core._GAME_COVERED_SINCE is None


@pytest.mark.parametrize("monitor", ["current", None])
def test_healing_and_after_run_input_wait_without_dropping(monkeypatch, monitor):
    # The monitor thread's own healing input, and after-run cleanup once the
    # monitor has stopped, still go through after the popup clears.
    monkeypatch.setattr(core.time, "sleep", lambda _: None)
    monkeypatch.setattr(core, "_GAME_COVERED_SINCE", core.time.monotonic())
    monkeypatch.setattr(core, "_USER_STOP_LATCHED", False)
    monkeypatch.setattr(core, "UI_STOP_REQUESTED", False)
    monkeypatch.setattr(core, "CURRENT_TRAINING_STATE", "Ki Control")
    monkeypatch.setattr(core, "_background_monitor_thread",
                        core.threading.current_thread() if monitor == "current" else None)
    monkeypatch.setattr(core, "_game_window_covering_title", Mock(side_effect=["Toast", None]))
    core._wait_while_game_covered()
    assert core._GAME_COVERED_SINCE is None


def test_key_sent_with_stop_gate_held_never_waits_on_popup(monkeypatch):
    # Majin taps hold _stop_input_gate; waiting there would block Stop.
    monkeypatch.setattr(core, "_GAME_COVERED_SINCE", core.time.monotonic())
    monkeypatch.setattr(core, "_USER_STOP_LATCHED", False)
    monkeypatch.setattr(core, "MAJIN_CONTROLLER_ACTIVE", False)
    covered = Mock(return_value="Toast")
    monkeypatch.setattr(core, "_game_window_covering_title", covered)
    monkeypatch.setattr(core, "_set_charge_hold", Mock())
    monkeypatch.setattr(core.pydirectinput, "keyDown", Mock())
    monkeypatch.setattr(core.pydirectinput, "keyUp", Mock())
    monkeypatch.setattr(core.time, "sleep", lambda _: None)
    assert core._tap_key_unchecked("tab", wait_for_cover=False)
    covered.assert_not_called()


def test_covered_game_blocks_charge_and_queued_ki_clicks(monkeypatch):
    monkeypatch.setattr(core, "_GAME_COVERED_SINCE", 1.0)
    monkeypatch.setattr(core, "_USER_STOP_LATCHED", False)
    monkeypatch.setattr(core, "UI_STOP_REQUESTED", False)
    monkeypatch.setattr(core, "CONTROLLER_PAUSED", False)
    monkeypatch.setattr(core, "PAUSE_TOGGLE_REQUESTED", False)
    monkeypatch.setattr(core, "_game_has_focus", lambda: True)
    assert core._charge_interrupted()
    monkeypatch.setattr(core, "_CHARGING_NOW", "controller")
    monkeypatch.setattr(core, "TRAINING_MENU_VISIBLE", False)
    monkeypatch.setattr(core, "GC_RECOVERY_VERIFY_PENDING", False)
    assert not core._charge_wanted("Agility")
    monkeypatch.setattr(core, "_GAME_COVERED_SINCE", None)
    assert core._charge_wanted("Agility") == (core.COMPATIBILITY_SETTINGS["charge_hold_mode"] != "off")


def test_menu_input_waits_out_popup_and_is_still_sent(monkeypatch):
    monkeypatch.setattr(core.time, "sleep", lambda _: None)
    monkeypatch.setattr(core, "_GAME_COVERED_SINCE", core.time.monotonic())
    monkeypatch.setattr(core, "_USER_STOP_LATCHED", False)
    monkeypatch.setattr(core, "UI_STOP_REQUESTED", False)
    monkeypatch.setattr(core, "CURRENT_TRAINING_STATE", None)
    monkeypatch.setattr(core, "_game_window_covering_title", Mock(side_effect=["Toast", None]))
    core._wait_while_game_covered()
    assert core._GAME_COVERED_SINCE is None


def test_stop_while_waiting_on_popup_stops_input(monkeypatch):
    monkeypatch.setattr(core, "_GAME_COVERED_SINCE", core.time.monotonic())
    monkeypatch.setattr(core, "_USER_STOP_LATCHED", True)
    monkeypatch.setattr(core, "_record_run_outcome", Mock())
    monkeypatch.setattr(core, "_game_window_covering_title", Mock(return_value="Toast"))
    with pytest.raises(core.QuitException):
        core._wait_while_game_covered()


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
