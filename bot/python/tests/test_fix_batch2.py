import json
import os
import sys
from collections import deque
from pathlib import Path
from unittest import mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import xynmacro_core as core


def test_stale_display_timer_cannot_clear_new_timer_or_revert_after_keep(monkeypatch):
    timers = []

    class Timer:
        def __init__(self, seconds, callback):
            self.callback = callback
            self.cancelled = False
            timers.append(self)

        def start(self):
            pass

        def cancel(self):
            self.cancelled = True

    monkeypatch.setattr(core.threading, "Timer", Timer)
    with mock.patch.object(core, "display_revert_resolution") as revert:
        core._arm_display_confirm(10)
        core._arm_display_confirm(10)
        timers[0].callback()
        assert core._display_confirm_state()["pending"]
        assert core.display_keep_resolution()[0]
        timers[1].callback()
        revert.assert_not_called()
        assert not core._display_confirm_state()["pending"]


@pytest.mark.parametrize("kind", ["button", "region"])
@pytest.mark.parametrize("had_override", [False, True])
def test_failed_calibration_replace_keeps_old_json_and_reports_error(
        tmp_path, monkeypatch, kind, had_override):
    target = tmp_path / f"{kind}.json"
    target.write_text('{"previous": true}', encoding="utf-8")
    monkeypatch.setattr(core, "JSON_DIR", str(tmp_path))
    monkeypatch.setattr(core, f"_{kind}_overrides_path", lambda: str(target))
    monkeypatch.setattr(core.os, "replace", mock.Mock(side_effect=OSError("disk full")))
    if kind == "button":
        monkeypatch.setattr(core, "BUTTON_CALIBRATION_WAITING", "Health")
        monkeypatch.setattr(core, "BUTTON_CALIBRATION_GENERATION", 42)
        monkeypatch.setattr(core, "BUTTON_CALIBRATION_ERROR", None)
        overrides = {"Health": [1, 2]} if had_override else {}
        monkeypatch.setattr(core, "USER_BUTTON_OVERRIDES", overrides)
        monkeypatch.setattr(core, "BUTTONS", {"Health": (1, 2)})
        monkeypatch.setattr(core, "_capture_click_via_overlay", lambda prompt: (3, 4))
        core._run_button_calibration("Health", 42)
        assert overrides == ({"Health": [1, 2]} if had_override else {})
        assert core.BUTTONS["Health"] == (1, 2)
        assert "disk full" in core.BUTTON_CALIBRATION_ERROR
        assert core.BUTTON_CALIBRATION_WAITING is None
    else:
        monkeypatch.setattr(core, "REGION_CALIBRATION_WAITING", "health_box")
        monkeypatch.setattr(core, "REGION_CALIBRATION_GENERATION", 42)
        monkeypatch.setattr(core, "REGION_CALIBRATION_ERROR", None)
        previous = {"top": 1, "left": 2, "width": 3, "height": 4}
        overrides = {"health_box": previous} if had_override else {}
        monkeypatch.setattr(core, "USER_REGION_OVERRIDES", overrides)
        monkeypatch.setattr(core, "HEALTH_BOX", previous)
        monkeypatch.setattr(core, "AGILITY_BOX", {"top": 9, "left": 10, "width": 11, "height": 12})
        monkeypatch.setattr(core, "_capture_region_via_overlay", lambda prompt: {
            "top": 5, "left": 6, "width": 7, "height": 8,
        })
        core._run_region_calibration("health_box", 42)
        assert overrides == ({"health_box": previous} if had_override else {})
        assert core.HEALTH_BOX == previous
        assert "disk full" in core.REGION_CALIBRATION_ERROR
        assert core.REGION_CALIBRATION_WAITING is None
    assert json.loads(target.read_text(encoding="utf-8")) == {"previous": True}
    assert not list(tmp_path.glob("*.tmp"))


@pytest.mark.parametrize("setting,key_attr,handle_attr", [
    ("start_stop_hotkey", "START_STOP_HOTKEY", "_start_stop_hotkey_handle"),
    ("manual_next_key", "MANUAL_NEXT_KEY", "_manual_next_hotkey_handle"),
    ("pause_hotkey", "PAUSE_HOTKEY", "_pause_hotkey_handle"),
])
def test_failed_hotkey_change_keeps_old_hook_and_setting(monkeypatch, setting, key_attr, handle_attr):
    old_key = getattr(core, key_attr)
    old_hook = mock.Mock()
    monkeypatch.setattr(core, handle_attr, old_hook)
    monkeypatch.setattr(core, "_hotkey_toggle_worker", mock.Mock(is_alive=lambda: True))
    monkeypatch.setattr(core, "_register_edge_hotkey", mock.Mock(side_effect=OSError("hook refused")))
    with mock.patch.object(core, "save_master_config") as save:
        with pytest.raises(ValueError, match="Could not register"):
            core._ui_apply_setting(setting, "f9")
    assert getattr(core, key_attr) == old_key
    assert getattr(core, handle_attr) is old_hook
    old_hook.assert_not_called()
    save.assert_not_called()


@pytest.mark.parametrize("status", ["requested", "failed", "unknown"])
def test_explicit_start_clears_old_shutdown_result(monkeypatch, status):
    monkeypatch.setattr(core, "_shutdown_status", status)
    monkeypatch.setattr(core, "_ui_is_running", lambda: False)
    monkeypatch.setattr(core, "update_game_window", lambda: False)
    monkeypatch.setattr(core, "restore_game_window", lambda: False)
    ok, message, _ = core._ui_start_macro()
    assert not ok and "Open Roblox" in message
    assert core._shutdown_snapshot()["status"] == "idle"


def test_clean_shutdown_command_stops_and_releases_input(tmp_path):

    apps = []
    with mock.patch.object(core, "DATA_DIR", str(tmp_path)), \
         mock.patch.object(core.sys, "stdout", core._TeeBuffer(sys.stdout, deque())), \
         mock.patch.object(core, "_start_parent_watchdog"), \
         mock.patch.object(core, "register_start_stop_hotkey"), \
         mock.patch.object(core, "register_manual_next_hotkey"), \
         mock.patch.object(core, "register_pause_hotkey"), \
         mock.patch.object(core, "_make_exclusive_server",
                           side_effect=lambda app: apps.append(app) or mock.Mock(port=8765)):
        core.run_ui_server(sidecar_pid=os.getpid())
    for running in (True, False):
        with mock.patch.object(core, "_ui_is_running", return_value=running), \
             mock.patch.object(core, "_sidecar_closing", False), \
             mock.patch.object(core, "_ui_stop_macro") as stop, \
             mock.patch.object(core, "_release_macro_input") as release, \
             mock.patch.object(core, "_release_session_marker"):
            response = apps[0].test_client().post(
                "/command", json={"action": "session_shutdown_clean"}
            )
        assert response.json["ok"]
        # An idle close must not release keys the player is holding.
        stop.assert_called_once()
        assert release.call_count == (1 if running else 0)


def test_setting_command_reports_hotkey_registration_failure(tmp_path, monkeypatch):

    apps = []
    old_key = core.START_STOP_HOTKEY
    old_hook = mock.Mock()
    monkeypatch.setattr(core, "_start_stop_hotkey_handle", old_hook)
    monkeypatch.setattr(core, "_hotkey_toggle_worker", mock.Mock(is_alive=lambda: True))
    with mock.patch.object(core, "DATA_DIR", str(tmp_path)), \
         mock.patch.object(core.sys, "stdout", core._TeeBuffer(sys.stdout, deque())), \
         mock.patch.object(core, "_start_parent_watchdog"), \
         mock.patch.object(core, "register_start_stop_hotkey"), \
         mock.patch.object(core, "register_manual_next_hotkey"), \
         mock.patch.object(core, "register_pause_hotkey"), \
         mock.patch.object(core, "_make_exclusive_server",
                           side_effect=lambda app: apps.append(app) or mock.Mock(port=8765)):
        core.run_ui_server(sidecar_pid=os.getpid())
    with mock.patch.object(core, "_register_edge_hotkey", side_effect=OSError("hook refused")), \
         mock.patch.object(core, "save_master_config") as save:
        response = apps[0].test_client().post("/command", json={
            "action": "set", "value": {"key": "start_stop_hotkey", "value": "f9"},
        })
    assert response.json["ok"] is False
    assert "Could not register" in response.json["msg"]
    assert core.START_STOP_HOTKEY == old_key
    assert core._start_stop_hotkey_handle is old_hook
    old_hook.assert_not_called()
    save.assert_not_called()


def test_close_input_release_sends_w_up_and_both_mouse_ups(monkeypatch):
    key_up = mock.Mock()
    send_input = mock.Mock(return_value=2)
    monkeypatch.setattr(core.pydirectinput, "keyUp", key_up)
    monkeypatch.setattr(core, "_user32", mock.Mock(SendInput=send_input))
    core._release_macro_input()
    assert [call.args[0] for call in key_up.call_args_list] == ["w", "s"]  # W walk, S pad walk
    packets = send_input.call_args.args[1]
    assert [packet.mi.dwFlags for packet in packets] == [0x0004, 0x0010]
