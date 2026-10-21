import io
import json
import sys
from collections import deque
from pathlib import Path
from unittest import mock

import pytest
from flask import Flask

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import xynmacro_core as core


def test_log_cursor_keeps_entries_with_identical_timestamps(monkeypatch):
    ring = deque(maxlen=400)
    monkeypatch.setattr(core, "_ui_log_ring", ring)
    monkeypatch.setattr(core, "_ui_log_sequence", 0)
    monkeypatch.setattr(core.time, "time", lambda: 123.0)
    tee = core._TeeBuffer(io.StringIO(), ring)

    tee.write("first\n")
    cursor = core._log_entries_since(0)[0]["seq"]
    tee.write("second\n")

    assert [entry["msg"] for entry in core._log_entries_since(cursor)] == ["second"]
    assert ring[0]["t"] == ring[1]["t"]


def test_exclusive_server_keeps_distinct_live_ports():
    first = core._make_exclusive_server(Flask("first"))
    second = core._make_exclusive_server(Flask("second"))
    try:
        assert first.port != second.port
        assert first.port > 0 and second.port > 0
    finally:
        first.server_close()
        second.server_close()


def test_invalid_config_rolls_back_earlier_settings(tmp_path, monkeypatch):
    config = tmp_path / "macro_config.json"
    config.write_text(json.dumps({
        "start_delay_sec": 2.5,
        "restart_health_after_hit": False,
        "health_hit_cooldown_sec": "invalid",
    }), encoding="utf-8")
    monkeypatch.setattr(core, "MACRO_CONFIG_FILE", str(config))
    monkeypatch.setattr(core, "JSON_DIR", str(tmp_path))
    monkeypatch.setattr(core, "START_DELAY", 5.0)
    old_compat = core.COMPATIBILITY_SETTINGS.copy()
    with mock.patch.object(core, "save_master_config"):
        core.load_master_config()
    assert core.START_DELAY == 5.0
    assert core.COMPATIBILITY_SETTINGS == old_compat


@pytest.mark.parametrize("point", [(-1, 20), (1920, 20), (20, 1080)])
def test_button_bounds_reject_outside_points(point):
    with pytest.raises(ValueError):
        core._validated_button_position(point)
    assert core._validated_button_position((1919, 1079)) == [1919, 1079]


@pytest.mark.parametrize("box", [
    {"top": 0, "left": 0, "width": 0, "height": 10},
    {"top": -1, "left": 0, "width": 10, "height": 10},
    {"top": 1070, "left": 0, "width": 10, "height": 11},
    {"top": 0, "left": 1910, "width": 11, "height": 10},
])
def test_region_bounds_reject_invalid_boxes(box):
    with pytest.raises(ValueError):
        core._validated_region_box(box)


def test_invalid_saved_calibration_is_ignored(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "JSON_DIR", str(tmp_path))
    buttons = {"Health": (42, 42), **{k: v for k, v in core.BUTTONS.items() if k != "Health"}}
    monkeypatch.setattr(core, "BUTTONS", buttons)
    monkeypatch.setattr(core, "USER_BUTTON_OVERRIDES", {})
    monkeypatch.setattr(core, "USER_REGION_OVERRIDES", {})
    (tmp_path / "button_calibration.json").write_text(
        json.dumps({"Health": [-1, 10], "Agility": [100, 100]}), encoding="utf-8"
    )
    (tmp_path / "region_calibration.json").write_text(
        json.dumps({"health_box": {"top": 0, "left": 0, "width": 0, "height": 10}}),
        encoding="utf-8",
    )
    core.load_button_overrides()
    core.load_region_overrides()
    assert core.BUTTONS["Health"] == (42, 42)
    assert core.BUTTONS["Agility"] == (100, 100)
    assert "health_box" not in core.USER_REGION_OVERRIDES
