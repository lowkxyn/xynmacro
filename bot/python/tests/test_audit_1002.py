import json
import sys
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import xynmacro_core as core


@pytest.fixture
def idle_runtime(monkeypatch):
    for name, value in {
        "_USER_STOP_LATCHED": False, "UI_STOP_REQUESTED": False,
        "_AFTER_ACTIONS_BLOCKED": False, "_background_monitor_stop": None,
        "_background_monitor_thread": None, "MACRO_THREAD": None,
        "CURRENT_TRAINING_STATE": None, "MACRO_STARTED_MONOTONIC": None,
        "_macro_start_generation": 0,
        "_macro_pending_start_generation": None, "_macro_pending_start_requests": 0,
        "_sidecar_closing": False,
        "_hotkey_toggle_busy": False, "_hotkey_toggle_last_accepted": 0.0,
    }.items():
        monkeypatch.setattr(core, name, value)
    monkeypatch.setattr(core, "_record_run_outcome", Mock())
    monkeypatch.setattr(core, "_cancel_pc_shutdown", Mock())
    yield
    stop = core._background_monitor_stop
    thread = core._background_monitor_thread
    if stop is not None:
        stop.set()
    if thread is not None and thread.is_alive():
        thread.join(timeout=2)
        assert not thread.is_alive(), "test leaked a monitor thread"


def test_monitor_constructor_failure_cancels_start(idle_runtime, monkeypatch):
    monkeypatch.setattr(core.mss, "MSS", Mock(side_effect=OSError("capture unavailable")))

    assert core._start_background_game_monitor() is False
    assert core.UI_STOP_REQUESTED
    assert core._AFTER_ACTIONS_BLOCKED
    assert core._background_monitor_stop.is_set()
    assert any("capture unavailable" in call.args[1]
               for call in core._record_run_outcome.call_args_list)


def test_monitor_ready_handshake_and_clean_stop(idle_runtime, monkeypatch):
    monkeypatch.setattr(core.mss, "MSS", Mock(return_value=MagicMock()))
    monkeypatch.setattr(core, "update_game_window", lambda: False)

    assert core._start_background_game_monitor() is True
    assert core._background_monitor_thread.is_alive()
    assert core._stop_background_game_monitor() is True
    assert not core.UI_STOP_REQUESTED
    core._record_run_outcome.assert_not_called()


def test_monitor_loss_after_start_stops_input(idle_runtime, monkeypatch):
    stop = threading.Event()
    ready = threading.Event()
    monkeypatch.setattr(core, "_background_game_monitor",
                        Mock(side_effect=OSError("capture worker exited")))

    core._background_game_monitor_worker(stop, ready)

    assert ready.is_set() and stop.is_set()
    assert core.UI_STOP_REQUESTED and core._AFTER_ACTIONS_BLOCKED


def test_continuous_monitor_capture_failure_cannot_run_forever(idle_runtime, monkeypatch):
    stop = threading.Event()
    ready = threading.Event()
    monkeypatch.setattr(core.mss, "MSS", Mock(return_value=MagicMock()))
    monkeypatch.setattr(core, "update_game_window", Mock(side_effect=OSError("capture failed")))
    monkeypatch.setattr(core.time, "monotonic", Mock(side_effect=[0, 10, 13, 16]))
    monkeypatch.setattr(core, "PROG_POLL_INTERVAL_SEC", 0)

    core._background_game_monitor_worker(stop, ready)

    assert stop.is_set() and core.UI_STOP_REQUESTED
    assert any("5 seconds" in call.args[1]
               for call in core._record_run_outcome.call_args_list)


def test_successful_monitor_sample_resets_transient_capture_failures(idle_runtime, monkeypatch):
    stop = Mock()
    stop.is_set.side_effect = [False, False, False, False, True]
    ready = threading.Event()
    monkeypatch.setattr(core.mss, "MSS", Mock(return_value=MagicMock()))
    monkeypatch.setattr(core, "update_game_window",
                        Mock(side_effect=[OSError("changing display"), True,
                                          OSError("changing display"), True]))
    monkeypatch.setattr(core, "_game_geometry", lambda: {})
    monkeypatch.setattr(core, "SENZU_ENABLED", False)
    monkeypatch.setattr(core.time, "monotonic", Mock(side_effect=[0, 10, 13, 16, 19]))
    core._background_game_monitor_worker(stop, ready)
    assert ready.is_set()
    assert not core.UI_STOP_REQUESTED
    core._record_run_outcome.assert_not_called()


def test_stop_during_native_start_setup_cannot_be_cleared(idle_runtime, monkeypatch):
    monkeypatch.setattr(core, "GAME_HWND", 123)
    monkeypatch.setattr(core, "update_game_window", lambda: True)
    monkeypatch.setattr(core, "_calibration_is_active", lambda: False)
    monkeypatch.setattr(core, "TRAINING_ORDER_CUSTOM", ["Health"])
    monkeypatch.setattr(core, "WINDOWED_MODE_ON_START", True)
    monkeypatch.setattr(core.threading.Thread, "start", Mock())

    def size_window(**_kwargs):
        core._ui_stop_macro()
        return True, "Window sized"

    monkeypatch.setattr(core, "ensure_game_windowed", size_window)

    ok, message, _code = core._ui_start_macro()

    assert not ok and "cancelled" in message
    assert core.UI_STOP_REQUESTED and core._USER_STOP_LATCHED
    core.threading.Thread.start.assert_not_called()


def test_idle_close_cancels_native_start_without_releasing_player_input(idle_runtime, monkeypatch):
    monkeypatch.setattr(core, "GAME_HWND", 123)
    monkeypatch.setattr(core, "update_game_window", lambda: True)
    monkeypatch.setattr(core, "_calibration_is_active", lambda: False)
    monkeypatch.setattr(core, "TRAINING_ORDER_CUSTOM", ["Health"])
    monkeypatch.setattr(core, "WINDOWED_MODE_ON_START", True)
    monkeypatch.setattr(core.threading.Thread, "start", Mock())
    monkeypatch.setattr(core, "_release_macro_input", Mock())
    monkeypatch.setattr(core, "_release_session_marker", Mock())

    def size_window(**_kwargs):
        core._ui_shutdown_clean()
        return True, "Window sized"

    monkeypatch.setattr(core, "ensure_game_windowed", size_window)

    ok, message, _code = core._ui_start_macro()

    assert not ok and "closing" in message
    assert core._sidecar_closing and core.UI_STOP_REQUESTED
    core.threading.Thread.start.assert_not_called()
    core._release_macro_input.assert_not_called()
    core._release_session_marker.assert_called_once()


def test_start_and_hotkeys_are_refused_after_clean_close(idle_runtime, monkeypatch):
    monkeypatch.setattr(core, "_release_macro_input", Mock())
    monkeypatch.setattr(core, "_release_session_marker", Mock())
    monkeypatch.setattr(core, "update_game_window", Mock())
    monkeypatch.setattr(core, "_hotkey_toggle_queue", Mock())
    monkeypatch.setattr(core, "_hotkey_toggle_busy", False)

    core._ui_shutdown_clean()
    ok, message, _code = core._ui_start_macro()
    core._hotkey_toggle_macro()

    assert not ok and "closing" in message
    core.update_game_window.assert_not_called()
    core._hotkey_toggle_queue.put.assert_not_called()
    assert not core._hotkey_toggle_busy
    core._release_macro_input.assert_not_called()


@pytest.fixture
def queued_hotkeys(idle_runtime, monkeypatch):
    queued = []

    def get():
        if not queued:
            raise SystemExit
        return queued.pop(0)

    monkeypatch.setattr(core, "_hotkey_toggle_queue", SimpleNamespace(put=queued.append, get=get))
    monkeypatch.setattr(core, "update_game_window", Mock())
    monkeypatch.setattr(core, "_release_macro_input", Mock())
    monkeypatch.setattr(core, "_release_session_marker", Mock())
    return queued


@pytest.fixture
def prepared_start(idle_runtime, monkeypatch):
    worker = Mock()
    worker.is_alive.return_value = False
    worker.start.side_effect = lambda: setattr(worker.is_alive, "return_value", True)
    monkeypatch.setattr(core, "threading", SimpleNamespace(Thread=lambda **_kwargs: worker))
    for name, value in {
        "GAME_HWND": 123, "TRAINING_ORDER_CUSTOM": ["Health"],
        "WINDOWED_MODE_ON_START": True, "RESTORE_FULLSCREEN_ON_START": False,
        "_shutdown_status": "idle",
        "update_game_window": Mock(return_value=True),
        "restore_game_window": Mock(return_value=False),
        "ensure_game_windowed": Mock(return_value=(True, "Window sized")),
        "ensure_game_fullscreen": Mock(), "_calibration_is_active": lambda: False,
        "_begin_run_result": Mock(), "_telemetry_reset": Mock(),
    }.items():
        monkeypatch.setattr(core, name, value)
    return worker


@pytest.mark.parametrize("close", [False, True])
def test_queued_f6_cannot_start_after_stop_or_close(close, queued_hotkeys):
    core._hotkey_toggle_macro()
    assert queued_hotkeys == [(0, False)]
    core._ui_shutdown_clean() if close else core._ui_stop_macro()
    with pytest.raises(SystemExit):
        core._process_hotkey_toggles()
    core.update_game_window.assert_not_called()
    assert core.UI_STOP_REQUESTED and not core._hotkey_toggle_busy


def test_stop_between_hotkey_dispatch_and_start_is_still_observed(queued_hotkeys, monkeypatch):
    start = core._ui_start_macro

    def delayed_start(**kwargs):
        core._ui_stop_macro()
        return start(**kwargs)

    monkeypatch.setattr(core, "_ui_start_macro", delayed_start)
    core._hotkey_toggle_macro()
    with pytest.raises(SystemExit):
        core._process_hotkey_toggles()
    core.update_game_window.assert_not_called()
    assert core.UI_STOP_REQUESTED


def test_queued_start_keeps_its_action_if_another_start_wins(queued_hotkeys, monkeypatch):
    core._hotkey_toggle_macro()
    monkeypatch.setattr(core, "_ui_is_running", lambda: True)
    monkeypatch.setattr(core, "_ui_stop_macro", Mock())
    with pytest.raises(SystemExit):
        core._process_hotkey_toggles()
    core._ui_stop_macro.assert_not_called()
    core.update_game_window.assert_not_called()


@pytest.mark.parametrize("boundary", ["before_start_lock", "native_setup"])
def test_f6_during_setup_still_stops_after_launch(boundary, queued_hotkeys, prepared_start, monkeypatch):
    preparing, release = threading.Event(), threading.Event()
    real_lock = threading.Lock()

    def wait_at_boundary():
        preparing.set()
        assert release.wait(2), "test did not release Start"

    class StartLock:
        def __enter__(self):
            wait_at_boundary()
            real_lock.acquire()

        def __exit__(self, *_args):
            real_lock.release()

    def size_window(**_kwargs):
        wait_at_boundary()
        return True, "Window sized"

    if boundary == "before_start_lock":
        monkeypatch.setattr(core, "_macro_start_lock", StartLock())
    else:
        monkeypatch.setattr(core, "ensure_game_windowed", size_window)
    outcomes = []
    start = threading.Thread(target=lambda: outcomes.append(core._ui_start_macro()))
    start.start()
    try:
        assert preparing.wait(2)
        assert core._stop_input_gate.acquire(timeout=2), "Start blocked input/Stop during setup"
        core._stop_input_gate.release()
        core._hotkey_toggle_macro()
        assert queued_hotkeys == [(1, True)]
        assert core._macro_pending_start_generation == 1
    finally:
        release.set()
        start.join(2)
    assert not start.is_alive()
    assert outcomes == [(True, "Started", None)]
    assert prepared_start.is_alive() and not core.UI_STOP_REQUESTED
    assert core._macro_start_generation == 1
    with pytest.raises(SystemExit):
        core._process_hotkey_toggles()
    assert core.UI_STOP_REQUESTED and core._USER_STOP_LATCHED
    assert core._macro_start_generation == 2
    assert core._macro_pending_start_generation is None
    assert core._macro_pending_start_requests == 0
    assert not core._hotkey_toggle_busy


@pytest.mark.parametrize("owner_result", ["started", "refused", "finished", "replaced"])
def test_coalesced_start_keeps_reservation_and_cannot_relaunch(
        owner_result, queued_hotkeys, prepared_start, monkeypatch):
    preparing, queued, release, admit_waiter = (threading.Event() for _ in range(4))
    real_lock = threading.Lock()

    class StartLock:
        def __enter__(self):
            if threading.current_thread().name == "coalesced-start":
                queued.set()
                assert admit_waiter.wait(2), "test did not admit coalesced Start"
            real_lock.acquire()

        def __exit__(self, *_args):
            real_lock.release()

    setup_calls = []

    def size_window(**_kwargs):
        setup_calls.append(True)
        if len(setup_calls) == 1:
            preparing.set()
            assert release.wait(2), "test did not release native setup"
            if owner_result == "refused":
                return False, "Window not ready"
        return True, "Window sized"

    monkeypatch.setattr(core, "_macro_start_lock", StartLock())
    monkeypatch.setattr(core, "ensure_game_windowed", size_window)
    outcomes = {}
    first = threading.Thread(target=lambda: outcomes.update(first=core._ui_start_macro()))
    second = threading.Thread(name="coalesced-start",
                              target=lambda: outcomes.update(second=core._ui_start_macro()))
    first.start()
    try:
        assert preparing.wait(2)
        second.start()
        assert queued.wait(2)
        assert core._macro_start_generation == core._macro_pending_start_generation == 1
        assert core._macro_pending_start_requests == 2
        release.set()
        first.join(2)
        assert not first.is_alive()
        if owner_result == "refused":
            assert not outcomes["first"][0]
            assert core._macro_pending_start_generation == 1
            assert core._macro_pending_start_requests == 1
        # Also accept Stop while only the coalesced waiter remains, with no
        # Start lock held. Its generation must survive that waiter's launch.
        core._hotkey_toggle_macro()
        assert queued_hotkeys == [(1, True)]
        if owner_result in ("finished", "replaced"):
            prepared_start.is_alive.return_value = False
        if owner_result == "replaced":
            assert core._ui_start_macro() == (True, "Started", None)
            assert core._macro_start_generation == 2
    finally:
        release.set()
        admit_waiter.set()
        first.join(2)
        if second.ident is not None:
            second.join(2)
    assert not first.is_alive() and not second.is_alive()
    assert outcomes["second"][0] is (owner_result != "replaced")
    assert len(setup_calls) == (2 if owner_result in ("refused", "replaced") else 1)
    assert prepared_start.start.call_count == (2 if owner_result == "replaced" else 1)
    assert core._macro_start_generation == (2 if owner_result == "replaced" else 1)
    assert core._macro_pending_start_generation is None
    assert core._macro_pending_start_requests == 0
    with pytest.raises(SystemExit):
        core._process_hotkey_toggles()
    assert core.UI_STOP_REQUESTED is (owner_result != "replaced")


def test_cancelled_waiters_cannot_clear_replacement_reservation(queued_hotkeys, prepared_start, monkeypatch):
    preparing, release = threading.Event(), threading.Event()
    waiting = {name: threading.Event() for name in ("old", "new")}
    admitted = {name: threading.Event() for name in waiting}
    real_lock = threading.Lock()

    class StartLock:
        def __enter__(self):
            name = threading.current_thread().name
            if name in waiting:
                waiting[name].set()
                assert admitted[name].wait(2), "test did not admit waiting Start"
            real_lock.acquire()

        def __exit__(self, *_args):
            real_lock.release()

    def size_window(**_kwargs):
        preparing.set()
        assert release.wait(2), "test did not release native setup"
        return True, "Window sized"

    monkeypatch.setattr(core, "_macro_start_lock", StartLock())
    monkeypatch.setattr(core, "ensure_game_windowed", size_window)
    outcomes = {}
    first = threading.Thread(target=lambda: outcomes.update(first=core._ui_start_macro()))
    old = threading.Thread(name="old", target=lambda: outcomes.update(old=core._ui_start_macro()))
    new = threading.Thread(name="new", target=lambda: outcomes.update(new=core._ui_start_macro()))
    first.start()
    try:
        assert preparing.wait(2)
        old.start()
        assert waiting["old"].wait(2)
        core._ui_stop_macro()
        new.start()
        assert waiting["new"].wait(2)
        assert core._macro_start_generation == core._macro_pending_start_generation == 3
        assert core._macro_pending_start_requests == 1
        release.set()
        first.join(2)
        admitted["old"].set()
        old.join(2)
        assert not first.is_alive() and not old.is_alive()
        assert not outcomes["first"][0] and not outcomes["old"][0]
        assert core._macro_pending_start_generation == 3
        assert core._macro_pending_start_requests == 1
        core._hotkey_toggle_macro()
        assert queued_hotkeys == [(3, True)]
    finally:
        release.set()
        for event in admitted.values():
            event.set()
        for thread in (first, old, new):
            if thread.ident is not None:
                thread.join(2)
    assert not any(thread.is_alive() for thread in (first, old, new))
    assert outcomes["new"] == (True, "Started", None)
    prepared_start.start.assert_called_once()
    assert core._macro_start_generation == 3
    with pytest.raises(SystemExit):
        core._process_hotkey_toggles()
    assert core.UI_STOP_REQUESTED and core._macro_start_generation == 4


@pytest.mark.parametrize("failure", ["refusal", "exception", "worker_start"])
def test_failed_start_releases_reservation_for_explicit_retry(failure, queued_hotkeys, prepared_start, monkeypatch):
    def size_window(**_kwargs):
        core._hotkey_toggle_macro()
        if failure == "exception":
            raise RuntimeError("Native setup failed")
        return failure != "refusal", "Window not ready"

    monkeypatch.setattr(core, "ensure_game_windowed", size_window)
    if failure == "worker_start":
        prepared_start.start.side_effect = RuntimeError("Worker failed to start")
    if failure == "refusal":
        assert not core._ui_start_macro()[0]
    else:
        with pytest.raises(RuntimeError):
            core._ui_start_macro()
    assert queued_hotkeys == [(1, True)]
    assert core._macro_pending_start_generation is None
    assert core._macro_pending_start_requests == 0
    monkeypatch.setattr(core, "ensure_game_windowed", Mock(return_value=(True, "Window sized")))
    prepared_start.start.side_effect = lambda: setattr(prepared_start.is_alive, "return_value", True)
    assert core._ui_start_macro()[0]
    assert core._macro_start_generation == 2
    with pytest.raises(SystemExit):
        core._process_hotkey_toggles()
    assert not core.UI_STOP_REQUESTED and prepared_start.is_alive()


def test_start_reservation_does_not_bypass_windows_shutdown_request(prepared_start, monkeypatch):
    monkeypatch.setattr(core, "_shutdown_status", "requesting")
    ok, message, _code = core._ui_start_macro()
    assert not ok and "shutdown request" in message
    assert core._shutdown_status == "requesting"
    assert core._macro_pending_start_generation is None
    assert core._macro_pending_start_requests == 0
    core.update_game_window.assert_not_called()
    prepared_start.start.assert_not_called()


def test_delayed_stop_for_a_finished_run_cannot_stop_a_new_run(queued_hotkeys, monkeypatch):
    previous_worker = Mock(is_alive=lambda: True)
    monkeypatch.setattr(core, "MACRO_THREAD", previous_worker)
    core._hotkey_toggle_macro()
    assert queued_hotkeys == [(0, True)]
    # Simulate natural completion, then start the next worker without a user Stop.
    monkeypatch.setattr(core, "MACRO_THREAD", None)
    worker = Mock()
    worker.is_alive.return_value = False
    worker.start.side_effect = lambda: setattr(worker.is_alive, "return_value", True)
    monkeypatch.setattr(core, "threading", SimpleNamespace(Thread=lambda **_kwargs: worker))
    monkeypatch.setattr(core, "GAME_HWND", 123)
    monkeypatch.setattr(core, "update_game_window", lambda: True)
    monkeypatch.setattr(core, "_calibration_is_active", lambda: False)
    monkeypatch.setattr(core, "TRAINING_ORDER_CUSTOM", ["Health"])
    monkeypatch.setattr(core, "WINDOWED_MODE_ON_START", False)
    monkeypatch.setattr(core, "RESTORE_FULLSCREEN_ON_START", False)
    monkeypatch.setattr(core, "_begin_run_result", Mock())
    monkeypatch.setattr(core, "_telemetry_reset", Mock())
    assert core._ui_start_macro()[0]
    assert core._macro_start_generation == 1
    with pytest.raises(SystemExit):
        core._process_hotkey_toggles()
    assert not core.UI_STOP_REQUESTED and worker.is_alive()


def test_stop_invalidates_a_second_start_waiting_for_native_setup(idle_runtime, monkeypatch):
    preparing, queued, release = (threading.Event() for _ in range(3))
    real_lock = threading.Lock()

    class StartLock:
        def __enter__(self):
            if real_lock.locked():
                queued.set()
            real_lock.acquire()

        def __exit__(self, *_args):
            real_lock.release()

    worker = Mock()
    monkeypatch.setattr(core, "_macro_start_lock", StartLock())
    monkeypatch.setattr(core, "threading", SimpleNamespace(Thread=lambda **_kwargs: worker))
    monkeypatch.setattr(core, "GAME_HWND", 123)
    monkeypatch.setattr(core, "update_game_window", lambda: True)
    monkeypatch.setattr(core, "_calibration_is_active", lambda: False)
    monkeypatch.setattr(core, "TRAINING_ORDER_CUSTOM", ["Health"])
    monkeypatch.setattr(core, "WINDOWED_MODE_ON_START", True)
    monkeypatch.setattr(core, "_begin_run_result", Mock())
    monkeypatch.setattr(core, "_telemetry_reset", Mock())

    def size_window(**_kwargs):
        preparing.set()
        assert release.wait(2), "test did not release native setup"
        return True, "Window sized"

    monkeypatch.setattr(core, "ensure_game_windowed", size_window)
    outcomes = []
    first = threading.Thread(target=lambda: outcomes.append(core._ui_start_macro()))
    second = threading.Thread(target=lambda: outcomes.append(core._ui_start_macro()))
    first.start()
    try:
        assert preparing.wait(2)
        second.start()
        assert queued.wait(2)
        core._ui_stop_macro()
        core._ui_stop_macro()
    finally:
        release.set()
        first.join(2)
        if second.ident is not None:
            second.join(2)
    assert not first.is_alive() and not second.is_alive()
    assert len(outcomes) == 2 and all(not outcome[0] for outcome in outcomes)
    worker.start.assert_not_called()


@pytest.mark.parametrize("clock_values", [[1000, -10000], [1000, 100000]])
def test_safe_sleep_uses_elapsed_time_despite_wall_clock_jump(clock_values, monkeypatch):
    monkeypatch.setattr(core.time, "time", Mock(side_effect=clock_values))
    monkeypatch.setattr(core.time, "monotonic", Mock(side_effect=[10, 10.03, 10.1]))
    monkeypatch.setattr(core.time, "sleep", Mock())
    monkeypatch.setattr(core, "check_exit", Mock())
    monkeypatch.setattr(core, "SHOW_DEBUG_HUD", False)

    core.safe_sleep(.05)

    core.time.time.assert_not_called()
    core.time.sleep.assert_called_once()
    assert core.time.sleep.call_args.args[0] <= .005


def test_retry_wait_has_a_monotonic_deadline(monkeypatch):
    monkeypatch.setattr(core, "_auto_retry_cancelled", lambda: False)
    monkeypatch.setattr(core.time, "time", Mock(side_effect=AssertionError("wall clock used")))
    monkeypatch.setattr(core.time, "monotonic", Mock(side_effect=[10, 10.02, 10.03, 10.2]))
    monkeypatch.setattr(core.time, "sleep", Mock())

    assert core._auto_retry_wait(.1)


def test_run_duration_keeps_real_elapsed_time_with_epoch_timestamps(monkeypatch):
    monkeypatch.setattr(core, "MACRO_STARTED_AT", 1000)
    monkeypatch.setattr(core, "MACRO_STARTED_MONOTONIC", 20)
    monkeypatch.setattr(core.time, "monotonic", lambda: 25)

    assert core._run_elapsed_seconds(-10000) == 5
    assert core._run_elapsed_seconds(100000) == 5


@pytest.mark.parametrize("sent", [False, True])
def test_health_does_not_count_a_hit_when_focus_refuses_input(sent, monkeypatch):
    monkeypatch.setattr(core, "_health_v1_probe", lambda _monitor: {
        "left": 0, "top": 0, "width": 100, "height": 100,
    })
    sct = Mock()
    sct.grab.return_value = np.full((100, 100, 4), (0, 0, 255, 255), dtype=np.uint8)
    monkeypatch.setattr(core, "hardware_tap", Mock(return_value=sent))
    monkeypatch.setattr(core, "safe_sleep", Mock())
    monkeypatch.setattr(core, "TELEMETRY", {**core.TELEMETRY, "health_hits": 0})

    core._logic_health_v1(sct, {})

    assert core.TELEMETRY["health_hits"] == int(sent)


@pytest.mark.parametrize("sent", [False, True])
def test_health_tracker_stays_armed_when_the_hit_is_not_sent(sent, monkeypatch):
    monitor = {"left": 0, "top": 0, "width": 1920, "height": 1080}
    tracker = {"geometry": (0, 0, 1920, 1080), "target": (100, 30, 100, 24),
               "armed": True, "last_hit": 0}
    frame = np.zeros((300, 620, 4), dtype=np.uint8)
    frame[30:54, 140:154] = (0, 0, 255, 255)
    sct = Mock()
    sct.grab.return_value = frame
    monkeypatch.setattr(core, "_HEALTH_V2_STATE", tracker)
    monkeypatch.setattr(core, "hardware_tap", Mock(return_value=sent))
    monkeypatch.setattr(core, "TELEMETRY", {**core.TELEMETRY, "health_hits": 0})
    core._logic_health_v2(sct, monitor)
    core.hardware_tap.assert_called_once_with("f")
    assert core.TELEMETRY["health_hits"] == int(sent)
    assert tracker["armed"] is not sent
    assert (tracker["last_hit"] > 0) is sent


@pytest.mark.parametrize("first, second, overlaps", [
    ("f6", "F6", True), ("shift+f6", "f6+shift", True),
    ("shift+f6", "left shift+f6", True), ("f6", "shift+f6", False),
    ("f6, f7", "f6", True), ("f6, f7", "F6, F7", True),
    ("f6", "f6+f7", True), ("ctrl+f6", "ctrl+f6+f7", True),
    ("f6, f7", "f6, f8", False),
    ("f7", "f6, f7", True), ("f7", "f6, f7, f8", True),
    ("f7, f8", "f6, f7, f8", True), ("f6, f8", "f6, f7, f8", True),
    ("f6, f7", "f8, f7", False),
    ("ctrl", "ctrl+shift+f6", True), ("ctrl, f6", "ctrl+shift, f6", True),
    ("ctrl+shift", "ctrl+shift+f6", True), ("ctrl", "left ctrl+shift+f6", True),
    ("ctrl+f6", "shift+f6", False), ("ctrl+f6", "ctrl+shift+f6", False),
    ("ctrl", "shift+f6", False),
    # Windows layouts can alias the name "right ctrl" to left-control codes.
    # Use physical scan codes when asserting genuinely distinct sided keys.
    ([29], [57629, 64], False),
])
def test_hotkey_aliases_and_chord_order_cannot_duplicate_actions(first, second, overlaps):
    assert core._hotkeys_overlap(first, second) is overlaps
    assert core._hotkeys_overlap(second, first) is overlaps


@pytest.mark.parametrize("first_binding, compound", [
    ("f6", "f6+f7"), ("f6", "f6, f7"),
    ("f7", "f6, f7"), ("f7", "f6, f7, f8"),
    ("f7, f8", "f6, f7, f8"), ("f6, f8", "f6, f7, f8"),
    ("ctrl", "ctrl+windows+f6"), ("ctrl, f6", "ctrl+windows, f6"),
    ("ctrl+windows", "ctrl+windows+f6"),
])
def test_real_hotkey_dispatch_conflicts_are_rejected(first_binding, compound, monkeypatch):
    hooks = []
    monkeypatch.setattr(core.keyboard, "hook", lambda handler, **_kwargs: hooks.append(handler) or Mock())
    first_action, compound_action = Mock(), Mock()
    core._register_edge_hotkey(first_binding, first_action)
    core._register_edge_hotkey(compound, compound_action)
    events = []
    for step in compound.split(","):
        codes = [core.keyboard.key_to_scan_codes(key.strip())[0] for key in step.split("+")]
        events.extend((core.keyboard.KEY_DOWN, code) for code in codes)
        events.extend((core.keyboard.KEY_UP, code) for code in reversed(codes))
    for tick, (event_type, scan_code) in enumerate(events, 1):
        for handler in hooks:
            handler(SimpleNamespace(event_type=event_type, scan_code=scan_code, time=tick / 10))
    first_action.assert_called_once()
    compound_action.assert_called_once()
    assert core._hotkeys_overlap(first_binding, compound)

    monkeypatch.setattr(core, "START_STOP_HOTKEY", first_binding)
    monkeypatch.setattr(core, "PAUSE_HOTKEY", "u")
    monkeypatch.setattr(core, "MANUAL_NEXT_KEY", "l")
    with pytest.raises(ValueError, match="already assigned"):
        core._ui_apply_setting("manual_next_key", compound)


@pytest.mark.parametrize("first_binding, second_binding", [
    ("f6", "f6"), ("ctrl", "ctrl+windows+f6"), ("ctrl, f6", "ctrl+windows, f6"),
])
def test_loaded_duplicate_hotkeys_roll_back_and_preserve_original_file(
        first_binding, second_binding, tmp_path, monkeypatch):
    config = tmp_path / "macro_config.json"
    original = json.dumps({"manual_next_key": first_binding, "pause_hotkey": second_binding})
    config.write_text(original, encoding="utf-8")
    for name, value in {"MACRO_CONFIG_FILE": str(config), "JSON_DIR": str(tmp_path),
                        "MANUAL_NEXT_KEY": "l", "START_STOP_HOTKEY": "f6",
                        "PAUSE_HOTKEY": "u", "_config_backup_required": False}.items():
        monkeypatch.setattr(core, name, value)
    core.load_master_config()
    assert (core.MANUAL_NEXT_KEY, core.START_STOP_HOTKEY, core.PAUSE_HOTKEY) == ("l", "f6", "u")
    assert next(tmp_path.glob("macro_config.invalid-*.json")).read_text(encoding="utf-8") == original


@pytest.mark.parametrize("existing, replacement", [
    ("windows+f6", "f6+windows"), ("ctrl", "ctrl+windows+f6"),
    ("ctrl, f6", "ctrl+windows, f6"),
])
def test_live_hotkey_rebind_rejects_equivalent_chord_before_registration(existing, replacement, monkeypatch):
    monkeypatch.setattr(core, "START_STOP_HOTKEY", existing)
    monkeypatch.setattr(core, "PAUSE_HOTKEY", "u")
    monkeypatch.setattr(core, "MANUAL_NEXT_KEY", "l")
    monkeypatch.setattr(core, "register_manual_next_hotkey", Mock())
    monkeypatch.setattr(core, "save_master_config", Mock())

    with pytest.raises(ValueError, match="already assigned"):
        core._ui_apply_setting("manual_next_key", replacement)

    assert core.MANUAL_NEXT_KEY == "l"
    core.register_manual_next_hotkey.assert_not_called()
    core.save_master_config.assert_not_called()


@pytest.mark.parametrize("binding", ["ctrl+f6", "windows+f6"])
def test_complete_chords_with_different_modifiers_remain_bindable(binding, tmp_path, monkeypatch):
    config = tmp_path / "macro_config.json"
    config.write_text(json.dumps({"start_stop_hotkey": "ctrl+f6", "manual_next_key": "windows+f6"}))
    for name, value in {"MACRO_CONFIG_FILE": str(config), "JSON_DIR": str(tmp_path),
                        "MANUAL_NEXT_KEY": "l", "START_STOP_HOTKEY": "f6",
                        "PAUSE_HOTKEY": "u", "_config_backup_required": False}.items():
        monkeypatch.setattr(core, name, value)
    core.load_master_config()
    assert (core.START_STOP_HOTKEY, core.MANUAL_NEXT_KEY) == ("ctrl+f6", "windows+f6")
    assert not list(tmp_path.glob("macro_config.invalid-*.json"))
    monkeypatch.setattr(core, "MANUAL_NEXT_KEY", "l")
    monkeypatch.setattr(core, "register_manual_next_hotkey", Mock(return_value=True))
    monkeypatch.setattr(core, "save_master_config", Mock())
    core._ui_apply_setting("manual_next_key", "windows+f6")
    core.register_manual_next_hotkey.assert_called_once()
    core.save_master_config.assert_called_once()

    hooks = []
    monkeypatch.setattr(core.keyboard, "hook", lambda handler, **_kwargs: hooks.append(handler) or Mock())
    ctrl_action, windows_action = Mock(), Mock()
    core._register_edge_hotkey("ctrl+f6", ctrl_action)
    core._register_edge_hotkey("windows+f6", windows_action)
    codes = [core.keyboard.key_to_scan_codes(key)[0] for key in binding.split("+")]
    events = [(core.keyboard.KEY_DOWN, code) for code in codes]
    events += [(core.keyboard.KEY_UP, code) for code in reversed(codes)]
    for tick, (event_type, scan_code) in enumerate(events, 1):
        for handler in hooks:
            handler(SimpleNamespace(event_type=event_type, scan_code=scan_code, time=tick / 10))
    assert ctrl_action.call_count == int(binding == "ctrl+f6")
    assert windows_action.call_count == int(binding == "windows+f6")
