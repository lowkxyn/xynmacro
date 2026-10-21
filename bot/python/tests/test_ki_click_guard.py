from unittest.mock import patch

import pytest

import xynmacro_core as core


OFFSET_CLIENT = dict(GAME_OFFSET_X=320, GAME_OFFSET_Y=271, GAME_WIDTH=1920, GAME_HEIGHT=1080)


@pytest.mark.parametrize("client_point", [
    (580, 333),   # live r09 click that raised GC gravity
    (527, 342),   # GRAVITY_CLICK_POINT
    (325, 565),   # Ki Control in the stat list
    (138, 34),    # Roblox chat button
])
def test_ki_never_clicks_menus_or_roblox_buttons(client_point):
    with patch.multiple(core, **OFFSET_CLIENT):
        assert core._ki_click_blocked(320 + client_point[0], 271 + client_point[1])


@pytest.mark.parametrize("client_point", [(960, 540), (700, 400), (1300, 900), (100, 900)])
def test_ki_still_clicks_the_open_playfield(client_point):
    with patch.multiple(core, **OFFSET_CLIENT):
        assert not core._ki_click_blocked(320 + client_point[0], 271 + client_point[1])


def test_blocked_boxes_scale_with_the_client():
    # A 1280x720 client at the origin: the gravity button sits at 2/3 scale.
    with patch.multiple(core, GAME_OFFSET_X=0, GAME_OFFSET_Y=0, GAME_WIDTH=1280, GAME_HEIGHT=720):
        assert core._ki_click_blocked(527 * 2 / 3, 342 * 2 / 3)
        assert not core._ki_click_blocked(960 * 2 / 3, 540 * 2 / 3)
