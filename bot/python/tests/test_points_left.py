import sys
from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import xynmacro_core as core

FIXTURES = Path(__file__).parent / "fixtures"
PANEL = cv2.imread(str(FIXTURES / "training_points_panel.png"))
LABEL_16_OF_20 = cv2.imread(str(FIXTURES / "progression_16of20.png"))


def test_menu_panel_reads_count_target_and_points_left_for_every_stat():
    # Live menu capture; Ki Control and Ki Damage overlap the moves panel text.
    expected = {
        "Health": (0, 20, 130),
        "Agility": (3, 20, 123),
        "Ki Control": (0, 20, 81),
        "Physical Damage": (20, 20, 127),
        "Ki Damage": (0, 20, 83),
    }
    for stat, numbers in expected.items():
        assert core._read_training_points(PANEL, stat) == numbers, stat


def test_unknown_stat_or_blank_panel_is_unread():
    assert core._read_training_points(PANEL, "Speed") is None
    assert core._read_training_points(np.zeros_like(PANEL), "Health") is None


def test_in_training_label_numbers():
    assert core._progression_numbers_from_frame(LABEL_16_OF_20) == (16, 20)
    assert core._progression_numbers_from_frame(np.zeros_like(LABEL_16_OF_20)) is None


def test_target_is_the_lower_of_the_display_target_and_points_left():
    with patch.dict(core.PROGRESSION_TARGETS, clear=True):
        core._record_points_target("Agility", [PANEL, PANEL])
        assert core.PROGRESSION_TARGETS["Agility"] == (20, 20)  # 3 + 123 points
        with patch.object(core, "_read_training_points", return_value=(7, 20, 4)):
            core._record_points_target("Health", [PANEL, PANEL])
        assert core.PROGRESSION_TARGETS["Health"] == (11, 20)
        with patch.object(core, "_read_training_points", return_value=(0, 20, 0)):
            core._record_points_target("Health", [PANEL, PANEL])
        assert core.PROGRESSION_TARGETS["Health"] == (0, 20)


def test_disagreeing_reads_leave_no_target():
    readings = iter([(3, 20, 4), (3, 20, 1)])
    with patch.dict(core.PROGRESSION_TARGETS, {"Health": (9, 20)}, clear=True), \
         patch.object(core, "_read_training_points", side_effect=lambda *_: next(readings)):
        core._record_points_target("Health", [PANEL, PANEL])
        assert "Health" not in core.PROGRESSION_TARGETS


def test_points_cap_completes_only_at_or_past_the_target():
    with patch.object(core, "CURRENT_TRAINING_STATE", "Physical Damage"), \
         patch.object(core, "_PROGRESSION_CAP_LOGGED", set()):
        with patch.dict(core.PROGRESSION_TARGETS, {"Physical Damage": (16, 20)}, clear=True):
            assert core._apply_points_cap(LABEL_16_OF_20, False) is True
        with patch.dict(core.PROGRESSION_TARGETS, {"Physical Damage": (17, 20)}, clear=True):
            assert core._apply_points_cap(LABEL_16_OF_20, False) is False
        with patch.dict(core.PROGRESSION_TARGETS, {"Physical Damage": (16, 25)}, clear=True):
            assert core._apply_points_cap(LABEL_16_OF_20, False) is False
        with patch.dict(core.PROGRESSION_TARGETS, clear=True):
            assert core._apply_points_cap(LABEL_16_OF_20, False) is False
        # An unreadable label stays unknown, and a real completion stays complete.
        with patch.dict(core.PROGRESSION_TARGETS, {"Physical Damage": (0, 20)}, clear=True):
            assert core._apply_points_cap(LABEL_16_OF_20, None) is None
            assert core._apply_points_cap(LABEL_16_OF_20, True) is True


def test_stuck_count_rule_can_be_switched_off():
    signature = (("a",), ("b",))
    with patch.dict(core.COMPATIBILITY_SETTINGS, progression_stall_enabled=False):
        assert core._progression_stall_state(
            "Health", "Health", False, signature, 500.0, ("Health", signature, 0.0)
        ) == (None, False)


def test_hold_w_fallback_can_be_switched_off():
    with patch.dict(core.COMPATIBILITY_SETTINGS, auto_retry_walk_fallback=False), \
         patch.object(core, "_gc_recovery_hold") as hold, \
         patch.object(core, "GC_RECOVERY_VERIFY_PENDING", False):
        assert not core._gc_fallback_walk("Line not visible", 2.0)
        assert not core.GC_RECOVERY_VERIFY_PENDING
    hold.assert_not_called()
