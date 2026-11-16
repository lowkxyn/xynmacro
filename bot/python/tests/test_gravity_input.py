"""November 2026 GC gravity panel: a number box with Apply replaced the
click-to-cycle "Gravity: 40G" button.

Fixtures are 270x80 crops at reference (400, 296) of live 1920x1080 frames
captured 2026-11-16 (no HUD or names).
"""
from pathlib import Path
from unittest.mock import patch

import cv2
import pytest

import training_ui as ui
import xynmacro_core as core

FIXTURES = Path(__file__).parent / "fixtures" / "ui_2026_11"
PANEL_LEFT, PANEL_TOP = 400, 296
GEOMETRY = {"left": 0, "top": 0, "width": 1920, "height": 1080}


def crop(name, box):
    image = cv2.imread(str(FIXTURES / f"{name}.png"))
    assert image is not None, name
    x, y, width, height = box
    return image[y - PANEL_TOP:y - PANEL_TOP + height, x - PANEL_LEFT:x - PANEL_LEFT + width]


@pytest.fixture(scope="module")
def templates():
    loaded = ui.load_gravity_input_templates()
    assert set(loaded["digits"]) == set("0123456789")
    return loaded


@pytest.mark.parametrize("name, value", [
    ("gravity_box_276", 276),
    ("gravity_box_1", 1),
    ("gravity_box_100_apply_hovered", 100),
    ("gravity_box_100_typing", 100),   # the focused box draws a caret after the digits
])
def test_number_box_reads_live_values(templates, name, value):
    assert ui.gravity_apply_visible(crop(name, ui.GRAVITY_APPLY_BOX), templates)
    assert ui.read_gravity_input(crop(name, ui.GRAVITY_INPUT_BOX), templates) == value


def test_no_number_box_while_the_training_menu_is_closed(templates):
    assert not ui.gravity_apply_visible(
        crop("gravity_box_menu_closed", ui.GRAVITY_APPLY_BOX), templates)


def test_old_button_reader_does_not_see_the_number_box():
    """Why the macro used to log "GC control not found" in the new GC panel."""
    label = crop("gravity_box_276", ui.GRAVITY_BOX)
    assert ui.read_gravity(label, ui.load_gravity_templates())[3] is False


class FakeGame:
    """Box readings in order, plus every key and click the macro sends."""

    def __init__(self, *readings):
        self.readings = list(readings)
        self.keys = []
        self.focus_lost_after = None   # number of keys after which Roblox loses focus

    def focused(self):
        return self.focus_lost_after is None or len(self.keys) < self.focus_lost_after

    def read(self, *_args):
        return self.readings.pop(0)

    def tap(self, key):
        self.keys.append(key)
        return True


def run_set(game, target, **kwargs):
    with patch.object(core, "_read_gc_gravity_input", side_effect=game.read), \
         patch.object(core, "_tap_key_unchecked", side_effect=lambda key, **_kw: game.tap(key)), \
         patch.object(core, "_game_has_focus", side_effect=game.focused), \
         patch.object(core, "_wait_while_game_covered"), \
         patch.object(core, "check_exit"), \
         patch.object(core, "_gravity_input_select_all",
                      side_effect=lambda _after_run: game.keys.append("ctrl+a")), \
         patch.object(core, "click_at", side_effect=lambda *_a: game.keys.append("click")), \
         patch.object(core, "_after_run_wait_training_menu", return_value=True), \
         patch.object(core, "_training_menu_template", return_value=object()), \
         patch.object(core, "robust_move"), \
         patch.object(core, "safe_sleep") as sleep, \
         patch.object(core.time, "sleep"):
        result = core._set_gc_gravity_by_input(None, GEOMETRY, target, **kwargs)
    return result, sleep


def test_raise_types_the_target_presses_enter_and_confirms_after_reopening(capsys):
    game = FakeGame((True, 1), (True, 100), (True, 100))
    result, _sleep = run_set(game, 100)
    assert result is True
    assert game.keys == ["click", "ctrl+a", "1", "0", "0", "enter", "tab", "tab"]
    assert "Raised 1G -> 100G" in capsys.readouterr().out


def test_raise_never_lowers_a_higher_gravity():
    game = FakeGame((True, 276))
    result, _sleep = run_set(game, 100)
    assert result is True
    assert game.keys == []


def test_target_above_the_characters_max_is_accepted_at_the_cap(capsys):
    # Live: typing 999 left the box at the account's "Max 276G".
    game = FakeGame((True, 50), (True, 100), (True, 80))
    result, _sleep = run_set(game, 100)
    assert result is True
    assert "capped gravity at 80G" in capsys.readouterr().out


def test_wrong_box_text_never_presses_enter():
    # An unfocused box would send the digits to the hotbar; stop before Enter.
    game = FakeGame((True, 1), (True, 10))
    result, _sleep = run_set(game, 100)
    assert result is False
    assert "enter" not in game.keys
    # Live: a focused box swallows Tab; the last click (on the title) lets go of it.
    assert game.keys[-1] == "click" and game.keys.count("click") == 2


def test_focus_lost_mid_typing_sends_no_more_keys(capsys):
    # Another program took focus after the first digit: the rest of the number and
    # Enter would be typed into it.
    game = FakeGame((True, 1), (True, 100), (True, 100))
    game.focus_lost_after = 3   # click, ctrl+a, "1"
    result, _sleep = run_set(game, 100)
    assert result is False
    assert game.keys == ["click", "ctrl+a", "1"]
    assert "lost focus" in capsys.readouterr().out


def test_unchanged_after_enter_waits_out_the_cooldown_once():
    game = FakeGame((True, 1), (True, 50), (True, 1), (True, 50), (True, 50))
    result, sleep = run_set(game, 50)
    assert result is True
    sleep.assert_any_call(core.GRAVITY_INPUT_COOLDOWN_SEC)
    assert game.keys.count("enter") == 2


def test_still_unchanged_after_the_retry_fails():
    game = FakeGame((True, 1), (True, 50), (True, 1), (True, 50), (True, 1))
    result, _sleep = run_set(game, 50)
    assert result is False


def test_zero_target_drops_to_the_games_lowest_1g(capsys):
    # Live: typing 0 + Enter leaves 1G.
    game = FakeGame((True, 276), (True, 0), (True, 1))
    result, _sleep = run_set(game, 0)
    assert result is True
    assert game.keys[-4:] == ["0", "enter", "tab", "tab"]
    assert "Lowered 276G -> 1G" in capsys.readouterr().out


def test_zero_target_at_1g_sends_nothing():
    game = FakeGame((True, 1))
    result, _sleep = run_set(game, 0)
    assert result is True
    assert game.keys == []


def test_unreadable_box_changes_nothing():
    game = FakeGame((True, None))
    result, _sleep = run_set(game, 100)
    assert result is False
    assert game.keys == []


def test_no_box_on_screen_hands_back_to_the_old_button_path():
    game = FakeGame((False, None))
    result, _sleep = run_set(game, 100)
    assert result is None


@pytest.mark.new_game_ui
def test_raise_uses_the_number_box_when_the_old_button_is_missing():
    with patch.object(core, "_load_gravity_templates", return_value=dict.fromkeys(range(0, 101, 10))), \
         patch.object(core, "robust_move"), \
         patch.object(core, "safe_sleep"), \
         patch.object(core, "_read_gc_gravity", return_value=(None, 0.0, 0.0, False)), \
         patch.object(core, "_set_gc_gravity_by_input", return_value=True) as by_input, \
         patch.object(core, "click_at") as click:
        assert core._raise_gc_gravity(None, GEOMETRY, 100) is True
    by_input.assert_called_once_with(None, GEOMETRY, 100)
    click.assert_not_called()


@pytest.mark.new_game_ui
def test_a_failed_raise_does_not_cancel_the_start(capsys):
    # A character already at its max with a higher target, or a box that can't be
    # read on this screen: the gravity it has is safe, so training goes ahead.
    with patch.object(core, "_load_gravity_templates", return_value=dict.fromkeys(range(0, 101, 10))), \
         patch.object(core, "robust_move"), \
         patch.object(core, "safe_sleep"), \
         patch.object(core, "_read_gc_gravity", return_value=(None, 0.0, 0.0, False)), \
         patch.object(core, "_set_gc_gravity_by_input", return_value=False):
        assert core._raise_gc_gravity(None, GEOMETRY, 100) is True
    assert "not raised to 100G" in capsys.readouterr().out


@pytest.mark.new_game_ui
def test_zero_gravity_uses_the_number_box_when_the_old_button_is_missing():
    with patch.object(core, "_load_gravity_templates", return_value=dict.fromkeys(range(0, 101, 10))), \
         patch.object(core, "robust_move"), \
         patch.object(core, "safe_sleep"), \
         patch.object(core, "_read_gc_gravity", return_value=(None, 0.0, 0.0, False)), \
         patch.object(core, "_set_gc_gravity_by_input", return_value=True) as by_input:
        assert core._cycle_gc_gravity_to_zero(None, GEOMETRY) is True
    by_input.assert_called_once_with(None, GEOMETRY, 0, after_run=False)


def test_auto_raise_off_never_looks_at_the_panel():
    """Users who set gravity themselves keep target 0 and are untouched."""
    with patch.object(core, "_set_gc_gravity_by_input") as by_input, \
         patch.object(core, "_read_gc_gravity") as old_reader:
        assert core._raise_gc_gravity(None, GEOMETRY, 0) is True
    by_input.assert_not_called()
    old_reader.assert_not_called()
