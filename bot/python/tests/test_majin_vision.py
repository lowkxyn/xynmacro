from pathlib import Path

import cv2
import numpy as np
import pytest

import majin_vision as vision
import xynmacro_core as core


FIXTURES = Path(__file__).parent / "fixtures" / "majin"


def crop(name, region):
    image = cv2.imread(str(FIXTURES / f"{name}_{region}.png"))
    assert image is not None
    return image


@pytest.mark.parametrize("name,expected", [
    ("115005_0", "ready"), ("115007_2", "cooldown"), ("115009_9", "cooldown"),
])
def test_recorded_transformation_slot(name, expected):
    panel = crop(name, "panel")
    assert vision.panel_visible(panel)
    assert vision.slot_state(panel, 3) == expected


@pytest.mark.parametrize("name", ["115000_2", "115024_4", "114958_0", "115012_0"])
def test_other_panels_cannot_supply_a_ready_slot(name):
    panel = crop(name, "panel")
    assert not vision.panel_visible(panel)
    assert vision.slot_state(panel, 3) == "unknown"


@pytest.mark.parametrize("slot", [5, 6, 7, 8, 0, 9, "3"])
def test_empty_and_invalid_slots_are_not_ready(slot):
    assert vision.slot_state(crop("115005_0", "panel"), slot) == "unknown"


@pytest.mark.parametrize("slot", [1, 2, 4])
def test_configurable_rows_use_their_own_text_colour(slot):
    assert vision.slot_state(crop("115007_2", "panel"), slot) == "ready"


def test_unreadable_panel_is_unknown():
    assert not vision.panel_visible(None)
    assert not vision.panel_visible(np.zeros((332, 430, 3), dtype=np.uint8))
    assert vision.slot_state(np.zeros((332, 430, 3), dtype=np.uint8), 3) == "unknown"


@pytest.mark.parametrize("name", ["115005_0", "115007_2", "115009_9", "115000_2", "115024_4"])
def test_enter_prompt_confirms_training_is_closed(name):
    assert vision.enter_prompt_visible(crop(name, "enter"))


def test_training_menu_does_not_match_enter_prompt():
    assert not vision.enter_prompt_visible(crop("114958_0", "enter"))


def test_recorded_hp_recovery_reuses_the_senzu_patch():
    before, after = crop("115000_2", "hp"), crop("115012_0", "hp")
    assert core._hp_fill_is_critical(before)
    assert not core._hp_fill_is_above_red(before)
    assert not core._hp_fill_is_critical(after)
    assert core._hp_fill_is_above_red(after)


def test_templates_are_packaged_for_the_sidecar():
    spec = (Path(core.__file__).parent / "XynMacro-core.spec").read_text(encoding="utf-8")
    for name in ("tpl_majin_transformations.png", "tpl_majin_enter_training.png"):
        assert name in spec


@pytest.mark.parametrize("name", ["115005_0", "115007_2", "115009_9"])
def test_auto_finds_super_regeneration_ready_or_grey(name):
    assert vision.find_regen_slot(crop(name, "panel")) == 3


@pytest.mark.parametrize("name", ["115000_2", "115024_4", "114958_0", "115012_0"])
def test_auto_finds_nothing_without_the_transformations_panel(name):
    assert vision.find_regen_slot(crop(name, "panel")) is None


@pytest.mark.parametrize("target", [1, 2, 4, 6])
def test_auto_follows_the_name_to_another_slot(target):
    # No live fixture has it elsewhere: move row 3 to the target row and blank row 3.
    panel = crop("115005_0", "panel").copy()

    def rows(slot):
        top = round(813 + (slot - 1) * 30.4) - vision.PANEL_BOX[1]
        return slice(top - 3, top + 21)

    regen = panel[rows(3), 62:415].copy()
    blank = panel[rows(8), 62:415].copy()
    panel[rows(3), 62:415] = blank
    panel[rows(target), 62:415] = regen
    assert vision.find_regen_slot(panel) == target


def test_auto_template_is_packaged():
    assert (Path(vision.__file__).with_name("tpl_majin_super_regen.png")).exists()
