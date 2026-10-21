"""Input regressions; native injection, captures and OS hooks are mocked."""
import sys
from collections import defaultdict
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, call

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import xynmacro_core as core


@pytest.fixture(autouse=True)
def clean_controls(monkeypatch):
    for name in ("_USER_STOP_LATCHED", "UI_STOP_REQUESTED", "MANUAL_NEXT_REQUESTED",
                 "PAUSE_TOGGLE_REQUESTED", "CONTROLLER_PAUSED"):
        monkeypatch.setattr(core, name, False)
    monkeypatch.setattr(core, "CURRENT_TRAINING_STATE", None)
    monkeypatch.setattr(core, "_background_monitor_thread", None)
    monkeypatch.setattr(core, "_controller_decisions_suspended", lambda: False)
    monkeypatch.setattr(core, "PROGRESSION_COMPLETE_REQUESTED", core.threading.Event())
    monkeypatch.setattr(core, "SENZU_CONTROLLER_ACTIVE", core.threading.Event())
    monkeypatch.setattr(core, "SENZU_CONTROLLER_RESUME_REQUIRED", core.threading.Event())


@pytest.mark.parametrize("key", ["h", "m", "tab", "1", "esc", "r", "enter"])
def test_utility_keys_hold_for_game_frames_with_gap(monkeypatch, key):
    events = []
    monkeypatch.setattr(core.pydirectinput, "keyDown", lambda key: events.append(("down", key)))
    monkeypatch.setattr(core.pydirectinput, "keyUp", lambda key: events.append(("up", key)))
    monkeypatch.setattr(core.time, "sleep", lambda seconds: events.append(("sleep", seconds)))
    monkeypatch.setattr(core, "KEY_PRESS_DELAY", 0.9)
    assert core._tap_key_unchecked(key)
    assert events == [("down", key), ("sleep", core.UTILITY_KEY_HOLD_SEC),
                      ("up", key), ("sleep", core.UTILITY_KEY_GAP_SEC)]
    assert 0.080 <= core.UTILITY_KEY_HOLD_SEC <= 0.120
    assert 0 < core.UTILITY_KEY_GAP_SEC <= 0.060


@pytest.mark.parametrize("key", ["w", "a", "s", "d", "f", "tab"])
def test_hardware_tap_preserves_minigame_timing(monkeypatch, key):
    monkeypatch.setattr(core, "_game_has_focus", lambda: True)
    monkeypatch.setattr(core.pydirectinput, "keyDown", Mock())
    monkeypatch.setattr(core.pydirectinput, "keyUp", Mock())
    sleep = Mock()
    monkeypatch.setattr(core.time, "sleep", sleep)
    monkeypatch.setattr(core, "KEY_PRESS_DELAY", 0.007)
    core.hardware_tap(key)
    expected = [0.100, 0.040] if key == "tab" else [0.030, 0.007]
    assert sleep.call_args_list == [call(seconds) for seconds in expected]


def test_utility_key_released_when_hold_fails_and_stop_blocks_new_down(monkeypatch):
    down, up = Mock(), Mock()
    monkeypatch.setattr(core.pydirectinput, "keyDown", down)
    monkeypatch.setattr(core.pydirectinput, "keyUp", up)
    monkeypatch.setattr(core.time, "sleep", Mock(side_effect=RuntimeError("interrupted")))
    with pytest.raises(RuntimeError, match="interrupted"):
        core._tap_key_unchecked("h")
    up.assert_called_once_with("h")
    monkeypatch.setattr(core, "_USER_STOP_LATCHED", True)
    assert core._tap_key_unchecked("m") is False
    down.assert_called_once_with("h")


def test_fullscreen_utility_hold_and_release(monkeypatch):
    monkeypatch.setattr(core, "update_game_window", lambda: True)
    monkeypatch.setattr(core, "game_window_is_fullscreen", Mock(side_effect=[False, True]))
    monkeypatch.setattr(core, "focus_game_window", lambda: True)
    down, up, sleep = Mock(), Mock(), Mock()
    monkeypatch.setattr(core.pydirectinput, "keyDown", down)
    monkeypatch.setattr(core.pydirectinput, "keyUp", up)
    monkeypatch.setattr(core.time, "sleep", sleep)
    assert core.ensure_game_fullscreen()
    down.assert_called_once_with("f11")
    up.assert_called_once_with("f11")
    assert sleep.call_args_list[:2] == [call(core.UTILITY_KEY_HOLD_SEC), call(core.UTILITY_KEY_GAP_SEC)]


@pytest.mark.parametrize("callback,flag", [
    (core._hotkey_manual_next, "MANUAL_NEXT_REQUESTED"),
    (core._hotkey_pause_toggle, "PAUSE_TOGGLE_REQUESTED"),
])
def test_control_tap_during_startup_waits_until_controller_ready(monkeypatch, callback, flag):
    monkeypatch.setattr(core, "_ui_is_running", lambda: True)
    monkeypatch.setattr(core, "_game_has_focus", lambda: True)
    callback()
    core.check_exit()  # Startup or switch has no consumer yet; must not unwind.
    assert getattr(core, flag)
    monkeypatch.setattr(core, "CURRENT_TRAINING_STATE", "Health")
    with pytest.raises(core.SkipMinigameException):
        core.check_exit()
    assert getattr(core, flag)  # Consumed by the main loop, never by a deep wait.
    monkeypatch.setattr(core, "UI_STOP_REQUESTED", True)
    with pytest.raises(core.QuitException):
        core.check_exit()


@pytest.mark.parametrize("running,stopped", [(False, False), (True, True)])
def test_idle_or_stopping_control_taps_are_not_carried_into_next_run(monkeypatch, running, stopped):
    monkeypatch.setattr(core, "_ui_is_running", lambda: running)
    monkeypatch.setattr(core, "_USER_STOP_LATCHED", stopped)
    core._hotkey_manual_next()
    core._hotkey_pause_toggle()
    assert not core.MANUAL_NEXT_REQUESTED
    assert not core.PAUSE_TOGGLE_REQUESTED


def test_pause_and_skip_do_not_interrupt_senzu_menu_transaction(monkeypatch):
    monkeypatch.setattr(core, "CURRENT_TRAINING_STATE", "Health")
    monkeypatch.setattr(core, "_background_monitor_thread", core.threading.current_thread())
    monkeypatch.setattr(core, "MANUAL_NEXT_REQUESTED", True)
    monkeypatch.setattr(core, "PAUSE_TOGGLE_REQUESTED", True)
    core.check_exit()


def _hook(monkeypatch, callback, steps=(((64,),),)):
    hook = Mock(return_value=Mock())
    monkeypatch.setattr(core.keyboard, "hook", hook)
    monkeypatch.setattr(core.keyboard, "parse_hotkey_combinations", lambda key: steps)
    monkeypatch.setattr(core.keyboard, "is_modifier", lambda code: code in (29, 97, 42, 54, 56, 100))
    remover = core._register_edge_hotkey("test", callback)
    assert remover is hook.return_value
    assert hook.call_args.kwargs == {"suppress": False}
    return hook.call_args.args[0]


def _event(kind, code=64, when=10.0):
    return core.keyboard.KeyboardEvent(kind, code, name="test", time=when)


@pytest.mark.parametrize("key_code", [64, 22, 38])  # F6, U, L scan codes.
def test_quick_tap_survives_delivery_after_key_already_released(monkeypatch, key_code):
    fired = Mock()
    on_event = _hook(monkeypatch, fired, (((key_code,),),))
    # Reproduce the installed keyboard library's delayed dispatch: its live
    # pressed-key table is empty by the time it processes the queued down event.
    monkeypatch.setattr(core.keyboard, "_pressed_events", {})
    listener = SimpleNamespace(nonblocking_keys=defaultdict(list),
                               nonblocking_hotkeys=defaultdict(list))
    for event in [_event("down", key_code), _event("up", key_code, 10.02)]:
        assert core.keyboard._KeyboardListener.pre_process_event(listener, event)
        on_event(event)
    fired.assert_called_once_with()


def test_holding_hotkey_fires_once_until_release_even_with_other_inputs(monkeypatch):
    fired = Mock()
    on_event = _hook(monkeypatch, fired)
    on_event(_event("down", 17))  # Macro's W injection must not hide F6.
    on_event(_event("down"))
    for i in range(100):
        on_event(_event("down", when=10.1 + i * 0.03))
    on_event(_event("up", 17))
    fired.assert_called_once_with()
    on_event(_event("up"))
    on_event(_event("down", when=14))
    on_event(_event("up", when=14.02))
    assert fired.call_count == 2


def test_missed_key_up_allows_next_press_after_repeats_stop(monkeypatch):
    fired = Mock()
    on_event = _hook(monkeypatch, fired)
    on_event(_event("down", when=10.0))
    on_event(_event("down", when=10.3))  # OS auto-repeat keeps the hold fresh.
    on_event(_event("down", when=11.0))
    fired.assert_called_once_with()
    on_event(_event("down", when=12.6))  # Key-up was lost on secure desktop.
    assert fired.call_count == 2


def test_chord_rearms_on_release_and_accepts_either_modifier(monkeypatch):
    fired = Mock()
    on_event = _hook(monkeypatch, fired, (((29, 64), (97, 64)),))
    on_event(_event("down", 29))
    on_event(_event("down", 64))
    on_event(_event("down", 64))
    on_event(_event("up", 64))
    on_event(_event("up", 29))
    on_event(_event("down", 97))
    on_event(_event("down", 64))
    assert fired.call_count == 2


def test_existing_hotkey_sequence_keeps_timeout(monkeypatch):
    fired = Mock()
    on_event = _hook(monkeypatch, fired, (((30,),), ((48,),)))
    for event in [_event("down", 30), _event("up", 30),
                  _event("down", 48, 10.2), _event("up", 48, 10.3)]:
        on_event(event)
    fired.assert_called_once_with()
    for event in [_event("down", 30, 11), _event("up", 30, 11.1),
                  _event("down", 48, 12.5)]:
        on_event(event)
    fired.assert_called_once_with()


@pytest.mark.parametrize("register,key_attr,handle_attr,callback_name", [
    ("register_manual_next_hotkey", "MANUAL_NEXT_KEY", "_manual_next_hotkey_handle", "_hotkey_manual_next"),
    ("register_pause_hotkey", "PAUSE_HOTKEY", "_pause_hotkey_handle", "_hotkey_pause_toggle"),
    ("register_start_stop_hotkey", "START_STOP_HOTKEY", "_start_stop_hotkey_handle", "_hotkey_toggle_macro"),
])
def test_rebinding_removes_previous_event_hook(monkeypatch, register, key_attr, handle_attr, callback_name):
    old, new = Mock(), Mock()
    monkeypatch.setattr(core, handle_attr, old)
    monkeypatch.setattr(core, "_hotkey_toggle_worker", SimpleNamespace(is_alive=lambda: True))
    install = Mock(return_value=new)
    monkeypatch.setattr(core, "_register_edge_hotkey", install)
    getattr(core, register)()
    old.assert_called_once_with()
    install.assert_called_once_with(getattr(core, key_attr), getattr(core, callback_name))
    assert getattr(core, handle_attr) is new


def test_start_stop_callback_only_enqueues_work(monkeypatch):
    queue = core.queue.SimpleQueue()
    monkeypatch.setattr(core, "_hotkey_toggle_queue", queue)
    monkeypatch.setattr(core, "_hotkey_toggle_busy", False)
    monkeypatch.setattr(core, "_hotkey_toggle_last_accepted", 0.0)
    monkeypatch.setattr(core, "_ui_is_running", lambda: False)
    start, stop = Mock(), Mock()
    monkeypatch.setattr(core, "_ui_start_macro", start)
    monkeypatch.setattr(core, "_ui_stop_macro", stop)
    core._hotkey_toggle_macro()
    assert queue.get_nowait() == (core._macro_start_generation, False)
    assert queue.empty()
    start.assert_not_called()
    stop.assert_not_called()


def test_toggle_worker_coalesces_busy_taps_and_survives_failure(monkeypatch):
    class Done(BaseException):
        pass

    class Queue:
        def __init__(self):
            self.items = []

        def put(self, item):
            self.items.append(item)

        def get(self):
            if not self.items:
                raise Done()
            return self.items.pop(0)

    queue = Queue()
    monkeypatch.setattr(core, "_hotkey_toggle_queue", queue)
    monkeypatch.setattr(core, "_hotkey_toggle_busy", False)
    monkeypatch.setattr(core, "_hotkey_toggle_last_accepted", 0.0)
    clock = {"now": 10.0}
    monkeypatch.setattr(core.time, "monotonic", lambda: clock["now"])
    monkeypatch.setattr(core, "_ui_is_running", Mock(side_effect=[False, False, True]))
    def slow_start(expected_generation):
        assert expected_generation == core._macro_start_generation
        if start.call_count == 2:
            clock["now"] += 0.5
        core._hotkey_toggle_macro()  # A second F6 during the slow action is dropped.
        if start.call_count == 1:
            raise RuntimeError("window unavailable")

    start = Mock(side_effect=slow_start)
    stop = Mock()
    monkeypatch.setattr(core, "_ui_start_macro", start)
    monkeypatch.setattr(core, "_ui_stop_macro", stop)
    core._hotkey_toggle_macro()
    clock["now"] = 10.1
    core._hotkey_toggle_macro()  # Pending also coalesces.
    assert len(queue.items) == 1
    with pytest.raises(Done):
        core._process_hotkey_toggles()
    assert start.call_count == 1
    assert not queue.items
    assert core._hotkey_toggle_busy is False
    clock["now"] = 10.2
    core._hotkey_toggle_macro()  # Cooldown still applies after failure.
    assert not queue.items
    clock["now"] = 10.5
    core._hotkey_toggle_macro()
    with pytest.raises(Done):
        core._process_hotkey_toggles()
    assert start.call_count == 2
    clock["now"] = 11.1
    core._hotkey_toggle_macro()
    with pytest.raises(Done):
        core._process_hotkey_toggles()
    stop.assert_called_once_with(expected_generation=core._macro_start_generation)


def test_l_during_real_controller_startup_is_consumed_after_trait_confirmation(monkeypatch):
    monkeypatch.setattr(core, "_game_has_focus", lambda: True)
    # Exercise the real countdown/menu/start/skip path, not just the callback.
    monkeypatch.setattr(core, "_ui_is_running", lambda: True)
    monkeypatch.setattr(core, "TRAINING_ORDER_CUSTOM", ["Health"])
    monkeypatch.setattr(core, "START_DELAY", 1.0)
    monkeypatch.setattr(core, "SENZU_ENABLED", False)
    monkeypatch.setattr(core, "SENZU_DISABLED_FOR_RUN", False)
    monkeypatch.setattr(core, "SHOW_DEBUG_HUD", False)
    monkeypatch.setattr(core, "DIAGNOSTIC_MODE", False)
    monkeypatch.setattr(core, "_stop_if_starting_on_death_screen", lambda: False)
    monkeypatch.setattr(core, "_start_background_game_monitor", lambda: True)
    monkeypatch.setattr(core, "_confirmed_game_capture_rect", lambda: dict(left=0, top=0, width=1920, height=1080))
    monkeypatch.setattr(core.os.path, "exists", lambda path: False)
    monkeypatch.setattr(core.cv2, "imread", lambda *args: object())
    monkeypatch.setattr(core.mss, "MSS", Mock(return_value=nullcontext(object())))
    monkeypatch.setattr(core, "_raise_gc_gravity", lambda *args: True)
    monkeypatch.setattr(core, "_gc_room_confirmed", Mock(return_value=False))
    monkeypatch.setattr(core, "focus_game_window", lambda: True)
    monkeypatch.setattr(core, "_trait_click_uses_approach", lambda attempt: False)
    monkeypatch.setattr(core, "_confirm_trait_click", Mock())
    monkeypatch.setattr(core, "_button_screen_point", lambda *args: (100, 100))
    click = Mock()
    monkeypatch.setattr(core, "click_at", click)
    monkeypatch.setattr(core, "detect_training_menu", lambda *args: (not click.called, 1.0))
    monkeypatch.setattr(core, "detect_yellow_progress", lambda *args: (1, None, None))
    monkeypatch.setattr(core, "PROGRESSION_STATE_STARTED_AT", 0)
    monkeypatch.setattr(core, "TRAINING_MENU_VISIBLE", False)
    monkeypatch.setattr(core, "TELEMETRY", dict(core.TELEMETRY))
    outcome = Mock()
    monkeypatch.setattr(core, "_record_run_outcome", outcome)
    slept = []

    def sleep(duration):
        if not slept:
            core._hotkey_manual_next()
        slept.append(duration)
        core.check_exit()

    monkeypatch.setattr(core, "safe_sleep", sleep)
    core.run_master_controller()
    click.assert_called_once_with(100, 100)
    assert not core.MANUAL_NEXT_REQUESTED
    assert outcome.call_args.args[0] == "incomplete"
    assert "skipped stats" in outcome.call_args.args[1]


def test_plain_key_does_not_fire_for_a_different_modifier_chord(monkeypatch):
    fired = Mock()
    on_event = _hook(monkeypatch, fired, (((38,),),))
    on_event(_event("down", 29))
    on_event(_event("down", 38))
    fired.assert_not_called()  # Ctrl+L must not also activate plain L.
    on_event(_event("up", 38))
    on_event(_event("up", 29))
    on_event(_event("down", 38))
    fired.assert_called_once_with()
