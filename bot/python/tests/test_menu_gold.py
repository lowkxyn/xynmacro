"""The Training Mode heading is gold on some clients (live GC capture, 20 Oct 2026)."""
import sys
from pathlib import Path
from unittest.mock import patch

import cv2
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import xynmacro_core as core

BOX = cv2.imread(str(Path(__file__).resolve().parent / "fixtures" / "menu_gold" / "gc_menu_box_gold.png"))


@pytest.mark.new_game_ui
def test_gold_heading_is_detected():
    with patch.object(core, "_grab_reference_box", return_value=BOX):
        visible, score = core.detect_training_menu(None, None, core._training_menu_template())
    assert visible and score > 0.99


@pytest.mark.new_game_ui
def test_a_closed_menu_still_scores_low():
    blank = BOX.copy()
    blank[:] = BOX[0:1, 0:1]
    with patch.object(core, "_grab_reference_box", return_value=blank):
        visible, _score = core.detect_training_menu(None, None, core._training_menu_template())
    assert not visible
