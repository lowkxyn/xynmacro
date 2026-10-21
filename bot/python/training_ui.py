"""Readers for the October 2026 DBOG training UI.

Each reader takes a BGR crop of one of the boxes below, grabbed in the macro's
1920x1080 client reference space (see _grab_reference_box in xynmacro_core),
and only measures pixels; input, timing and retries stay in the controller.
Coordinates were measured live on 2026-10-11 with the Roblox client at exactly
1920x1080.

Screen layout research: Omar Toketa <omar.toketa47@proton.me>
"""
import math
import os
import time
from functools import lru_cache

import cv2
import numpy as np

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Tab menu's right panel; row order differs from STAT_BUTTONS.
TRAINING_POINTS_BOX = (1250, 283, 660, 572)
TRAINING_POINTS_ROW_Y = {
    "Health": 94, "Agility": 195, "Ki Control": 297,
    "Physical Damage": 398, "Ki Damage": 500,
}
_POINTS_ASSETS = {}
_DIGIT_MIN_IOU = 0.90
_DIGIT_MIN_MARGIN = 0.12


def _points_asset(name):
    if name not in _POINTS_ASSETS:
        image = cv2.imread(os.path.join(BASE_DIR, name), cv2.IMREAD_GRAYSCALE)
        _POINTS_ASSETS[name] = None if image is None else image > 127
    return _POINTS_ASSETS[name]


def _points_text(image):
    return cv2.cvtColor(image[:, :, :3], cv2.COLOR_BGR2GRAY) > 100


def _digit_pieces(mask):
    """Split at empty columns, retaining every stroke (including damaged ones)."""
    columns = np.flatnonzero(mask.any(axis=0))
    runs = np.split(columns, np.flatnonzero(np.diff(columns) > 1) + 1)
    return [mask[:, run[0]:run[-1] + 1] for run in runs if run.size]


def _digit_mask(piece):
    rows, columns = np.nonzero(piece)
    if not rows.size:
        return None
    tight = piece[rows.min():rows.max() + 1, columns.min():columns.max() + 1]
    return cv2.resize(tight.astype(np.uint8), (20, 24), interpolation=cv2.INTER_NEAREST) > 0


def _text_matches(mask, template):
    if template is None or template.shape != mask.shape:
        return False
    union = np.count_nonzero(mask | template)
    return bool(union and np.count_nonzero(mask & template) / union >= 0.90)


def _read_digits(mask, sheets):
    columns = np.flatnonzero(mask.any(axis=0))
    # A missing middle digit must not collapse into a shorter valid number.
    if columns.size == 0 or np.any(np.diff(columns) > 4):
        return None
    pieces = _digit_pieces(mask)
    if not 1 <= len(pieces) <= 3:
        return None
    digits = []
    for piece in pieces:
        rows, columns = np.nonzero(piece)
        if not rows.size or rows.max() - rows.min() < 6 or columns.max() - columns.min() < 2:
            return None
        shape = _digit_mask(piece)
        scores = []
        for digit in range(10):
            best = 0.0
            for sheet in sheets:
                if sheet is None or sheet.shape != (24, 200):
                    continue
                glyph = sheet[:, digit * 20:(digit + 1) * 20]
                union = np.count_nonzero(shape | glyph)
                if glyph.any():
                    best = max(best, np.count_nonzero(shape & glyph) / union)
            scores.append((best, digit))
        scores.sort(reverse=True)
        if scores[0][0] < _DIGIT_MIN_IOU or scores[0][0] - scores[1][0] < _DIGIT_MIN_MARGIN:
            return None
        digits.append(str(scores[0][1]))
    if len(digits) > 1 and digits[0] == "0":
        return None
    return int("".join(digits))


def _read_points_fraction(image):
    text = _points_text(image)
    columns = np.flatnonzero(text.any(axis=0))
    # Both fields begin immediately after their labels. Losing the first digit
    # must not turn 120 into a confident 20 (and an early completion cap).
    if columns.size == 0 or columns[0] > 3:
        return None
    count, _, stats, _ = cv2.connectedComponentsWithStats(text.astype(np.uint8), 8)
    pieces = [tuple(int(v) for v in s) for s in stats[1:count]]
    slash = _find_slash(text, pieces, minimum_width=2)
    if slash is None:
        return None
    sheet = _points_asset("tpl_points_digits_v2.png")
    left = _read_digits(text[:, :slash[0]], [sheet])
    right = _read_digits(text[:, slash[1]:], [sheet])
    return None if left is None or right is None else (left, right)


# (Points Left total, Level total): GC 260 and 20, HTC 520 and 50 (live 2026-10-19).
POINTS_TOTALS = {(260, 20), (520, 50)}
PROGRESSION_LEVEL_TOTALS = {level for _points, level in POINTS_TOTALS}


def read_training_points(panel_crop, stat):
    """(level, target, points_left) from a TRAINING_POINTS_BOX crop, or None.

    Both fractions, title and row labels must be present. Templates contain
    verified screenshot glyphs only; unsupported or uncertain digits fail closed.
    """
    y = TRAINING_POINTS_ROW_Y.get(stat)
    if (y is None or panel_crop is None or panel_crop.ndim != 3
            or panel_crop.shape[:2] != (572, 660) or panel_crop.shape[2] < 3):
        return None
    if not _text_matches(_points_text(panel_crop[18:50, 200:460]),
                         _points_asset("tpl_points_title_v2.png")):
        return None
    labels = _points_asset("tpl_points_labels_v2.png")
    if labels is None or labels.shape != (20, 102):
        return None
    if not _text_matches(_points_text(panel_crop[y:y + 20, 45:112]), labels[:, :67]):
        return None
    # This label is right-aligned, so "Level:" moves left for a wider count.
    level_x = next((x + 35 for x in range(520, 551)
                    if _text_matches(_points_text(panel_crop[y:y + 20, x:x + 35]), labels[:, 67:])),
                   None)
    if level_x is None:
        return None
    points = _read_points_fraction(panel_crop[y:y + 20, 112:185])
    level = _read_points_fraction(panel_crop[y:y + 20, level_x:607])
    if (points is None or level is None or (points[1], level[1]) not in POINTS_TOTALS
            or points[0] > points[1] or level[0] > level[1]):
        return None
    return level[0], level[1], points[0]

# Left Training Mode panel. The button order changed in the overhaul.
STAT_BUTTONS = {
    "Health": (346, 439),
    "Ki Control": (346, 508),
    "Ki Damage": (346, 577),
    "Physical Damage": (346, 646),
    "Agility": (346, 716),
}
MENU_HEADING_BOX = (200, 340, 300, 90)  # "Training Mode", (262,370)-(428,400)
MENU_HEADING_MIN = 0.90                 # live: 1.000 open, <= 0.241 otherwise

# Top-left "GRAVITY TRAINING" banner: shown in the GC whether training or not,
# hidden only behind the open Training Mode menu. Live: >= 0.99 in the GC on
# two accounts and camera angles, <= 0.37 elsewhere (menu open, North Earth).
GC_BANNER_BOX = (0, 240, 420, 100)
GC_BANNER_MIN = 0.80

# "Gravity: 40G" button; one click is +10G and 100G wraps to 0G.
GRAVITY_BOX = (455, 316, 170, 40)
GRAVITY_CLICK_POINT = (532, 336)
GRAVITY_NUMBER_WIDTH = 46   # right-anchored window holding "40G"
GRAVITY_MATCH_MIN = 0.80
GRAVITY_MATCH_MARGIN_MIN = 0.08  # closest live pair (20G/70G) leaves 0.177

# Bottom-centre minigame panel.
HEALTH_BAR_BOX = (730, 888, 460, 34)
AGILITY_LETTER_BOX = (660, 850, 600, 100)   # centred row; sets grow past 4 letters with level
AGILITY_LETTER_MIN = 0.70
ROUND_TIMER_BOX = (780, 843, 360, 10)      # drains in ~3.3 s, refills each round
PROGRESSION_LABEL_BOXES = {
    "standard": (780, 810, 360, 32),   # Health / Agility / Physical Damage
    "ki": (700, 940, 520, 26),         # Ki Control / Ki Damage one-line panel
}

# HUD (hidden while Ki minigames run and while the side MENU is open). The
# whole HP bar is read: it hasn't been seen low yet, so whether it turns red or
# only shrinks is unknown, and measuring its length works either way.
HP_FILL_BOX = (185, 131, 525, 10)
# HP/KI/FR labels left of the bars. When the HUD is hidden the GC floor shows
# through HP_FILL_BOX and its red read as a full bar (live 2026-10-13), so
# the bar only counts while these labels are drawn. Live: >= 0.81 shown on
# two accounts, <= 0.21 hidden.
HUD_LABELS_BOX = (150, 120, 45, 92)    # below the player name on purpose
HUD_LABELS_MIN = 0.60
HP_CRITICAL_FRACTION = 0.30   # chosen, not measured: no low-HP frame captured yet
HP_HEALED_FRACTION = 0.90
# Ki bar, a few rows through its middle. Charging fills it in up to three
# layers, each a darker blue drawn from the left over the one below. The green
# channel tells them apart (live capture 2026-10-19, 1080p client):
# layer 1 ~102, layer 2 ~75, layer 3 ~39-46; the empty part is near black.
KI_BAR_BOX = (200, 163, 484, 5)
KI_LAYER_GREEN_MIN = (88, 60, 20)     # lowest green for layers 1, 2, 3
KI_LAYER_GREEN_MAX = 125

# Ki circles never sit under the Roblox buttons, the GC banner, the side MENU
# tab or the score panel; never click there even if something looks circular.
KI_PLAYFIELD_TOP = 80
KI_PLAYFIELD_BOTTOM = 910
# The 17 October recordings span x=518..1400; leave room for the full discs.
# Ring sampling still uses the full image, including rings beyond these bounds.
KI_PLAYFIELD_LEFT = 400
KI_PLAYFIELD_RIGHT = 1520
# What a Ki pass grabs (x, y, w, h): circle centres (x 518-1401, y 257-757 on both rigs)
# plus ~190 px for an approach ring at first sight (ratio ~2.4), and the progression label
# at y 940-966. The full-screen grab was the biggest cost of a pass on slow PCs.
KI_CAPTURE_BOX = (330, 60, 1260, 910)
KI_CLICK_BLOCKED_BOXES = (
    (0, 0, 230, 70),        # Roblox menu, chat and voice buttons
    (0, 255, 410, 70),      # GRAVITY TRAINING banner
    (0, 505, 50, 180),      # side MENU tab
    (700, 920, 520, 160),   # Ki score panel
)
KI_CIRCLE_AREA = (4000, 16000)     # live radius ~55 px
KI_CIRCLE_FILL_MIN = 0.75
KI_RING_MAX_FACTOR = 3.2
KI_RING_MIN_HITS = 14              # of 48 samples; a clean ring scores 44-48
KI_RING_EDGE_PX = 5                # the ring line is ~3-4 px wide at 1080p
KI_RING_CONTRAST_MIN = 40          # summed |BGR| difference from either side of the line
_RING_ANGLES = np.linspace(0, 2 * math.pi, 48, endpoint=False)
_RING_COS = np.cos(_RING_ANGLES)
_RING_SIN = np.sin(_RING_ANGLES)
_KI_CLOSE_KERNEL = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7))
_KI_PEAK_KERNEL = np.ones((5, 5), np.uint8)


def crop(frame, box):
    """Cut a reference box out of a full 1920x1080 frame (tests and Ki use this)."""
    x, y, w, h = box
    return frame[y:y + h, x:x + w]


def _white_mask(image, minimum=190, spread=55):
    bgr = image[:, :, :3]
    low = bgr.min(axis=2)
    return (low > minimum) & (bgr.max(axis=2) - low < spread)


# --- Training menu -----------------------------------------------------------

def load_menu_heading():
    return cv2.imread(os.path.join(BASE_DIR, "tpl_training_mode_v2.png"), cv2.IMREAD_GRAYSCALE)


def load_menu_heading_gold():
    # Some clients draw the heading in gold, which only scores ~0.76 against the
    # white one (live GC capture, 20 Oct 2026).
    return cv2.imread(os.path.join(BASE_DIR, "tpl_training_mode_gold_v2.png"), cv2.IMREAD_GRAYSCALE)


# Second banner line. The banner is see-through, so colour matching shifts
# with the room behind it; the white text mask doesn't. Live IoU: "open
# training" 1.0 on red and grey backgrounds, "stop training" 0.49, menus 0.
OPEN_TRAINING_BOX = (40, 286, 210, 26)
OPEN_TRAINING_MIN = 0.80
_OPEN_TRAINING = []


def open_training_prompt(line_box):
    """For an OPEN_TRAINING_BOX crop: does the banner say "Press TAB to open training."?"""
    if not _OPEN_TRAINING:
        image = cv2.imread(os.path.join(BASE_DIR, "tpl_gc_open_training_v2.png"), cv2.IMREAD_GRAYSCALE)
        _OPEN_TRAINING.append(None if image is None else image > 0)
    template = _OPEN_TRAINING[0]
    if template is None or line_box.shape[:2] != template.shape:
        return False
    text = line_box[:, :, :3].min(axis=2) > 185
    best = 0.0
    for dy in (-1, 0, 1):          # a pixel of subpixel drift either way
        for dx in (-2, -1, 0, 1, 2):
            shifted = np.roll(np.roll(text, dy, 0), dx, 1)
            union = np.count_nonzero(shifted | template)
            if union:
                best = max(best, np.count_nonzero(shifted & template) / union)
    return bool(best >= OPEN_TRAINING_MIN)   # numpy 2.4 counts are numpy ints


def load_gc_banner():
    return cv2.imread(os.path.join(BASE_DIR, "tpl_gc_banner_v2.png"), cv2.IMREAD_GRAYSCALE)


def gc_banner_visible(banner_box, template):
    """For a GC_BANNER_BOX crop: is the GRAVITY TRAINING banner on screen?"""
    if template is None:
        return False
    gray = cv2.cvtColor(banner_box[:, :, :3], cv2.COLOR_BGR2GRAY)
    return float(cv2.minMaxLoc(cv2.matchTemplate(gray, template, cv2.TM_CCOEFF_NORMED))[1]) >= GC_BANNER_MIN


# --- Majin: the G "TRANSFORM" list replaced the Change Technique panel --------
# Measured live 2026-10-13 (one Majin character, Super Regeneration in slot 3).
# PANEL_BOX and the three functions mirror majin_vision so core can use either.

# The TRANSFORM and ITEMS titles sit on a see-through pill, so a grey match
# moved with the room behind it (live 2026-10-14: 0.20 on a held panel), and
# ITEMS is drawn at two sizes. Match the bright text alone against each
# variant: on 271 saved frames every panel scored >= 0.76, everything else
# < 0.45.
_PANEL_TITLE_BOX = (30, 15, 180, 55)        # client (1630,625) inside the 1600,610 panel box
_PANEL_TITLE_MIN = 0.60
_title_masks = {}


def _panel_title_is(panel, *names):
    text = (cv2.cvtColor(crop(panel, _PANEL_TITLE_BOX)[:, :, :3], cv2.COLOR_BGR2GRAY) > 140)
    text = text.astype(np.uint8) * 255
    if not text.any():
        return False
    for name in names:
        if name not in _title_masks:
            _title_masks[name] = cv2.imread(os.path.join(BASE_DIR, name), cv2.IMREAD_GRAYSCALE)
        template = _title_masks[name]
        if template is None or template.shape[0] > text.shape[0] or template.shape[1] > text.shape[1]:
            continue
        scores = np.nan_to_num(cv2.matchTemplate(text, template, cv2.TM_CCOEFF_NORMED))
        if float(scores.max()) >= _PANEL_TITLE_MIN:
            return True
    return False


class MajinTransform:
    PANEL_BOX = (1600, 610, 320, 420)
    _TITLE_BOX = (0, 0, 320, 90)              # "TRANSFORM" at (1660,644) in the client
    _TITLE_MIN = 0.85                          # live: 1.000 held, <= 0.203 otherwise
    _ROW_X = (50, 300)
    _FIRST_ROW_Y = 127                         # slot 1 text centre, panel coordinates
    _ROW_PITCH = 36.5
    _NAME_MIN = 0.80                           # live: 1.000 for the row, <= 0.585 others
    _templates = {}

    @classmethod
    def _template(cls, name):
        if name not in cls._templates:
            cls._templates[name] = cv2.imread(os.path.join(BASE_DIR, name), cv2.IMREAD_GRAYSCALE)
        return cls._templates[name]

    @staticmethod
    def _score(image, template):
        if template is None or image.shape[0] < template.shape[0] or image.shape[1] < template.shape[1]:
            return 0.0
        gray = cv2.cvtColor(image[:, :, :3], cv2.COLOR_BGR2GRAY)
        return float(cv2.minMaxLoc(cv2.matchTemplate(gray, template, cv2.TM_CCOEFF_NORMED))[1])

    @classmethod
    def _name_score(cls, row):
        """Super Regeneration's label; the list also comes in a smaller text size."""
        template = cls._template("tpl_majin_super_regen_v2.png")
        if template is None:
            return 0.0
        best = 0.0
        for scale in (1.0, 0.85, 0.75, 0.68):
            scaled = template if scale == 1.0 else cv2.resize(
                template, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
            best = max(best, cls._score(row, scaled))
        return best

    @classmethod
    def _row(cls, panel, slot):
        y = int(round(cls._FIRST_ROW_Y + cls._ROW_PITCH * (slot - 1)))
        return panel[y - 14:y + 14, cls._ROW_X[0]:cls._ROW_X[1]]

    @classmethod
    def panel_visible(cls, panel):
        return _panel_title_is(panel, "tpl_majin_transform_mask_v2.png",
                               "tpl_majin_transform_alt_mask_v2.png")

    @classmethod
    def find_regen_slot(cls, panel):
        if not cls.panel_visible(panel):
            return None
        scores = [(cls._name_score(cls._row(panel, slot)), slot) for slot in range(1, 9)]
        best, slot = max(scores)
        return slot if best >= cls._NAME_MIN else None

    @classmethod
    def slot_state(cls, panel, slot):
        """"ready" when Super Regeneration's name is gold, "cooldown" when it is
        there but not gold, None for any other or empty row.

        Only the ready look has been seen live; the cooldown look is assumed to
        drop the gold (the old panel greyed the row) while keeping the shape.
        """
        row = cls._row(panel, slot)
        if cls._name_score(row) < cls._NAME_MIN:
            return None
        pixels = row.astype(np.int16)
        gold = (pixels[:, :, 2] > 170) & (pixels[:, :, 1] > 120) & (pixels[:, :, 0] < 120)
        return "ready" if np.count_nonzero(gold) >= 120 else "cooldown"


# --- Senzu: H "ITEMS" hotbar and the M side menu (live 2026-10-13) -------------
# The hotbar shares the TRANSFORM list's rows. Refill path: M -> Customize icon
# -> Items -> "Senzu Bean" row -> "N) Empty" slot -> back on Items, stock - 1.

class SenzuMenus:
    HOTBAR_BOX = MajinTransform.PANEL_BOX
    SIDE_TAB_OPEN_BOX = (500, 470, 120, 270)    # vertical MENU tab, menu open
    SIDE_TAB_CLOSED_BOX = (0, 470, 90, 270)     # same tab at the left edge
    HEADING_BOX = (95, 385, 265, 55)
    LIST_BOX = (60, 440, 470, 330)              # first five list rows
    CUSTOMIZE_ICON = (34, 540)
    CUSTOMIZE_ITEMS_ROW = (300, 735)            # 5th row of the Customize root list
    LIST_ROW_X = 300
    SLOT_ROW_X = 292
    FIRST_SLOT_ROW_Y = 485                      # "1) ..." in the SENZU BEAN picker
    SLOT_ROW_PITCH = 62
    _MIN = 0.80                                 # live: 1.000 matches, <= 0.52 otherwise
    HEADINGS = ("items", "customize", "senzu_bean")
    _templates = {}

    @classmethod
    def _template(cls, name):
        if name not in cls._templates:
            cls._templates[name] = cv2.imread(os.path.join(BASE_DIR, name), cv2.IMREAD_GRAYSCALE)
        return cls._templates[name]

    @classmethod
    def _match(cls, image, name):
        """(score, (x, y)) of a template inside a crop."""
        template = cls._template(name)
        if template is None or image.shape[0] < template.shape[0] or image.shape[1] < template.shape[1]:
            return 0.0, (0, 0)
        gray = cv2.cvtColor(image[:, :, :3], cv2.COLOR_BGR2GRAY)
        _, score, _, location = cv2.minMaxLoc(cv2.matchTemplate(gray, template, cv2.TM_CCOEFF_NORMED))
        return float(score), location

    @classmethod
    def side_menu_state(cls, frame):
        """"open", "closed" or None (e.g. Training Mode covers both spots)."""
        if cls._match(crop(frame, cls.SIDE_TAB_OPEN_BOX), "tpl_side_menu_tab_v2.png")[0] >= cls._MIN:
            return "open"
        if cls._match(crop(frame, cls.SIDE_TAB_CLOSED_BOX), "tpl_side_menu_tab_v2.png")[0] >= cls._MIN:
            return "closed"
        return None

    @classmethod
    def heading(cls, frame):
        """Which known side-menu page is showing, or None."""
        area = crop(frame, cls.HEADING_BOX)
        for name in cls.HEADINGS:
            if cls._match(area, f"tpl_menu_heading_{name}_v2.png")[0] >= cls._MIN:
                return name
        return None

    @classmethod
    def senzu_row_y(cls, frame):
        """Client y of the "Senzu Bean" row in the Items list, or None."""
        score, (x, y) = cls._match(crop(frame, cls.LIST_BOX), "tpl_senzu_row_v2.png")
        # Row names start at the left edge (x 36 live); "Senzu Bean" further
        # right would be the end of a longer name such as a Half bean.
        if score < cls._MIN or x > 60:
            return None
        return cls.LIST_BOX[1] + y + cls._template("tpl_senzu_row_v2.png").shape[0] // 2

    @classmethod
    def slot_row_y(cls, slot):
        return cls.FIRST_SLOT_ROW_Y + cls.SLOT_ROW_PITCH * (int(slot) - 1)

    @classmethod
    def hotbar_visible(cls, frame):
        return _panel_title_is(crop(frame, cls.HOTBAR_BOX), "tpl_hotbar_items_mask_v2.png",
                               "tpl_hotbar_items_small_mask_v2.png")

    @classmethod
    def hotbar_slot_has_senzu(cls, frame, slot):
        # The list is drawn at two text sizes (live 2026-10-14).
        row = MajinTransform._row(crop(frame, cls.HOTBAR_BOX), slot)
        for name in ("tpl_senzu_slot_v2.png", "tpl_senzu_slot_small_v2.png"):
            score, (x, _y) = cls._match(row, name)
            if score < cls._MIN:
                continue
            # Hotbar names are right-aligned; gold text left of the match means
            # a longer name ending in "Senzu Bean" (e.g. a Half bean).
            left = row[:, 40:max(40, x - 8)].astype(np.int16)
            gold = (left[:, :, 2] > 150) & (left[:, :, 1] > 110) & (left[:, :, 0] < 120)
            return np.count_nonzero(gold) < 20
        return False


# --- Death: "YOU ARE DEAD." with RESPAWN above GO TO OTHER WORLD ------------
# The legacy respawn point (960, 610) now lands on GO TO OTHER WORLD.
RESPAWN_POINT = (960, 550)
DEAD_BAND_BOX = (700, 468, 520, 40)      # black band behind the title
DEAD_TITLE_BOX = (850, 470, 220, 35)     # red "YOU ARE DEAD." at (864,479)-(1048,493)
DEAD_BUTTON_BOXES = ((680, 532, 560, 34), (680, 584, 560, 34))   # RESPAWN, GO TO OTHER WORLD


def death_dialog_visible(frame):
    """Dark band + red title + two green buttons, in a full client frame.

    The buttons are see-through, so their text changes with what is behind
    them (a label template scored 0.40 over the body); the bars' green does
    not. Only the 4 real death frames of 995 saved live frames pass.
    """
    band = crop(frame, DEAD_BAND_BOX)[:, :, :3]
    if float((band.max(axis=2) < 45).mean()) < 0.30:
        return False
    title = crop(frame, DEAD_TITLE_BOX)[:, :, :3].astype(np.int16)
    if np.count_nonzero((title[:, :, 2] > 200) & (title[:, :, 1] < 120) & (title[:, :, 0] < 120)) < 300:
        return False
    for box in DEAD_BUTTON_BOXES:
        bar = crop(frame, box)[:, :, :3].astype(np.int16)
        # Over a dark body the buttons sit near green 110 (live capture 20 Oct 2026, median
        # BGR 40,110,47); "> 110" missed half of them and every death went unseen.
        # Checked on 720 captured frames: true on every dead stretch, nothing else.
        blue, green_ch, red = bar[:, :, 0], bar[:, :, 1], bar[:, :, 2]
        green = (green_ch > 80) & (green_ch > red + 10) & (green_ch > blue + 25)
        if float(green.mean()) < 0.60:
            return False
    return True


# --- Roblox chat button --------------------------------------------------------

# Client pixels, not reference: Roblox draws its top bar at a fixed size. The chat
# icon sits at x 128-148, y 25-43 in a 1920x1080 client (live capture, 20 Oct 2026). Open
# chat fills the bubble white (0.75 of its box); closed is an outline (0.21).
CHAT_BUTTON_BOX = (115, 15, 50, 37)
CHAT_OPEN_FILL = 0.55
CHAT_CLOSED_FILL = 0.35


def roblox_chat_state(button_box):
    """For a CHAT_BUTTON_BOX crop: ("open" | "closed" | None, icon centre in the crop)."""
    white = button_box[:, :, :3].min(axis=2) > 200
    # Without the voice button the pill ends right after chat, and a light wall
    # shows in the box (20 Oct 2026). Background touches the box edge; the icon
    # (and an unread badge's digit) never does.
    count, labels, stats, _ = cv2.connectedComponentsWithStats(white.astype(np.uint8), connectivity=8)
    height_px, width_px = white.shape
    for index in range(1, count):
        x, y, w, h = stats[index][:4]
        if x == 0 or y == 0 or x + w >= width_px or y + h >= height_px:
            white[labels == index] = False
    rows, cols = np.flatnonzero(white.any(axis=1)), np.flatnonzero(white.any(axis=0))
    if rows.size == 0:
        return None, None
    height, width = rows[-1] - rows[0] + 1, cols[-1] - cols[0] + 1
    if not (15 <= width <= 28 and 14 <= height <= 26):
        return None, None
    fill = float(white[rows[0]:rows[-1] + 1, cols[0]:cols[-1] + 1].mean())
    centre = (int(cols[0] + width // 2), int(rows[0] + height // 2))
    if fill >= CHAT_OPEN_FILL:
        return "open", centre
    if fill <= CHAT_CLOSED_FILL:
        return "closed", centre
    return None, None


# --- Gravity -------------------------------------------------------------------

def gravity_number_mask(label):
    """Right-anchored mask of the "40G" part; the label text is centred."""
    mask = _white_mask(label)
    columns = np.flatnonzero(mask.any(axis=0))
    if columns.size == 0:
        return None
    right = int(columns[-1]) + 1
    piece = mask[:, max(0, right - GRAVITY_NUMBER_WIDTH):right]
    number = np.zeros((mask.shape[0], GRAVITY_NUMBER_WIDTH), bool)
    number[:, GRAVITY_NUMBER_WIDTH - piece.shape[1]:] = piece
    return number


def load_gravity_templates():
    templates = {}
    for value in range(0, 101, 10):
        image = cv2.imread(os.path.join(BASE_DIR, "gravity_v2", f"gravity_{value}.png"),
                           cv2.IMREAD_GRAYSCALE)
        if image is None:
            return {}
        templates[value] = image > 0
    return templates


def read_gravity(label, templates):
    """Return (value or None, best IoU, second IoU, control_present) for a GRAVITY_BOX crop."""
    # The button is black (grey while hovered) behind ~340+ text pixels; the
    # GC wall or floor showing through instead means the menu is closed.
    dark = float((label[:, :, :3].max(axis=2) < 70).mean())
    number = gravity_number_mask(label)
    if number is None or dark < 0.4 or np.count_nonzero(_white_mask(label)) < 200:
        return None, 0.0, 0.0, False
    scores = []
    for value, template in templates.items():
        if template.shape != number.shape:
            continue
        union = np.count_nonzero(number | template)
        scores.append((np.count_nonzero(number & template) / union if union else 0.0, value))
    if not scores:
        return None, 0.0, 0.0, True
    scores.sort(reverse=True)
    best, value = scores[0]
    second = scores[1][0] if len(scores) > 1 else 0.0
    reliable = best >= GRAVITY_MATCH_MIN and best - second >= GRAVITY_MATCH_MARGIN_MIN
    return (value if reliable else None), float(best), float(second), True


# --- Health: tap F while the sweeping white marker is inside the gold zone -----

def health_bar(bar):
    """For a HEALTH_BAR_BOX crop, return (marker_centre_x, (zone_left, zone_right)) or None."""
    white = (bar[:, :, :3].min(axis=2) > 225).sum(axis=0)
    gold = ((bar[:, :, 2] > 180) & (bar[:, :, 1] > 140) & (bar[:, :, 0] < 130)).sum(axis=0)
    marker = np.flatnonzero(white >= 20)
    zone = np.flatnonzero(gold >= 8)
    if marker.size == 0 or zone.size == 0 or marker[-1] - marker[0] > 20:
        return None
    return (marker[0] + marker[-1]) / 2.0, (int(zone[0]), int(zone[-1]))


def health_should_press(marker_x, zone, velocity_px_s, lead_s, margin=12):
    """Press when the marker, projected over the input delay, is inside the zone."""
    projected = marker_x + velocity_px_s * lead_s
    return zone[0] + margin <= projected <= zone[1] - margin


def health_press_delay(marker_x, zone, velocity_px_s, lead_s, frame_interval_s, margin=12):
    """Seconds to wait before pressing F so it lands on the zone's centre, or None to keep watching.

    Pressing as soon as the marker crosses the edge (the old rule) put live presses at
    the zone's first 20%, and jitter of a frame or two turned some into FAILED. The
    centre leaves half the zone of slack either way. When the next frame would arrive
    after the centre, wait out the remainder here instead of overshooting.
    """
    if velocity_px_s == 0:
        return None
    centre = (zone[0] + zone[1]) / 2.0
    to_centre = (centre - marker_x) / velocity_px_s - lead_s
    if to_centre < 0:
        # Already past the centre (a slow frame): still worth pressing while inside.
        return 0.0 if health_should_press(marker_x, zone, velocity_px_s, lead_s, margin) else None
    if to_centre <= frame_interval_s * 1.5:
        return to_centre
    return None


# --- Agility / Physical Damage: one big letter at a time ----------------------

def load_agility_templates():
    templates = {}
    for key in "wasd":
        image = cv2.imread(os.path.join(BASE_DIR, f"tpl_agility_v2_{key}.png"), cv2.IMREAD_GRAYSCALE)
        if image is None:
            return {}
        templates[key] = image
    return templates


def agility_letter(letter_box, templates):
    """For an AGILITY_LETTER_BOX crop return (key or None, score). Red FAILED never matches."""
    return agility_letter_at(letter_box, templates)[:2]


def agility_letter_at(letter_box, templates):
    """(key or None, score, centre_x) of the current letter: the only white one of the
    round's letters (done ones are green, upcoming ones grey). centre_x tells a repeat
    like the second D of "W D D" apart from the D that was just pressed."""
    whiteness = letter_box[:, :, :3].min(axis=2)
    # HTC's peach floor reaches ~211 in every channel, which flooded a fixed >200 cut.
    # The letter fill is ~253, so cut just above the floor (never below the old 200).
    floor = float(np.percentile(whiteness, 90))
    mask = (whiteness > min(240.0, max(200.0, floor + 20))).astype(np.uint8) * 255
    if np.count_nonzero(mask) < 150 or not templates:
        return None, 0.0, None
    best = None
    for key, template in templates.items():
        if template.shape[0] > mask.shape[0] or template.shape[1] > mask.shape[1]:
            continue
        _, score, _, (x, _y) = cv2.minMaxLoc(cv2.matchTemplate(mask, template, cv2.TM_CCOEFF_NORMED))
        if best is None or score > best[1]:
            best = (key, float(score), x + template.shape[1] / 2.0)
    if best is None:
        return None, 0.0, None
    key, score, centre_x = best
    return (key if score >= AGILITY_LETTER_MIN else None), score, centre_x


def round_timer_fill(strip):
    """Filled columns of a ROUND_TIMER_BOX crop; 0 between rounds and after FAILED."""
    strip = strip[:, :, :3].astype(np.int16)
    blue, green, red = strip[:, :, 0], strip[:, :, 1], strip[:, :, 2]
    # Cream-to-orange fill; the lavender GC wall has blue >= red.
    filled = (red > 200) & (green > 140) & (red - blue > 25)
    return int(filled.any(axis=0).sum())


# --- HP ------------------------------------------------------------------------

def read_ki_charge(bar_box):
    """For a KI_BAR_BOX crop: charge as layers, 0.0 (empty) to 3.0 (third layer full).

    None when the bar isn't readable (HUD hidden, something drawn over it, or a
    colour this reader hasn't seen).
    """
    columns = bar_box[:, :, :3].astype(np.float32).mean(axis=0)
    blue, green, red = columns[:, 0], columns[:, 1], columns[:, 2]
    layers = np.full(len(columns), -1)
    layers[columns.max(axis=1) < 70] = 0
    is_blue = (blue > 120) & (red < 60) & (green <= KI_LAYER_GREEN_MAX)
    for layer, green_min in enumerate(KI_LAYER_GREEN_MIN, start=1):
        layers[is_blue & (green >= green_min) & (layers == -1)] = layer
    if np.mean(layers == -1) > 0.10:
        return None
    # A layer only counts from 3% of the bar; single columns at its slanted
    # right edge blend into the next colour.
    present = [layer for layer in range(4) if np.mean(layers == layer) >= 0.03]
    top = max(present, default=0)
    if top == 0:
        return 0.0
    # The top layer fills from the left; the rest of the bar is the layer below.
    return top - 1 + float(np.mean(layers == top))


def hud_visible(labels_box):
    """For a HUD_LABELS_BOX crop: are the HP/KI/FR labels on screen?"""
    template = SenzuMenus._template("tpl_hud_labels_v2.png")
    if template is None:
        return False
    gray = cv2.cvtColor(labels_box[:, :, :3], cv2.COLOR_BGR2GRAY)
    # Only the three label boxes count. The character portrait shows between and
    # around them, and a dark-haired portrait dropped the unmasked score to 0.45:
    # the HUD read as hidden, HP was never checked, and the character died live.
    scores = cv2.matchTemplate(gray, template, cv2.TM_CCOEFF_NORMED, mask=_hud_label_mask(template))
    scores[~np.isfinite(scores)] = 0
    return bool(scores.max() >= HUD_LABELS_MIN)


_HUD_LABEL_BOX_ROWS = ((3, 23), (31, 52), (60, 80))   # HP, KI, FR boxes in tpl_hud_labels_v2.png
_HUD_LABEL_BOX_COLUMNS = (2, 24)


def _hud_label_mask(template):
    mask = np.zeros_like(template)
    for top, bottom in _HUD_LABEL_BOX_ROWS:
        mask[top:bottom, _HUD_LABEL_BOX_COLUMNS[0]:_HUD_LABEL_BOX_COLUMNS[1]] = 255
    return mask


def hp_fill_fraction(bar):
    """Filled share of an HP_FILL_BOX crop, or None when the HUD isn't drawn."""
    hsv = cv2.cvtColor(bar[:, :, :3], cv2.COLOR_BGR2HSV)
    vivid = (hsv[:, :, 1] > 140) & (hsv[:, :, 2] > 140)
    filled = np.flatnonzero(vivid.mean(axis=0) >= 0.5)
    if filled.size == 0:
        return None
    return float(filled[-1] + 1) / bar.shape[1]


# --- Ki Control / Ki Damage: osu-style numbered circles -----------------------

def ki_click_blocked(x, y):
    return any(bx <= x < bx + bw and by <= y < by + bh
               for bx, by, bw, bh in KI_CLICK_BLOCKED_BOXES)


def find_ki_circles(frame):
    """Dark round discs (navy/olive/maroon fill, white number) in a full frame: [(x, y, r)]."""
    playfield = frame[KI_PLAYFIELD_TOP:KI_PLAYFIELD_BOTTOM,
                      KI_PLAYFIELD_LEFT:KI_PLAYFIELD_RIGHT, :3]
    if playfield.size == 0:
        return []
    hsv = cv2.cvtColor(playfield, cv2.COLOR_BGR2HSV)
    blue_window = ((hsv[:, :, 0] > 105) & (hsv[:, :, 0] < 135)
                   & (hsv[:, :, 1] > 200) & (hsv[:, :, 2] > 120))
    dark = ((hsv[:, :, 2] < 150) & ~blue_window).astype(np.uint8) * 255
    contours, _ = cv2.findContours(dark, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    circles = []
    for contour in contours:
        area = cv2.contourArea(contour)
        if not KI_CIRCLE_AREA[0] < area < KI_CIRCLE_AREA[1]:
            continue
        (x, y), radius = cv2.minEnclosingCircle(contour)
        x += KI_PLAYFIELD_LEFT
        y += KI_PLAYFIELD_TOP
        if area / (math.pi * radius * radius) < KI_CIRCLE_FILL_MIN or ki_click_blocked(x, y):
            continue
        circles.append((float(x), float(y), float(radius)))
    for x, y, radius in _touching_circles_cached(playfield, hsv, dark):
        x += KI_PLAYFIELD_LEFT
        y += KI_PLAYFIELD_TOP
        if not ki_click_blocked(x, y) and all(math.hypot(x - cx, y - cy) > cr
                                              for cx, cy, cr in circles):
            circles.append((x, y, radius))
    return circles


KI_TOUCHING_REFRESH_SEC = 0.12
_TOUCHING_CACHE = {"at": -1.0, "circles": []}


def _touching_circles_cached(frame, hsv, dark):
    """The touching-circle passes cost ~45 ms a frame on a slower PC, which made every
    Ki click late there. Circles never move once drawn and appear ~0.7 s before they
    close, so finding them ~8 times a second is plenty; the cheap pass above still
    runs every frame. A circle cached after its click gets no ring, so no click."""
    now = time.monotonic()
    if (0 <= now - _TOUCHING_CACHE["at"] < KI_TOUCHING_REFRESH_SEC
            and _TOUCHING_CACHE.get("shape") == dark.shape):
        # A cached centre is only a discovery hint. Validate its current fill and
        # number so a disappeared target cannot keep a scheduled click alive.
        return [c for c in _TOUCHING_CACHE["circles"] if _ki_cached_circle_present(frame, dark, *c)]
    half = lambda mask: cv2.resize(mask, None, fx=0.5, fy=0.5, interpolation=cv2.INTER_NEAREST)
    # Circle fills are saturated (navy/olive/maroon, S 150+, V 45-100); the character's
    # near-black body (V ~17) is not, so a circle over the body still stands alone there.
    fill = cv2.inRange(hsv, (0, 140, 40), (180, 255, 110))
    white = half(cv2.inRange(frame[:, :, :3], (236, 236, 236), (255, 255, 255)))
    found = []
    for mask in (half(dark), half(fill)):
        for x, y, radius in _ki_circles_touching_dark(mask, white):
            if all(math.hypot(x - fx, y - fy) > fr for fx, fy, fr in found):
                found.append((x, y, radius))
    _TOUCHING_CACHE.update(at=now, circles=found, shape=dark.shape)
    return found


def _ki_cached_circle_present(frame, dark, x, y, radius):
    reach = int(radius * .4)
    cx, cy = int(x), int(y)
    patch = frame[max(0, cy-reach):cy+reach+1, max(0, cx-reach):cx+reach+1, :3]
    if not patch.size:
        return False
    white = np.all(patch >= 236, axis=2).mean()
    xs = np.clip((x + .75*radius*_RING_COS).astype(int), 0, dark.shape[1]-1)
    ys = np.clip((y + .75*radius*_RING_SIN).astype(int), 0, dark.shape[0]-1)
    return .03 <= white <= .6 and np.count_nonzero(dark[ys, xs]) >= 40


def _ki_circles_touching_dark(dark, white):
    """Circles whose dark fill runs into other dark pixels (GC's floor stripe, the
    character), so their blob isn't round. About half the live clicks had their
    circle found only ~0.13 s before closing this way; 41% of those were PERFECT,
    against 100% of the circles tracked from a large ring.

    Works on half-resolution masks and returns full-resolution (x, y, r). The disc's
    centre is still the point farthest from any non-dark pixel, and the white number
    sits in it, which a stripe or a body never has.
    """
    # The number's anti-aliased outline is neither dark nor white; close those gaps.
    solid = cv2.morphologyEx(cv2.bitwise_or(dark, white), cv2.MORPH_CLOSE,
                             _KI_CLOSE_KERNEL)
    distance = cv2.distanceTransform(solid, cv2.DIST_L2, 5)
    low, high = (math.sqrt(KI_CIRCLE_AREA[0] / math.pi) / 2, math.sqrt(KI_CIRCLE_AREA[1] / math.pi) / 2)
    peaks = (distance >= low * 0.9) & (distance <= high) & (
        distance >= cv2.dilate(distance, _KI_PEAK_KERNEL))
    found = []
    for py, px in zip(*np.nonzero(peaks)):
        radius = float(distance[py, px])
        if any(math.hypot(px - fx, py - fy) < fr for fx, fy, fr in found):
            continue
        r = int(radius)
        top, left = py - r, px - r
        patch_white = white[max(0, top):py + r + 1, max(0, left):px + r + 1]
        patch_dark = dark[max(0, top):py + r + 1, max(0, left):px + r + 1]
        if top < 0 or left < 0 or patch_white.shape != (2 * r + 1, 2 * r + 1):
            continue
        yy, xx = np.ogrid[-r:r + 1, -r:r + 1]
        r2 = xx * xx + yy * yy
        centre = r2 <= (0.55 * radius) ** 2
        rim = (r2 > (0.6 * radius) ** 2) & (r2 <= (0.9 * radius) ** 2)
        number = np.count_nonzero(patch_white[centre]) / max(1, np.count_nonzero(centre))
        fill = np.count_nonzero(patch_dark[rim]) / max(1, np.count_nonzero(rim))
        if 0.03 <= number <= 0.5 and fill >= 0.85:
            found.append((px, py, radius))
    return [(px * 2.0 + 1, py * 2.0 + 1, radius * 2.0) for px, py, radius in found]


@lru_cache(maxsize=64)
def _ki_ring_offsets(radius):
    rings = np.arange(radius, radius * KI_RING_MAX_FACTOR, 2.0)
    radii = rings[:, None, None] + np.array([0, -KI_RING_EDGE_PX, KI_RING_EDGE_PX])[None, :, None]
    return rings, radii*_RING_COS, radii*_RING_SIN


def ki_ring_radius(frame, x, y, radius):
    """Radius of the bright approach ring around a circle, or None if not seen."""
    height, width = frame.shape[:2]
    if not math.isfinite(radius) or radius <= 0 or not height or not width:
        return None
    rings, x_offsets, y_offsets = _ki_ring_offsets(radius)
    xs = np.clip((x + x_offsets).astype(int), 0, width-1)
    ys = np.clip((y + y_offsets).astype(int), 0, height-1)
    pixels = frame[ys, xs, :3].astype(np.int16)
    line = pixels[:, 0]
    # A bright floor is not a thin ring: require contrast on both sides.
    inner = np.abs(line-pixels[:, 1]).sum(axis=2)
    outer = np.abs(line-pixels[:, 2]).sum(axis=2)
    coloured = (line[:, :, 2] > 225) & (line[:, :, 1] > 150)
    hits = (coloured & (inner > KI_RING_CONTRAST_MIN) & (outer > KI_RING_CONTRAST_MIN)).sum(axis=1)
    best = int(np.argmax(hits))
    return float(rings[best]) if hits[best] >= KI_RING_MIN_HITS else None


# --- "Progression X/20" in the minigame panel ---------------------------------

def progression_label_box(stat):
    return PROGRESSION_LABEL_BOXES["ki" if stat in ("Ki Control", "Ki Damage") else "standard"]


KI_LABEL_WORD_MIN = 0.45   # live: Ki label 0.59-1.0, other screens <= 0.19
_KI_LABEL_WORD = []


def ki_label_visible(label_box):
    """For a Ki progression_label_box crop: is "Multiplier" showing on the label's panel?

    The numbers at the label's right end sit over the floor, and HTC's pale floors
    break them up (live capture: 10/43 crops read), which stopped every Ki click. The
    word "Multiplier" is always on the dark middle of the panel.
    """
    if not _KI_LABEL_WORD:
        _KI_LABEL_WORD.append(cv2.imread(os.path.join(BASE_DIR, "tpl_ki_label_multiplier_v2.png"),
                                         cv2.IMREAD_GRAYSCALE))
    template = _KI_LABEL_WORD[0]
    if template is None or label_box.shape[0] < template.shape[0] or label_box.shape[1] < template.shape[1]:
        return False
    grey = cv2.cvtColor(label_box[:, :, :3], cv2.COLOR_BGR2GRAY)
    blue, green, red = label_box[:, :, :3].astype(np.int16).transpose(2, 0, 1)
    gold = ((grey > 100) & (red - blue > 60) & (green - blue > 20)).astype(np.uint8) * 255
    return bool(cv2.minMaxLoc(cv2.matchTemplate(gold, template, cv2.TM_CCOEFF_NORMED))[1]
                >= KI_LABEL_WORD_MIN)


def progression_spans(label_box):
    """Columns of the numbers beside the slash in a progression_label_box crop.

    Returns (label_grey, (numerator_start, numerator_end), (denominator_start,
    denominator_end)) or None. Digit identities are never read: completion
    compares the two images (20/20 is the same picture twice), the same idea
    the legacy label reader used.
    """
    if label_box.shape[0] == PROGRESSION_LABEL_BOXES["ki"][3]:
        # Gold Ki label on its own dark panel; the anti-aliased 1-2 px slash only
        # reaches ~100-220 grey, so a high cut would break it apart.
        label = cv2.cvtColor(label_box[:, :, :3], cv2.COLOR_BGR2GRAY)
        # HTC's panel fades into a cream/pale-blue floor right where "0/50" sits;
        # brightness alone merged the numbers into it. Gold is red/green over blue.
        blue, green, red = label_box[:, :, :3].astype(np.int16).transpose(2, 0, 1)
        # HTC's yellow floor is gold too, but far more saturated (S ~187) than the
        # pale gold text (S 90-154), so cap saturation.
        saturation = cv2.cvtColor(label_box[:, :, :3], cv2.COLOR_BGR2HSV)[:, :, 1]
        # Over that floor the digits' brown outline turns mid-gold too; the fill is
        # still brighter than its surroundings, which keeps the glyphs apart.
        # Grown by a pixel so thin strokes (the slash) keep their width.
        brighter = cv2.dilate(cv2.adaptiveThreshold(label, 255, cv2.ADAPTIVE_THRESH_MEAN_C,
                                                    cv2.THRESH_BINARY, 11, -8),
                              np.ones((3, 3), np.uint8)) > 0
        text = ((label > 100) & (red - blue > 60) & (green - blue > 20)
                & (saturation < 170) & brighter).astype(np.uint8)
    else:
        # The standard label floats over the game world. Plain brightness merged the
        # white text into a green cape behind it (0/173 frames found live); white
        # text is bright in every channel, red floor and coloured clothing are not.
        label = label_box[:, :, :3].min(axis=2)
        # HTC's beige wall sits at ~130-150 behind it, so the cut follows the
        # background (text is ~15% of the box, so the 80th percentile is background).
        text = (label > max(110.0, float(np.percentile(label, 80)) + 30)).astype(np.uint8)
    count, _labels, stats, _ = cv2.connectedComponentsWithStats(text, 8)
    pieces = sorted((tuple(int(v) for v in s) for s in stats[1:count] if s[4] >= 6),
                    key=lambda s: s[0])
    if not pieces:
        return None
    # The number pair is a run of pieces separated by <= 3 px. Take the rightmost
    # run that has a slash: bright scenery at the box's right edge forms its own
    # run, and treating that as the numbers hid the label for whole live runs.
    runs = [[pieces[0]]]
    for piece in pieces[1:]:
        previous = runs[-1][-1]
        if piece[0] - (previous[0] + previous[2]) > 3:
            runs.append([piece])
        else:
            runs[-1].append(piece)
    # The bold Ki slash measures -0.242 (read_progression_level allows it too);
    # the strict standard cutoff hid Ki Damage's label for a whole live run.
    max_slope = -0.20 if label_box.shape[0] == PROGRESSION_LABEL_BOXES["ki"][3] else -0.25
    for run in reversed(runs):
        slash = _find_slash(text, run, maximum_slope=max_slope)
        if slash is not None:
            break
    else:
        return None
    left = [p for p in run if p[0] + p[2] <= slash[0] + 1]
    right = [p for p in run if p[0] >= slash[1] - 1]
    if not left or not right:
        return None
    return (label,
            (min(p[0] for p in left), max(p[0] + p[2] for p in left)),
            (min(p[0] for p in right), max(p[0] + p[2] for p in right)))


def _find_slash(text, run, minimum_width=4, maximum_slope=-0.25):
    """Columns (start, end) of the '/', the one stroke rising left to right."""
    best = None
    for x, y, w, h, _area in run:
        if not minimum_width <= w <= 12 or h < 8:
            continue
        piece = text[y:y + h, x:x + w]
        centres = [(r, np.flatnonzero(piece[r]).mean()) for r in range(h) if piece[r].any()]
        if len(centres) < 6:
            continue
        slope = np.polyfit([c[0] for c in centres], [c[1] for c in centres], 1)[0]
        if slope < maximum_slope and (best is None or slope < best[0]):
            best = (slope, (x, x + w))
    return None if best is None else best[1]


def progression_numbers(label_box):
    """(numerator, denominator) grey patches, or None when the label isn't found."""
    spans = progression_spans(label_box)
    if spans is None:
        return None
    label, (a, b), (c, d) = spans
    return label[:, a:b].copy(), label[:, c:d].copy()


def _progression_text(label_box):
    text = _points_text(label_box)
    if label_box.shape[0] == 26:
        # Gold Ki letters are warmer than the blue/pink scenery behind them.
        # Signed channels avoid uint8 wraparound in these colour differences.
        blue, green, red = label_box[:, :, :3].astype(np.int16).transpose(2, 0, 1)
        text &= (green - blue > 10) & (red - blue > 10)
    return text


def read_progression_level(label_box):
    """Numeric (level, target) for the minigame label, or None if unsure.

    Completion and stall detection still compare progression_numbers images.
    This extra reader is only needed to compare a count against a Points Left
    cap. Only captured progression glyphs are used, with the word
    "Progression" anchoring the fraction so scenery cannot truncate a number.
    """
    if (label_box is None or label_box.ndim != 3 or label_box.shape[2] < 3
            or label_box.shape[:2] not in ((32, 360), (26, 520))):
        return None
    sheet = _points_asset("tpl_progression_digits_v2.png")
    if sheet is None or sheet.shape != (72, 200):
        return None
    standard = label_box.shape[0] == 32
    text = _progression_text(label_box)
    layouts = (("standard", (20, 104), 0),) if standard else (
        ("ki", (17, 89), 24), ("ki_large", (18, 94), 48))
    matches = []
    for name, shape, row in layouts:
        prefix = _points_asset(f"tpl_progression_prefix_{name}_v2.png")
        if prefix is None or prefix.shape != shape:
            return None
        scores = cv2.matchTemplate(text.astype(np.float32), prefix.astype(np.float32),
                                   cv2.TM_SQDIFF_NORMED)
        _, _, (x, y), _ = cv2.minMaxLoc(scores)
        h, w = shape
        if _text_matches(text[y:y + h, x:x + w], prefix):
            matches.append((x, y, h, w, row))
    if len(matches) != 1:
        return None
    x, y, h, w, row = matches[0]
    # Leave at most three empty columns before the first digit. A missing
    # leading 1 in 17/20 must fail rather than become a confident 7/20.
    start = x + w + (6 if standard else 3)
    fraction = text[max(0, y - 2):y + h, start:]
    columns = np.flatnonzero(fraction.any(axis=0))
    if columns.size == 0 or columns[0] > 3:
        return None
    # Ignore scenery beyond the fraction, never holes within either number.
    gaps = np.flatnonzero(np.diff(columns) > 4)
    end = columns[gaps[0]] + 1 if gaps.size else columns[-1] + 1
    fraction = fraction[:, :end]
    count, _, stats, _ = cv2.connectedComponentsWithStats(fraction.astype(np.uint8), 8)
    # The larger, bold Ki slash has a measured slope of -0.242. Other readers
    # retain the original cutoff; the label anchor guards this wider allowance.
    slash = _find_slash(fraction, [tuple(int(v) for v in s) for s in stats[1:count]],
                        maximum_slope=-0.20 if row == 48 else -0.25)
    if slash is None:
        return None
    glyphs = [sheet[row:row + 24]]
    values = (_read_digits(fraction[:, :slash[0]], glyphs),
              _read_digits(fraction[:, slash[1]:], glyphs))
    # GC levels run to 20 and HTC to 50 (POINTS_TOTALS).
    if None in values or values[1] not in PROGRESSION_LEVEL_TOTALS or values[0] > values[1]:
        return None
    return values


def progression_complete(numbers):
    """True for 20/20-style labels, False otherwise, None when unreadable."""
    if numbers is None:
        return None
    numerator, denominator = numbers
    if abs(numerator.shape[1] - denominator.shape[1]) > 1:
        return False
    # The two numbers can land a pixel apart in subpixel position.
    best = -1.0
    for shift in (-1, 0, 1):
        a = numerator[:, max(0, shift):numerator.shape[1] + min(0, shift)]
        width = min(a.shape[1], denominator.shape[1])
        if width < 3:
            continue
        a = a[:, :width].astype(np.float32)
        b = denominator[:, :width].astype(np.float32)
        if a.std() < 1 or b.std() < 1:
            continue
        best = max(best, float(np.corrcoef(a.ravel(), b.ravel())[0, 1]))
    return best >= 0.85


def progression_signature(numbers):
    """Same shape as the legacy signature: one 32x32 mask per side of the slash."""
    if numbers is None:
        return None
    return tuple(
        (cv2.resize((patch > 100).astype(np.uint8), (32, 32), interpolation=cv2.INTER_NEAREST) > 0,)
        for patch in numbers
    )
