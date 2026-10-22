from unittest.mock import Mock

import pytest

import xynmacro_core as core
from test_progression_stall import _run_monitor


@pytest.mark.parametrize("target, checks", [((44, 50), 0), ((20, 20), 1)])
def test_monitor_skips_ki_hp_checks_in_htc_only(monkeypatch, target, checks):
    check = Mock(return_value=False)
    monkeypatch.setattr(core, "_ki_hp_check", check)
    monkeypatch.setattr(core, "_new_game_ui", lambda: True)
    monkeypatch.setattr(core, "_healing_monitor_enabled", lambda: True)
    monkeypatch.setattr(core, "_claim_healing", lambda *_a, **_k: True)
    monkeypatch.setattr(core, "_game_window_covering_title", lambda: None)
    monkeypatch.setattr(core, "_new_ui_progression", lambda *_: (None, None))
    monkeypatch.setattr(core, "KI_HP_CHECK_MODE", "fixed")
    monkeypatch.setattr(core, "PROGRESSION_TARGETS", {"Ki Control": target})
    monkeypatch.setattr(core, "GC_RUN_CONFIRMED", False)
    _run_monitor(monkeypatch, [(10, {}), (45, {})], CURRENT_TRAINING_STATE="Ki Control")
    assert check.call_count == checks
