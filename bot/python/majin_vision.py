"""Read the Majin transformation panel in normalized 1920x1080 client crops."""

from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np


PANEL_BOX = (1490, 748, 430, 332)
ENTER_PROMPT_BOX = (0, 260, 395, 55)


def _white_text(image):
    bgr = image[:, :, :3].astype(np.int16)
    return ((bgr.min(axis=2) >= 185)
            & (bgr.max(axis=2) - bgr.min(axis=2) <= 35)).astype(np.uint8) * 255


@lru_cache(maxsize=2)
def _template(name):
    image = cv2.imread(str(Path(__file__).with_name(name)))
    return None if image is None else _white_text(image)


def _text_matches(crop, name):
    template = _template(name)
    if (crop is None or template is None or crop.ndim != 3
            or crop.shape[0] < template.shape[0]
            or crop.shape[1] < template.shape[1]):
        return False
    score = cv2.matchTemplate(_white_text(crop), template, cv2.TM_CCOEFF_NORMED)
    return bool(score.max() >= 0.85)


def panel_visible(panel):
    """Require 'Change Technique'; the normal Moves panel must not qualify."""
    if panel is None or panel.shape[:2] != (PANEL_BOX[3], PANEL_BOX[2]):
        return False
    return _text_matches(panel[:32, 90:290], "tpl_majin_transformations.png")


def enter_prompt_visible(prompt):
    return _text_matches(prompt, "tpl_majin_enter_training.png")


def _row_top(slot):
    return round(813 + (slot - 1) * 30.4) - PANEL_BOX[1]


def _row_ink(row):
    """White (ready) and grey (cooldown) text alike, so a name matches either way."""
    bgr = row[:, :, :3].astype(np.int16)
    low, high = bgr.min(axis=2), bgr.max(axis=2)
    return (((high - low) <= 35) & (low >= 55)).astype(np.uint8) * 255


@lru_cache(maxsize=1)
def _regen_name():
    return cv2.imread(str(Path(__file__).with_name("tpl_majin_super_regen.png")),
                      cv2.IMREAD_GRAYSCALE)


def find_regen_slot(panel):
    """Return the slot whose row reads Super Regeneration, or None.

    Fixture scores: the name row 1.0 (ready) / 0.89 (grey), other names <= 0.34.
    """
    template = _regen_name()
    if template is None or not panel_visible(panel):
        return None
    for slot in range(1, 9):
        top = _row_top(slot)
        ink = _row_ink(panel[top - 3:top + 21, 62:415])
        if (ink.shape[0] < template.shape[0] or ink.shape[1] < template.shape[1]
                or not ink.any()):
            continue
        if cv2.matchTemplate(ink, template, cv2.TM_CCOEFF_NORMED).max() >= 0.7:
            return slot
    return None


def slot_state(panel, slot):
    """Return ready, cooldown, or unknown (including empty or unreadable rows)."""
    if not isinstance(slot, int) or not 1 <= slot <= 8 or not panel_visible(panel):
        return "unknown"
    top = _row_top(slot)
    # Exclude the slot number: even an empty row has a white number.
    row = panel[top:top + 18, 62:415, :3].astype(np.int16)
    low, high = row.min(axis=2), row.max(axis=2)
    ink = low[(high - low <= 35) & (low >= 55)]
    if ink.size < 50:
        return "unknown"
    white = np.count_nonzero(ink >= 185)
    if white >= 30 and white / ink.size >= 0.45:
        return "ready"
    if white < 5 and np.percentile(ink, 90) <= 145:
        return "cooldown"
    return "unknown"
