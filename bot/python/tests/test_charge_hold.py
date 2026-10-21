import sys
from pathlib import Path
from unittest.mock import patch

import cv2
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import training_ui
import xynmacro_core as core

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "ki_charge"


def _ready(**overrides):
    values = dict(CONTROLLER_PAUSED=False, PAUSE_TOGGLE_REQUESTED=False, TRAINING_MENU_VISIBLE=False,
                  GC_RECOVERY_VERIFY_PENDING=False, UI_STOP_REQUESTED=False,
                  _USER_STOP_LATCHED=False, _CHARGE_HELD=False, _CHARGING_NOW=None)
    values.update(overrides)
    return patch.multiple(core, **values)


def test_charge_mode_maps_old_values_and_rejects_unknown_ones():
    with patch.dict(core.COMPATIBILITY_SETTINGS):
        core._set_compatibility_setting("charge_hold_mode", "until_ki_control")
        assert core.COMPATIBILITY_SETTINGS["charge_hold_mode"] == "third"
        core._set_compatibility_setting("charge_hold_mode", "whole_run")
        assert core.COMPATIBILITY_SETTINGS["charge_hold_mode"] == "third"
        core._set_compatibility_setting("charge_hold_mode", "second")
        assert core.COMPATIBILITY_SETTINGS["charge_hold_mode"] == "second"
        with pytest.raises(ValueError):
            core._set_compatibility_setting("charge_hold_mode", "always")


def test_shift_is_only_wanted_while_charging():
    with _ready(), patch.dict(core.COMPATIBILITY_SETTINGS, charge_hold_mode="third"), \
         patch.object(core, "_game_has_focus", return_value=True):
        assert not core._charge_wanted("Health")      # in a minigame: never
        core._CHARGING_NOW = "controller"
        assert core._charge_wanted(None)
        core.COMPATIBILITY_SETTINGS["charge_hold_mode"] = "off"
        assert not core._charge_wanted(None)


def test_healing_blocks_a_controller_charge_but_not_its_own():
    with _ready(), patch.dict(core.COMPATIBILITY_SETTINGS, charge_hold_mode="third"),          patch.object(core, "_game_has_focus", return_value=True),          patch.object(core.SENZU_CONTROLLER_ACTIVE, "is_set", return_value=True):
        core._CHARGING_NOW = "controller"
        assert not core._charge_wanted(None)
        core._CHARGING_NOW = "healing"
        assert core._charge_wanted(None)


def test_charge_released_while_paused_in_menu_or_unfocused():
    with patch.dict(core.COMPATIBILITY_SETTINGS, charge_hold_mode="third"):
        with _ready(_CHARGING_NOW="controller", CONTROLLER_PAUSED=True), \
             patch.object(core, "_game_has_focus", return_value=True):
            assert not core._charge_wanted(None)
        with _ready(_CHARGING_NOW="controller", TRAINING_MENU_VISIBLE=True), \
             patch.object(core, "_game_has_focus", return_value=True):
            assert not core._charge_wanted(None)
        with _ready(_CHARGING_NOW="controller"), patch.object(core, "_game_has_focus", return_value=False):
            assert not core._charge_wanted(None)


def test_menu_keys_release_shift_but_minigame_keys_keep_it():
    with _ready(_CHARGE_HELD=True), patch.object(core.pydirectinput, "keyDown") as down, \
         patch.object(core.pydirectinput, "keyUp") as up, patch.object(core.time, "sleep"):
        core._tap_key_unchecked("w", minigame=True)
        assert core._CHARGE_HELD
        assert [c.args[0] for c in up.call_args_list] == ["w"]
        core._tap_key_unchecked("tab")
        assert not core._CHARGE_HELD
        assert [c.args[0] for c in up.call_args_list] == ["w", "shift", "tab"]
        assert [c.args[0] for c in down.call_args_list] == ["w", "tab"]


def test_charge_never_starts_after_stop():
    with _ready(_USER_STOP_LATCHED=True, _CHARGING_NOW="controller"), \
         patch.object(core.pydirectinput, "keyDown") as down:
        core._set_charge_hold(True)
    down.assert_not_called()


@pytest.mark.parametrize("name, low, high", [
    ("charge_03s", 0.99, 1.01),    # first bar full
    ("charge_05s", 0.88, 0.97),    # first bar part empty
    ("charge_09s", 1.30, 1.40),    # second bar filling
    ("charge_13s", 1.90, 1.99),
    ("charge_17s", 2.22, 2.32),    # third bar filling
    ("charge_21s", 2.95, 3.00),    # fully charged
    ("charge_24s", 2.20, 2.30),    # after a super from full
    ("full_layer1", 0.99, 1.01),
])
def test_ki_charge_reads_each_layer(name, low, high):
    level = training_ui.read_ki_charge(cv2.imread(str(FIXTURES / f"{name}.png")))
    assert low <= level <= high


def test_ki_charge_is_unknown_while_the_hud_is_hidden():
    assert training_ui.read_ki_charge(cv2.imread(str(FIXTURES / "hud_hidden.png"))) is None


def _charge(levels, mode="third", **kwargs):
    readings = iter(levels)
    with _ready(), patch.dict(core.COMPATIBILITY_SETTINGS, charge_hold_mode=mode),          patch.object(core, "_KI_CHARGE_CEILING", core._KI_CHARGE_CEILING), \
         patch.object(core, "_new_game_ui", return_value=True), \
         patch.object(core, "_game_has_focus", return_value=True), \
         patch.object(core, "_ki_charge_level", side_effect=lambda _sct: next(readings)), \
         patch.object(core.pydirectinput, "keyDown") as down, \
         patch.object(core.pydirectinput, "keyUp") as up, \
         patch.object(core.time, "sleep"):
        charged = core.charge_ki_bar(object(), "test", **kwargs)
        held_after = core._CHARGE_HELD
        _charge.ceiling = core._KI_CHARGE_CEILING
    return charged, down, up, held_after


def test_charges_until_the_target_layer_then_lets_go():
    charged, down, up, held_after = _charge([1.0, 1.6, 2.4, 2.99])
    assert charged
    down.assert_called_once_with("shift")
    up.assert_called_once_with("shift")
    assert not held_after


def test_second_bar_target_stops_at_two():
    charged, down, _up, _held = _charge([1.0, 1.98], mode="second")
    assert charged
    charged, down, _up, _held = _charge([2.5], mode="second")
    assert not charged
    down.assert_not_called()


def test_off_or_already_full_never_touches_shift():
    for mode, levels in (("off", [1.0]), ("third", [2.99])):
        charged, down, _up, _held = _charge(levels, mode=mode)
        assert not charged
        down.assert_not_called()


def test_recharge_waits_until_the_bar_is_a_layer_below_target():
    charged, down, _up, _held = _charge([2.2], only_if_drained=True)
    assert not charged
    down.assert_not_called()
    # LSSJ settles on a full second layer; that is a whole layer below the third.
    charged, down, _up, _held = _charge([2.0, 3.0], only_if_drained=True)
    assert charged
    charged, down, _up, _held = _charge([1.5], mode="second", only_if_drained=True)
    assert not charged
    charged, down, _up, _held = _charge([1.0, 2.0], mode="second", only_if_drained=True)
    assert charged


def test_a_form_that_stops_below_the_target_is_not_charged_forever():
    clock = iter(range(0, 100))
    with patch.object(core.time, "monotonic", side_effect=lambda: next(clock)):
        charged, _down, up, held_after = _charge([1.0] + [2.0] * 50)
    assert charged
    up.assert_called_once_with("shift")
    assert not held_after


def test_hidden_hud_skips_charging():
    charged, down, _up, _held = _charge([None])
    assert not charged
    down.assert_not_called()


def _ticking(step):
    times = iter(i * step for i in range(1000))
    return patch.object(core.time, "monotonic", side_effect=lambda: next(times))


def test_a_short_dim_while_leaving_training_does_not_stop_charging():
    with _ticking(0.2):
        charged, _down, up, held_after = _charge([1.0, None, None, None, 1.8, 2.6, 3.0])
    assert charged
    up.assert_called_once_with("shift")
    assert not held_after


def test_a_bar_hidden_for_long_stops_charging():
    with _ticking(0.2):
        charged, _down, up, held_after = _charge([1.0] + [None] * 30)
    assert charged
    up.assert_called_once_with("shift")
    assert not held_after


def test_a_form_capped_below_the_target_is_drained_only_below_its_own_ceiling():
    with _ticking(0.2):
        _charge([1.0] + [2.0] * 50)
    assert _charge.ceiling == 2.0
    with patch.object(core, "_KI_CHARGE_CEILING", 2.0):
        # Sitting at its own top layer is full for this form, not a layer short.
        assert not core._ki_charge_drained(2.0, 3.0)
        assert core._ki_charge_drained(1.0, 3.0)
    with patch.object(core, "_KI_CHARGE_CEILING", None):
        assert core._ki_charge_drained(2.0, 3.0)


def test_mid_stat_recharge_leaves_charges_and_resumes_in_that_order():
    order = []
    with patch.object(core, "_senzu_assets", return_value={}),          patch.object(core, "_menu_cursor_guard"),          patch.object(core, "_leave_training_to_gameplay", side_effect=lambda _s: order.append("leave") or True),          patch.object(core, "charge_ki_bar", side_effect=lambda *a, **k: order.append("charge")),          patch.object(core, "_senzu_abort_requested", return_value=False),          patch.object(core, "_ensure_training_menu_for_senzu", side_effect=lambda *a: order.append("menu") or True),          patch.object(core, "_resume_training_after_senzu", side_effect=lambda *a: order.append("resume") or True):
        core._mid_stat_recharge(object())
    assert order == ["leave", "charge", "menu", "resume"]


def test_mid_stat_recharge_raises_when_it_cannot_get_back_in():
    with patch.object(core, "_senzu_assets", return_value={}),          patch.object(core, "_menu_cursor_guard"),          patch.object(core, "_leave_training_to_gameplay", return_value=True),          patch.object(core, "charge_ki_bar"),          patch.object(core, "_senzu_abort_requested", return_value=False),          patch.object(core, "_ensure_training_menu_for_senzu", return_value=True),          patch.object(core, "_resume_training_after_senzu", return_value=False):
        with pytest.raises(RuntimeError):
            core._mid_stat_recharge(object())


def test_charging_stops_after_ki_control_only_when_the_option_is_on():
    with patch.object(core, "_KI_CONTROL_DONE", False),          patch.object(core, "STOP_CHARGING_AFTER_KI_CONTROL", True):
        core._mark_ki_control_done()
        charged, down, _up, _held = _charge([1.0, 3.0])
        assert not charged
        down.assert_not_called()
    with patch.object(core, "_KI_CONTROL_DONE", True),          patch.object(core, "STOP_CHARGING_AFTER_KI_CONTROL", False):
        charged, _down, _up, _held = _charge([1.0, 3.0])
        assert charged


def test_a_charge_that_is_held_off_reports_nothing_charged():
    # Right after a respawn the macro holds charging off; the mid-stat check
    # must see that as "couldn't charge" and back off instead of stepping out
    # of the minigame every few seconds.
    with patch.object(core, "_charge_wanted", return_value=False):
        charged, down, _up, _held = _charge([1.0, 3.0])
    assert not charged
    down.assert_not_called()
