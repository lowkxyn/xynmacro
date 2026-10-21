"""Offline panel/label fixtures and controller integration; no native capture."""
import ast
from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np
import pytest

import training_ui as ui
import xynmacro_core as core

FIXTURES = Path(__file__).parent / "fixtures/ui_2026_10"
STATS = tuple(ui.TRAINING_POINTS_ROW_Y)
HIGH_POINTS = (120, 117, 75, 137, 93)
FRAMES = {
    "d2_02": HIGH_POINTS, "mj00": HIGH_POINTS, "sz13": HIGH_POINTS,
    "c04": (10,) * 5, "g01": (10,) * 5, "m02": (10,) * 5,
    "m03": (10,) * 5, "ref_menu_0g": (10,) * 5,
}


def fixture(name):
    image = cv2.imread(str(FIXTURES / f"{name}.png"))
    assert image is not None
    return image


def panel_fraction(value, denominator):
    """Compose synthetic fractions from captured panel strokes for boundary tests."""
    panel = fixture("panel_d2_02")
    glyphs = {}
    for y, text in zip(ui.TRAINING_POINTS_ROW_Y.values(),
                       ("120/260", "117/260", "75/260", "137/260", "93/260")):
        for piece, char in zip(ui._digit_pieces(ui._points_text(panel[y:y + 20, 112:185])), text):
            glyphs.setdefault(char, piece)
    columns = []
    for char in f"{value}/{denominator}":
        columns.extend((glyphs[char], np.zeros((20, 1), bool)))
    return np.hstack(columns[:-1])


def set_panel_numbers(panel, stat, *, level=0, points=10, target=20, points_target=260):
    y = ui.TRAINING_POINTS_ROW_Y[stat]
    changed = panel.copy()
    changed[y:y + 20, 112:185] = 0
    fraction = panel_fraction(points, points_target)
    changed[y:y + 20, 114:114 + fraction.shape[1]] = fraction[:, :, None] * 200
    # Preserve the right alignment of the complete Level label.
    prefix = panel[y:y + 20, 544:579].copy()
    changed[y:y + 20, 520:607] = 0
    fraction = panel_fraction(level, target)
    start = 606 - fraction.shape[1]
    changed[y:y + 20, start - 35:start] = prefix
    changed[y:y + 20, start:606] = fraction[:, :, None] * 200
    return changed


def label_number(name, value):
    """Synthetic 2/20 and 20/20 use the actual label's denominator pixels."""
    label = fixture(name)
    _, (a, b), (c, d) = ui.progression_spans(label)
    width = d - c if value == 20 else ui._digit_pieces(ui.progression_numbers(label)[1] > 100)[0].shape[1]
    grow = width - (b - a)
    changed = label.copy()
    if grow > 0:
        changed[:, :a - grow] = label[:, grow:a]
    changed[:, min(a, a - grow):b] = 0
    changed[:, a - grow:b] = label[:, c:c + width]
    return changed


@pytest.mark.parametrize("frame,points", FRAMES.items())
@pytest.mark.parametrize("stat", STATS)
def test_every_observed_row(frame, points, stat):
    panel = fixture(f"panel_{frame}")
    assert panel.shape == (572, 660, 3)  # panel only, no HUD/name pixels
    assert ui.read_training_points(panel, stat) == (0, 20, points[STATS.index(stat)])


@pytest.mark.parametrize("image", [None, np.zeros((572, 660, 3), np.uint8),
                                   np.full((572, 660, 3), 255, np.uint8),
                                   np.zeros((571, 660, 3), np.uint8),
                                   np.zeros((572, 660), np.uint8)])
def test_non_panel_crops_are_unknown(image):
    assert ui.read_training_points(image, "Health") is None


def test_unknown_stat_and_minigame_crop_are_unknown():
    assert ui.read_training_points(fixture("panel_d2_02"), "Speed") is None
    assert ui.read_training_points(fixture("progression_standard_0of20"), "Health") is None


@pytest.mark.parametrize("box", [(200, 18, 260, 32), (45, 94, 67, 20), (544, 94, 35, 20),
                                 (114, 94, 4, 20), (134, 94, 3, 20), (600, 94, 5, 20)])
def test_missing_title_labels_digits_or_slash_are_unknown(box):
    panel = fixture("panel_d2_02")
    x, y, w, h = box
    panel[y:y + h, x:x + w] = 0
    assert ui.read_training_points(panel, "Health") is None


@pytest.mark.parametrize("level,points", [(0, 0), (7, 3), (10, 260), (20, 0)])
def test_synthetic_counts_include_two_digit_right_aligned_levels(level, points):
    panel = set_panel_numbers(fixture("panel_d2_02"), "Health", level=level, points=points)
    assert ui.read_training_points(panel, "Health") == (level, 20, points)


@pytest.mark.parametrize("changes", [dict(target=25), dict(points_target=250),
                                     dict(level=21), dict(points=261)])
def test_wrong_denominators_and_out_of_range_counts_are_unknown(changes):
    panel = set_panel_numbers(fixture("panel_d2_02"), "Health", **changes)
    assert ui.read_training_points(panel, "Health") is None


@pytest.mark.parametrize("name", ["progression_standard_0of20", "progression_ki_0of20"])
def test_numeric_progression_uses_captured_zero_and_denominator_glyphs(name):
    assert ui.read_progression_level(fixture(name)) == (0, 20)
    assert ui.read_progression_level(label_number(name, 2)) == (2, 20)
    assert ui.read_progression_level(label_number(name, 20)) == (20, 20)
    assert ui.read_progression_level(np.zeros_like(fixture(name))) is None


@pytest.mark.new_game_ui
def test_target_semantics_and_capture_agreement():
    panel = set_panel_numbers(fixture("panel_d2_02"), "Health", level=7, points=3)
    with patch.dict(core.PROGRESSION_TARGETS, clear=True), patch.object(core, "_PROGRESSION_CAP_LOGGED", set()):
        core._record_points_target("Health", [panel, panel])
        assert core.PROGRESSION_TARGETS["Health"] == (10, 20)
        core._record_points_target("Agility", [panel, panel])
        assert core.PROGRESSION_TARGETS["Agility"] == (20, 20)
        core._record_points_target("Health", [panel, fixture("panel_c04")])
        assert "Health" not in core.PROGRESSION_TARGETS
        core._record_points_target("Health", [panel, np.zeros_like(panel)])
        assert "Health" not in core.PROGRESSION_TARGETS
        core._record_points_target("Health", [panel])
        assert "Health" not in core.PROGRESSION_TARGETS


@pytest.mark.new_game_ui
@pytest.mark.parametrize("stat,name", [("Health", "progression_standard_0of20"),
                                      ("Ki Control", "progression_ki_0of20")])
def test_capture_record_and_live_label_cap_integration(stat, name):
    panel = set_panel_numbers(fixture("panel_d2_02"), stat, points=2)
    label = label_number(name, 2)
    with patch.dict(core.PROGRESSION_TARGETS, clear=True), \
         patch.object(core, "_PROGRESSION_CAP_LOGGED", set()), \
         patch.object(core, "_grab_reference_box", side_effect=[panel, panel, fixture(name), label, label]) as grab, \
         patch.object(core.time, "sleep"), patch.object(core, "CURRENT_TRAINING_STATE", stat):
        core._record_points_target(stat, core._capture_points_panels(None))
        assert core.PROGRESSION_TARGETS[stat] == (2, 20)
        assert core.read_progression_state(None, stat=stat)[::2] == (False, None)
        complete, signature, reason = core.read_progression_state(None, stat=stat)
        assert complete is True and signature is not None and reason == "points_left"
        assert core.read_progression_completion(None) is True
    assert grab.call_args_list[0].args == (None, ui.TRAINING_POINTS_BOX)
    assert grab.call_args_list[2].args == (None, ui.progression_label_box(stat))


@pytest.mark.new_game_ui
@pytest.mark.parametrize("cap,completion,expected", [((0, 20), False, "points_left"),
                                                     ((1, 20), False, None),
                                                     ((0, 25), False, None),
                                                     ((20, 20), False, None),
                                                     ((0, 20), None, None),
                                                     ((0, 20), True, "displayed_target")])
def test_cap_boundaries_and_unknown_completion(cap, completion, expected):
    with patch.dict(core.PROGRESSION_TARGETS, {"Health": cap}, clear=True):
        assert core._progression_completion_reason(fixture("progression_standard_0of20"), completion, "Health") == expected


@pytest.mark.new_game_ui
def test_uncertain_numeric_label_never_sets_a_cap():
    label = fixture("progression_standard_0of20")
    _, (a, b), _ = ui.progression_spans(label)
    label[12:18, a:b] = 0
    with patch.dict(core.PROGRESSION_TARGETS, {"Health": (0, 20)}, clear=True):
        assert ui.read_progression_level(label) is None
        assert core._progression_completion_reason(label, False, "Health") is None


@pytest.mark.new_game_ui
def test_capture_error_is_optional():
    with patch.object(core, "_grab_reference_box", side_effect=RuntimeError("mock capture failure")):
        assert core._capture_points_panels(None) == []


@pytest.mark.parametrize("broken", [None, np.zeros((1, 1), bool)])
@pytest.mark.parametrize("asset", ["tpl_points_digits_v2.png", "tpl_points_title_v2.png",
                                   "tpl_points_labels_v2.png", "tpl_progression_digits_v2.png",
                                   "tpl_progression_prefix_standard_v2.png"])
def test_missing_or_malformed_assets_fail_closed(monkeypatch, asset, broken):
    monkeypatch.setitem(ui._POINTS_ASSETS, asset, broken)
    if asset.startswith("tpl_progression_"):
        assert ui.read_progression_level(fixture("progression_standard_0of20")) is None
    else:
        assert ui.read_training_points(fixture("panel_d2_02"), "Health") is None


def test_four_and_eight_come_from_a_real_panel_and_blanks_stay_unread():
    sheet = ui._points_asset("tpl_points_digits_v2.png")
    # 4 and 8 were first seen on an HTC panel (test_points_htc.py, 19 Oct 2026).
    assert sheet[:, 80:100].any()  # 4
    assert sheet[:, 160:180].any()  # 8
    assert ui._read_digits(np.zeros((20, 6), bool), [sheet]) is None


def test_assets_are_packaged_and_load_from_staged_directory(tmp_path, monkeypatch):
    source = Path(ui.BASE_DIR)
    spec = ast.parse((source / "XynMacro-core.spec").read_text(encoding="utf-8"))
    analysis = next(n for n in ast.walk(spec) if isinstance(n, ast.Call)
                    and isinstance(n.func, ast.Name) and n.func.id == "Analysis")
    assets = dict(ast.literal_eval(next(k.value for k in analysis.keywords if k.arg == "datas")))
    for name in ("tpl_points_digits_v2.png", "tpl_points_title_v2.png",
                 "tpl_points_labels_v2.png", "tpl_progression_digits_v2.png",
                 "tpl_progression_prefix_standard_v2.png", "tpl_progression_prefix_ki_v2.png",
                 "tpl_progression_prefix_ki_large_v2.png"):
        assert assets[name] == "."
        (tmp_path / name).write_bytes((source / name).read_bytes())
    monkeypatch.setattr(ui, "BASE_DIR", str(tmp_path))
    monkeypatch.setattr(ui, "_POINTS_ASSETS", {})
    assert ui.read_training_points(fixture("panel_d2_02"), "Health") == (0, 20, 120)
    assert ui.read_progression_level(fixture("progression_standard_0of20")) == (0, 20)
