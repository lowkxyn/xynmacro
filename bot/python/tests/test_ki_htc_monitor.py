from unittest.mock import Mock

import pytest

import xynmacro_core as core
from test_progression_stall import _run_monitor


@pytest.mark.parametrize("target, mode, checks", [
    ((44, 50), "fixed", 0),     # HTC
    ((20, 20), "fixed", 1),     # GC
    ((20, 20), "off", 0),       # GC with checks turned off
])
def test_monitor_ki_hp_checks_follow_area_and_setting(monkeypatch, target, mode, checks):
    check = Mock(return_value=False)
    monkeypatch.setattr(core, "_ki_hp_check", check)
    monkeypatch.setattr(core, "_new_game_ui", lambda: True)
    monkeypatch.setattr(core, "_healing_monitor_enabled", lambda: True)
    monkeypatch.setattr(core, "_claim_healing", lambda *_a, **_k: True)
    monkeypatch.setattr(core, "_game_window_covering_title", lambda: None)
    monkeypatch.setattr(core, "_new_ui_progression", lambda *_: (None, None))
    monkeypatch.setattr(core, "KI_HP_CHECK_MODE", mode)
    monkeypatch.setattr(core, "PROGRESSION_TARGETS", {"Ki Control": target})
    monkeypatch.setattr(core, "GC_RUN_CONFIRMED", False)
    _run_monitor(monkeypatch, [(10, {}), (45, {})], CURRENT_TRAINING_STATE="Ki Control")
    assert check.call_count == checks



def test_ki_hp_check_off_is_accepted_and_bad_values_are_not(monkeypatch):
    import ui_server
    monkeypatch.setattr(core, "KI_HP_CHECK_MODE", "adaptive")
    ui_server._ui_apply_setting_unlocked("ki_hp_check_mode", "off")
    assert core.KI_HP_CHECK_MODE == "off"
    with pytest.raises(ValueError):
        ui_server._ui_apply_setting_unlocked("ki_hp_check_mode", "never")
    assert core.KI_HP_CHECK_MODE == "off"
