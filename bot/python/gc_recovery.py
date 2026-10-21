"""Screen-only GC landmarks, calibrated against lossless room captures from a live client.

Room calibration: O. Toketa
"""
from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np

import training_ui


# October 2026 UI: a two-line "GRAVITY TRAINING / Press TAB to open training."
# panel replaced the old prompt. tpl_gc_enter_v2.png is a text-only crop at
# its native capture scale. Canonical frames stretch each axis by the window's
# own factor, so the prompt's size there depends on the window: search a small
# band of heights and of width stretches (16:9 windows need none, 4:3 ~1.33).
V2_PROMPT_BOX = (150, 450, 0, 700)  # y0, y1, x0, x1 of the top-left HUD area
V2_HEIGHT_SCALES = (.85, .95, 1.05, 1.15, 1.3, 1.45)
V2_WIDTH_STRETCHES = (1.0, 1.12, 1.26, 1.4)
V2_REFINE_STEPS = (.94, .96, .98, 1.0, 1.02, 1.04, 1.06)
V2_THRESHOLD = .80
_v2_scale = None  # (sx, sy) of the last confirmed match; tried first next time


def _canonical_bgr(frame):
    """The frame's BGR channels, or None unless it is a 1920x1080 uint8 image."""
    if (not isinstance(frame, np.ndarray) or frame.dtype != np.uint8 or frame.ndim != 3
            or frame.shape[2] < 3 or frame.shape[:2] != (1080, 1920)):
        return None
    return frame[:, :, :3]


@lru_cache(maxsize=1)
def _v2_template():
    """The new prompt template, or None when the asset is missing or damaged."""
    template = cv2.imread(str(Path(__file__).with_name('tpl_gc_enter_v2.png')),
                          cv2.IMREAD_GRAYSCALE)
    if template is None or template.shape[0] < 16 or template.shape[1] < 100 or template.std() < 10:
        return None
    return template


def _v2_score(gray, template, sx, sy):
    h, w = round(template.shape[0] * sy), round(template.shape[1] * sx)
    if h < 8 or w < 40 or h > gray.shape[0] or w > gray.shape[1]:
        return 0.0
    scaled = cv2.resize(template, (w, h), interpolation=cv2.INTER_AREA if sx < 1 else cv2.INTER_LINEAR)
    scores = cv2.matchTemplate(gray, scaled, cv2.TM_CCOEFF_NORMED)
    # Flat windows (blank or masked screens) divide by zero; never let that pass.
    return float(np.nan_to_num(scores, nan=0.0, posinf=0.0, neginf=0.0).max())


def v2_prompt_match(frame):
    """Return (score, (sx, sy)) of the best new-prompt match, or (0.0, None).

    A coarse half-resolution pass picks the scale (its smoothing tolerates the
    grid's few pixels of misalignment at the line ends), then a full-resolution
    pass around it makes the call, so a half-hidden prompt scores well below a
    real one.
    """
    global _v2_scale
    bgr, template = _canonical_bgr(frame), _v2_template()
    if bgr is None or template is None:
        return 0.0, None
    y0, y1, x0, x1 = V2_PROMPT_BOX
    gray = cv2.cvtColor(bgr[y0:y1, x0:x1], cv2.COLOR_BGR2GRAY)
    if gray.std() < 5:
        return 0.0, None
    if _v2_scale is not None:
        score = _v2_score(gray, template, *_v2_scale)
        if score >= V2_THRESHOLD:
            return score, _v2_scale
    small = cv2.resize(gray, None, fx=.5, fy=.5, interpolation=cv2.INTER_AREA)
    coarse = max((_v2_score(small, template, sy * stretch / 2, sy / 2), sy * stretch, sy)
                 for sy in V2_HEIGHT_SCALES for stretch in V2_WIDTH_STRETCHES)
    _, sx, sy = coarse
    # Refine height, then width; 2% steps keep the 32 px tall text aligned.
    _, sy = max((_v2_score(gray, template, sx, sy * step), sy * step) for step in V2_REFINE_STEPS)
    score, sx = max((_v2_score(gray, template, sx * step, sy), sx * step) for step in V2_REFINE_STEPS)
    scale = (sx, sy)
    if score >= V2_THRESHOLD:
        _v2_scale = scale
    return score, scale


def _legacy_prompt_visible(bgr, enter_prompt):
    if (not isinstance(enter_prompt, np.ndarray) or enter_prompt.ndim != 2
            or enter_prompt.dtype != np.uint8 or enter_prompt.size == 0):
        return False
    # A constant template has no variance, so its normalized score is meaningless.
    if enter_prompt.min() == enter_prompt.max():
        return False
    prompt = cv2.cvtColor(bgr[250:325, :430], cv2.COLOR_BGR2GRAY)
    if enter_prompt.shape[0] > prompt.shape[0] or enter_prompt.shape[1] > prompt.shape[1]:
        return False
    return cv2.matchTemplate(prompt, enter_prompt, cv2.TM_CCOEFF_NORMED).max() >= .90


def enter_prompt_visible(frame, enter_prompt):
    """True when GC's TAB prompt (pre- or post-October 2026 UI) is on screen.

    The new prompt also requires the GC room itself: its text is plain enough
    that the prompt alone should not prove where we are. Neither prompt shows
    whether a game menu is open on top of the room.
    """
    bgr = _canonical_bgr(frame)
    if bgr is None:
        return False
    if _legacy_prompt_visible(bgr, enter_prompt):
        return True
    if not room_visible(bgr):
        return False
    # The scaled grey match scored 0.62 on a second account's red-backed
    # banner (live 2026-10-13); the white text mask reads it at 1.0.
    if training_ui.open_training_prompt(training_ui.crop(bgr, training_ui.OPEN_TRAINING_BOX)):
        return True
    return _v2_template() is not None and v2_prompt_match(bgr)[0] >= V2_THRESHOLD


def room_landmarks(frame, enter_prompt):
    """Return (pillar_x, safety_edge_y), or None outside the confirmed GC room."""
    if not enter_prompt_visible(frame, enter_prompt) or not room_visible(frame):
        return None
    bgr = frame[:, :, :3]
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    # The support column is narrow and reaches down from the top of the scene.
    # Doorways and the character's clothes are wider or start much lower.
    dark = (bgr[:350].max(axis=2) < 55).astype(np.uint8) * 255
    dark[:, :500] = 0  # Exclude the player's HUD.
    contours, _ = cv2.findContours(dark, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    pillars = []
    for contour in contours:
        x, y, w, h = cv2.boundingRect(contour)
        if y < 30 and 12 <= w <= 110 and h >= 155 and h / w >= 3:
            pillars.append((h, x + w / 2))
    pillar = max(pillars)[1] if pillars else None
    # The near-horizontal front boundary moves down as we leave safety. Its glow
    # renders cyan-cored on one client but lavender (hue ~112-135) on another.
    cyan = cv2.inRange(hsv[420:1060, 350:1450], (75, 55, 50), (135, 255, 255))
    lines = cv2.HoughLinesP(cyan, 1, np.pi / 180, 80, minLineLength=350, maxLineGap=70)
    edges = []
    if lines is not None:
        for x1, y1, x2, y2 in lines[:, 0]:
            if abs(y2 - y1) <= abs(x2 - x1) * .25:
                edges.append(420 + (y1 + y2) / 2)
    return pillar, min(edges) if edges else None


def room_visible(frame):
    """Recognize GC even while Training Mode or chat hides its TAB prompt."""
    bgr = _canonical_bgr(frame)
    if bgr is None:
        return False
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    floor = hsv[400:700, 400:1450]
    red = ((floor[:, :, 0] < 12) | (floor[:, :, 0] > 170)) & (floor[:, :, 1] > 150)
    if red.mean() < .35:
        return False
    windows = cv2.inRange(hsv[140:390], (100, 150, 15), (130, 255, 255))
    contours, _ = cv2.findContours(windows, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    windows = [c for c in contours if cv2.contourArea(c) > 700
               and 20 < cv2.boundingRect(c)[2] < 240
               and 20 < cv2.boundingRect(c)[3] < 260]
    return len(windows) >= 3


def progress_fill(frame, state):
    """Measure the live minigame meter, requiring its blue UI background."""
    if frame.shape[:2] != (1080, 1920):
        return None
    y = 566 if 'Ki' in state else 684
    border = frame[y - 60:y + 61, 1450:1800, :3].max(axis=2) < 25
    if border[:55].mean(axis=1).max() < .95 or border[66:].mean(axis=1).max() < .95:
        return None
    bgr = frame[y - 2:y + 3, 1382:1910, :3]
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    yellow = cv2.inRange(hsv, (15, 83, 130), (40, 255, 255)) > 0
    blue = cv2.inRange(hsv, (90, 100, 15), (125, 255, 180)) > 0
    filled = yellow.mean(axis=0) >= .8
    background = blue.mean(axis=0) >= .8
    if (filled | background).mean() < .95:
        return None
    ends = np.flatnonzero(~filled)
    width = int(ends[0]) if len(ends) else len(filled)
    if width < len(filled) and background[width:].mean() < .95:
        return None
    return width
