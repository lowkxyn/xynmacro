import sys
from pathlib import Path

import cv2
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import training_ui

PANEL = Path(__file__).resolve().parent / "fixtures" / "points_htc" / "htc_panel_2026-10-19.png"


@pytest.mark.parametrize("stat, expected", [
    ("Health", (0, 50, 16)),
    ("Agility", (0, 50, 13)),
    ("Ki Control", (0, 50, 18)),        # needs the 8 glyph
    ("Physical Damage", (0, 50, 14)),   # needs the 4 glyph
    ("Ki Damage", (5, 50, 13)),
])
def test_htc_panel_reads_points_out_of_520_and_levels_out_of_50(stat, expected):
    assert training_ui.read_training_points(cv2.imread(str(PANEL)), stat) == expected


def test_totals_must_be_a_known_room_pair():
    assert (260, 20) in training_ui.POINTS_TOTALS and (520, 50) in training_ui.POINTS_TOTALS
    assert (520, 20) not in training_ui.POINTS_TOTALS
