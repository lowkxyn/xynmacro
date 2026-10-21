from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np

import training_ui
import xynmacro_core as core


FIXTURES = Path(__file__).parent / "fixtures" / "chat_button"
MONITOR = {"left": 630, "top": 512, "width": 1920, "height": 1080}


def _crop(name):
    return cv2.imread(str(FIXTURES / f"{name}.png"))


def test_open_and_closed_chat_buttons_read_correctly():
    # Live screenshots, 20 Oct 2026: filled bubble = chat on, outline = chat off.
    assert training_ui.roblox_chat_state(_crop("chat_on"))[0] == "open"
    assert training_ui.roblox_chat_state(_crop("chat_off"))[0] == "closed"
    # Unread messages put a blue number badge on the closed button (20 Oct 2026).
    assert training_ui.roblox_chat_state(_crop("chat_off_unread_badge"))[0] == "closed"


def test_light_wall_past_the_pill_end_is_ignored():
    # No voice button: the pill ends after chat and the GC wall fills the box's
    # right side (live capture, 20 Oct 2026). Live crop, chat on.
    assert training_ui.roblox_chat_state(_crop("chat_on_no_voice_button"))[0] == "open"
    closed = _crop("chat_off").copy()
    closed[:, 40:] = (230, 200, 200)   # wall touching the box edge
    assert training_ui.roblox_chat_state(closed)[0] == "closed"


def test_no_button_or_a_bright_background_is_unknown():
    x, y, width, height = training_ui.CHAT_BUTTON_BOX
    assert training_ui.roblox_chat_state(np.zeros((height, width, 3), np.uint8)) == (None, None)
    assert training_ui.roblox_chat_state(np.full((height, width, 3), 255, np.uint8)) == (None, None)


def _close_with(frames):
    grabs = iter(np.dstack([frame, np.full(frame.shape[:2], 255, np.uint8)]) for frame in frames)
    sct = type("Sct", (), {"grab": lambda self, box: next(grabs)})()
    with (
        patch.object(core, "robust_move"),
        patch.object(core, "safe_sleep"),
        patch.object(core, "click_at") as click,
    ):
        core._close_roblox_chat(sct, MONITOR)
    return click


def test_open_chat_gets_exactly_one_click_on_the_button():
    click = _close_with([_crop("chat_on"), _crop("chat_on"), _crop("chat_off")])
    assert click.call_count == 1
    x, y = click.call_args.args
    assert 630 + 128 <= x <= 630 + 148 and 512 + 25 <= y <= 512 + 43


def test_closed_or_unreadable_chat_is_left_alone():
    assert _close_with([_crop("chat_off")]).call_count == 0
    assert _close_with([np.zeros((37, 50, 3), np.uint8)]).call_count == 0


def test_chat_that_stays_open_gets_one_more_click_and_never_a_third():
    assert _close_with([_crop("chat_on")] * 30).call_count == 2


def test_one_open_read_followed_by_closed_is_not_clicked():
    assert _close_with([_crop("chat_on"), _crop("chat_off")]).call_count == 0


def test_a_slow_redraw_after_the_click_is_waited_out():
    frames = [_crop("chat_on"), _crop("chat_on")] + [_crop("chat_on")] * 5 + [_crop("chat_off")]
    assert _close_with(frames).call_count == 1
