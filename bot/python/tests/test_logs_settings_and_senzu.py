import json
import os
import sys
from collections import deque
from pathlib import Path
from unittest import mock

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import xynmacro_core as core


@pytest.mark.parametrize("bad_marker", [
    ["not", "a", "dict"],
    {"pid": "broken", "clean": False, "version": "old", "started_at": 1, "log": None},
    {"pid": 123, "clean": "false", "version": "old", "started_at": 1, "log": None},
    {"pid": 123, "clean": False, "version": "old", "started_at": [], "log": None},
    {"pid": 123, "clean": False, "version": "old", "started_at": 1, "log": []},
])
def test_bad_session_marker_does_not_block_claim(tmp_path, bad_marker):
    marker = tmp_path / "last_session.json"
    marker.write_text(json.dumps(bad_marker), encoding="utf-8")
    with mock.patch.object(core, "JSON_DIR", str(tmp_path)), \
         mock.patch.object(core, "_pid_alive") as pid_alive:
        core.PREVIOUS_SESSION_CRASH = {"stale": True}
        core._claim_session_marker(None)
    assert core.PREVIOUS_SESSION_CRASH is None
    pid_alive.assert_not_called()
    assert json.loads(marker.read_text(encoding="utf-8"))["pid"] == os.getpid()


def test_legacy_setting_aliases_only_fill_missing_modern_keys(tmp_path):
    config = tmp_path / "macro_config.json"
    config.write_text(json.dumps({
        "start_delay_sec": 5, "START_DELAY": 1,
        "after_switch_wait_sec": 4, "NEW_GAME_WAIT": 1,
        "manual_next_key": "q", "MANUAL_NEXT_KEY": "x",
        "wasd_key_press_delay_sec": 0.21, "AGILITY_KEY_HOLD": 0.03,
        "wasd_stabilize_delay_sec": 0.31, "AGILITY_STABILIZE": 0.04,
        "wasd_post_burst_delay_sec": 0.41, "AGILITY_POST_BURST": 0.05,
    }), encoding="utf-8")
    names = ("START_DELAY", "NEW_GAME_WAIT", "MANUAL_NEXT_KEY", "KEY_PRESS_DELAY",
             "STABILIZE_DELAY", "POST_COMBO_DELAY")
    original = {name: getattr(core, name) for name in names}
    try:
        with mock.patch.object(core, "JSON_DIR", str(tmp_path)), \
             mock.patch.object(core, "MACRO_CONFIG_FILE", str(config)), \
             mock.patch.object(core, "save_master_config"):
            core.load_master_config()
        assert tuple(getattr(core, name) for name in names) == (5, 4, "q", 0.21, 0.31, 0.41)
    finally:
        for name, value in original.items():
            setattr(core, name, value)


@pytest.mark.parametrize("cycle", [False, True])
def test_gravity_stops_when_control_disappears_after_click(cycle):
    # The label stays gone for longer than GRAVITY_MISSING_SEC on a 50 ms clock.
    reads = [(20, 1.0, 0.2, True)] + [(None, 0.0, 0.0, False)] * 100
    clock = iter(i * 0.05 for i in range(10000))
    geometry = {"left": 0, "top": 0, "width": 1920, "height": 1080}
    with mock.patch.object(core, "_load_gravity_templates", return_value=dict.fromkeys(range(0, 101, 10))), \
         mock.patch.object(core.time, "monotonic", side_effect=lambda: next(clock)), \
         mock.patch.object(core, "_read_gc_gravity", side_effect=reads), \
         mock.patch.object(core, "robust_move"), \
         mock.patch.object(core, "safe_sleep"), \
         mock.patch.object(core, "check_exit"), \
         mock.patch.object(core, "click_at") as click:
        if cycle:
            result = core._cycle_gc_gravity_to_zero(None, geometry)
        else:
            result = core._raise_gc_gravity(None, geometry, 30)
    assert result is False
    click.assert_called_once()


@pytest.mark.parametrize("cycle", [False, True])
def test_gravity_ignores_one_blank_poll_after_click(cycle):
    expected = 0 if cycle else 30
    reads = [(100 if cycle else 20, 1.0, 0.2, True), (None, 0.0, 0.0, False),
             (expected, 1.0, 0.2, True)]
    geometry = {"left": 0, "top": 0, "width": 1920, "height": 1080}
    with mock.patch.object(core, "_load_gravity_templates", return_value=dict.fromkeys(range(0, 101, 10))), \
         mock.patch.object(core, "_read_gc_gravity", side_effect=reads), \
         mock.patch.object(core, "robust_move"), \
         mock.patch.object(core, "safe_sleep"), \
         mock.patch.object(core, "check_exit"), \
         mock.patch.object(core, "click_at") as click:
        result = (core._cycle_gc_gravity_to_zero(None, geometry) if cycle
                  else core._raise_gc_gravity(None, geometry, 30))
    assert result is True
    click.assert_called_once()


def test_health_error_logs_once_per_five_seconds_and_control_exceptions_escape():
    with mock.patch.object(core, "HEALTH_MODE", "v2_track"), \
         mock.patch.object(core, "_logic_health_v2", side_effect=RuntimeError("capture failed")), \
         mock.patch.object(core.time, "monotonic", side_effect=[10.0, 12.0, 15.1]), \
         mock.patch.object(core, "_health_last_error_log_ts", 0.0), \
         mock.patch("builtins.print") as printed:
        for _ in range(3):
            core.logic_health(None, None)
    assert printed.call_count == 2
    printed.assert_any_call("[HEALTH] RuntimeError: capture failed")
    for exception in (core.QuitException, core.SkipMinigameException):
        with mock.patch.object(core, "HEALTH_MODE", "v2_track"), \
             mock.patch.object(core, "_logic_health_v2", side_effect=exception):
            with pytest.raises(exception):
                core.logic_health(None, None)


@pytest.mark.parametrize("walk", [False, True])
def test_respawn_settle_waits_once_with_or_without_walk(walk):
    with mock.patch.object(core, "AUTO_RETRY_WALK_OUT", walk), \
         mock.patch.object(core, "AUTO_RETRY_RECOVERY_MODE", "wait_for_death"), \
         mock.patch.dict(core.COMPATIBILITY_SETTINGS, {"respawn_settle_sec": 6}), \
         mock.patch.object(core, "_auto_retry_cancelled", return_value=False), \
         mock.patch.object(core, "_auto_retry_wait_for_death", return_value=True), \
         mock.patch.object(core, "_auto_retry_wait", return_value=True) as wait, \
         mock.patch.object(core, "focus_game_window", return_value=False):
        assert core._auto_retry_recover() is (not walk)
    wait.assert_called_once_with(6)


def test_log_tail_reads_from_end_in_chunks(tmp_path):
    path = tmp_path / "large.log"
    path.write_text("old line\n" * 10000 + "last one\nlast two\n", encoding="utf-8")
    real_open = open
    reads = []

    class TrackedFile:
        def __init__(self, file):
            self.file = file

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return self.file.__exit__(*args)

        def __getattr__(self, name):
            return getattr(self.file, name)

        def read(self, size=-1):
            reads.append(size)
            return self.file.read(size)

    def tracked_open(name, mode, *args, **kwargs):
        file = real_open(name, mode, *args, **kwargs)
        return TrackedFile(file) if name == path else file

    with mock.patch("builtins.open", side_effect=tracked_open):
        assert core._read_log_tail(path, 2) == "last one\nlast two"
    assert reads and sum(reads) < path.stat().st_size


def _test_app(tmp_path):
    apps = []
    with mock.patch.object(core, "DATA_DIR", str(tmp_path)), \
         mock.patch.object(core, "SAVE_DIR", str(tmp_path)), \
         mock.patch.object(core.sys, "stdout", core._TeeBuffer(sys.stdout, deque())), \
         mock.patch.object(core, "_start_parent_watchdog"), \
         mock.patch.object(core, "register_start_stop_hotkey"), \
         mock.patch.object(core, "register_manual_next_hotkey"), \
         mock.patch.object(core, "register_pause_hotkey"), \
         mock.patch.object(core, "_make_exclusive_server", side_effect=lambda app: apps.append(app) or mock.Mock(port=8765)):
        core.run_ui_server(sidecar_pid=os.getpid())
    return apps[0]


def test_save_dir_write_failure_keeps_old_dir_and_endpoint_reports_error(tmp_path):
    app = _test_app(tmp_path)
    old_dir = core.SAVE_DIR
    try:
        with mock.patch.object(core, "_save_dir_pref_file", return_value=str(tmp_path / "missing" / "pref.txt")), \
             mock.patch("tkinter.filedialog.askdirectory", return_value=str(tmp_path / "new")):
            response = app.test_client().post("/pick_save_dir")
        assert response.json["ok"] is False
        assert core.SAVE_DIR == old_dir
    finally:
        core.SAVE_DIR = old_dir


def test_log_exports_in_same_second_keep_both_files(tmp_path):
    app = _test_app(tmp_path)
    with mock.patch.object(core, "SAVE_DIR", str(tmp_path)), \
         mock.patch.object(core, "_ui_log_ring", deque([{"t": 0, "msg": "first"}])), \
         mock.patch.object(core.time, "strftime", return_value="20260926-120000"):
        first = app.test_client().post("/save_log").json
        core._ui_log_ring.append({"t": 0, "msg": "second"})
        second = app.test_client().post("/save_log").json
    assert first["ok"] and second["ok"]
    assert first["path"] != second["path"]
    assert "second" not in Path(first["path"]).read_text(encoding="utf-8")
    assert "second" in Path(second["path"]).read_text(encoding="utf-8")


def test_full_session_log_exports_in_same_second_keep_both_files(tmp_path):
    app = _test_app(tmp_path)
    source = tmp_path / "session.log"
    source.write_text("first\n", encoding="utf-8")
    with source.open("a+", encoding="utf-8") as handle, \
         mock.patch.object(core, "SAVE_DIR", str(tmp_path)), \
         mock.patch.object(core.sys, "stdout", core._TeeBuffer(sys.stdout, deque(), handle)), \
         mock.patch.object(core.time, "strftime", return_value="20260926-120000"):
        first = app.test_client().post("/save_log").json
        handle.write("second\n")
        second = app.test_client().post("/save_log").json
    assert first["ok"] and second["ok"]
    assert first["full_session"] and second["full_session"]
    assert first["path"] != second["path"]
    assert "second" not in Path(first["path"]).read_text(encoding="utf-8")
    assert "second" in Path(second["path"]).read_text(encoding="utf-8")


@pytest.mark.parametrize("full_session", [False, True])
def test_log_export_scrubs_profile_paths(tmp_path, full_session):
    app = _test_app(tmp_path)
    profile = r"C:\Users\sampleuser"
    line = rf"[sidecar] Data dir: {profile}\AppData\XynMacro"
    source = tmp_path / "session.log"
    source.write_text(line + "\n", encoding="utf-8")
    ring = deque([{"t": 0, "msg": line}])
    with source.open("a+", encoding="utf-8") as handle, \
         mock.patch.object(core, "SAVE_DIR", str(tmp_path)), \
         mock.patch.object(core, "_ui_log_ring", ring), \
         mock.patch.object(core.sys, "stdout", core._TeeBuffer(sys.stdout, deque(), handle if full_session else None)), \
         mock.patch.dict(os.environ, {"USERPROFILE": profile, "USERNAME": "sampleuser"}):
        response = app.test_client().post("/save_log").json
    exported = Path(response["path"]).read_text(encoding="utf-8")
    assert response["ok"] and response["full_session"] is full_session
    assert "sampleuser" not in exported
    assert "%USERPROFILE%" in exported


def test_log_export_closes_created_file_if_session_flush_fails(tmp_path):
    app = _test_app(tmp_path)
    source = tmp_path / "session.log"
    source.write_text("source\n", encoding="utf-8")
    handle = mock.Mock()
    handle.name = str(source)
    handle.flush.side_effect = OSError("flush failed")
    real_open = open
    created = []

    def tracked_open(path, mode="r", *args, **kwargs):
        opened = real_open(path, mode, *args, **kwargs)
        if mode == "xb":
            created.append(opened)
        return opened

    with mock.patch.object(core, "SAVE_DIR", str(tmp_path)), \
         mock.patch.object(core.sys, "stdout", mock.Mock(_file=handle)), \
         mock.patch("builtins.open", side_effect=tracked_open):
        response = app.test_client().post("/save_log").json
    assert response["ok"] is False
    assert "flush failed" in response["msg"]
    assert len(created) == 1 and created[0].closed


@pytest.mark.parametrize("action", ["reset_defaults", "factory_reset"])
def test_reset_reports_failed_default_hotkey_registration(tmp_path, action):
    app = _test_app(tmp_path)
    with mock.patch.object(core, "_ui_is_running", return_value=False), \
         mock.patch.object(core, "reset_user_settings_to_defaults"), \
         mock.patch.object(core, "save_master_config"), \
         mock.patch.object(core, "factory_reset_configuration"), \
         mock.patch.object(core.notifications, "clear"), \
         mock.patch.object(core, "_cancel_pc_shutdown"), \
         mock.patch.object(core, "register_start_stop_hotkey", return_value=True) as start, \
         mock.patch.object(core, "register_manual_next_hotkey", return_value=False) as manual, \
         mock.patch.object(core, "register_pause_hotkey", return_value=True) as pause:
        response = app.test_client().post("/command", json={"action": action}).json
    assert response["ok"] is False
    assert (f"Could not register the Skip Current Stat hotkey ({core.MANUAL_NEXT_KEY.upper()}). "
            "Another app may be using it.") in response["msg"]
    start.assert_called_once_with()
    manual.assert_called_once_with()
    pause.assert_called_once_with()


def test_unreadable_senzu_fallback_rescans_at_current_position():
    with mock.patch.object(core, "_validate_cached_senzu_row", return_value=None), \
         mock.patch.object(core, "_senzu_type_priority", return_value=["full", "half"]), \
         mock.patch.object(core, "_find_senzu_row", side_effect=[
             (500, None, 0.9), (None, None, 0.2), (620, None, 0.91),
         ]) as find:
        assert core._find_preferred_senzu_row(None, {}) == (620, None, 0.91, "full")
    assert [call.args[2] for call in find.call_args_list] == ["full", "half", "full"]


def test_missing_senzu_on_rescan_does_not_return_stale_row():
    with mock.patch.object(core, "_validate_cached_senzu_row", return_value=None), \
         mock.patch.object(core, "_senzu_type_priority", return_value=["full", "half"]), \
         mock.patch.object(core, "_find_senzu_row", side_effect=[
             (500, None, 0.9), (None, None, 0.2), (None, None, 0.1),
         ]):
        row_y, count, score, bean_type = core._find_preferred_senzu_row(None, {})
    assert row_y is None and count is None and bean_type is None


def test_senzu_scan_requires_visible_inventory_before_input():
    with mock.patch.object(core, "_template_in_reference_box", return_value=(False, 0.1, None)) as visible, \
         mock.patch.object(core, "robust_move") as move:
        assert core._find_senzu_row(None, {"inventory": object()}) == (None, None, 0.0)
    visible.assert_called()
    move.assert_not_called()


def test_ki_no_dot_images_only_after_five_seconds_or_verbose(tmp_path):
    frame = np.zeros((8, 8, 4), dtype=np.uint8)
    monitor = {"left": 0, "top": 0, "width": 8, "height": 8}
    sct = mock.Mock()
    sct.grab.return_value = frame
    with mock.patch.object(core, "JSON_DIR", str(tmp_path)), \
         mock.patch.object(core, "_ki_v8_no_dot_last_log_ts", 0.0), \
         mock.patch.object(core, "_ki_v8_no_dot_since", None), \
         mock.patch.object(core, "VERBOSE_DETECTOR_LOGS", False), \
         mock.patch.object(core, "check_exit"), \
         mock.patch.object(core.time, "monotonic", side_effect=[2, 2, 4, 4, 8, 8, 10, 10]), \
         mock.patch.object(core.cv2, "imwrite") as write, \
         mock.patch.object(core, "_ki_v8_state", {**core._ki_v8_state, "last_click_at": -100}):
        for _ in range(2):
            core.logic_ki_v8(sct, monitor)
        write.assert_not_called()
        for _ in range(2):
            core.logic_ki_v8(sct, monitor)
        assert write.call_count >= 2

    with mock.patch.object(core, "JSON_DIR", str(tmp_path)), \
         mock.patch.object(core, "_ki_v8_no_dot_last_log_ts", 0.0), \
         mock.patch.object(core, "_ki_v8_no_dot_since", None), \
         mock.patch.object(core, "VERBOSE_DETECTOR_LOGS", True), \
         mock.patch.object(core, "check_exit"), \
         mock.patch.object(core.time, "monotonic", side_effect=[2, 2]), \
         mock.patch.object(core.cv2, "imwrite") as write, \
         mock.patch.object(core, "_ki_v8_state", {**core._ki_v8_state, "last_click_at": -100}):
        core.logic_ki_v8(sct, monitor)
        assert write.call_count >= 2
