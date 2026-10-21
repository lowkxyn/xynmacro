import json
import subprocess
import sys
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import xynmacro_core as core


@pytest.fixture
def config_path(tmp_path, monkeypatch):
    config = tmp_path / "macro_config.json"
    monkeypatch.setattr(core, "MACRO_CONFIG_FILE", str(config))
    monkeypatch.setattr(core, "JSON_DIR", str(tmp_path))
    monkeypatch.setattr(core, "_config_backup_required", False)
    return config


@pytest.mark.parametrize("original", [
    b'{"start_delay_sec":',
    b'{"start_delay_sec": 2.5, "health_hit_cooldown_sec": "invalid"}',
    b'[]',
    b'\xff',
])
def test_failed_config_is_preserved_before_a_later_save(original, config_path, monkeypatch, capsys):
    config_path.write_bytes(original)
    monkeypatch.setattr(core, "START_DELAY", 5.0)

    core.load_master_config()

    backups = list(config_path.parent.glob("macro_config.invalid-*.json"))
    assert len(backups) == 1
    assert backups[0].read_bytes() == original
    assert str(backups[0]) in capsys.readouterr().out
    assert not config_path.exists()
    assert core.START_DELAY == 5.0

    core.save_master_config()
    assert json.loads(config_path.read_text(encoding="utf-8"))["start_delay_sec"] == 5.0
    assert backups[0].read_bytes() == original


def test_failed_backup_blocks_saves_until_preservation_succeeds(config_path, monkeypatch, capsys):
    original = b'{"start_delay_sec":'
    config_path.write_bytes(original)
    monkeypatch.setattr(core, "START_DELAY", 5.0)

    with mock.patch.object(core.os, "rename", side_effect=PermissionError("backup blocked")):
        core.load_master_config()
        with pytest.raises(PermissionError, match="backup blocked"):
            core._ui_apply_setting("start_delay_sec", 1.0)
        assert core.START_DELAY == 5.0
        assert config_path.read_bytes() == original
        assert not Path(str(config_path) + ".tmp").exists()

    assert "saving is blocked" in capsys.readouterr().out
    core.save_master_config()
    backups = list(config_path.parent.glob("macro_config.invalid-*.json"))
    assert len(backups) == 1
    assert backups[0].read_bytes() == original
    assert core._config_backup_required is False


@pytest.mark.parametrize("value, expected", [("false", False), ("0", False), ("true", True), ("1", True)])
def test_senzu_config_uses_string_boolean_parser(value, expected, config_path, monkeypatch):
    config_path.write_text(json.dumps({"senzu_enabled": value}), encoding="utf-8")
    monkeypatch.setattr(core, "SENZU_ENABLED", not expected)

    core.load_master_config()

    assert core.SENZU_ENABLED is expected
    assert json.loads(config_path.read_text(encoding="utf-8"))["senzu_enabled"] is expected


@pytest.mark.parametrize("value", [False, "false", "0"])
def test_disabling_shutdown_cancels_pending_and_stale_timer(value, config_path, monkeypatch):
    for name, initial in {
        "SHUTDOWN_PC_WHEN_FINISHED": True,
        "_USER_STOP_LATCHED": False,
        "_AFTER_ACTIONS_BLOCKED": False,
        "_shutdown_status": "idle",
        "_shutdown_generation": 0,
        "_shutdown_timer": None,
        "_shutdown_deadline": 0.0,
    }.items():
        monkeypatch.setattr(core, name, initial)

    with (
        mock.patch.object(core.threading, "Timer") as timer,
        mock.patch.object(core.subprocess, "run") as shutdown,
    ):
        assert core._schedule_pc_shutdown("completed", "test completion")
        generation = core._shutdown_generation
        core._ui_apply_setting("shutdown_pc_when_finished", value)
        core._dispatch_pc_shutdown(generation)

    timer.return_value.cancel.assert_called_once()
    shutdown.assert_not_called()
    assert core.SHUTDOWN_PC_WHEN_FINISHED is False
    assert core._shutdown_snapshot()["status"] == "cancelled"


@pytest.mark.inert_python_child
def test_dpi_awareness_is_per_monitor_before_and_after_first_capture():
    script = """
import ctypes, json, sys, threading
from unittest import mock
mode = {"value": 0}
def set_context(context):
    assert context.value == ctypes.c_void_p(-4).value
    mode["value"] = 2
    return True
def get_awareness(_process, pointer):
    pointer._obj.value = mode["value"]
    return 0
# Keep the import/startup contract under test without changing native DPI or
# opening a capture session on the user's desktop.
ctypes.windll.user32.SetProcessDpiAwarenessContext = mock.Mock(side_effect=set_context)
ctypes.windll.shcore.GetProcessDpiAwareness = mock.Mock(side_effect=get_awareness)
sys.path.insert(0, 'python')
import xynmacro_core as core
ctypes.windll.user32.SetProcessDpiAwarenessContext.assert_called_once()
core.mss.MSS = mock.MagicMock()
def awareness():
    value = ctypes.c_int()
    assert ctypes.windll.shcore.GetProcessDpiAwareness(None, ctypes.byref(value)) == 0
    return value.value
modes = [awareness()]
worker = threading.Thread(target=lambda: modes.append(awareness()))
worker.start()
worker.join(2)
assert not worker.is_alive()
with core.mss.MSS():
    modes.append(awareness())
print(json.dumps(modes))
"""
    process = subprocess.run(
        [sys.executable, "-c", script], cwd=Path(__file__).resolve().parents[2],
        capture_output=True, text=True, timeout=20,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert process.returncode == 0, process.stderr
    assert json.loads(process.stdout.strip().splitlines()[-1]) == [2, 2, 2]


def test_dpi_initializer_selects_v2_before_using_legacy_api(monkeypatch):
    set_context = mock.Mock(return_value=True)
    monkeypatch.setattr(core, "_user32", SimpleNamespace(SetProcessDpiAwarenessContext=set_context))
    with mock.patch.object(core._ctypes.windll, "shcore") as shcore:
        core._set_process_dpi_awareness()
    assert set_context.call_args.args[0].value == core._ctypes.c_void_p(-4).value
    shcore.SetProcessDpiAwareness.assert_not_called()


@pytest.mark.parametrize("current_mode, fallback_result", [(0, 0), (2, -1), (1, -1)])
def test_dpi_initializer_handles_fallback_and_preexisting_mode(current_mode, fallback_result, monkeypatch, capsys):
    def read_awareness(_process, pointer):
        pointer._obj.value = current_mode
        return 0

    monkeypatch.setattr(core, "_user32", SimpleNamespace())
    shcore = SimpleNamespace(
        GetProcessDpiAwareness=mock.Mock(side_effect=read_awareness),
        SetProcessDpiAwareness=mock.Mock(return_value=fallback_result),
    )
    with mock.patch.object(core._ctypes.windll, "shcore", shcore):
        core._set_process_dpi_awareness()
    # A locked non-per-monitor mode warns instead of killing the sidecar at import.
    warned = "Could not enable per-monitor DPI awareness" in capsys.readouterr().out
    assert warned == (current_mode == 1)
    if current_mode == 2:
        shcore.SetProcessDpiAwareness.assert_not_called()
    else:
        shcore.SetProcessDpiAwareness.assert_called_once_with(2)


@pytest.mark.parametrize("dpi, expected_scale", [(120, 1.25), (144, 1.5)])
def test_scale_warning_uses_dpi_with_physical_monitor_bounds(dpi, expected_scale, monkeypatch):
    monitor = {
        "device": "TEST_DISPLAY", "width": 2560, "height": 1440,
        "left": -2560, "top": 0, "right": 0, "bottom": 1440, "primary": False,
    }
    monkeypatch.setattr(core, "GAME_HWND", 123)
    monkeypatch.setattr(core, "_current_game_monitor_info", lambda: monitor)
    with (
        mock.patch.object(core.win32api, "EnumDisplaySettings", return_value=SimpleNamespace(
            PelsWidth=2560, PelsHeight=1440, DisplayFrequency=60,
        )),
        mock.patch.object(core._user32, "GetDpiForWindow", return_value=dpi),
    ):
        screen = core._get_screen_info()
    assert screen["scale"] == expected_scale
    assert (screen["width"], screen["height"], screen["left"]) == (2560, 1440, -2560)


@pytest.mark.parametrize("left, top", [(-1700, 0), (0, -900), (-1700, -900), (0, 0)])
def test_overlay_geometry_uses_absolute_monitor_offsets(left, top, monkeypatch):
    for name, value in {
        "GAME_HWND": 123, "GAME_WIDTH": 1600, "GAME_HEIGHT": 900,
        "GAME_OFFSET_X": left, "GAME_OFFSET_Y": top,
    }.items():
        monkeypatch.setattr(core, name, value)
    monkeypatch.setattr(core, "update_game_window", lambda: True)
    geometry, client = core._calibration_monitor_geometry()
    assert geometry == f"1600x900+{left}+{top}"
    assert (client["left"], client["top"]) == (left, top)


@pytest.fixture
def idle_calibration(monkeypatch):
    for name in ("BUTTON_CALIBRATION_WAITING", "REGION_CALIBRATION_WAITING",
                 "BUTTON_CALIBRATION_THREAD", "REGION_CALIBRATION_THREAD", "MACRO_THREAD"):
        monkeypatch.setattr(core, name, None)
    monkeypatch.setattr(core, "_shutdown_status", "idle")
    monkeypatch.setattr(core, "BUTTON_CALIBRATION_GENERATION", 0)
    monkeypatch.setattr(core, "REGION_CALIBRATION_GENERATION", 0)


@pytest.mark.parametrize("active_field", [
    "BUTTON_CALIBRATION_WAITING", "REGION_CALIBRATION_WAITING",
    "BUTTON_CALIBRATION_THREAD", "REGION_CALIBRATION_THREAD",
])
@pytest.mark.parametrize("start_source", ["button", "f6"])
def test_start_is_refused_during_calibration(active_field, start_source, idle_calibration, monkeypatch, capsys):
    active = mock.Mock(is_alive=mock.Mock(return_value=True)) if active_field.endswith("THREAD") else "Health"
    monkeypatch.setattr(core, active_field, active)
    with (
        mock.patch.object(core, "update_game_window") as update,
        mock.patch.object(core.threading.Thread, "start") as start_worker,
    ):
        if start_source == "button":
            assert core._ui_start_macro() == (False, "Finish or cancel calibration first.", None)
        else:
            monkeypatch.setattr(core, "_hotkey_toggle_queue", mock.Mock(
                get=mock.Mock(side_effect=[(core._macro_start_generation, False), StopIteration]),
            ))
            monkeypatch.setattr(core, "_hotkey_toggle_busy", True)
            with pytest.raises(StopIteration):
                core._process_hotkey_toggles()
        update.assert_not_called()
        start_worker.assert_not_called()
    assert "Finish or cancel calibration first." in capsys.readouterr().out


@pytest.mark.parametrize("kind", ["button", "region"])
def test_cancelled_overlay_worker_still_blocks_start_until_exit(kind, idle_calibration, monkeypatch):
    begin = core._ui_calibrate_button_begin if kind == "button" else core._ui_calibrate_region_begin
    cancel = core._ui_calibrate_button_cancel if kind == "button" else core._ui_calibrate_region_cancel
    argument = "Health" if kind == "button" else "health_box"
    worker = mock.Mock(is_alive=mock.Mock(return_value=True))
    monkeypatch.setattr(core, "BUTTON_CALIBRATION_ERROR", None)
    monkeypatch.setattr(core, "REGION_CALIBRATION_ERROR", None)
    with mock.patch.object(core.threading, "Thread", return_value=worker):
        assert begin(argument)[0]
    worker.start.assert_called_once()
    assert cancel()[0]
    assert core._ui_start_macro()[0] is False
    worker.is_alive.return_value = False
    assert core._calibration_is_active() is False


@pytest.mark.parametrize("kind", ["button", "region"])
def test_ui_cancel_closes_overlay_on_its_tk_thread(kind, idle_calibration, monkeypatch):
    root = mock.Mock()
    tkinter = SimpleNamespace(Tk=mock.Mock(return_value=root), Frame=mock.Mock(),
                              Label=mock.Mock(), Canvas=mock.Mock())
    monkeypatch.setitem(sys.modules, "tkinter", tkinter)
    monkeypatch.setattr(core, "_calibration_monitor_geometry", lambda: (
        "1600x900+-1700+-900", {"left": -1700, "top": -900, "width": 1600, "height": 900},
    ))
    waiting_name = "BUTTON_CALIBRATION_WAITING" if kind == "button" else "REGION_CALIBRATION_WAITING"
    monkeypatch.setattr(core, waiting_name, "Health" if kind == "button" else "health_box")
    cancel = core._ui_calibrate_button_cancel if kind == "button" else core._ui_calibrate_region_cancel

    def cancel_during_mainloop():
        root.after.call_args.args[1]()
        root.destroy.assert_not_called()
        cancel()
        root.after.call_args.args[1]()

    root.mainloop.side_effect = cancel_during_mainloop
    capture = core._capture_click_via_overlay if kind == "button" else core._capture_region_via_overlay
    assert capture("test prompt") is None
    root.destroy.assert_called_once()
    assert root.after.call_count == 2


@pytest.mark.parametrize("final_state", [True, None])
@pytest.mark.parametrize("allow_fallback", [False, True])
def test_ignored_f11_never_resizes_fullscreen(final_state, allow_fallback, monkeypatch):
    monkeypatch.setattr(core, "GAME_HWND", 123)
    monkeypatch.setattr(core, "WINDOWED_BLOCK_CODE", None)
    with (
        mock.patch.object(core, "update_game_window", return_value=True),
        mock.patch.object(core, "_monitor_info_for_window", return_value={"width": 2560, "height": 1440}),
        mock.patch.object(core, "game_window_is_fullscreen", side_effect=[True, final_state]),
        mock.patch.object(core, "focus_game_window", return_value=True),
        mock.patch.object(core, "_game_window_frame_padding") as frame,
        mock.patch.object(core, "_move_game_window") as move,
        mock.patch.object(core, "pydirectinput") as keys,
        mock.patch.object(core.time, "sleep"),
    ):
        ok, message = core.ensure_game_windowed(wait=0, allow_fallback=allow_fallback)
    assert ok is False
    assert message == "Roblox did not leave fullscreen (F11 not accepted)."
    assert core.WINDOWED_BLOCK_CODE is None
    keys.keyDown.assert_called_once_with("f11")
    keys.keyUp.assert_called_once_with("f11")
    frame.assert_not_called()
    move.assert_not_called()


def test_unexpected_senzu_error_logs_traceback_and_exits_monitor(monkeypatch, capsys):
    stop_event = mock.Mock(is_set=mock.Mock(side_effect=[False, False, True]))
    capture = mock.MagicMock()
    for name, value in {
        "CURRENT_TRAINING_STATE": "Ki Control", "PROGRESSION_STATE_STARTED_AT": 0.0,
        "PROGRESSION_TRACKED_STATE": None, "PROGRESSION_COMPLETE": None,
        "SENZU_ENABLED": True, "SENZU_DISABLED_FOR_RUN": False, "SENZU_DELAY_SEC": 0.0,
        "SENZU_CONTROLLER_ACTIVE": threading.Event(),
        "SENZU_CONTROLLER_RESUME_REQUIRED": threading.Event(),
        "PROGRESSION_COMPLETE_REQUESTED": threading.Event(),
    }.items():
        monkeypatch.setattr(core, name, value)
    with (
        mock.patch.object(core.mss, "MSS", return_value=capture),
        mock.patch.object(core, "update_game_window", return_value=True),
        mock.patch.object(core, "_gc_death_dialog_visible", return_value=False),
        mock.patch.object(core, "read_progression_state", return_value=(None, None, None)),
        mock.patch.object(core, "_hp_bar_is_red", return_value=True),
        mock.patch.object(core, "eat_senzu", side_effect=RuntimeError("synthetic Senzu error")) as eat,
        mock.patch.object(core, "_stop_for_senzu_failure") as stop_failure,
    ):
        core._background_game_monitor(stop_event)
    output = capsys.readouterr().out
    assert "Traceback (most recent call last)" in output
    assert "RuntimeError: synthetic Senzu error" in output
    assert "[MONITOR]" not in output
    eat.assert_called_once()
    stop_failure.assert_called_once_with("Unexpected RuntimeError: synthetic Senzu error")
    assert stop_event.is_set.call_count == 2
    assert stop_event.wait.call_count == 1
    capture.__exit__.assert_called_once_with(None, None, None)
    assert not core.SENZU_CONTROLLER_ACTIVE.is_set()


def test_refilled_slot_already_visible_needs_no_toggling_h(monkeypatch):
    # Live 2026-10-04: Items list was already up after Inventory closed, and a
    # blind H hid it, failing the slot check after a good refill.
    monkeypatch.setattr(core, "CURRENT_TRAINING_STATE", None)
    monkeypatch.setattr(core, "SENZU_STATUS", "idle")
    with (
        mock.patch.object(core, "_senzu_assets", return_value={"slot": object()}),
        mock.patch.object(core, "_park_menu_cursor"),
        mock.patch.object(core, "_restore_menu_cursor"),
        mock.patch.object(core, "_senzu_abort_requested", return_value=False),
        mock.patch.object(core, "_ensure_training_menu_for_senzu", return_value=True),
        mock.patch.object(core, "_focus_game_for_senzu", side_effect=[True, True, True, False]),
        mock.patch.object(core, "_stable_senzu_slot_state", side_effect=[(False, 0.1), (True, 0.9)]),
        mock.patch.object(core, "_refill_senzu_slot", return_value=True),
        mock.patch.object(core, "_consume_open_senzu_slot", return_value=(True, 0.9)) as consume,
        mock.patch.object(core, "_tap_key_unchecked") as tap,
        mock.patch.object(core, "_stop_for_senzu_failure"),
        mock.patch.object(core, "_grab_reference_box"),
        mock.patch.object(core.cv2, "imwrite"),
        mock.patch.object(core.time, "sleep"),
    ):
        core.eat_senzu(mock.Mock())
    # Only the opening H and the close-before-refill H; none after the refill.
    assert tap.call_args_list[:2] == [mock.call("h")] * 2
    assert mock.call("h") not in tap.call_args_list[2:]
    consume.assert_called_once()


@pytest.mark.parametrize("slot_states, focus_results, expected_h_taps, expected_refills", [
    ([(False, 0.1)], [True, False], 1, 0),
    # Before H, the post-refill look, then the check after the toggling H.
    ([(False, 0.1), (False, 0.1), (False, 0.1)], [True, True, True, False], 3, 1),
    ([(True, 0.9)], [True, False], 1, 0),
])
def test_senzu_closing_h_checks_focus(slot_states, focus_results, expected_h_taps, expected_refills, monkeypatch):
    monkeypatch.setattr(core, "CURRENT_TRAINING_STATE", None)
    monkeypatch.setattr(core, "SENZU_STATUS", "idle")
    with (
        mock.patch.object(core, "_senzu_assets", return_value={"slot": object()}),
        mock.patch.object(core, "_park_menu_cursor"),
        mock.patch.object(core, "_restore_menu_cursor"),
        mock.patch.object(core, "_senzu_abort_requested", return_value=False),
        mock.patch.object(core, "_ensure_training_menu_for_senzu", return_value=True),
        mock.patch.object(core, "_focus_game_for_senzu", side_effect=focus_results) as focus,
        mock.patch.object(core, "_stable_senzu_slot_state", side_effect=slot_states),
        mock.patch.object(core, "_refill_senzu_slot", return_value=True) as refill,
        mock.patch.object(core, "_consume_open_senzu_slot", return_value=(True, 0.9)),
        mock.patch.object(core, "_tap_key_unchecked") as tap,
        mock.patch.object(core, "_stop_for_senzu_failure") as stop_failure,
        mock.patch.object(core, "_grab_reference_box"),
        mock.patch.object(core.cv2, "imwrite"),
        mock.patch.object(core.time, "sleep"),
    ):
        assert core.eat_senzu(mock.Mock()) is False
    assert focus.call_count == len(focus_results)
    assert tap.call_args_list == [mock.call("h")] * expected_h_taps
    assert refill.call_count == expected_refills
    stop_failure.assert_called_once_with("Roblox focus not confirmed before using Senzu")
