import io
import os
import sys
import threading
from collections import deque
from pathlib import Path
from unittest import mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import xynmacro_core as core


HOTKEYS = [
    ("manual_next_key", "MANUAL_NEXT_KEY", "register_manual_next_hotkey"),
    ("start_stop_hotkey", "START_STOP_HOTKEY", "register_start_stop_hotkey"),
    ("pause_hotkey", "PAUSE_HOTKEY", "register_pause_hotkey"),
]


@pytest.mark.parametrize("setting,attribute,register", HOTKEYS)
@pytest.mark.parametrize("value", ["", " ", "\t", None])
def test_blank_hotkey_is_rejected_without_setting_defaults(setting, attribute, register, value, monkeypatch):
    previous = getattr(core, attribute)
    with mock.patch.object(core, register) as hook, mock.patch.object(core, "save_master_config") as save:
        with pytest.raises(ValueError, match="non-empty hotkey"):
            core._ui_apply_setting(setting, value)
    assert getattr(core, attribute) == previous
    hook.assert_not_called()
    save.assert_not_called()


@pytest.mark.parametrize("setting,attribute,register", HOTKEYS)
def test_space_can_be_bound_and_saved(setting, attribute, register, monkeypatch):
    monkeypatch.setattr(core, attribute, "f8")
    with mock.patch.object(core, register, return_value=True) as hook, mock.patch.object(core, "save_master_config") as save:
        core._ui_apply_setting(setting, "space")
    assert getattr(core, attribute) == "space"
    assert core._ui_config_snapshot()[setting] == "space"
    hook.assert_called_once()
    save.assert_called_once()


@pytest.fixture
def picker_app(tmp_path, monkeypatch):
    apps = []
    monkeypatch.setattr(core, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(core, "SAVE_DIR", str(tmp_path))
    monkeypatch.setattr(core, "_save_dir_picker_lock", threading.Lock())
    monkeypatch.setattr(core.sys, "stdout", core._TeeBuffer(io.StringIO(), deque()))
    for name in ["_start_parent_watchdog", "register_start_stop_hotkey", "register_manual_next_hotkey", "register_pause_hotkey"]:
        monkeypatch.setattr(core, name, mock.Mock())
    monkeypatch.setattr(core, "_make_exclusive_server", lambda app: apps.append(app) or mock.Mock(port=8765))
    core.run_ui_server(sidecar_pid=os.getpid())
    return apps[0]


def test_picker_rejects_a_second_request_while_open_and_allows_retry(picker_app, tmp_path):
    chosen = str(tmp_path / "chosen")

    def choose(**kwargs):
        second = picker_app.test_client().post("/pick_save_dir")
        assert second.json == {"ok": False, "msg": "Folder picker already open"}
        return chosen

    with (
        mock.patch("tkinter.Tk") as root,
        mock.patch("tkinter.filedialog.askdirectory", side_effect=choose) as dialog,
        mock.patch.object(core, "_set_save_dir") as save,
    ):
        assert picker_app.test_client().post("/pick_save_dir").json["ok"] is True
        root.assert_called_once()
        root.return_value.destroy.assert_called_once()
        save.assert_called_once_with(chosen)
        dialog.side_effect = None
        dialog.return_value = ""
        assert picker_app.test_client().post("/pick_save_dir").json["msg"] == "cancelled"
        assert root.call_count == 2
    assert not core._save_dir_picker_lock.locked()


def test_picker_error_destroys_root_and_releases_the_busy_guard(picker_app):
    with mock.patch("tkinter.Tk") as root, mock.patch("tkinter.filedialog.askdirectory", side_effect=RuntimeError("dialog failed")):
        response = picker_app.test_client().post("/pick_save_dir")
    assert response.json == {"ok": False, "msg": "dialog failed"}
    root.return_value.destroy.assert_called_once()
    assert not core._save_dir_picker_lock.locked()
