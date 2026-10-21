"""Focused regressions for training input, pause, retry, and hotkeys."""
import sys
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import xynmacro_core as core


def _controller_setup(monkeypatch):
    monkeypatch.setattr(core, "_USER_STOP_LATCHED", False)
    monkeypatch.setattr(core, "UI_STOP_REQUESTED", False)
    monkeypatch.setattr(core, "MANUAL_NEXT_REQUESTED", False)
    monkeypatch.setattr(core, "PAUSE_TOGGLE_REQUESTED", False)
    monkeypatch.setattr(core, "SENZU_TRAIT_RESELECT_DEFERRED", False)
    monkeypatch.setattr(core, "PROGRESSION_COMPLETE_REQUESTED", core.threading.Event())
    monkeypatch.setattr(core, "SENZU_CONTROLLER_ACTIVE", core.threading.Event())
    monkeypatch.setattr(core, "SENZU_CONTROLLER_RESUME_REQUIRED", core.threading.Event())
    monkeypatch.setattr(core, "TRAINING_ORDER_CUSTOM", ["Health", "Agility"])
    monkeypatch.setattr(core, "START_DELAY", 0)
    monkeypatch.setattr(core, "SENZU_ENABLED", False)
    monkeypatch.setattr(core, "SHOW_DEBUG_HUD", False)
    monkeypatch.setattr(core, "DIAGNOSTIC_MODE", False)
    monkeypatch.setattr(core, "NO_YELLOW_FALLBACK_ENABLED", False)
    monkeypatch.setattr(core, "PROGRESSION_TRACKED_STATE", None)
    monkeypatch.setattr(core, "_stop_if_starting_on_death_screen", lambda: False)
    monkeypatch.setattr(core, "_start_background_game_monitor", lambda: True)
    monkeypatch.setattr(core, "_confirmed_game_capture_rect", lambda: dict(left=0, top=0, width=1920, height=1080))
    monkeypatch.setattr(core.os.path, "exists", lambda path: False)
    monkeypatch.setattr(core.cv2, "imread", lambda *args: object())
    monkeypatch.setattr(core.mss, "MSS", lambda: nullcontext(object()))
    monkeypatch.setattr(core, "_raise_gc_gravity", lambda *args: True)
    monkeypatch.setattr(core, "_gc_room_confirmed", Mock(return_value=False))
    monkeypatch.setattr(core, "_button_screen_point", lambda *args: (100, 100))
    monkeypatch.setattr(core, "focus_game_window", lambda: True)
    monkeypatch.setattr(core, "_trait_click_uses_approach", lambda attempt: False)
    monkeypatch.setattr(core, "_confirm_trait_click", Mock())
    click = Mock()
    monkeypatch.setattr(core, "click_at", click)
    monkeypatch.setattr(core, "detect_training_menu", lambda *args: (not click.called, 1.0))
    monkeypatch.setattr(core, "detect_yellow_progress", lambda *args: (1, None, None))
    monkeypatch.setattr(core, "safe_sleep", lambda duration: None)
    monkeypatch.setattr(core, "TELEMETRY", dict(core.TELEMETRY))
    return click


def test_hardware_tap_checks_foreground_once_when_already_focused(monkeypatch):
    foreground = Mock(return_value=42)
    focus = Mock()
    down = Mock()
    monkeypatch.setattr(core, "GAME_HWND", 42)
    monkeypatch.setattr(core, "GAME_WINDOW_MINIMIZED", False)
    monkeypatch.setattr(core, "_user32", SimpleNamespace(GetForegroundWindow=foreground))
    monkeypatch.setattr(core, "focus_game_window", focus)
    monkeypatch.setattr(core.pydirectinput, "keyDown", down)
    monkeypatch.setattr(core.pydirectinput, "keyUp", Mock())
    monkeypatch.setattr(core.time, "sleep", Mock())
    monkeypatch.setattr(core, "_USER_STOP_LATCHED", False)
    monkeypatch.setattr(core, "UI_STOP_REQUESTED", False)
    monkeypatch.setattr(core, "CURRENT_TRAINING_STATE", None)
    assert core.hardware_tap("f") is True
    foreground.assert_called_once_with()
    focus.assert_not_called()
    down.assert_called_once_with("f")


def test_failed_focus_blocks_keys_and_clicks_with_rate_limited_log(monkeypatch):
    foreground = Mock(return_value=7)
    focus = Mock(return_value=False)
    down = Mock()
    send = Mock()
    log = Mock()
    times = iter((10.0, 12.0, 16.0, 16.1, 16.2))
    monkeypatch.setattr(core, "GAME_HWND", 42)
    monkeypatch.setattr(core, "GAME_WINDOW_MINIMIZED", False)
    monkeypatch.setattr(core, "_user32", SimpleNamespace(GetForegroundWindow=foreground))
    monkeypatch.setattr(core, "focus_game_window", focus)
    monkeypatch.setattr(core.pydirectinput, "keyDown", down)
    monkeypatch.setattr(core, "_send_click_buttons", send)
    monkeypatch.setattr(core, "_focus_failure_last_log_ts", None)
    monkeypatch.setattr(core.time, "monotonic", lambda: next(times))
    monkeypatch.setattr("builtins.print", log)
    monkeypatch.setattr(core, "_USER_STOP_LATCHED", False)
    monkeypatch.setattr(core, "UI_STOP_REQUESTED", False)
    monkeypatch.setattr(core, "CURRENT_TRAINING_STATE", None)
    for _ in range(3):
        assert core.hardware_tap("w") is False
    with pytest.raises(RuntimeError, match="not foreground for click"):
        core.click_current_position()
    with pytest.raises(RuntimeError, match="not foreground for click"):
        core.click_at(100, 200)
    assert focus.call_count == 5
    down.assert_not_called()
    send.assert_not_called()
    assert log.call_count == 2


def test_hardware_tap_retries_focus_once_then_sends(monkeypatch):
    foreground = Mock(side_effect=[7, 42])
    focus = Mock(return_value=True)
    down = Mock()
    monkeypatch.setattr(core, "GAME_HWND", 42)
    monkeypatch.setattr(core, "GAME_WINDOW_MINIMIZED", False)
    monkeypatch.setattr(core, "_user32", SimpleNamespace(GetForegroundWindow=foreground))
    monkeypatch.setattr(core, "focus_game_window", focus)
    monkeypatch.setattr(core.pydirectinput, "keyDown", down)
    monkeypatch.setattr(core.pydirectinput, "keyUp", Mock())
    monkeypatch.setattr(core.time, "sleep", Mock())
    monkeypatch.setattr(core, "_USER_STOP_LATCHED", False)
    monkeypatch.setattr(core, "UI_STOP_REQUESTED", False)
    monkeypatch.setattr(core, "CURRENT_TRAINING_STATE", None)
    assert core.hardware_tap("a") is True
    focus.assert_called_once_with()
    down.assert_called_once_with("a")


@pytest.mark.parametrize("setting,key", [
    ("manual_next_key", "w"), ("pause_hotkey", "ctrl+f"),
    ("start_stop_hotkey", "f11"), ("manual_next_key", "2"),
    ("pause_hotkey", "tab"), ("start_stop_hotkey", "alt"),
])
def test_macro_input_keys_cannot_become_hotkeys(monkeypatch, setting, key):
    register = Mock()
    monkeypatch.setattr(core, "register_manual_next_hotkey", register)
    monkeypatch.setattr(core, "register_pause_hotkey", register)
    monkeypatch.setattr(core, "register_start_stop_hotkey", register)
    with pytest.raises(ValueError, match="XynMacro sends to Roblox"):
        core._ui_apply_setting_unlocked(setting, key)
    register.assert_not_called()


def test_saved_conflicting_hotkey_falls_back_to_safe_key():
    assert core._loaded_hotkey("w", "l") == "l"
    assert core._loaded_hotkey("f8", "l") == "f8"


def test_manual_next_and_pause_require_roblox_focus(monkeypatch):
    monkeypatch.setattr(core, "_ui_is_running", lambda: True)
    monkeypatch.setattr(core, "_USER_STOP_LATCHED", False)
    monkeypatch.setattr(core, "MANUAL_NEXT_REQUESTED", False)
    monkeypatch.setattr(core, "PAUSE_TOGGLE_REQUESTED", False)
    monkeypatch.setattr(core, "_game_has_focus", lambda: False)
    core._hotkey_manual_next()
    core._hotkey_pause_toggle()
    assert not core.MANUAL_NEXT_REQUESTED and not core.PAUSE_TOGGLE_REQUESTED
    monkeypatch.setattr(core, "_game_has_focus", lambda: True)
    core._hotkey_manual_next()
    core._hotkey_pause_toggle()
    assert core.MANUAL_NEXT_REQUESTED and core.PAUSE_TOGGLE_REQUESTED


def test_controller_classifies_caught_retryable_runtime_error(monkeypatch):
    _controller_setup(monkeypatch)
    outcome = Mock()
    monkeypatch.setattr(core, "_record_run_outcome", outcome)
    monkeypatch.setattr(core, "logic_health", Mock(side_effect=RuntimeError(
        "Training Mode is open and Health could not be resumed")))
    core.run_master_controller()
    assert outcome.call_args.kwargs["retryable"] is True


def test_no_yellow_switch_failure_is_retryable(monkeypatch):
    _controller_setup(monkeypatch)
    outcome = Mock()
    monkeypatch.setattr(core, "_record_run_outcome", outcome)
    monkeypatch.setattr(core, "NO_YELLOW_FALLBACK_ENABLED", True)
    monkeypatch.setattr(core, "NO_YELLOW_TIMEOUT_SEC", 0)
    monkeypatch.setattr(core, "TRAINING_LOAD_GRACE_SEC", 0)
    monkeypatch.setattr(core, "YELLOW_SAMPLE_INTERVAL_SEC", 0)
    monkeypatch.setattr(core, "detect_yellow_progress", lambda *args: (0, None, None))
    monkeypatch.setattr(core, "hardware_tap", Mock(side_effect=RuntimeError(
        "Training Mode menu was not confirmed after Tab; switch cancelled")))
    core.run_master_controller()
    assert outcome.call_args.kwargs["retryable"] is True


def test_paused_controller_sleeps_with_pending_skip_and_completion(monkeypatch):
    _controller_setup(monkeypatch)
    monkeypatch.setattr(core, "PAUSE_TOGGLE_REQUESTED", True)
    sleeps = []

    def paused_sleep(duration):
        sleeps.append(duration)
        if len(sleeps) == 1:
            core.MANUAL_NEXT_REQUESTED = True
        elif len(sleeps) == 2:
            core.PROGRESSION_COMPLETE_REQUESTED.set()
        elif len(sleeps) == 3:
            core.UI_STOP_REQUESTED = True

    monkeypatch.setattr(core.time, "sleep", paused_sleep)
    with pytest.raises(core.QuitException):
        core.run_master_controller()
    assert sleeps == [0.05, 0.05, 0.05]


def test_senzu_trait_click_waits_for_resume(monkeypatch):
    click = Mock()
    monkeypatch.setattr(core, "CONTROLLER_PAUSED", True)
    monkeypatch.setattr(core, "SENZU_TRAIT_RESELECT_DEFERRED", False)
    monkeypatch.setattr(core, "CURRENT_TRAINING_STATE", "Health")
    monkeypatch.setattr(core, "_senzu_abort_requested", lambda: False)
    monkeypatch.setattr(core, "_training_menu_visible_for_senzu", lambda *args: (True, 1.0))
    monkeypatch.setattr(core, "click_at", click)
    assert core._resume_training_after_senzu(object(), {"training": object()})
    click.assert_not_called()
    assert core.SENZU_TRAIT_RESELECT_DEFERRED

    _controller_setup(monkeypatch)
    monkeypatch.setattr(core, "SENZU_TRAIT_RESELECT_DEFERRED", True)
    click = Mock()
    monkeypatch.setattr(core, "click_at", click)
    monkeypatch.setattr(core, "detect_training_menu", lambda *args: (click.call_count == 0, 1.0))
    monkeypatch.setattr(core, "logic_health", Mock(side_effect=core.QuitException()))
    with pytest.raises(core.QuitException):
        core.run_master_controller()
    assert click.call_count == 2
    assert not core.SENZU_TRAIT_RESELECT_DEFERRED


def test_ki_errors_log_at_most_once_per_five_seconds(monkeypatch):
    log = Mock()
    times = iter((10.0, 12.0, 16.0))
    monkeypatch.setattr(core, "_ki_last_error_log_ts", None)
    monkeypatch.setattr(core, "check_exit", Mock(side_effect=ValueError("capture failed")))
    monkeypatch.setattr(core.time, "monotonic", lambda: next(times))
    monkeypatch.setattr("builtins.print", log)
    for _ in range(3):
        core.logic_ki_v8(None, {})
    assert sum("[KI v8] Error:" in str(call.args[0]) for call in log.call_args_list) == 2
