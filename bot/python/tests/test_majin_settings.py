import json
from unittest.mock import Mock

import pytest

import settings_schema
import xynmacro_core as core


@pytest.fixture(autouse=True)
def settings(monkeypatch):
    for _, name, _ in settings_schema.SETTING_FIELDS:
        monkeypatch.setattr(core, name, getattr(core, name))
    monkeypatch.setattr(core, "save_master_config", Mock())
    core.reset_user_settings_to_defaults()


def test_defaults_keep_senzu_as_the_primary_healer():
    for snapshot in (core.DEFAULT_USER_SETTINGS, core._master_config_snapshot(), core._ui_config_snapshot()):
        assert snapshot["heal_method"] == "senzu"
        assert snapshot["majin_regen_slot"] == 3
        assert snapshot["majin_regen_senzu_reserve"] is True


@pytest.mark.parametrize("key,value,expected", [
    ("heal_method", "majin_regen", "majin_regen"),
    ("heal_method", "senzu", "senzu"),
    ("majin_regen_slot", 0, 1), ("majin_regen_slot", 20, 8),
    ("majin_regen_slot", 2.9, 2), ("majin_regen_slot", "7", 7),
    ("majin_regen_senzu_reserve", "false", False),
    ("majin_regen_senzu_reserve", "true", True),
])
def test_set_command_validates_using_existing_setting_rules(key, value, expected):
    core._ui_apply_setting(key, value)
    assert core._ui_config_snapshot()[key] == expected
    core.save_master_config.assert_called_once()


@pytest.mark.parametrize("key,value", [
    ("heal_method", "unknown"), ("heal_method", None),
    ("majin_regen_slot", float("nan")), ("majin_regen_slot", float("inf")),
    ("majin_regen_slot", "nope"),
])
def test_invalid_settings_do_not_persist(key, value):
    with pytest.raises(ValueError):
        core._ui_apply_setting(key, value)
    core.save_master_config.assert_not_called()


def test_load_normalizes_method_slot_and_reserve(tmp_path, monkeypatch):
    config = tmp_path / "macro_config.json"
    config.write_text(json.dumps({"heal_method": "unknown", "majin_regen_slot": 99,
                                  "majin_regen_senzu_reserve": "false"}), encoding="utf-8")
    monkeypatch.setattr(core, "MACRO_CONFIG_FILE", str(config))
    monkeypatch.setattr(core, "JSON_DIR", str(tmp_path))
    core.load_master_config()
    assert core.HEAL_METHOD == "senzu"
    assert core.MAJIN_REGEN_SLOT == 8
    assert core.MAJIN_REGEN_SENZU_RESERVE is False


def test_failed_save_restores_the_previous_method():
    core.save_master_config.side_effect = OSError("disk full")
    with pytest.raises(OSError):
        core._ui_apply_setting("heal_method", "majin_regen")
    assert core.HEAL_METHOD == "senzu"
