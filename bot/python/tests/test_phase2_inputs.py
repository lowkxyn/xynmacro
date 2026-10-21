from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import xynmacro_core as core


@pytest.mark.parametrize("code", [64, 22, 38])
@pytest.mark.parametrize("extra_modifier", [None, 29, 54, 56])
def test_charge_allows_controls_but_preserves_physical_modifiers(monkeypatch, code, extra_modifier):
    hook, action = Mock(), Mock()
    monkeypatch.setattr(core.keyboard, "hook", hook)
    monkeypatch.setattr(core.keyboard, "parse_hotkey_combinations", lambda _: (((code,),),))
    monkeypatch.setattr(core.keyboard, "is_modifier", lambda key: key in (29, 42, 54, 56))
    monkeypatch.setattr(core, "_CHARGE_HELD", True)
    core._register_edge_hotkey("test", action)
    handler = hook.call_args.args[0]
    codes = [core._CHARGE_SCAN_CODE]
    if extra_modifier is not None:
        codes.append(extra_modifier)
    codes.append(code)
    for tick, key in enumerate(codes):
        handler(SimpleNamespace(event_type="down", scan_code=key, time=10 + tick / 10))
    assert action.call_count == int(extra_modifier is None)


def test_physical_shift_without_charge_still_distinguishes_chords(monkeypatch):
    hook, action = Mock(), Mock()
    monkeypatch.setattr(core.keyboard, "hook", hook)
    monkeypatch.setattr(core.keyboard, "parse_hotkey_combinations", lambda _: (((64,),),))
    monkeypatch.setattr(core.keyboard, "is_modifier", lambda key: key == 42)
    monkeypatch.setattr(core, "_CHARGE_HELD", False)
    core._register_edge_hotkey("f6", action)
    handler = hook.call_args.args[0]
    for key in (42, 64):
        handler(SimpleNamespace(event_type="down", scan_code=key, time=10))
    action.assert_not_called()
