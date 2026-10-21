"""October live label crops, uncertainty rejection and Points Left integration."""
import hashlib
import json
from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np
import pytest

import training_ui as ui
import xynmacro_core as core

FIXTURES = Path(__file__).parent / "fixtures/progression_2026_10"
TRUTH = json.loads((FIXTURES / "ground_truth.json").read_text())
CASES = [(name, entry) for name, entry in TRUTH.items() if entry["fixture"]]
CLIPPED = "d2kid1_KiDamage_0020.png"


def fixture(name):
    image = cv2.imread(str(FIXTURES / name))
    assert image is not None
    return image


@pytest.mark.parametrize("name,entry", CASES, ids=[name for name, _ in CASES])
def test_live_label_crops(name, entry):
    image = fixture(name)
    assert image.shape[:2] in ((32, 360), (26, 520))  # label only, no HUD
    assert hashlib.sha256((FIXTURES / name).read_bytes()).hexdigest() == entry["sha256"]
    expected = None if entry["level"] is None or name == CLIPPED else (entry["level"], 20)
    assert ui.read_progression_level(image) == expected
    # Recorded before this change; the numeric reader must not change completion.
    assert ui.progression_complete(ui.progression_numbers(image)) is entry["completion"]


@pytest.mark.parametrize("image", [None, np.zeros((32, 360), np.uint8),
                                   np.zeros((31, 360, 3), np.uint8),
                                   np.zeros((32, 360, 2), np.uint8),
                                   np.full((32, 360, 3), 255, np.uint8)])
def test_invalid_crops_fail_closed(image):
    assert ui.read_progression_level(image) is None


@pytest.mark.parametrize("region", ["prefix", "first_digit", "slash", "target", "stroke"])
def test_damaged_seventeen_never_becomes_seven(region):
    image = fixture("d2chain_Health_0580.png")
    _, (a, b), (c, d) = ui.progression_spans(image)
    if region == "prefix":
        image[:, :a - 6] = 0
    elif region == "first_digit":
        image[:, a:a + 7] = 0
    elif region == "slash":
        image[:, b:c] = 0
    elif region == "target":
        image[:, c:d] = 0
    else:
        image[12:18, a:b] = 0
    assert ui.read_progression_level(image) is None


@pytest.mark.parametrize("offset", [-12, -2, 2, 12])
def test_fraction_moves_with_its_label(offset):
    image = np.roll(fixture("d2chain_Health_0420.png"), offset, axis=1)
    assert ui.read_progression_level(image) == (14, 20)


def captured_fraction(numerator, denominator="20"):
    """Boundary cases made only from actual screenshot strokes."""
    glyphs = {}
    for name, value in (("d3full_PhysicalDamage_0010.png", "0"),
                        ("d3full_PhysicalDamage_0080.png", "1"),
                        ("d3full_PhysicalDamage_0160.png", "2"),
                        ("d2chain_Health_0180.png", "9")):
        image = fixture(name)
        _, (a, b), (c, d) = ui.progression_spans(image)
        glyphs[value] = image[:, a:b]
        glyphs["/"] = image[:, b + 2:c - 2]
    image = fixture("d3full_PhysicalDamage_0010.png")
    _, (a, _b), _ = ui.progression_spans(image)
    pieces = []
    for char in f"{numerator}/{denominator}":
        pieces.extend((glyphs[char], np.zeros((32, 2, 3), np.uint8)))
    fraction = np.hstack(pieces[:-1])
    image[:, a:] = 0
    image[:, a:a + fraction.shape[1]] = fraction
    return image


@pytest.mark.parametrize("numerator,denominator", [("21", "20"), ("29", "20"),
                                                   ("00", "20"), ("2", "21"),
                                                   ("2", "200")])
def test_invalid_counts_and_targets_fail_closed(numerator, denominator):
    assert ui.read_progression_level(captured_fraction(numerator, denominator)) is None


def test_twenty_of_twenty_keeps_image_completion():
    image = captured_fraction("20")
    assert ui.read_progression_level(image) == (20, 20)
    assert ui.progression_complete(ui.progression_numbers(image)) is True


@pytest.mark.new_game_ui
@pytest.mark.parametrize("name,stat,cap", [("d3full_PhysicalDamage_final.png", "Physical Damage", 7),
                                         ("d2chain_Health_0420.png", "Health", 14),
                                         ("d2chain_Health_0630.png", "Health", 18)])
def test_actual_label_reaches_points_left_cap(name, stat, cap):
    image = fixture(name)
    with patch.dict(core.PROGRESSION_TARGETS, {stat: (cap, 20)}, clear=True):
        assert core._progression_numbers_from_frame(image) == (cap, 20)
        assert core._progression_completion_reason(image, False, stat) == "points_left"
    with patch.dict(core.PROGRESSION_TARGETS, {stat: (cap + 1, 20)}, clear=True):
        assert core._progression_completion_reason(image, False, stat) is None


@pytest.mark.parametrize("asset", ["tpl_progression_prefix_ki_v2.png",
                                   "tpl_progression_prefix_ki_large_v2.png"])
def test_missing_ki_assets_fail_closed(monkeypatch, asset):
    monkeypatch.setitem(ui._POINTS_ASSETS, asset, None)
    assert ui.read_progression_level(fixture("d2k1_KiControl_final.png")) is None


def test_panel_four_and_eight_are_their_own_glyphs_not_the_progression_font():
    sheet = ui._points_asset("tpl_progression_digits_v2.png")
    assert sheet[:24, 80:100].any() and sheet[:24, 160:180].any()
    panel = ui._points_asset("tpl_points_digits_v2.png")
    for column in (slice(80, 100), slice(160, 180)):
        assert panel[:, column].any()
        assert not (panel[:, column] == sheet[:24, column]).all()
