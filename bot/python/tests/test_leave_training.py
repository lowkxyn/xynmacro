"""Leaving a running minigame for an HP check when the game drops a Tab."""
from unittest.mock import patch

import pytest

import xynmacro_core as core


def leave(menu_visible, prompt_after_third):
    taps = []
    waits = iter([False, False, False, prompt_after_third])
    with patch.object(core, "_client_frame", return_value=None),          patch.object(core.training_ui, "open_training_prompt", return_value=False),          patch.object(core.training_ui, "crop"),          patch.object(core, "_focus_game_for_senzu", return_value=True),          patch.object(core, "_tap_key_unchecked", side_effect=taps.append),          patch.object(core, "_wait_for_frame", side_effect=lambda *_a, **_k: next(waits)),          patch.object(core, "_training_menu_template", return_value=object()),          patch.object(core, "_training_menu_visible_for_senzu", return_value=(menu_visible, 0.99)):
        return core._leave_training_to_gameplay(None), taps


def test_dropped_second_tab_is_retried_while_training_mode_is_open():
    # Live 2026-11-16: Training Mode stayed open after the second Tab and the
    # Ki HP check stopped the run.
    left, taps = leave(menu_visible=True, prompt_after_third=True)
    assert left is True
    assert taps == ["tab", "tab", "tab"]


def test_no_blind_third_tab_when_training_mode_is_not_showing():
    left, taps = leave(menu_visible=False, prompt_after_third=True)
    assert left is False
    assert taps == ["tab", "tab"]
