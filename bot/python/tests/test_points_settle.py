"""The Training Progression panel slides and scales in, so Points Left waits for it to settle.

Frames are lossless grabs from a live client (HTC, 19 Oct 2026), 0.25 s apart, as the menu opened.
"""
import sys
from pathlib import Path
from unittest.mock import patch

import cv2
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import xynmacro_core as core

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "points_htc"
SLIDING, SCALING, STILL_A, STILL_B = (cv2.imread(str(FIXTURES / name)) for name in (
    "settle_1_sliding.png", "settle_2_scaling.png", "settle_3_still.png", "settle_4_still.png"))


def capture(frames):
    with patch.dict(core.PROGRESSION_TARGETS, clear=True), \
         patch.object(core, "_grab_reference_box", side_effect=frames) as grab, \
         patch.object(core.time, "sleep"):
        panels = core._capture_points_panels(None, "Ki Damage")
        core._record_points_target("Ki Damage", panels)
        return dict(core.PROGRESSION_TARGETS), grab.call_count


@pytest.mark.new_game_ui
def test_the_mid_animation_frames_do_not_read():
    assert core._read_training_points(SLIDING, "Ki Damage") is None
    assert core._read_training_points(SCALING, "Ki Damage") is None
    assert core._read_training_points(STILL_A, "Ki Damage") == (13, 50, 7)


@pytest.mark.new_game_ui
def test_waits_through_the_opening_animation_and_reads_the_settled_panel():
    targets, grabs = capture([SLIDING, SCALING, STILL_A, STILL_B])
    assert targets == {"Ki Damage": (20, 50)}
    assert grabs == 4


@pytest.mark.new_game_ui
def test_a_panel_that_is_already_still_costs_two_grabs():
    targets, grabs = capture([STILL_A, STILL_B])
    assert targets == {"Ki Damage": (20, 50)}
    assert grabs == 2


@pytest.mark.new_game_ui
def test_gives_up_after_the_settle_time_and_leaves_points_unknown():
    targets, grabs = capture([SLIDING] * 40)
    assert targets == {}
    assert grabs == 2 + core.POINTS_PANEL_SETTLE_GRABS


@pytest.mark.new_game_ui
def test_a_stat_already_at_its_cap_ends_without_reading_the_label():
    # 20/50 with 0 Points Left (live capture, HTC): the label on a pale sky never reads,
    # so the panel alone has to end the stat.
    with patch.dict(core.PROGRESSION_TARGETS, clear=True), \
         patch.object(core, "_POINTS_CAP_REACHED", set()), \
         patch.object(core, "_read_training_points", return_value=(20, 50, 0)):
        core._record_points_target("Ki Damage", [STILL_A, STILL_B])
        assert core.PROGRESSION_TARGETS["Ki Damage"] == (20, 50)
        assert core._progression_completion_reason(None, None, "Ki Damage") == "points_left"
        assert core._progression_completion_reason(None, None, "Ki Control") is None


@pytest.mark.new_game_ui
def test_a_stat_with_points_left_is_not_capped_yet():
    with patch.dict(core.PROGRESSION_TARGETS, clear=True), \
         patch.object(core, "_POINTS_CAP_REACHED", set()):
        core._record_points_target("Ki Damage", [STILL_A, STILL_B])
        assert core.PROGRESSION_TARGETS["Ki Damage"] == (20, 50)
        assert "Ki Damage" not in core._POINTS_CAP_REACHED
