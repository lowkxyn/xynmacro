# XynMacro Python sidecar.
# Screen capture, game-state detection, input injection, and a loopback HTTP API
# the Tauri shell calls. Launched headless by the desktop app; not run directly.
import time
import math
import cv2
import mss
import numpy as np
import keyboard
import pydirectinput
import win32api
import win32con
import os
import sys
import json
import threading
import hmac

# ================= APP INFO =================
APP_NAME = "XynMacro"
APP_VERSION = None  # Injected by the Tauri launcher from package_info().version.
_DISPLAY_RESTORE = None
_display_resolution_lock = threading.Lock()


def set_app_version(version):
    global APP_VERSION
    normalized = str(version or "").strip()
    if not normalized:
        raise ValueError("Tauri package version is required")
    APP_VERSION = normalized


def _display_restore_path():
    return os.path.join(DATA_DIR, "display_restore.json")


def _persist_display_restore(restore):
    mode = restore["mode"]
    payload = {
        "device": restore["device"],
        "width": int(mode.PelsWidth),
        "height": int(mode.PelsHeight),
        "hz": int(getattr(mode, "DisplayFrequency", 0)),
    }
    path = _display_restore_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    temp_path = path + ".tmp"
    with open(temp_path, "w", encoding="utf-8") as file:
        json.dump(payload, file)
        file.flush()
        os.fsync(file.fileno())
    os.replace(temp_path, path)


def _load_display_restore():
    path = _display_restore_path()
    try:
        with open(path, "r", encoding="utf-8") as file:
            payload = json.load(file)
        device = str(payload.get("device") or "").strip()
        width = int(payload.get("width") or 0)
        height = int(payload.get("height") or 0)
        if not device or width <= 0 or height <= 0:
            raise ValueError("invalid display restore metadata")
        mode = win32api.EnumDisplaySettings(device, win32con.ENUM_CURRENT_SETTINGS)
        mode.PelsWidth = width
        mode.PelsHeight = height
        hz = int(payload.get("hz") or 0)
        mode.Fields |= win32con.DM_PELSWIDTH | win32con.DM_PELSHEIGHT
        if hz > 0:
            mode.DisplayFrequency = hz
            mode.Fields |= win32con.DM_DISPLAYFREQUENCY
        return {"device": device, "mode": mode}
    except FileNotFoundError:
        return None
    except Exception as error:
        print(f"[display] Could not load saved display restore state: {error}")
        return None


def _clear_display_restore_file():
    try:
        os.remove(_display_restore_path())
    except FileNotFoundError:
        pass
    except OSError as error:
        print(f"[display] Could not remove display restore state: {error}")

# Base paths.
# BASE_DIR  — where this script lives. Read-only in installed builds (resource dir).
#             Used for template assets (tpl_*.png).
# DATA_DIR  — writable runtime location. Defaults to BASE_DIR in dev; overridden by
#             --data-dir flag in installed builds (points to app-data dir).
# JSON_DIR  — DATA_DIR/json. Holds macro_config.json and saved logs.
if getattr(sys, "frozen", False):
    # PyInstaller onefile build: bundled read-only assets (tpl_*.png, defaults/)
    # are unpacked to sys._MEIPASS at runtime; __file__ would point at a temp copy.
    BASE_DIR = sys._MEIPASS
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = BASE_DIR
JSON_DIR = os.path.join(DATA_DIR, "json")
MACRO_CONFIG_FILE = os.path.join(JSON_DIR, "macro_config.json")

# One-time migration paths (older builds used these locations)
LEGACY_SETTINGS_FILE = os.path.join(BASE_DIR, "settings.json")
LEGACY_CONFIG_DIR = os.path.join(BASE_DIR, "config")
LEGACY_MACRO_CONFIG_FILE = os.path.join(LEGACY_CONFIG_DIR, "macro_config.json")

# Where saved logs (and future exports) are written. User-pickable; remembered in
# save_dir.txt next to the config. Defaults to a "saves" folder in the data dir.
SAVE_DIR = os.path.join(DATA_DIR, "saves")
def _save_dir_pref_file():
    return os.path.join(DATA_DIR, "save_dir.txt")
def _load_save_dir():
    global SAVE_DIR
    try:
        with open(_save_dir_pref_file(), "r", encoding="utf-8") as f:
            p = f.read().strip()
        if p:
            SAVE_DIR = p
    except Exception:
        pass
def _set_save_dir(path):
    global SAVE_DIR
    SAVE_DIR = path
    try:
        with open(_save_dir_pref_file(), "w", encoding="utf-8") as f:
            f.write(path)
    except Exception as e:
        print(f"[save] could not persist save dir: {e}")

# ================= CONFIGURATION =================

# 1. SETUP MODE
# Set False now that you have captured everything
SETUP_MODE = False
SHOW_DEBUG_HUD = False
DIAGNOSTIC_MODE = False
VERBOSE_DETECTOR_LOGS = False
START_DELAY = 5.0  # seconds before the macro starts (lets you tab into the game)
GC_GRAVITY_TARGET_G = 0  # 0 leaves the current GC gravity unchanged
PREVENT_SLEEP_WHILE_RUNNING = True
# Roblox restored from the taskbar comes back windowed even when it was fullscreen,
# which moves every scan region. Put it back to fullscreen before a run.
RESTORE_FULLSCREEN_ON_START = True
# A resolution change can leave an unreadable screen. Keep it only if confirmed.
DISPLAY_CONFIRM_CHANGES = True
DISPLAY_CONFIRM_TIMEOUT_SEC = 10.0
SHUTDOWN_PC_WHEN_FINISHED = False
AFTER_RUN_GAME_ACTION = "none"
AFTER_RUN_ON_FAILURE = False
AUTO_RETRY_ON_FAILURE = False
AUTO_RETRY_MAX_ATTEMPTS = 3
AUTO_RETRY_RECOVERY_MODE = "reset"
AUTO_RETRY_WALK_OUT = True
AUTO_RETRY_WALK_SECONDS = 2.5
PC_SHUTDOWN_DELAY_SEC = 60
AFTER_RUN_GAME_ACTIONS = {"none", "main_menu", "close_game", "zero_gravity"}
AUTO_RETRY_RECOVERY_MODES = {"reset", "wait_for_death"}

CALIB_FREEZE_COUNTDOWN = 3

# 2. COORDINATES (LOCKED)
SEARCH_PADDING = 15
CONFIDENCE_TEXT = 0.85
NEW_GAME_WAIT = 0.8

# 3. PROGRESSION (YELLOW ONLY)
# Strategy: if YELLOW is NOT seen for N seconds, assume complete.
NO_YELLOW_TIMEOUT_SEC = 5.0           # seconds without yellow before switching; numeric progression OCR is not reliable enough to replace this
NO_YELLOW_FALLBACK_ENABLED = False    # Progression tracking is the primary completion signal. The
                                      # no-yellow switch is an opt-in fallback; when off, an untracked
                                      # stat keeps training until tracking locks or L skips it.
YELLOW_SAMPLE_INTERVAL_SEC = 0.25     # seconds between samples (lower = faster response, more CPU)
TRAINING_LOAD_GRACE_SEC = 12.0        # after a switch/start, allow this long for the FIRST yellow
                                      # (slow load/ping) before the no-yellow switch can fire

# Manual override: press L to skip to next category immediately
MANUAL_NEXT_KEY = "l"
MANUAL_NEXT_DEBOUNCE = 0.8  # seconds

# Global hotkey that toggles macro start/stop (any keyboard-lib-compatible name).
START_STOP_HOTKEY = "f6"
PAUSE_HOTKEY = "u"


def _normalize_hotkey_name(value):
    """Coerce a hotkey value into a string the `keyboard` library accepts.
    Accepts strings like 'l', 'F6', 'shift+s'. Returns lowercased+stripped,
    or empty string if unusable."""
    if value is None:
        return ""
    s = str(value).strip().lower()
    return s

# Yellow debug (code-only; not in config)
YELLOW_DEBUG_LOG = False
YELLOW_DEBUG_LOG_INTERVAL = 1.0

# HSV ranges (locked to your calibrated values)
YELLOW_HSV_LOWER_STD = np.array([15, 83, 165], dtype=np.uint8)
YELLOW_HSV_UPPER_STD = np.array([35, 255, 255], dtype=np.uint8)
YELLOW_HSV_LOWER_KI = np.array([16, 83, 140], dtype=np.uint8)
YELLOW_HSV_UPPER_KI = np.array([36, 255, 255], dtype=np.uint8)

# Pixel-only mode: sample one fixed point in the 1920x1080 client reference space.
YELLOW_PIXEL_STD = {"x": 1384, "y": 684}
YELLOW_PIXEL_KI = {"x": 1384, "y": 566}

# Pixel-sampled HSV tolerances (auto-generated from clicked pixel)
YELLOW_H_TOL = 10
YELLOW_S_TOL = 90
YELLOW_V_TOL = 90

BUTTONS = {
    "Health": (327, 425),
    "Agility": (329, 504),
    "Ki Control": (324, 582),
    "Physical Damage": (328, 653),
    "Ki Damage": (326, 732)
}
DEFAULT_BUTTONS = dict(BUTTONS)

TRAINING_ORDER = ["Health", "Agility", "Ki Control", "Physical Damage", "Ki Damage"]
DEFAULT_TRAINING_ORDER = list(TRAINING_ORDER)
TRAINING_ORDER_CUSTOM = list(TRAINING_ORDER)

# Goals are not used (yellow progression is used for all categories).

# Coordinate constants and calibration overrides use a canonical 1920x1080
# client-local reference space. They are converted to the live Roblox client rect
# only at capture/click time, so moving or resizing Roblox cannot stack offsets.
GAME_REFERENCE_WIDTH = 1920
GAME_REFERENCE_HEIGHT = 1080
HEALTH_BOX = {"top": 547, "left": 1611, "width": 81, "height": 110}
AGILITY_BOX = {"top": 543, "left": 1414, "width": 504, "height": 112}
DEFAULT_HEALTH_BOX = dict(HEALTH_BOX)
DEFAULT_AGILITY_BOX = dict(AGILITY_BOX)
TRAINING_MENU_BOX = {"top": 280, "left": 0, "width": 500, "height": 120}
TRAINING_MENU_MATCH_THRESHOLD = 0.90
TRAINING_MENU_STABLE_FRAMES = 3
GRAVITY_LABEL_BOX = {"left": 456, "top": 316, "width": 142, "height": 51}
GRAVITY_CLICK_POINT = (527, 342)
GRAVITY_POINTER_PARK_POINT = (1000, 200)
GRAVITY_MATCH_MIN = 0.80
GRAVITY_MATCH_MARGIN_MIN = 0.035
GC_DEATH_DIALOG_BANDS = (
    (750, 545, 420, 50),
    (535, 588, 850, 45),
    (535, 630, 850, 45),
)
GC_RESPAWN_POINT = (960, 610)
AGILITY_CONFIDENCE = 0.85
SCALES = [1.2, 1.1, 1.0, 0.9, 0.8, 0.7]

# Game window detection.
# Substring patterns to match against window titles (case-insensitive). User can refine
# via the UI once we expose it; for now this list covers common DBOG client names.
DBOG_WINDOW_TITLES = ["roblox"]
SUPPORTED_ROBLOX_EXECUTABLES = {
    "robloxplayerbeta.exe",
    "windows10universal.exe",
}
GAME_HWND = None
# True when Roblox is running but minimized — a minimized window reports a 0x0
# client rect, so find_dbog_window() can't see it even though the game is open.
GAME_WINDOW_MINIMIZED = False
GAME_OFFSET_X = 0
GAME_OFFSET_Y = 0
GAME_WIDTH = 1920
GAME_HEIGHT = 1080
# Monitor containing the largest part of GAME_HWND. Updated alongside the game
# client rect and used for state reporting, resolution changes, and calibration.
GAME_MONITOR_INFO = None
_game_window_refresh_lock = threading.Lock()
_last_game_window_refresh_at = 0.0
# Button calibration.
# Each of the 5 training-trait buttons in the Tab menu has its own click position.
# Defaults in BUTTONS are baked in for 1920x1080; user-calibrated values in
# USER_BUTTON_OVERRIDES (keyed by stat name) take precedence on next macro start.
USER_BUTTON_OVERRIDES = {}        # {"Health": [x, y], "Agility": [x, y], ...}
BUTTON_CALIBRATION_WAITING = None # stat name currently capturing, or None
BUTTON_CALIBRATION_GENERATION = 0 # invalidates a late overlay result after cancel/restart

# Region calibration (HEALTH_BOX, AGILITY_BOX). User drags a rectangle in Roblox; we save
# {"top","left","width","height"} per key and apply on startup. Same persistence pattern
# as button calibration.
REGION_NAMES = {
    "health_box":  "Health Box",
    "agility_box": "Agility Box",
}
USER_REGION_OVERRIDES = {}        # {"health_box": {"top","left","width","height"}, ...}
REGION_CALIBRATION_WAITING = None # "health_box" / "agility_box" / None
REGION_CALIBRATION_GENERATION = 0 # invalidates a late overlay result after cancel/restart

# AGILITY TIMINGS
KEY_PRESS_DELAY = 0.005
STABILIZE_DELAY = 0.15
POST_COMBO_DELAY = 0.3
AGILITY_MODE = "v2"          # "v2" = burst + chained strings, "v1" = burst (single call)
AGILITY_GREEN_OBSERVE_SEC = 0.7          # v2: observe per-letter green flash up to this long (telemetry)
AGILITY_INTER_STRING_WAIT_SEC = 1.0      # v2: after a string completes, poll this long for the next
AGILITY_AFTER_GREEN_SETTLE_SEC = 0.1     # v2: brief pause after a burst before polling for the next string

# HEALTH TIMING
HEALTH_HIT_COOLDOWN_SEC = 0.8
HEALTH_MODE = "v2_track"             # v1: old narrow red probe; v2: tracked marker enters green target
_HEALTH_V2_STATE = {
    "geometry": None,
    "target": None,
    "armed": True,
    "last_hit": 0.0,
}

# KI CONTROL
# The target sits near hue 7. Keeping the upper bound below the gold player halo
# and HUD accents prevents false positives in GC while retaining HTC brightness range.
LOWER_ORANGE = np.array([4, 90, 120])
UPPER_ORANGE = np.array([14, 255, 255])
MIN_CIRCULARITY = 0.6
# MIN_AREA tuned to admit dots at 1280x720 (area ~600-900) and 1920x1080 (~1500-2200).
# The dark-digit-in-center filter (v8's vertical-"1" check) is the primary false-positive
# guard, so the area band can be wide without admitting halo/HUD noise.
MIN_AREA = 500
MAX_AREA = 50000

KI_NO_DOT_LOG_INTERVAL_SEC = 2.0    # how often to log the "no dot found" breakdown (per-filter pass counts)

# logic_agility_v3 — sequential per-letter green-gated press
AGILITY_PER_LETTER_TIMEOUT_SEC = 0.15  # max wait after press for letter to turn green; re-press once on timeout
AGILITY_FAIL_BACKOFF_SEC = 2.0          # after detecting RED letter, wait this long before re-scanning

# logic_ki_v8 — tighter detection with vertical-"1" + black-border filters,
# area-ratio (occlusion-tolerant) shape check, multi-frame stability.
# Legacy mode clicks immediately after stable dot detection; v2 tracks the ring.
KI_V8_AREA_MIN = 400                  # dot at 1920x1080 has r ≈ 20-30 → area 1200-2800; floor wider for distance
KI_V8_AREA_MAX = 8000                 # ceiling wider for close-up dots
KI_V8_AREA_RATIO_MIN = 0.55           # contour_area / minEnclosingCircle_area; ~0.75 for 3/4 occluded
KI_V8_ASPECT_TOL = 0.25               # bounding box aspect must be in [0.75, 1.25]
KI_V8_BORDER_DARK_FRAC_MIN = 0.45     # ≥this fraction of border samples must be dark
KI_V8_DIGIT_DARK_FRAC_MIN = 0.40      # ≥this fraction of a column must be dark for it to count as a digit stroke
KI_V8_DIGIT_WIDTH_MIN = 1             # "1" stroke is 1-12 columns wide (more generous for close-up dots)
KI_V8_DIGIT_WIDTH_MAX = 12
KI_V8_DARK_THRESH = 90                # pixel gray < this counts as "dark" (raised from 80 for anti-aliased "1" edges)
KI_V8_STABLE_POS_TOL_PX = 25          # detected dot center must be within this of the last frame's
KI_V8_STABLE_FRAMES_REQUIRED = 2      # require this many consecutive frames at same pos before clicking
KI_V8_POST_CLICK_COOLDOWN_SEC = 0.12  # enough to avoid the clicked dot without missing the next ring

# Click-timing mode.
#   "v1_time" — wait KI_V8_CLICK_DELAY_SEC after stable detect, then click. Simple and
#               reliable; landing-time = detection_lag + delay + roblox_lag. Tune delay
#               up if clicks land too early, down if too late.
#   "v2_ring" — radial-scan ring tracker. Each frame, scan radii outer→inner and locate
#               where the shrinking white ring currently is (outermost radius with a bright
#               circle of pixels). Fire click when ring crosses target_r. Requires having
#               seen the ring at r > target on a previous frame, to avoid firing instantly
#               on static bright background pixels.
KI_V8_MODE = "v2_ring"

# v1 settings — fixed delay after stable detection. Kept for users who explicitly
# select the legacy timing mode; the shipped default is the v2 ring tracker.
KI_V8_CLICK_DELAY_SEC = 0.29

# v2 settings — brightness-based ring tracking.
# Pivoted from Canny radial scan because Canny had a hard floor at r_min that made
# the convergence path fire on first frame (r_min ≈ target_r). New approach: sample
# 32 points at a fixed radius around the dot, count how many are "bright" (V>threshold,
# = the white shrinking ring). Click immediately when count crosses threshold —
# the ring just reached this radius. Click lands ~150ms later when ring is closer
# to the dot (= Perfect window). The single most important tunable is SAMPLE_R_FACTOR
# because it defines WHERE we wait for the ring to be when we fire.
#
# Math: at 1080p dot_r=31, ring shrinks from ~r=70 to ~r=33 over ~700ms (rate ~50 px/s).
# Perfect ≈ ring touching dot's outer border at r=34. Click lag ~150ms shrinks ring
# v2 ring tracking — OUTER→INNER scan with motion gate.
#
# Why outer→inner with r_min above the dot's outline:
#   The Ki dot has an orange interior PLUS a permanent white outline at ~1.30 × dot_r.
#   That outline is a static bright circle. Inner→outer scan hits the outline first on
#   every dot — clicks fire instantly, before the osu ring is anywhere near Perfect.
#   By starting r_min ABOVE the outline (1.45 × dot_r) and scanning outer→inner, the
#   first bright thing we find is the OSU ring, not the outline.
#
# Calibration from ki_ring_sequences/seq_011 (dot_r=20):
#   Ring inner_r decays 47 → 26 over 930ms, then sits at 26 for ~14 frames before the
#   dot vanishes. r=26 = 1.30 × dot_r = where the outline lives — ring merges into it
#   at the "Perfect" moment. Shrink rate ~23.6 px/s at dot_r=20, scales to ~37 px/s at
#   dot_r=31. Roblox click lag ~150ms → ring shrinks ~5.5 px during lag. So firing at
#   r = 46 (1.50 × 31) makes the click LAND at r=40.5 ≈ outline ≈ Perfect.
#
# Motion gate:
#   The outline is static, so its detected radius is constant across frames. The osu
#   ring is dynamic — its radius DECREASES frame to frame. We require either:
#     (a) Ring was previously detected at r > target_r, OR
#     (b) Detected r has decreased monotonically across 3+ frames
#   before firing on r ≤ target_r. This kills false fires on any static bright structure
#   that happens to live inside the scan range.
SENZU_ENABLED = True                  # Autofeed: eat a senzu (H + slot key) when the HP bar turns red.
SENZU_SLOT = 1                        # Item slot the senzu is assigned to (1-4).
SENZU_SLOT_MATCH_MIN = 0.74
SENZU_DELAY_SEC = 0.0                 # Extra wait between detecting red HP and eating.
SENZU_RECOVERY_TIMEOUT_SEC = 7.0      # Wait for green HP before one confirmed retry.
SENZU_PREFERENCE_MODE = "full_only"  # full_only/full_then_half/half_only/half_then_full.
SENZU_ZERO_GRAVITY_ON_EMPTY = True    # GC only: return to 0G when no allowed bean type remains.
SENZU_REMAINING = None                # Last inventory count read from the Items list.
SENZU_ACTIVE_TYPE = None              # full/half currently assigned and counted.
SENZU_ROW_CACHE = {"mode": None, "bean_type": None, "row_y": None}
SENZU_STATUS = "idle"                 # UI-facing: idle/ready/eating/refilling/not_consumed/empty/error.
SENZU_DISABLED_FOR_RUN = False        # Empty full-bean stock suppresses retries until next Start.
# Fixed patch near the left edge, fully inside the opaque HP fill. The right side
# becomes empty background as HP drops and must not influence the colour state.
SENZU_HP_FILL_BOX = {"left": 96, "top": 108, "width": 72, "height": 16}

SENZU_PREFERENCE_PRIORITIES = {
    "full_only": ("full",),
    "full_then_half": ("full", "half"),
    "half_only": ("half",),
    "half_then_full": ("half", "full"),
}


def _normalize_senzu_preference(value, *, strict=False):
    mode = str(value or "full_only")
    if mode in SENZU_PREFERENCE_PRIORITIES:
        return mode
    if strict:
        raise ValueError(
            "Senzu preference must be full_only, full_then_half, "
            "half_only, or half_then_full"
        )
    return "full_only"


def _senzu_type_priority():
    return SENZU_PREFERENCE_PRIORITIES[
        _normalize_senzu_preference(SENZU_PREFERENCE_MODE)
    ]


def _senzu_type_label(bean_type):
    if bean_type == "full":
        return "Full Senzu Bean"
    if bean_type == "half":
        return "Half Senzu Bean"
    return "Senzu Bean"


def _invalidate_senzu_row_cache():
    SENZU_ROW_CACHE.update(mode=None, bean_type=None, row_y=None)


def _remember_senzu_row(bean_type, row_y):
    SENZU_ROW_CACHE.update(
        mode=SENZU_PREFERENCE_MODE,
        bean_type=bean_type,
        row_y=int(row_y),
    )
KI_LATENCY_COMP_MS = 0                # Latency compensation for the v2 click: predict where the ring will be
                                       # this many ms ahead (capture->input->server lead) using its measured
                                       # shrink velocity, and fire that much earlier. 0 = off (tuned local
                                       # machine); raise for weak PCs / high ping where clicks land late.
                                       # Clamped 0..250.
KI_V8_V2_TARGET_R_FACTOR = 1.40       # 1.40 is the safe tuning floor until static-outline rejection is redesigned.
KI_V8_V2_R_MIN_FACTOR = 1.40          # stays above the dot's static outline (~1.30)
KI_V8_V2_R_MAX_FACTOR = 2.80          # ring starts at ~2.4 × dot_r in HTC
KI_V8_V2_BRIGHTNESS_THRESHOLD = 220   # CRITICAL: HTC floor max-channel ~215; threshold must be above. Ring brightness 225-254
KI_V8_V2_BRIGHT_COUNT_THRESHOLD = 6   # peak-finding minimum. Real HTC ring peaks at 10-32 hits
KI_V8_V2_SAMPLE_COUNT = 32
KI_V8_V2_SCAN_STEP_PX = 1
KI_V8_V2_BAND_OFFSET = 1              # at each angle, count hit if ANY pixel in [r-band, r+band] qualifies (catches thin rings)
KI_V8_V2_MOTION_STREAK_MIN = 3        # frames of monotone-decreasing r to count as confirmed motion
KI_V8_V2_TIMEOUT_SEC = 1.0
KI_V8_V2_DEBUG_IMAGE = False          # Enable only while diagnosing Ki detection.
KI_V8_V2_CONTRAST_DELTA = 8           # Lab-L ring/background gap; shadow-only pending coherent track fusion
KI_V8_V2_CONTRAST_CLICK = True        # contrast owns a separate shrinking-motion track; no state is shared with bright
KI_V8_V2_DEBUG_BUFFER = 10            # keep last N debug images (rotating)

# v2 outcome grading — after click, sample the dot region for the yellow "Perfect!" text
# overlay that the game draws on a successful click. Lets the user see Perfect-rate
# without manually counting. Disable if performance matters.
KI_V8_V2_GRADE_OUTCOMES = False        # async live grading was inconsistent; keep disabled until reworked
KI_V8_V2_GRADE_DELAY_SEC = 0.25       # wait this long after click for feedback text to render
KI_V8_V2_GRADE_YELLOW_MIN_PX = 250    # min yellow pixels for "Perfect". The dot's permanent "1" digit
                                       # registers ~144 yellow_px baseline — must threshold ABOVE that.
                                       # Real Perfect text overlays register 280+ (often 5000+ during animation).
_ki_v8_state = {"last_dot": None, "consecutive_seen": 0, "last_click_at": 0.0}

# Minigame toggles (UI can enable/disable per type)
ENABLE_HEALTH_MINIGAME = True
ENABLE_PHYSICAL_MINIGAME = True
ENABLE_KI_MINIGAME = True


# UI / thread state (set by desktop/web control panel)
UI_STOP_REQUESTED = False
# Surfaced to the UI so it can toast when a run ends on an error. Count increments each
# time the macro thread dies on an exception; last_error holds the message for the toast.
MACRO_ERROR_COUNT = 0
MACRO_LAST_ERROR = None
LAST_RUN_RESULT = None
_CURRENT_RUN_OUTCOME = None
_CURRENT_RUN_REASON = None
_CURRENT_RUN_CATEGORY = None
_CURRENT_RUN_RETRYABLE = False
_USER_STOP_LATCHED = False
_AFTER_ACTIONS_BLOCKED = False
_run_result_lock = threading.RLock()
MANUAL_NEXT_REQUESTED = False           # set by global L hotkey, consumed by main loop
PAUSE_TOGGLE_REQUESTED = False          # set by global U hotkey, consumed by main loop
CONTROLLER_PAUSED = False               # authoritative UI state for the pause hotkey.
_manual_next_hotkey_handle = None       # keyboard.add_hotkey handle for re-registration on rebind
_pause_hotkey_handle = None
CURRENT_TRAINING_STATE = None
TRAINING_MENU_VISIBLE = False
MACRO_THREAD = None
MACRO_STARTED_AT = 0.0

# A separate capture thread owns progression and senzu checks so blocking
# minigame handlers cannot starve either one. Events are reset for every run.
PROGRESSION_COMPLETE_REQUESTED = threading.Event()
PROGRESSION_TRACKED_STATE = None
PROGRESSION_COMPLETE = None
PROGRESSION_STATE_STARTED_AT = 0.0
_background_monitor_stop = None
_background_monitor_thread = None

# While Auto-Senzu owns the game menus, the main controller must not finish a
# stale minigame input or category switch as soon as the shared input lock opens.
SENZU_CONTROLLER_ACTIVE = threading.Event()
SENZU_CONTROLLER_RESUME_REQUIRED = threading.Event()

# Input from the minigame loop and the senzu monitor must never interleave.
# RLock lets the atomic H -> slot -> H transaction call shared input helpers.
_input_lock = threading.RLock()
# Linearizes explicit Stop with the next key/click packet. Hold this only for
# one short input action so Stop remains responsive during captures and waits.
_stop_input_gate = threading.RLock()

# Per-run telemetry. Reset to zero each time the macro starts.
TELEMETRY = {
    "wasd_sequences":   0,    # how many sequences the macro started pressing
    "wasd_greens":      0,    # letters that confirmed green
    "wasd_unconfirmed": 0,    # letters that never confirmed (input-eat or detection-miss)
    "wasd_reds":        0,    # sequence failures detected via red
    "ki_dots_found":    0,
    "ki_clicks":        0,
    "ki_timeouts":      0,    # ring detection timeout (no convergence)
    "ki_graded":        0,    # how many v2 clicks were post-graded for outcome
    "ki_perfect":       0,    # of graded clicks, how many showed the "Perfect!" yellow text
    "health_hits":      0,
    "senzu_eaten":      0,
    "senzu_refills":    0,
    "switches":         0,
    "recovery_attempts": 0,
}

def _telemetry_reset():
    for k in list(TELEMETRY.keys()):
        TELEMETRY[k] = 0


def _begin_run_result():
    global _CURRENT_RUN_OUTCOME, _CURRENT_RUN_REASON, _CURRENT_RUN_CATEGORY
    global _CURRENT_RUN_RETRYABLE
    global _USER_STOP_LATCHED, _AFTER_ACTIONS_BLOCKED
    with _run_result_lock:
        _CURRENT_RUN_OUTCOME = None
        _CURRENT_RUN_REASON = None
        _CURRENT_RUN_CATEGORY = None
        _CURRENT_RUN_RETRYABLE = False
        _USER_STOP_LATCHED = False
        _AFTER_ACTIONS_BLOCKED = False


def _record_run_outcome(outcome, reason, category=None, retryable=False):
    """Record the strongest operational result seen during the active run."""
    global _CURRENT_RUN_OUTCOME, _CURRENT_RUN_REASON, _CURRENT_RUN_CATEGORY
    global _CURRENT_RUN_RETRYABLE
    global MACRO_LAST_ERROR
    priorities = {"completed": 1, "incomplete": 1, "stopped": 2, "error": 3}
    outcome = str(outcome or "error")
    reason = str(reason or "No reason reported")
    with _run_result_lock:
        # A late worker error must not turn an explicit user Stop into an
        # after-run failure action such as closing the game or shutting down.
        if _USER_STOP_LATCHED and outcome == "error":
            MACRO_LAST_ERROR = reason
            return
        if _USER_STOP_LATCHED and outcome == "stopped":
            _CURRENT_RUN_OUTCOME = "stopped"
            _CURRENT_RUN_REASON = reason
            _CURRENT_RUN_CATEGORY = category or CURRENT_TRAINING_STATE
            _CURRENT_RUN_RETRYABLE = False
            return
        current_priority = priorities.get(_CURRENT_RUN_OUTCOME, 0)
        new_priority = priorities.get(outcome, 3)
        if _CURRENT_RUN_OUTCOME is None or new_priority > current_priority:
            _CURRENT_RUN_OUTCOME = outcome
            _CURRENT_RUN_REASON = reason
            _CURRENT_RUN_CATEGORY = category or CURRENT_TRAINING_STATE
            _CURRENT_RUN_RETRYABLE = bool(retryable and outcome == "error")
        elif new_priority == current_priority:
            if _CURRENT_RUN_CATEGORY is None:
                _CURRENT_RUN_CATEGORY = category or CURRENT_TRAINING_STATE
            if outcome == "error":
                # One unexpected/non-operational failure makes the whole
                # attempt unsafe to retry, even if a retryable error came first.
                _CURRENT_RUN_RETRYABLE = bool(
                    _CURRENT_RUN_RETRYABLE and retryable
                )
        if outcome == "error":
            MACRO_LAST_ERROR = reason


def _controller_decisions_suspended():
    """True for non-Senzu threads until the main loop acknowledges recovery."""
    is_senzu_thread = threading.current_thread() is _background_monitor_thread
    return bool(
        not is_senzu_thread
        and (SENZU_CONTROLLER_ACTIVE.is_set()
             or SENZU_CONTROLLER_RESUME_REQUIRED.is_set())
    )

import collections as _collections, io as _io
_ui_log_ring = _collections.deque(maxlen=400)

class _TeeBuffer(_io.TextIOBase):
    """Tee stdout to (a) the original stream, (b) the UI log ring, and (c) a
    timestamped per-sidecar log file under bot/python/json/logs/session_*.log.
    The session log captures the full console output of each run so the user can
    review what happened after the fact without needing to keep the dev terminal open."""
    def __init__(self, original, ring, file_handle=None):
        self._orig = original
        self._ring = ring
        self._file = file_handle
    def write(self, s):
        if s and s.strip():
            self._ring.append({"t": time.time(), "msg": s.rstrip("\n")})
            if self._file is not None:
                try:
                    ts = time.strftime("%H:%M:%S")
                    self._file.write(f"{ts} {s if s.endswith(chr(10)) else s + chr(10)}")
                    self._file.flush()
                except Exception:
                    pass
        # The frozen windowed sidecar (PyInstaller --noconsole) starts with
        # sys.stdout = None; the ring and session file are the real sinks then.
        if self._orig is None:
            return len(s)
        return self._orig.write(s)
    def flush(self):
        if self._file is not None:
            try: self._file.flush()
            except Exception: pass
        if self._orig is None:
            return None
        return self._orig.flush()
    @property
    def encoding(self):
        return getattr(self._orig, "encoding", "utf-8")


def _open_session_log_file():
    """Open a new timestamped log file for this sidecar instance. Returns the file
    handle or None on failure. Old session logs are kept (rotation = newest 25)."""
    try:
        logs_dir = os.path.join(JSON_DIR, "logs")
        os.makedirs(logs_dir, exist_ok=True)
        # Make room for this session so the total remains capped at 25.
        try:
            existing = sorted(
                (os.path.join(logs_dir, f) for f in os.listdir(logs_dir) if f.startswith("session_")),
                key=os.path.getmtime,
            )
            for old in existing[:-24]:
                try: os.remove(old)
                except Exception: pass
        except Exception:
            pass
        stamp = time.strftime("%Y-%m-%d_%H-%M-%S")
        path = os.path.join(logs_dir, f"session_{stamp}_pid{os.getpid()}.log")
        return open(path, "w", encoding="utf-8", buffering=1)
    except Exception:
        return None

# Snapshot of hard-coded defaults (used by Settings -> Reset to defaults)
DEFAULT_USER_SETTINGS = {
    "start_delay_sec": float(START_DELAY),
    "gc_gravity_target_g": int(GC_GRAVITY_TARGET_G),
    "prevent_sleep_while_running": bool(PREVENT_SLEEP_WHILE_RUNNING),
    "restore_fullscreen_on_start": bool(RESTORE_FULLSCREEN_ON_START),
    "display_confirm_changes": bool(DISPLAY_CONFIRM_CHANGES),
    "shutdown_pc_when_finished": bool(SHUTDOWN_PC_WHEN_FINISHED),
    "after_run_game_action": str(AFTER_RUN_GAME_ACTION),
    "after_run_on_failure": bool(AFTER_RUN_ON_FAILURE),
    "auto_retry_on_failure": bool(AUTO_RETRY_ON_FAILURE),
    "auto_retry_max_attempts": int(AUTO_RETRY_MAX_ATTEMPTS),
    "auto_retry_recovery_mode": str(AUTO_RETRY_RECOVERY_MODE),
    "auto_retry_walk_out": bool(AUTO_RETRY_WALK_OUT),
    "auto_retry_walk_seconds": float(AUTO_RETRY_WALK_SECONDS),
    "diagnostic_mode": bool(DIAGNOSTIC_MODE),
    "after_switch_wait_sec": float(NEW_GAME_WAIT),
    "no_yellow_timeout_sec": float(NO_YELLOW_TIMEOUT_SEC),
    "no_yellow_fallback_enabled": bool(NO_YELLOW_FALLBACK_ENABLED),
    "manual_next_key": str(MANUAL_NEXT_KEY),
    "start_stop_hotkey": str(START_STOP_HOTKEY),
    "pause_hotkey": str(PAUSE_HOTKEY),
    "health_hit_cooldown_sec": float(HEALTH_HIT_COOLDOWN_SEC),
    "health_mode": str(HEALTH_MODE),
    "wasd_key_press_delay_sec": float(KEY_PRESS_DELAY),
    "wasd_stabilize_delay_sec": float(STABILIZE_DELAY),
    "wasd_post_burst_delay_sec": float(POST_COMBO_DELAY),
    "agility_mode": str(AGILITY_MODE),
    "agility_green_observe_sec": float(AGILITY_GREEN_OBSERVE_SEC),
    "agility_inter_string_wait_sec": float(AGILITY_INTER_STRING_WAIT_SEC),
    "agility_after_green_settle_sec": float(AGILITY_AFTER_GREEN_SETTLE_SEC),
    "training_order": list(DEFAULT_TRAINING_ORDER),
    "ki_v8_mode": str(KI_V8_MODE),
    "ki_v8_click_delay_sec": float(KI_V8_CLICK_DELAY_SEC),
    "ki_v8_v2_target_r_factor": float(KI_V8_V2_TARGET_R_FACTOR),
    "ki_v8_v2_brightness_threshold": int(KI_V8_V2_BRIGHTNESS_THRESHOLD),
    "ki_v8_v2_bright_count_threshold": int(KI_V8_V2_BRIGHT_COUNT_THRESHOLD),
    "ki_latency_comp_ms": int(KI_LATENCY_COMP_MS),
    "senzu_enabled": bool(SENZU_ENABLED),
    "senzu_slot": int(SENZU_SLOT),
    "senzu_delay_sec": float(SENZU_DELAY_SEC),
    "senzu_recovery_timeout_sec": float(SENZU_RECOVERY_TIMEOUT_SEC),
    "senzu_preference_mode": str(SENZU_PREFERENCE_MODE),
    "senzu_zero_gravity_on_empty": bool(SENZU_ZERO_GRAVITY_ON_EMPTY),
}


def _sanitize_training_order(value):
    """
    Keep only known stat names, preserve order, drop duplicates.
    Accept [] as valid (UI can intentionally clear all and re-add).
    """
    if not isinstance(value, list):
        return list(DEFAULT_TRAINING_ORDER)
    out = []
    for item in value:
        name = str(item)
        if name in BUTTONS and name not in out:
            out.append(name)
    return out


def reset_user_settings_to_defaults():
    """
    Resets configurable values back to the hard-coded defaults in this script.
    The UI command persists the reset immediately after calling this function.
    """
    global START_DELAY, GC_GRAVITY_TARGET_G, PREVENT_SLEEP_WHILE_RUNNING
    global RESTORE_FULLSCREEN_ON_START, DISPLAY_CONFIRM_CHANGES
    global SHUTDOWN_PC_WHEN_FINISHED, AFTER_RUN_GAME_ACTION, AFTER_RUN_ON_FAILURE
    global AUTO_RETRY_ON_FAILURE, AUTO_RETRY_MAX_ATTEMPTS
    global AUTO_RETRY_RECOVERY_MODE, AUTO_RETRY_WALK_OUT, AUTO_RETRY_WALK_SECONDS
    global DIAGNOSTIC_MODE
    global NEW_GAME_WAIT, NO_YELLOW_TIMEOUT_SEC, NO_YELLOW_FALLBACK_ENABLED
    global MANUAL_NEXT_KEY, START_STOP_HOTKEY, PAUSE_HOTKEY
    global HEALTH_HIT_COOLDOWN_SEC, HEALTH_MODE
    global KEY_PRESS_DELAY, STABILIZE_DELAY, POST_COMBO_DELAY
    global TRAINING_ORDER_CUSTOM, AGILITY_MODE
    global AGILITY_GREEN_OBSERVE_SEC, AGILITY_INTER_STRING_WAIT_SEC, AGILITY_AFTER_GREEN_SETTLE_SEC

    START_DELAY = float(DEFAULT_USER_SETTINGS["start_delay_sec"])
    GC_GRAVITY_TARGET_G = int(DEFAULT_USER_SETTINGS["gc_gravity_target_g"])
    PREVENT_SLEEP_WHILE_RUNNING = bool(
        DEFAULT_USER_SETTINGS["prevent_sleep_while_running"]
    )
    RESTORE_FULLSCREEN_ON_START = bool(
        DEFAULT_USER_SETTINGS["restore_fullscreen_on_start"]
    )
    DISPLAY_CONFIRM_CHANGES = bool(
        DEFAULT_USER_SETTINGS["display_confirm_changes"]
    )
    SHUTDOWN_PC_WHEN_FINISHED = bool(
        DEFAULT_USER_SETTINGS["shutdown_pc_when_finished"]
    )
    AFTER_RUN_GAME_ACTION = str(DEFAULT_USER_SETTINGS["after_run_game_action"])
    AFTER_RUN_ON_FAILURE = bool(DEFAULT_USER_SETTINGS["after_run_on_failure"])
    AUTO_RETRY_ON_FAILURE = bool(DEFAULT_USER_SETTINGS["auto_retry_on_failure"])
    AUTO_RETRY_MAX_ATTEMPTS = int(DEFAULT_USER_SETTINGS["auto_retry_max_attempts"])
    AUTO_RETRY_RECOVERY_MODE = str(DEFAULT_USER_SETTINGS["auto_retry_recovery_mode"])
    AUTO_RETRY_WALK_OUT = bool(DEFAULT_USER_SETTINGS["auto_retry_walk_out"])
    AUTO_RETRY_WALK_SECONDS = float(DEFAULT_USER_SETTINGS["auto_retry_walk_seconds"])
    DIAGNOSTIC_MODE = bool(DEFAULT_USER_SETTINGS["diagnostic_mode"])
    NEW_GAME_WAIT = float(DEFAULT_USER_SETTINGS["after_switch_wait_sec"])
    NO_YELLOW_TIMEOUT_SEC = float(DEFAULT_USER_SETTINGS["no_yellow_timeout_sec"])
    NO_YELLOW_FALLBACK_ENABLED = bool(
        DEFAULT_USER_SETTINGS.get("no_yellow_fallback_enabled", False)
    )
    MANUAL_NEXT_KEY = _normalize_hotkey_name(DEFAULT_USER_SETTINGS["manual_next_key"]) or MANUAL_NEXT_KEY
    START_STOP_HOTKEY = _normalize_hotkey_name(DEFAULT_USER_SETTINGS.get("start_stop_hotkey", "f6")) or "f6"
    PAUSE_HOTKEY = _normalize_hotkey_name(DEFAULT_USER_SETTINGS.get("pause_hotkey", "u")) or "u"
    HEALTH_HIT_COOLDOWN_SEC = float(DEFAULT_USER_SETTINGS["health_hit_cooldown_sec"])
    HEALTH_MODE = str(DEFAULT_USER_SETTINGS.get("health_mode", "v2_track"))
    KEY_PRESS_DELAY = float(DEFAULT_USER_SETTINGS["wasd_key_press_delay_sec"])
    STABILIZE_DELAY = float(DEFAULT_USER_SETTINGS["wasd_stabilize_delay_sec"])
    POST_COMBO_DELAY = float(DEFAULT_USER_SETTINGS["wasd_post_burst_delay_sec"])
    TRAINING_ORDER_CUSTOM = _sanitize_training_order(
        DEFAULT_USER_SETTINGS.get("training_order", DEFAULT_TRAINING_ORDER)
    )
    AGILITY_MODE = str(DEFAULT_USER_SETTINGS.get("agility_mode", "v2"))
    AGILITY_GREEN_OBSERVE_SEC = float(DEFAULT_USER_SETTINGS.get("agility_green_observe_sec", 0.7))
    AGILITY_INTER_STRING_WAIT_SEC = float(DEFAULT_USER_SETTINGS.get("agility_inter_string_wait_sec", 1.0))
    AGILITY_AFTER_GREEN_SETTLE_SEC = float(DEFAULT_USER_SETTINGS.get("agility_after_green_settle_sec", 0.1))
    global KI_V8_CLICK_DELAY_SEC, KI_V8_MODE, KI_V8_V2_TARGET_R_FACTOR
    global KI_V8_V2_BRIGHTNESS_THRESHOLD, KI_V8_V2_BRIGHT_COUNT_THRESHOLD
    global KI_LATENCY_COMP_MS
    global SENZU_ENABLED, SENZU_SLOT, SENZU_DELAY_SEC, SENZU_RECOVERY_TIMEOUT_SEC
    global SENZU_PREFERENCE_MODE
    global SENZU_ZERO_GRAVITY_ON_EMPTY
    KI_V8_CLICK_DELAY_SEC = float(DEFAULT_USER_SETTINGS.get("ki_v8_click_delay_sec", 0.29))
    KI_V8_MODE = str(DEFAULT_USER_SETTINGS.get("ki_v8_mode", "v2_ring"))
    KI_V8_V2_TARGET_R_FACTOR = min(
        3.0, max(KI_V8_V2_R_MIN_FACTOR, float(DEFAULT_USER_SETTINGS.get("ki_v8_v2_target_r_factor", 1.40)))
    )
    KI_V8_V2_BRIGHTNESS_THRESHOLD = int(DEFAULT_USER_SETTINGS.get("ki_v8_v2_brightness_threshold", 220))
    KI_V8_V2_BRIGHT_COUNT_THRESHOLD = int(DEFAULT_USER_SETTINGS.get("ki_v8_v2_bright_count_threshold", 6))
    KI_LATENCY_COMP_MS = min(250, max(0, int(DEFAULT_USER_SETTINGS.get("ki_latency_comp_ms", 0))))
    SENZU_ENABLED = bool(DEFAULT_USER_SETTINGS.get("senzu_enabled", True))
    SENZU_SLOT = min(4, max(1, int(DEFAULT_USER_SETTINGS.get("senzu_slot", 1))))
    SENZU_DELAY_SEC = max(0.0, float(DEFAULT_USER_SETTINGS.get("senzu_delay_sec", 0.0)))
    SENZU_RECOVERY_TIMEOUT_SEC = min(
        30.0,
        max(1.0, float(DEFAULT_USER_SETTINGS.get("senzu_recovery_timeout_sec", 7.0))),
    )
    SENZU_PREFERENCE_MODE = _normalize_senzu_preference(
        DEFAULT_USER_SETTINGS.get("senzu_preference_mode", "full_only")
    )
    SENZU_ZERO_GRAVITY_ON_EMPTY = bool(
        DEFAULT_USER_SETTINGS.get("senzu_zero_gravity_on_empty", True)
    )

# =================================================

pydirectinput.FAILSAFE = False
pydirectinput.PAUSE = 0.0


# ---------------- Game window detection ----------------

def _window_process_executable(hwnd):
    """Return the executable basename that owns hwnd, or None."""
    if os.name != "nt" or not hwnd:
        return None
    import ctypes

    process_id = ctypes.c_ulong(0)
    ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(process_id))
    if not process_id.value:
        return None
    process_handle = ctypes.windll.kernel32.OpenProcess(
        0x1000, False, process_id.value  # PROCESS_QUERY_LIMITED_INFORMATION
    )
    if not process_handle:
        return None
    try:
        path_buffer = ctypes.create_unicode_buffer(32768)
        path_size = ctypes.c_ulong(len(path_buffer))
        if not ctypes.windll.kernel32.QueryFullProcessImageNameW(
            process_handle, 0, path_buffer, ctypes.byref(path_size)
        ):
            return None
        return os.path.basename(path_buffer.value).lower()
    finally:
        ctypes.windll.kernel32.CloseHandle(process_handle)


def _is_supported_roblox_window(hwnd):
    """Reject title-only matches such as browsers showing a Roblox page."""
    executable = _window_process_executable(hwnd)
    return executable in SUPPORTED_ROBLOX_EXECUTABLES

def find_minimized_roblox_hwnd():
    """Return the hwnd of a minimized Roblox game window, or None.

    Minimized windows report a 0x0 client rect, so find_dbog_window() rejects
    them by size. This lets the UI say "Roblox is minimized" instead of "not
    found", and lets Start restore it rather than refusing.
    """
    if os.name != "nt":
        return None
    try:
        import ctypes
        user32 = ctypes.windll.user32
        found = []
        WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

        @WNDENUMPROC
        def _cb(hwnd, lparam):
            try:
                if not user32.IsIconic(hwnd):
                    return True
                length = user32.GetWindowTextLengthW(hwnd)
                if length <= 0:
                    return True
                buf = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, buf, length + 1)
                title = (buf.value or "").lower()
                if not any(target.lower() in title for target in DBOG_WINDOW_TITLES):
                    return True
                if not _is_supported_roblox_window(hwnd):
                    return True
                found.append(hwnd)
                return False
            except Exception:
                return True

        user32.EnumWindows(_cb, 0)
        return found[0] if found else None
    except Exception as e:
        print(f"[window] find_minimized_roblox_hwnd error: {e}")
        return None


def restore_game_window(wait=1.5):
    """Un-minimize Roblox and wait for a real client rect. Returns True on success."""
    hwnd = find_minimized_roblox_hwnd()
    if not hwnd:
        return update_game_window()
    import ctypes
    ctypes.windll.user32.ShowWindow(hwnd, 9)  # SW_RESTORE
    print("[window] Roblox was minimized — restoring it.")
    deadline = time.monotonic() + wait
    while time.monotonic() < deadline:
        if update_game_window():
            return True
        time.sleep(0.1)
    return False


def game_window_is_fullscreen():
    """True when the Roblox client area covers its whole monitor.

    Returns None when the monitor can't be identified, so callers can tell
    "definitely windowed" apart from "don't know" and leave the window alone.
    """
    if GAME_HWND is None:
        return None
    monitor = _monitor_info_for_window(GAME_HWND)
    if monitor is None:
        return None
    # Roblox's fullscreen client area matches the monitor exactly; a maximized
    # window loses the title bar and borders, so it always falls short.
    return GAME_WIDTH >= monitor["width"] and GAME_HEIGHT >= monitor["height"]


def ensure_game_fullscreen(wait=2.0):
    """Put a windowed Roblox back into fullscreen with F11. Returns True if fullscreen.

    Restoring from the taskbar brings Roblox back windowed even when it was
    fullscreen before, which shifts every scan region. F11 is a toggle, so this
    only fires when the window is measurably smaller than its monitor — pressing
    it blind would kick an already-fullscreen client back into a window.
    """
    if not update_game_window():
        return False
    state = game_window_is_fullscreen()
    if state is not False:  # already fullscreen, or monitor unknown — don't guess
        return bool(state)
    if not focus_game_window():
        print("[window] Could not focus Roblox to set fullscreen.")
        return False
    print("[window] Roblox is windowed — pressing F11 for fullscreen.")
    pydirectinput.keyDown("f11")
    time.sleep(0.030)
    pydirectinput.keyUp("f11")
    deadline = time.monotonic() + wait
    while time.monotonic() < deadline:
        time.sleep(0.15)
        update_game_window()
        if game_window_is_fullscreen():
            print(f"[window] Roblox is fullscreen: {GAME_WIDTH}x{GAME_HEIGHT}")
            return True
    print("[window] Roblox did not reach fullscreen; continuing windowed.")
    return False


def find_dbog_window():
    """Locate the DBOG game window. Returns (hwnd, (client_x, client_y, w, h)) or (None, None).

    Uses GetClientRect + ClientToScreen so the coordinates reflect the game's actual
    viewport (no titlebar/borders), which works the same for windowed, borderless, and
    fullscreen modes.

    Multiple Roblox windows can coexist (launcher, login overlay, the game itself).
    EnumWindows order is z-order, so picking the first match is unreliable — it varies
    depending on which window is on top at the moment of the call. Instead, collect ALL
    matches and return the one with the LARGEST client area, which in practice is the
    game viewport (launcher/login dialogs are smaller).
    """
    if os.name != "nt":
        return None, None
    try:
        import ctypes
        user32 = ctypes.windll.user32

        class _RECT(ctypes.Structure):
            _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                        ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

        class _POINT(ctypes.Structure):
            _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

        matches = []  # list of (hwnd, (cx, cy, w, h))
        WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

        @WNDENUMPROC
        def _cb(hwnd, lparam):
            try:
                if not user32.IsWindowVisible(hwnd):
                    return True
                length = user32.GetWindowTextLengthW(hwnd)
                if length <= 0:
                    return True
                buf = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, buf, length + 1)
                title = (buf.value or "").lower()
                title_matches = any(target.lower() in title for target in DBOG_WINDOW_TITLES)
                if not title_matches:
                    return True
                if not _is_supported_roblox_window(hwnd):
                    return True
                client = _RECT()
                if not user32.GetClientRect(hwnd, ctypes.byref(client)):
                    return True
                w = client.right - client.left
                h = client.bottom - client.top
                # Skip tiny windows (launcher/loading dialogs are typically <800x600,
                # the actual game is ≥800x600 in any reasonable mode).
                if w < 800 or h < 600:
                    return True
                pt = _POINT(0, 0)
                if not user32.ClientToScreen(hwnd, ctypes.byref(pt)):
                    return True
                matches.append((hwnd, (pt.x, pt.y, w, h)))
                return True
            except Exception:
                return True

        user32.EnumWindows(_cb, 0)
        if not matches:
            return None, None
        # Largest client area wins.
        matches.sort(key=lambda m: m[1][2] * m[1][3], reverse=True)
        hwnd, rect = matches[0]
        return hwnd, rect
    except Exception as e:
        print(f"[window] find_dbog_window error: {e}")
        return None, None


def _monitor_info_for_window(hwnd):
    """Return normalized monitor metadata for hwnd, or None.

    MonitorFromWindow chooses the display containing the largest part of the
    window, which is the behavior users expect when dragging Roblox between
    displays. A device name is required so display-mode changes never silently
    fall back to the primary monitor.
    """
    if os.name != "nt" or not hwnd:
        return None
    try:
        monitor_handle = win32api.MonitorFromWindow(
            int(hwnd), win32con.MONITOR_DEFAULTTONEAREST
        )
        raw = win32api.GetMonitorInfo(monitor_handle)
        left, top, right, bottom = [int(value) for value in raw["Monitor"]]
        device = str(raw.get("Device") or "").strip()
        if not device or right <= left or bottom <= top:
            return None
        return {
            "device": device,
            "left": left,
            "top": top,
            "right": right,
            "bottom": bottom,
            "width": right - left,
            "height": bottom - top,
            "primary": bool(int(raw.get("Flags", 0)) & 1),
        }
    except Exception as e:
        print(f"[window] monitor lookup failed: {e}")
        return None


def _current_game_monitor_info(refresh_window=False):
    """Return the monitor associated with Roblox, never an assumed primary."""
    global GAME_MONITOR_INFO
    if refresh_window or GAME_HWND is None:
        update_game_window()
    if GAME_HWND is not None:
        info = _monitor_info_for_window(GAME_HWND)
        if info is not None:
            GAME_MONITOR_INFO = info
    return dict(GAME_MONITOR_INFO) if GAME_MONITOR_INFO is not None else None


def apply_game_offset():
    """Deprecated compatibility no-op.

    Coordinates stay client-local/reference-space and are transformed at use time.
    Mutating the globals here caused double offsets after moving Roblox.
    """
    return None


_last_logged_window_state = None  # (hwnd, rect) of the last state we printed

def update_game_window():
    """Refresh GAME_* globals from the live Roblox window position. Returns True on success.

    Only logs when the detected state changes (UI polls this every 800ms).
    """
    global GAME_HWND, GAME_OFFSET_X, GAME_OFFSET_Y, GAME_WIDTH, GAME_HEIGHT
    global GAME_MONITOR_INFO, GAME_WINDOW_MINIMIZED
    global _last_logged_window_state
    global _last_game_window_refresh_at
    _last_game_window_refresh_at = time.monotonic()
    hwnd, rect = find_dbog_window()
    if not hwnd or not rect:
        GAME_HWND = None
        GAME_WINDOW_MINIMIZED = find_minimized_roblox_hwnd() is not None
        new_state = (None, None)
        if new_state != _last_logged_window_state:
            print("[window] Roblox window minimized." if GAME_WINDOW_MINIMIZED
                  else "[window] Roblox window not found.")
            _last_logged_window_state = new_state
        return False
    GAME_HWND = hwnd
    GAME_WINDOW_MINIMIZED = False
    GAME_OFFSET_X, GAME_OFFSET_Y, GAME_WIDTH, GAME_HEIGHT = rect
    monitor_info = _monitor_info_for_window(hwnd)
    if monitor_info is not None:
        GAME_MONITOR_INFO = monitor_info
    new_state = (hwnd, rect)
    if new_state != _last_logged_window_state:
        print(f"[window] Roblox client rect: ({GAME_OFFSET_X},{GAME_OFFSET_Y}) {GAME_WIDTH}x{GAME_HEIGHT}")
        _last_logged_window_state = new_state
    return True


def _refresh_game_window_if_stale(max_age=0.25):
    """Rate-limit live client-rect refreshes used by high-frequency captures."""
    if time.monotonic() - _last_game_window_refresh_at <= max_age:
        return GAME_HWND is not None
    with _game_window_refresh_lock:
        if time.monotonic() - _last_game_window_refresh_at > max_age:
            return update_game_window()
    return GAME_HWND is not None


def _confirmed_game_capture_rect():
    """Return the live Roblox client rect or fail before any capture/input."""
    if not update_game_window() or GAME_HWND is None or GAME_WIDTH <= 0 or GAME_HEIGHT <= 0:
        raise RuntimeError(
            "Roblox client window was not found. Open Roblox before starting XynMacro."
        )
    return {
        "left": int(GAME_OFFSET_X),
        "top": int(GAME_OFFSET_Y),
        "width": int(GAME_WIDTH),
        "height": int(GAME_HEIGHT),
    }


def focus_game_window():
    """Bring the Roblox window to the foreground so injected keys reach it.

    Starting the macro from the app leaves the app focused; without this, the
    first Tab lands in the app and the menu handshake fails. Windows blocks
    SetForegroundWindow from a background process unless it just sent input,
    so tap-and-release Alt first — the standard unlock.

    Returns True when the game window is foreground afterwards.
    """
    import ctypes
    user32 = ctypes.windll.user32
    if GAME_HWND is None:
        update_game_window()
    if GAME_HWND is None:
        return False
    if user32.GetForegroundWindow() == GAME_HWND:
        return True
    with _stop_input_gate:
        if _USER_STOP_LATCHED:
            return False
        win32api.keybd_event(win32con.VK_MENU, 0, 0, 0)
        try:
            user32.SetForegroundWindow(GAME_HWND)
        finally:
            win32api.keybd_event(win32con.VK_MENU, 0, win32con.KEYEVENTF_KEYUP, 0)
    time.sleep(0.15)
    focused = user32.GetForegroundWindow() == GAME_HWND
    if not focused:
        print("[window] Could not focus the Roblox window — injected keys may not reach the game.")
    return focused


def _hp_fill_is_critical(raw):
    """Classify a small, always-filled HP patch as critical red."""
    hsv = cv2.cvtColor(raw[:, :, :3], cv2.COLOR_BGR2HSV)
    vivid = (hsv[:, :, 1] > 140) & (hsv[:, :, 2] > 140)
    vivid_count = int(np.count_nonzero(vivid))
    if vivid_count < int(raw.shape[0] * raw.shape[1] * 0.45):
        return False
    hue = hsv[:, :, 0]
    red = int(np.count_nonzero(((hue <= 5) | (hue >= 175)) & vivid))
    return red >= int(vivid_count * 0.82)


def _hp_fill_is_green(raw):
    """Classify the same opaque HP patch as restored green."""
    hsv = cv2.cvtColor(raw[:, :, :3], cv2.COLOR_BGR2HSV)
    vivid = (hsv[:, :, 1] > 140) & (hsv[:, :, 2] > 140)
    vivid_count = int(np.count_nonzero(vivid))
    if vivid_count < int(raw.shape[0] * raw.shape[1] * 0.45):
        return False
    hue = hsv[:, :, 0]
    green = int(np.count_nonzero((hue >= 35) & (hue <= 75) & vivid))
    return green >= int(vivid_count * 0.82)


def _hp_bar_is_red(sct):
    """True only when the fixed left-side HP fill patch is critical red."""
    raw = _grab_reference_box(sct, SENZU_HP_FILL_BOX)
    return _hp_fill_is_critical(raw)


def _hp_bar_is_green(sct):
    """True only when the fixed left-side HP fill patch is restored green."""
    raw = _grab_reference_box(sct, SENZU_HP_FILL_BOX)
    return _hp_fill_is_green(raw)


_SENZU_ASSET_CACHE = None


def _senzu_assets():
    global _SENZU_ASSET_CACHE
    if _SENZU_ASSET_CACHE is None:
        names = {
            "training": "tpl_training_mode.png",
            "game_menu": "tpl_game_menu.png",
            "inventory": "tpl_inventory_menu.png",
            "bean": "tpl_senzu_bean.png",
            "slot": "tpl_slot_senzu.png",
            "digits": "tpl_inventory_digits.png",
        }
        _SENZU_ASSET_CACHE = {
            key: cv2.imread(os.path.join(BASE_DIR, name), cv2.IMREAD_GRAYSCALE)
            for key, name in names.items()
        }
    return _SENZU_ASSET_CACHE


def _game_geometry(geometry=None):
    """Return an immutable (left, top, width, height) client geometry snapshot."""
    if geometry is None:
        _refresh_game_window_if_stale()
        values = (GAME_OFFSET_X, GAME_OFFSET_Y, GAME_WIDTH, GAME_HEIGHT)
    elif isinstance(geometry, dict):
        values = tuple(geometry[key] for key in ("left", "top", "width", "height"))
    else:
        values = tuple(geometry)
    if len(values) != 4:
        raise ValueError("game geometry must contain left, top, width, and height")
    left, top, width, height = [int(value) for value in values]
    if width <= 0 or height <= 0:
        raise ValueError("game geometry must have positive width and height")
    return left, top, width, height


def _reference_point(x, y, geometry=None):
    left, top, width, height = _game_geometry(geometry)
    return (
        left + int(round(float(x) * width / GAME_REFERENCE_WIDTH)),
        top + int(round(float(y) * height / GAME_REFERENCE_HEIGHT)),
    )


def _reference_box(box, geometry=None):
    """Convert a canonical client-local box to absolute virtual-screen pixels."""
    if isinstance(box, dict):
        x, y = box["left"], box["top"]
        width, height = box["width"], box["height"]
    else:
        x, y, width, height = box
    left, top = _reference_point(x, y, geometry)
    _gx, _gy, game_width, game_height = _game_geometry(geometry)
    return {
        "left": left,
        "top": top,
        "width": max(1, int(round(float(width) * game_width / GAME_REFERENCE_WIDTH))),
        "height": max(1, int(round(float(height) * game_height / GAME_REFERENCE_HEIGHT))),
    }


def _screen_point_to_reference(x, y, geometry=None):
    """Convert an absolute virtual-screen point to canonical client coordinates."""
    left, top, width, height = _game_geometry(geometry)
    return (
        int(round((float(x) - left) * GAME_REFERENCE_WIDTH / width)),
        int(round((float(y) - top) * GAME_REFERENCE_HEIGHT / height)),
    )


def _screen_box_to_reference(box, geometry=None):
    """Convert an absolute virtual-screen box to canonical client coordinates."""
    left, top = _screen_point_to_reference(box["left"], box["top"], geometry)
    right, bottom = _screen_point_to_reference(
        box["left"] + box["width"], box["top"] + box["height"], geometry
    )
    return {
        "left": min(left, right),
        "top": min(top, bottom),
        "width": max(1, abs(right - left)),
        "height": max(1, abs(bottom - top)),
    }


def _button_screen_point(stat_name, geometry=None):
    return _reference_point(*BUTTONS[stat_name], geometry=geometry)


def _grab_reference_box(sct, box, geometry=None):
    """Grab a client-relative box and normalize it to the 1920x1080 reference."""
    if isinstance(box, dict):
        width, height = int(box["width"]), int(box["height"])
    else:
        _x, _y, width, height = [int(value) for value in box]
    frame = np.array(sct.grab(_reference_box(box, geometry)))[:, :, :3]
    if frame.shape[1] != width or frame.shape[0] != height:
        frame = cv2.resize(frame, (width, height), interpolation=cv2.INTER_AREA)
    return frame


def _template_in_reference_box(sct, template, box, threshold=0.82):
    if template is None:
        return False, 0.0, None
    frame = _grab_reference_box(sct, box)
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    if gray.shape[0] < template.shape[0] or gray.shape[1] < template.shape[1]:
        return False, 0.0, None
    _, score, _, location = cv2.minMaxLoc(
        cv2.matchTemplate(gray, template, cv2.TM_CCOEFF_NORMED)
    )
    return score >= threshold, float(score), location


def _wait_for_senzu_screen(sct, template, box, timeout=1.5, threshold=0.82):
    deadline = time.time() + timeout
    best_score = 0.0
    while time.time() < deadline and not _senzu_abort_requested():
        visible, score, location = _template_in_reference_box(
            sct, template, box, threshold
        )
        best_score = max(best_score, score)
        if visible:
            return location, score
        time.sleep(0.08)
    return None, best_score


def _wait_for_training_menu_for_senzu(sct, training_template, timeout=1.5):
    """Require two consecutive Training Mode detections before accepting it."""
    deadline = time.time() + timeout
    stable = 0
    best_score = 0.0
    while time.time() < deadline and not _senzu_abort_requested():
        visible, score = _training_menu_visible_for_senzu(sct, training_template)
        best_score = max(best_score, score)
        stable = stable + 1 if visible else 0
        if stable >= 2:
            return True, best_score
        time.sleep(0.08)
    return False, best_score


def _focus_game_for_senzu():
    """Focus Roblox before a menu key, with one bounded focus retry."""
    for _ in range(2):
        if _senzu_abort_requested():
            return False
        if focus_game_window() and not _senzu_abort_requested():
            return True
        time.sleep(0.12)
    return False


def _training_menu_visible_for_senzu(sct, template):
    _refresh_game_window_if_stale()
    monitor = {
        "left": GAME_OFFSET_X,
        "top": GAME_OFFSET_Y,
        "width": GAME_WIDTH,
        "height": GAME_HEIGHT,
    }
    visible, score = detect_training_menu(sct, monitor, template)
    return visible, score


def _senzu_abort_requested():
    return bool(
        UI_STOP_REQUESTED
        or (_background_monitor_stop is not None and _background_monitor_stop.is_set())
    )


def _ensure_training_menu_for_senzu(sct, assets):
    if _senzu_abort_requested():
        return False
    training_template = assets["training"]
    visible, _ = _training_menu_visible_for_senzu(sct, training_template)
    if visible:
        return True

    # Red HP can be detected while the player already has Game Menu or
    # Inventory open. Route those known screens back to Training Mode instead
    # of blindly toggling Tab from an unknown menu state.
    inventory_visible = _wait_for_senzu_screen(
        sct, assets["inventory"], (200, 340, 280, 80), timeout=0.35
    )[0] is not None
    game_menu_visible = False
    if not inventory_visible:
        game_menu_visible = _wait_for_senzu_screen(
            sct, assets["game_menu"], (0, 340, 220, 80), timeout=0.35
        )[0] is not None
    if inventory_visible or game_menu_visible:
        return _close_inventory_to_training(sct, assets)

    if not _focus_game_for_senzu():
        return False
    _tap_key_unchecked("tab")
    deadline = time.time() + 1.8
    stable = 0
    while time.time() < deadline and not _senzu_abort_requested():
        visible, _ = _training_menu_visible_for_senzu(sct, training_template)
        stable = stable + 1 if visible else 0
        if stable >= 2:
            return True
        time.sleep(0.08)
    return False


def _senzu_slot_has_bean(
        sct, slot, slot_template, *, hotbar=False, bean_type="full"):
    # GC renders the H-item list 26px above the assignment slots shown inside
    # Inventory. Keep both coordinates explicit instead of sampling between rows.
    first_row_top = 920 if hotbar else 946
    top = first_row_top + (int(slot) - 1) * 31
    if slot_template is None:
        return False, 0.0
    frame = _grab_reference_box(sct, (1510, top, 260, 42))
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    # Match only the low-saturation white label. Slot hover changes the row
    # background from blue to yellow, but the text itself remains identical.
    frame_text = ((hsv[:, :, 1] < 90) & (hsv[:, :, 2] > 200)).astype(np.uint8) * 255
    template_text = (slot_template > 200).astype(np.uint8) * 255
    scores = cv2.matchTemplate(frame_text, template_text, cv2.TM_CCOEFF_NORMED)
    matches_y, matches_x = np.where(scores >= SENZU_SLOT_MATCH_MIN)
    # Full labels start around x=42. Half labels contain the same suffix farther
    # right, so the match position identifies the assigned type without a second
    # template or weakening the established full-vs-Half filter.
    type_scores = [
        float(scores[y, x])
        for y, x in zip(matches_y, matches_x)
        if _senzu_slot_match_is_reliable(float(scores[y, x]), int(x), bean_type)
    ]
    best_score = float(np.max(scores)) if scores.size else 0.0
    return bool(type_scores), max(type_scores, default=best_score)


def _senzu_slot_match_is_reliable(score, match_x, bean_type):
    return bool(
        score >= SENZU_SLOT_MATCH_MIN
        and (int(match_x) <= 60) == (bean_type == "full")
    )


def _stable_senzu_slot_state(
        sct, slot, slot_template, samples=3, *, hotbar=False, bean_type="full"):
    """Return True/False only when several consecutive slot frames agree."""
    states = []
    best_score = 0.0
    for index in range(samples):
        if _senzu_abort_requested():
            return None, best_score
        assigned, score = _senzu_slot_has_bean(
            sct, slot, slot_template, hotbar=hotbar, bean_type=bean_type
        )
        states.append(assigned)
        best_score = max(best_score, score)
        if index + 1 < samples:
            time.sleep(0.08)
    if all(states):
        return True, best_score
    if not any(states):
        return False, best_score
    return None, best_score


def _wait_for_hotbar_slot_clear(
        sct, slot, slot_template, bean_type, max_samples=12):
    """Confirm two clear H-menu frames after one consume keypress."""
    clear_streak = 0
    best_score = 0.0
    for index in range(max_samples):
        if _senzu_abort_requested():
            return False, best_score
        assigned, score = _senzu_slot_has_bean(
            sct,
            slot,
            slot_template,
            hotbar=True,
            bean_type=bean_type,
        )
        best_score = max(best_score, score)
        clear_streak = 0 if assigned else clear_streak + 1
        if clear_streak >= 2:
            return True, best_score
        if index + 1 < max_samples:
            time.sleep(0.07)
    return False, best_score


def _consume_open_senzu_slot(
        sct, slot, slot_template, bean_type, max_key_attempts=3):
    """Press the open H-menu slot until GC confirms that row cleared.

    GC occasionally ignores a digit even though H is visibly open. Keep that
    same menu open and retry only after the slot remained loaded for the full
    confirmation window. This avoids the much slower Inventory -> recovery
    timeout loop for a keypress the game simply dropped.
    """
    best_score = 0.0
    for key_attempt in range(1, max_key_attempts + 1):
        if _senzu_abort_requested():
            return False, best_score
        if not _focus_game_for_senzu():
            return False, best_score
        _tap_key_unchecked(str(slot))
        accepted, score = _wait_for_hotbar_slot_clear(
            sct, slot, slot_template, bean_type
        )
        best_score = max(best_score, score)
        if accepted:
            if key_attempt > 1:
                print(
                    f"[SENZU] Slot {slot} accepted on digit retry "
                    f"{key_attempt}/{max_key_attempts}"
                )
            return True, best_score
        if key_attempt < max_key_attempts:
            print(
                f"[SENZU] Slot {slot} ignored the digit press; retrying while "
                f"H remains confirmed ({key_attempt}/{max_key_attempts})"
            )
            time.sleep(0.12)
    return False, best_score


def _read_inventory_count(list_frame, row_top, digit_sheet):
    """Read the green stock number before selecting the Senzu row."""
    if digit_sheet is None:
        return None
    center_y = int(row_top - 395 + 18)
    crop = list_frame[max(0, center_y - 15):center_y + 15, 480:555]
    if crop.shape[0] < 15:
        return None
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    # Green = normal stock display. Red = the row is selected for assignment
    # (GC keeps that state across menu opens — live 22:31 failure read None on
    # a still-selected row and aborted the whole refill).
    vivid = (hsv[:, :, 1] > 130) & (hsv[:, :, 2] > 100)
    green_digits = (hsv[:, :, 0] >= 35) & (hsv[:, :, 0] <= 85)
    red_digits = (hsv[:, :, 0] <= 8) | (hsv[:, :, 0] >= 175)
    mask = ((green_digits | red_digits) & vivid).astype(np.uint8) * 255
    candidates = []
    for digit in range(10):
        template = digit_sheet[:, digit * 12:(digit + 1) * 12]
        scores = cv2.matchTemplate(mask, template, cv2.TM_CCOEFF_NORMED)
        for y, x in zip(*np.where(scores > 0.70)):
            candidates.append((float(scores[y, x]), int(x), int(y), digit))
    picked = []
    for candidate in sorted(candidates, reverse=True):
        if all(abs(candidate[1] - prior[1]) > 5 for prior in picked):
            picked.append(candidate)
    picked.sort(key=lambda item: item[1])
    if not picked or len(picked) > 7:
        return None
    try:
        return int("".join(str(item[3]) for item in picked))
    except ValueError:
        return None


def _inventory_quantity_is_red(sct, row_y):
    """Return whether the selected row's quantity has entered its red ready state."""
    frame = _grab_reference_box(sct, (480, int(row_y) - 15, 75, 30))
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    hue = hsv[:, :, 0]
    saturation = hsv[:, :, 1]
    value = hsv[:, :, 2]
    vivid = (saturation > 120) & (value > 100)
    red = ((hue <= 8) | (hue >= 175)) & vivid
    green = (hue >= 35) & (hue <= 85) & vivid
    red_count = int(np.count_nonzero(red))
    green_count = int(np.count_nonzero(green))
    return red_count >= 6 and red_count >= green_count * 2


def _wait_for_inventory_quantity_red(sct, row_y, max_samples=8):
    """Require two consecutive red quantity frames before slot assignment."""
    red_streak = 0
    for index in range(max_samples):
        if _senzu_abort_requested():
            return False
        red_streak = red_streak + 1 if _inventory_quantity_is_red(sct, row_y) else 0
        if red_streak >= 2:
            return True
        if index + 1 < max_samples:
            time.sleep(0.05)
    return False


def _find_senzu_row(sct, assets, bean_type="full"):
    """Search the Items list for one exact Senzu type."""
    list_box = (0, 395, 650, 335)
    best_frame = None
    last_frame = None
    best_score = 0.0
    # Legacy mouse_event wheel packets were ignored by Roblox on this setup.
    # Use the same SendInput path as reliable clicks, first forcing the custom
    # list to its top so remembered Inventory scroll state cannot matter.
    cursor_x, cursor_y = _reference_point(310, 560)
    robust_move(cursor_x, cursor_y)

    def _send_wheel(notches):
        wheel = (_INPUT * 1)(_make_mouse_input(0x0800))  # MOUSEEVENTF_WHEEL
        wheel[0].mi.mouseData = int(notches * 120) & 0xFFFFFFFF
        with _stop_input_gate:
            if _USER_STOP_LATCHED:
                return False
            _user32.SendInput(1, wheel, _ctypes.sizeof(_INPUT))
        return True

    for _ in range(15):
        if _senzu_abort_requested():
            return None, None, 0.0
        _send_wheel(4)
        time.sleep(0.01)
    time.sleep(0.20)

    previous_gray = None
    unchanged_after_scroll = 0
    for _ in range(40):
        if _senzu_abort_requested():
            return None, None, 0.0
        frame = _grab_reference_box(sct, list_box)
        last_frame = frame.copy()
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        # Each iteration scrolls before the next grab, so two consecutive
        # identical frames mean the list bottom is reached (or the list is
        # shorter than the view). Stop instead of burning the remaining
        # iterations — an empty inventory previously cost ~8s per scan pass.
        if previous_gray is not None and float(
                np.mean(cv2.absdiff(gray, previous_gray))) < 1.0:
            unchanged_after_scroll += 1
            if unchanged_after_scroll >= 2:
                break
        else:
            unchanged_after_scroll = 0
        previous_gray = gray
        template = assets["bean"]
        if template is not None and gray.shape[0] >= template.shape[0]:
            scores = cv2.matchTemplate(gray, template, cv2.TM_CCOEFF_NORMED)
            _min_score, raw_score, _min_location, _raw_location = cv2.minMaxLoc(scores)
            if raw_score > best_score:
                best_score = float(raw_score)
                best_frame = frame.copy()
            matches_y, matches_x = np.where(scores >= 0.80)
            # A Full item label is left-aligned around x=8. A Half label contains
            # the same template after the "Half " prefix, so its match is farther
            # right. Keep this established positional split explicit.
            type_matches = [
                (float(scores[y, x]), int(x), int(y))
                for y, x in zip(matches_y, matches_x)
                if (int(x) <= 24) == (bean_type == "full")
            ]
            if type_matches:
                score, _match_x, match_y = max(type_matches)
                row_top = 395 + match_y
                count = _read_inventory_count(frame, row_top, assets["digits"])
                row_y = row_top + template.shape[0] // 2
                # Every successful scan refreshes the cache, including the
                # delayed refill re-checks, so the fast path always points at
                # the list position GC actually remembers.
                _remember_senzu_row(bean_type, row_y)
                return row_y, count, float(score)
            if len(matches_x):
                best_index = int(np.argmax([scores[y, x] for y, x in zip(matches_y, matches_x)]))
                rejected_x = int(matches_x[best_index])
                print(
                    f"[SENZU] Ignoring non-{bean_type} Senzu label match "
                    f"at x={rejected_x}"
                )
        _send_wheel(-4)
        time.sleep(0.16)
    if best_frame is not None:
        try:
            cv2.imwrite(
                os.path.join(DATA_DIR, "senzu_inventory_not_found.png"),
                best_frame,
            )
            if last_frame is not None:
                cv2.imwrite(
                    os.path.join(DATA_DIR, "senzu_inventory_last_position.png"),
                    last_frame,
                )
        except Exception:
            pass
    return None, None, best_score


def _validate_cached_senzu_row(sct, assets):
    """Confirm the remembered Items row without any scrolling or tab clicks.

    GC keeps the Items list exactly where it was between menu opens unless the
    user navigated, the character died, or the screen changed. One frame at the
    cached position proves whether that still holds: the bean template must
    match at the remembered row with the correct Full/Half position, and the
    stock count must read back consistent with the last known count. Any
    mismatch discards the cache and falls back to the full scroll scan.
    """
    if SENZU_ROW_CACHE["row_y"] is None:
        return None
    if SENZU_ROW_CACHE["mode"] != SENZU_PREFERENCE_MODE:
        # Priority change invalidates: a cached fallback type may no longer be
        # allowed, or a now-preferred type must be searched for first.
        _invalidate_senzu_row_cache()
        return None
    bean_type = SENZU_ROW_CACHE["bean_type"]
    cached_row_y = int(SENZU_ROW_CACHE["row_y"])
    template = assets["bean"]
    if template is None or bean_type not in _senzu_type_priority():
        _invalidate_senzu_row_cache()
        return None
    list_box = (0, 395, 650, 335)
    frame = _grab_reference_box(sct, list_box)
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    # Search only a small band around the cached row: a match anywhere else in
    # the list means the position moved and must be rescanned from the top.
    expected_top = cached_row_y - 395 - template.shape[0] // 2
    band_top = max(0, expected_top - 4)
    band_bottom = min(gray.shape[0], expected_top + template.shape[0] + 4)
    band = gray[band_top:band_bottom]
    if band.shape[0] < template.shape[0]:
        _invalidate_senzu_row_cache()
        return None
    scores = cv2.matchTemplate(band, template, cv2.TM_CCOEFF_NORMED)
    matches_y, matches_x = np.where(scores >= 0.80)
    type_matches = [
        (float(scores[y, x]), int(x), int(y))
        for y, x in zip(matches_y, matches_x)
        if (int(x) <= 24) == (bean_type == "full")
    ]
    if not type_matches:
        print(
            f"[SENZU] Cached {_senzu_type_label(bean_type)} row no longer "
            "matches; rescanning the Items list"
        )
        _invalidate_senzu_row_cache()
        return None
    score, _match_x, match_y = max(type_matches)
    row_top = 395 + band_top + match_y
    count = _read_inventory_count(frame, row_top, assets["digits"])
    if count is None or count <= 0 or (
            SENZU_REMAINING is not None and count != SENZU_REMAINING):
        print(
            f"[SENZU] Cached row count mismatch (read={count}, "
            f"expected={SENZU_REMAINING}); rescanning the Items list"
        )
        _invalidate_senzu_row_cache()
        return None
    confirmed_row_y = row_top + template.shape[0] // 2
    _remember_senzu_row(bean_type, confirmed_row_y)
    print(
        f"[SENZU] Cached {_senzu_type_label(bean_type)} row confirmed at "
        f"y={confirmed_row_y} with {count} in stock; skipping the scroll scan"
    )
    return confirmed_row_y, count, score, bean_type


def _find_preferred_senzu_row(sct, assets):
    """Return the first stocked type allowed by the configured priority."""
    cached = _validate_cached_senzu_row(sct, assets)
    if cached is not None:
        return cached
    best_score = 0.0
    priority = _senzu_type_priority()
    unreadable_candidate = None
    for index, bean_type in enumerate(priority):
        row_y, count, score = _find_senzu_row(sct, assets, bean_type)
        best_score = max(best_score, score)
        if row_y is None:
            continue
        if count is None:
            # A visible label with unreadable quantity may be stocked or may be
            # GC's stale zero-count row. Keep it as a last resort, but first look
            # for a lower-priority type whose positive stock is confirmed. This
            # avoids stopping on an empty preferred row without declaring OCR
            # failure itself to mean "empty".
            if unreadable_candidate is None:
                unreadable_candidate = (row_y, count, score, bean_type)
            if index < len(priority) - 1:
                print(
                    f"[SENZU] {_senzu_type_label(bean_type)} quantity unreadable; "
                    "checking the configured fallback"
                )
            continue
        if count <= 0:
            print(f"[SENZU] {_senzu_type_label(bean_type)} stock is empty")
            continue
        if index:
            print(
                f"[SENZU] Preferred {_senzu_type_label(priority[0])} stock is absent; "
                f"falling back to {_senzu_type_label(bean_type)}"
            )
        return row_y, count, score, bean_type
    if unreadable_candidate is not None:
        return unreadable_candidate
    _invalidate_senzu_row_cache()
    return None, None, best_score, None


def _open_training_menu_from_gameplay(sct, assets):
    """Open Training Mode after a confirmed menu close returned to gameplay."""
    if _senzu_abort_requested() or not _focus_game_for_senzu():
        return False
    _tap_key_unchecked("tab")
    training_visible, _ = _wait_for_training_menu_for_senzu(
        sct, assets["training"], timeout=1.8
    )
    if not training_visible:
        print("[SENZU] Training Mode not confirmed after returning to gameplay")
    return training_visible


def _close_inventory_to_training(sct, assets):
    if _senzu_abort_requested():
        return False
    training_visible, _ = _training_menu_visible_for_senzu(sct, assets["training"])
    if training_visible:
        return True

    inventory_box = (200, 340, 280, 80)
    game_menu_box = (0, 340, 220, 80)
    inventory_visible = _wait_for_senzu_screen(
        sct, assets["inventory"], inventory_box, timeout=0.35
    )[0] is not None
    game_menu_visible = False
    if not inventory_visible:
        game_menu_visible = _wait_for_senzu_screen(
            sct, assets["game_menu"], game_menu_box, timeout=0.35
        )[0] is not None

    # The recorded fast path is Inventory -> M -> Training Mode. Only send M
    # after Windows confirms Roblox is foreground, then inspect the resulting
    # screen before deciding whether another input is safe.
    if inventory_visible:
        if _senzu_abort_requested():
            return False
        if not _focus_game_for_senzu():
            print("[SENZU] Roblox focus not confirmed before closing Inventory")
            return False
        if _senzu_abort_requested():
            return False
        _tap_key_unchecked("m")
        training_visible, _ = _wait_for_training_menu_for_senzu(
            sct, assets["training"], timeout=1.8
        )
        if training_visible:
            return True
        inventory_visible = _wait_for_senzu_screen(
            sct, assets["inventory"], inventory_box, timeout=0.35
        )[0] is not None
        if not inventory_visible:
            game_menu_visible = _wait_for_senzu_screen(
                sct, assets["game_menu"], game_menu_box, timeout=0.5
            )[0] is not None
            if not game_menu_visible:
                # On some routes M closes Inventory all the way to gameplay;
                # Training Mode then needs its own Tab toggle.
                print("[SENZU] Inventory closed to gameplay; reopening Training Mode")
                return _open_training_menu_from_gameplay(sct, assets)

    # Bounded fallback: Inventory -> Back -> Game Menu -> M -> Training Mode.
    # Back is clicked only while Inventory is visually confirmed.
    if inventory_visible:
        if _senzu_abort_requested():
            return False
        if not _focus_game_for_senzu():
            print("[SENZU] Roblox focus not confirmed before Inventory Back")
            return False
        if _senzu_abort_requested():
            return False
        bx, by = _reference_point(80, 377)
        click_at(bx, by)
        game_menu_visible = _wait_for_senzu_screen(
            sct, assets["game_menu"], game_menu_box, timeout=1.2
        )[0] is not None
        if not game_menu_visible:
            print("[SENZU] Game Menu not confirmed after Inventory Back")
            return False

    if not game_menu_visible:
        print("[SENZU] Inventory or Game Menu was not confirmed before closing")
        return False
    if _senzu_abort_requested():
        return False
    if not _focus_game_for_senzu():
        print("[SENZU] Roblox focus not confirmed before closing Game Menu")
        return False
    if _senzu_abort_requested():
        return False
    _tap_key_unchecked("m")
    training_visible, _ = _wait_for_training_menu_for_senzu(
        sct, assets["training"], timeout=1.8
    )
    if not training_visible:
        print("[SENZU] Game Menu closed to gameplay; reopening Training Mode")
        return _open_training_menu_from_gameplay(sct, assets)
    return True


def _refill_senzu_slot(
        sct, assets, require_assignment=False, _inventory_reopen_attempted=False):
    global SENZU_REMAINING, SENZU_STATUS, SENZU_ACTIVE_TYPE
    SENZU_STATUS = "refilling"
    if not _focus_game_for_senzu():
        print("[SENZU] Roblox focus not confirmed before opening Game Menu")
        SENZU_STATUS = "error"
        return False
    _tap_key_unchecked("m")
    location, game_menu_score = _wait_for_senzu_screen(
        sct, assets["game_menu"], (0, 340, 220, 80), timeout=0.8
    )
    inventory_open = False
    inventory_score = 0.0
    if location is None:
        # GC remembers the last open submenu. M can reopen Inventory directly
        # instead of showing the Game Menu first.
        inventory_location, inventory_score = _wait_for_senzu_screen(
            sct, assets["inventory"], (200, 340, 280, 80), timeout=1.0
        )
        inventory_open = inventory_location is not None
        if not inventory_open:
            print(
                "[SENZU] Neither Game Menu nor Inventory confirmed after M "
                f"(menu={game_menu_score:.3f}, inventory={inventory_score:.3f})"
            )
            SENZU_STATUS = "error"
            _close_inventory_to_training(sct, assets)
            return False

    if not inventory_open:
        # The Game Menu header becomes detectable slightly before its rows
        # accept clicks. Give the opening animation time to settle, then retry
        # only while the Game Menu is still visually confirmed.
        time.sleep(0.30)
        inventory_location = None
        for click_attempt in range(1, 4):
            if _senzu_abort_requested():
                return False
            if not _focus_game_for_senzu():
                SENZU_STATUS = "error"
                return False
            x, y = _reference_point(389, 568)
            click_at(x, y)
            inventory_location, inventory_score = _wait_for_senzu_screen(
                sct, assets["inventory"], (200, 340, 280, 80), timeout=1.2
            )
            if inventory_location is not None:
                break
            game_menu_location, game_menu_score = _wait_for_senzu_screen(
                sct, assets["game_menu"], (0, 340, 220, 80), timeout=0.4
            )
            if game_menu_location is None:
                print(
                    "[SENZU] Screen changed after Inventory click; refusing a blind retry "
                    f"(inventory={inventory_score:.3f}, menu={game_menu_score:.3f})"
                )
                break
            print(
                f"[SENZU] Inventory click not accepted; retrying "
                f"({click_attempt}/3, score={inventory_score:.3f}, "
                f"target=({x},{y}), cursor={win32api.GetCursorPos()})"
            )
            time.sleep(0.25)
        if inventory_location is None:
            print(f"[SENZU] Inventory not confirmed (score={inventory_score:.3f})")
            SENZU_STATUS = "error"
            _close_inventory_to_training(sct, assets)
            return False
    else:
        print("[SENZU] M reopened Inventory directly")

    time.sleep(0.30)
    # GC keeps the Items tab and list position exactly where they were. When
    # the cached row validates on the freshly opened Inventory, both the Items
    # tab click and the scroll scan are unnecessary.
    cached_row = _validate_cached_senzu_row(sct, assets)
    if cached_row is not None:
        row_y, count, score, selected_type = cached_row
    else:
        x, y = _reference_point(414, 758)
        click_at(x, y)
        time.sleep(0.25)
        row_y, count, score, selected_type = _find_preferred_senzu_row(sct, assets)
    if _senzu_abort_requested():
        return False
    if row_y is None:
        # Inventory can likewise render its header before the bottom tabs are
        # clickable. If it is still open, reselect Items once and rescan.
        inventory_visible = _wait_for_senzu_screen(
            sct, assets["inventory"], (200, 340, 280, 80), timeout=0.4
        )[0] is not None
        if inventory_visible and not _senzu_abort_requested():
            print("[SENZU] Allowed Senzu row not found; retrying the Items tab once")
            click_at(x, y)
            time.sleep(0.40)
            row_y, count, score, selected_type = _find_preferred_senzu_row(
                sct, assets
            )
            if _senzu_abort_requested():
                return False
    if row_y is None and not _inventory_reopen_attempted:
        # A consumed-slot redraw can leave the Items list stale for the whole
        # first open. Reopen Inventory once before declaring all allowed stock
        # empty; a false empty at 100G is more dangerous than a slower preflight.
        print(
            f"[SENZU] Allowed Senzu stock still missing (score={score:.3f}); "
            "reopening Inventory for one final scan"
        )
        if not _close_inventory_to_training(sct, assets):
            SENZU_STATUS = "error"
            return False
        if _senzu_abort_requested():
            return False
        time.sleep(0.55)
        return _refill_senzu_slot(
            sct,
            assets,
            require_assignment=require_assignment,
            _inventory_reopen_attempted=True,
        )
    if row_y is None:
        allowed = " then ".join(
            _senzu_type_label(bean_type) for bean_type in _senzu_type_priority()
        )
        print(
            f"[SENZU] No allowed stock found after scrolling ({allowed}, "
            f"score={score:.3f})"
        )
        SENZU_REMAINING = 0
        SENZU_ACTIVE_TYPE = None
        if not _close_inventory_to_training(sct, assets):
            SENZU_STATUS = "error"
            print("[SENZU] Empty stock found, but return to Training Mode was not confirmed")
            return False
        SENZU_STATUS = "empty"
        return False
    SENZU_REMAINING = count
    if count is not None:
        print(
            f"[SENZU] {_senzu_type_label(selected_type)} stock before refill: "
            f"{count}"
        )
        if count <= 0:
            SENZU_ACTIVE_TYPE = None
            if not _close_inventory_to_training(sct, assets):
                SENZU_STATUS = "error"
                print("[SENZU] Empty stock found, but return to Training Mode was not confirmed")
                return False
            SENZU_STATUS = "empty"
            return False

    x, y = _reference_point(311, row_y)
    click_at(x, y)
    if _wait_for_inventory_quantity_red(sct, row_y):
        print(
            f"[SENZU] {_senzu_type_label(selected_type)} selection is ready "
            "for slot assignment"
        )
    else:
        print(
            "[SENZU] Selected-row quantity did not settle red quickly; "
            "continuing with confirmed slot/count fallback"
        )

    # GC's Items screen can redraw a consumed Senzu as a ghost in the slot.
    # Selecting any item clears that ghost, while a genuinely loaded slot stays.
    # Only assign another bean when the post-selection label is actually gone.
    assigned, assigned_score = _stable_senzu_slot_state(
        sct, SENZU_SLOT, assets["slot"], bean_type=selected_type
    )
    if assigned is None:
        print(
            f"[SENZU] Slot {SENZU_SLOT} did not settle after selecting Senzu "
            f"(score={assigned_score:.3f})"
        )
        SENZU_STATUS = "error"
        _close_inventory_to_training(sct, assets)
        return False
    did_assign = not assigned
    if require_assignment and not did_assign:
        print(
            f"[SENZU] Slot {SENZU_SLOT} stayed loaded after the eat attempt; "
            "consumption was not confirmed"
        )
        if not _close_inventory_to_training(sct, assets):
            SENZU_STATUS = "error"
            print("[SENZU] Could not return to Training Mode after the missed eat")
            return False
        SENZU_STATUS = "not_consumed"
        return False
    remaining_after = None
    if did_assign:
        slot_y = (973, 1004, 1034, 1064)[SENZU_SLOT - 1]
        assignment_deadline = time.time() + (
            SENZU_RECOVERY_TIMEOUT_SEC if require_assignment else 0.5
        )
        assignment_attempt = 0
        while True:
            if _senzu_abort_requested():
                return False
            if assignment_attempt:
                delayed_assigned, delayed_score = _stable_senzu_slot_state(
                    sct, SENZU_SLOT, assets["slot"], bean_type=selected_type
                )
                _, delayed_remaining, _ = _find_senzu_row(
                    sct, assets, selected_type
                )
                delayed_count_confirmed = (
                    count is not None
                    and delayed_remaining is not None
                    and delayed_remaining == count - 1
                )
                if delayed_assigned is True or delayed_count_confirmed:
                    assigned = True
                    assigned_score = max(assigned_score, delayed_score)
                    remaining_after = delayed_remaining
                    break
                if time.time() >= assignment_deadline:
                    break
            assignment_attempt += 1
            x, y = _reference_point(1720, slot_y)
            click_at(x, y)
            time.sleep(0.45)
            if _senzu_abort_requested():
                return False
            assigned, assigned_score = _stable_senzu_slot_state(
                sct, SENZU_SLOT, assets["slot"], bean_type=selected_type
            )

            # The assignment list can remain on screen while GC is still in
            # the Senzu recovery animation. A reduced inventory count is just
            # as conclusive as the slot template and avoids a false stop when
            # the slot redraw lags behind the successful click.
            retry_row_y, remaining_after, retry_score = _find_senzu_row(
                sct, assets, selected_type
            )
            stock_decreased = (
                count is not None
                and remaining_after is not None
                and remaining_after == count - 1
            )
            if assigned is True or stock_decreased:
                assigned = True
                if stock_decreased and assigned_score < 0.7:
                    print(
                        f"[SENZU] Replacement confirmed by inventory count "
                        f"({count} -> {remaining_after})"
                    )
                break

            if not require_assignment or time.time() >= assignment_deadline:
                break
            if retry_row_y is None or (
                remaining_after is not None and remaining_after <= 0
            ):
                assigned_score = max(assigned_score, retry_score)
                break

            print(
                f"[SENZU] Replacement not ready during recovery; "
                f"retrying assignment ({assignment_attempt})"
            )
            time.sleep(min(0.55, max(0.0, assignment_deadline - time.time())))
            if _senzu_abort_requested() or time.time() >= assignment_deadline:
                break
            delayed_row_y = None
            while time.time() < assignment_deadline and not _senzu_abort_requested():
                delayed_assigned, delayed_score = _stable_senzu_slot_state(
                    sct, SENZU_SLOT, assets["slot"], bean_type=selected_type
                )
                delayed_row_y, delayed_remaining, _ = _find_senzu_row(
                    sct, assets, selected_type
                )
                delayed_count_confirmed = (
                    count is not None
                    and delayed_remaining is not None
                    and delayed_remaining == count - 1
                )
                if delayed_assigned is True or delayed_count_confirmed:
                    assigned = True
                    assigned_score = max(assigned_score, delayed_score)
                    remaining_after = delayed_remaining
                    break
                if delayed_row_y is not None:
                    break
                time.sleep(0.12)
            if assigned is True or _senzu_abort_requested():
                break
            if delayed_row_y is None or time.time() >= assignment_deadline:
                break
            x, y = _reference_point(311, delayed_row_y)
            click_at(x, y)
            time.sleep(0.35)
    if assigned is not True:
        try:
            cv2.imwrite(
                os.path.join(DATA_DIR, "senzu_slot_failure.png"),
                _grab_reference_box(sct, (1400, 850, 520, 230)),
            )
        except Exception:
            pass
        print(f"[SENZU] Slot {SENZU_SLOT} assignment not confirmed (score={assigned_score:.3f})")
        SENZU_STATUS = "error"
        _close_inventory_to_training(sct, assets)
        return False
    if did_assign:
        # Read the post-assignment stock instead of assuming count - 1.
        if remaining_after is None:
            _, remaining_after, _ = _find_senzu_row(
                sct, assets, selected_type
            )
        if remaining_after is not None:
            SENZU_REMAINING = remaining_after
        elif count is not None:
            SENZU_REMAINING = max(0, count - 1)
        TELEMETRY["senzu_refills"] += 1
        print(
            f"[SENZU] Slot {SENZU_SLOT} refilled with "
            f"{_senzu_type_label(selected_type)}"
            + (f"; {SENZU_REMAINING} left" if SENZU_REMAINING is not None else "")
        )
    else:
        SENZU_REMAINING = count
        print(
            f"[SENZU] Slot {SENZU_SLOT} kept its "
            f"{_senzu_type_label(selected_type)} after ghost clear"
            + (f"; {SENZU_REMAINING} in inventory" if SENZU_REMAINING is not None else "")
        )
    if not _close_inventory_to_training(sct, assets):
        print("[SENZU] Could not confirm return to Training Mode")
        SENZU_STATUS = "error"
        return False
    SENZU_STATUS = "ready"
    SENZU_ACTIVE_TYPE = selected_type
    return True


def _resume_training_after_senzu(sct, assets):
    if _senzu_abort_requested():
        return False
    if CURRENT_TRAINING_STATE not in BUTTONS:
        return True
    visible, _ = _training_menu_visible_for_senzu(sct, assets["training"])
    if not visible:
        return False
    if _senzu_abort_requested():
        return False
    bx, by = _button_screen_point(CURRENT_TRAINING_STATE)
    click_at(int(bx), int(by))
    hidden = 0
    deadline = time.time() + 1.5
    while time.time() < deadline and not _senzu_abort_requested():
        visible, _ = _training_menu_visible_for_senzu(sct, assets["training"])
        hidden = 0 if visible else hidden + 1
        if hidden >= 2:
            return True
        time.sleep(0.08)
    return False


def _stop_for_senzu_failure(message, status="error", retryable=False):
    global UI_STOP_REQUESTED, SENZU_STATUS
    SENZU_STATUS = status
    # Failure paths leave the menu state unknown; never fast-path off it later.
    _invalidate_senzu_row_cache()
    _record_run_outcome(
        "error", f"Auto-Senzu: {message}", retryable=retryable
    )
    UI_STOP_REQUESTED = True
    print(f"[SENZU] {message}; stopping macro safely")


def _stop_for_game_death():
    global UI_STOP_REQUESTED
    _record_run_outcome(
        "error", "Character death was confirmed", retryable=True
    )
    UI_STOP_REQUESTED = True
    print("[RECOVERY] Character death dialog confirmed; stopping current attempt")


def _disable_senzu_for_run(message):
    """Suppress Auto-Senzu until the next Start without changing saved settings."""
    global SENZU_DISABLED_FOR_RUN, SENZU_REMAINING, SENZU_STATUS, SENZU_ACTIVE_TYPE
    SENZU_DISABLED_FOR_RUN = True
    SENZU_REMAINING = 0
    SENZU_ACTIVE_TYPE = None
    SENZU_STATUS = "empty"
    print(
        f"[SENZU] {message}; Auto-Senzu disabled for this run, training continues"
    )


def _lower_gc_gravity_for_empty_senzu(sct):
    """Apply the optional GC-only 0G fallback without stopping the run on failure."""
    if not SENZU_ZERO_GRAVITY_ON_EMPTY:
        return True
    if _senzu_abort_requested():
        return False
    if not _focus_game_for_senzu():
        print("[GRAVITY] Could not focus Roblox for the empty-Senzu 0G fallback")
        return False
    try:
        geometry = _confirmed_game_capture_rect()
    except RuntimeError as error:
        print(f"[GRAVITY] Empty-Senzu 0G fallback skipped: {error}")
        return False
    lowered = _cycle_gc_gravity_to_zero(sct, geometry)
    if not lowered:
        print("[GRAVITY] Could not confirm the empty-Senzu 0G fallback; training continues")
    return lowered


def _resume_after_senzu_stock_empty(
        sct, assets, *, bean_was_used, bean_type=None):
    """Return to the current category after all configured stock is exhausted."""
    global SENZU_STATUS
    used_label = _senzu_type_label(bean_type) if bean_type else "allowed Senzu Bean"
    _disable_senzu_for_run("No allowed Senzu Bean stock remains")
    if bean_was_used:
        TELEMETRY["senzu_eaten"] += 1
        if _wait_for_green_hp(sct, SENZU_RECOVERY_TIMEOUT_SEC, bean_type):
            recovery_label = "Green HP" if bean_type == "full" else "HP above red"
            print(f"[SENZU] {recovery_label} confirmed after the last {used_label}")
        else:
            print(
                f"[SENZU] HP was not green after {SENZU_RECOVERY_TIMEOUT_SEC:g}s; "
                "no allowed Senzu stock remains, so no retry will be used"
            )
    _lower_gc_gravity_for_empty_senzu(sct)
    if not _resume_training_after_senzu(sct, assets):
        if _senzu_abort_requested():
            return False
        _stop_for_senzu_failure("Could not resume the active training category")
        return False
    # Preserve the UI-facing empty state after the category click succeeds.
    SENZU_STATUS = "empty"
    return True


def ensure_senzu_ready(sct):
    """Preflight the configured slot while the Training Mode menu is visible."""
    global SENZU_STATUS
    assets = _senzu_assets()
    if any(asset is None for asset in assets.values()):
        _stop_for_senzu_failure("Inventory recognition assets are missing")
        return False
    assigned_type = None
    score = 0.0
    for bean_type in _senzu_type_priority():
        assigned, type_score = _senzu_slot_has_bean(
            sct, SENZU_SLOT, assets["slot"], bean_type=bean_type
        )
        score = max(score, type_score)
        if assigned:
            assigned_type = bean_type
            break
    if assigned_type:
        print(
            f"[SENZU] Slot {SENZU_SLOT} looks loaded with "
            f"{_senzu_type_label(assigned_type)} (score={score:.3f}); "
            "refreshing assignment to rule out the inventory ghost"
        )
    else:
        print(f"[SENZU] Slot {SENZU_SLOT} is not loaded; preflight refill")
    ready = _refill_senzu_slot(sct, assets)
    if not ready and SENZU_STATUS == "empty":
        _disable_senzu_for_run("No allowed Senzu Bean stock found during preflight")
        _lower_gc_gravity_for_empty_senzu(sct)
        return True
    return ready


def _wait_for_green_hp(sct, timeout, bean_type="full"):
    """Full beans require green HP; Half beans only need HP to leave critical red."""
    recovered_streak = 0
    deadline = time.time() + timeout
    while time.time() < deadline and not _senzu_abort_requested():
        recovered = (
            not _hp_bar_is_critical(sct)
            if bean_type == "half"
            else _hp_bar_is_green(sct)
        )
        recovered_streak = recovered_streak + 1 if recovered else 0
        if recovered_streak >= 2:
            return True
        time.sleep(0.08)
    return False


def _recover_after_unconfirmed_senzu(sct, assets, bean_type=None):
    """Handle a delayed consume; return None when one clean retry is needed."""
    global SENZU_STATUS
    print(
        f"[SENZU] Waiting {SENZU_RECOVERY_TIMEOUT_SEC:g}s to verify HP "
        "before retrying the missed eat"
    )
    if not _wait_for_green_hp(sct, SENZU_RECOVERY_TIMEOUT_SEC, bean_type):
        if _senzu_abort_requested():
            return False
        return None

    # The slot redraw can lag even after a successful consume. Once green HP
    # proves it was eaten, refresh the slot from Inventory before resuming.
    recovery_label = "Green HP" if bean_type == "full" else "HP above red"
    print(f"[SENZU] {recovery_label} confirmed despite the delayed slot redraw")
    if not _refill_senzu_slot(sct, assets):
        if _senzu_abort_requested():
            return False
        if SENZU_STATUS == "empty":
            return _resume_after_senzu_stock_empty(
                sct, assets, bean_was_used=True, bean_type=bean_type
            )
        _stop_for_senzu_failure("Could not refresh the Senzu slot after recovery")
        return False
    TELEMETRY["senzu_eaten"] += 1
    print(f"[SENZU] Delayed {_senzu_type_label(bean_type)} consumption confirmed")
    if not _resume_training_after_senzu(sct, assets):
        if _senzu_abort_requested():
            return False
        _stop_for_senzu_failure("Could not resume the active training category")
        return False
    SENZU_STATUS = "ready"
    return True


def eat_senzu(sct):
    """Return to Training, eat/refill, wait for green HP, then resume."""
    global SENZU_STATUS
    assets = _senzu_assets()
    with _input_lock:
        SENZU_STATUS = "eating"
        # The Training menu stays on screen while Agility/Ki minigames run, so
        # a visible menu does NOT mean the minigame stopped. An active minigame
        # swallows the digit consume keys. Mirror the manual flow exactly:
        # Tab out of the minigame first, then H, 1.
        if CURRENT_TRAINING_STATE in BUTTONS:
            if not _focus_game_for_senzu():
                if _senzu_abort_requested():
                    return False
                _stop_for_senzu_failure("Roblox focus not confirmed before using Senzu")
                return False
            print("[SENZU] Exiting the active minigame with Tab before H")
            _tap_key_unchecked("tab")
            time.sleep(0.40)
        if not _ensure_training_menu_for_senzu(sct, assets):
            if _senzu_abort_requested():
                return False
            _stop_for_senzu_failure("Training Mode menu not confirmed")
            return False

        for attempt in range(1, 3):
            if _senzu_abort_requested():
                return False
            if not _focus_game_for_senzu():
                if _senzu_abort_requested():
                    return False
                _stop_for_senzu_failure("Roblox focus not confirmed before using Senzu")
                return False
            _tap_key_unchecked("h")
            time.sleep(0.30)
            priority = _senzu_type_priority()
            expected_type = (
                SENZU_ACTIVE_TYPE if SENZU_ACTIVE_TYPE in priority else priority[0]
            )
            assigned, assigned_score = _stable_senzu_slot_state(
                sct,
                SENZU_SLOT,
                assets["slot"],
                hotbar=True,
                bean_type=expected_type,
            )
            if _senzu_abort_requested():
                return False

            # Startup preflight normally guarantees this. If the slot was
            # changed or emptied afterward, load it before the first eat.
            if assigned is not True:
                try:
                    cv2.imwrite(
                        os.path.join(DATA_DIR, "senzu_hotbar_missing_before_refill.png"),
                        _grab_reference_box(sct, (1400, 850, 520, 230)),
                    )
                except Exception:
                    pass
                _tap_key_unchecked("h")
                time.sleep(0.18)
                print(
                    f"[SENZU] Slot {SENZU_SLOT} missing before eat attempt {attempt}; "
                    "loading it first"
                )
                if not _refill_senzu_slot(sct, assets):
                    if _senzu_abort_requested():
                        return False
                    if SENZU_STATUS == "empty":
                        return _resume_after_senzu_stock_empty(
                            sct, assets, bean_was_used=False
                        )
                    _stop_for_senzu_failure("No Senzu could be loaded", SENZU_STATUS)
                    return False
                if not _focus_game_for_senzu():
                    if _senzu_abort_requested():
                        return False
                    _stop_for_senzu_failure("Roblox focus not confirmed before using Senzu")
                    return False
                _tap_key_unchecked("h")
                time.sleep(0.30)
                expected_type = SENZU_ACTIVE_TYPE or _senzu_type_priority()[0]
                assigned, assigned_score = _stable_senzu_slot_state(
                    sct,
                    SENZU_SLOT,
                    assets["slot"],
                    hotbar=True,
                    bean_type=expected_type,
                )
                if _senzu_abort_requested():
                    return False
            if assigned is not True:
                try:
                    cv2.imwrite(
                        os.path.join(DATA_DIR, "senzu_hotbar_missing_after_refill.png"),
                        _grab_reference_box(sct, (1400, 850, 520, 230)),
                    )
                except Exception:
                    pass
                _tap_key_unchecked("h")
                _stop_for_senzu_failure(
                    f"Senzu slot {SENZU_SLOT} was not confirmed in the Items list "
                    f"(score={assigned_score:.3f})"
                )
                return False

            print(
                f"[SENZU] Critical red HP — using {_senzu_type_label(expected_type)} "
                f"from slot {SENZU_SLOT} "
                f"(attempt {attempt}/2)"
            )
            if _senzu_abort_requested():
                return False
            consume_accepted, consume_score = _consume_open_senzu_slot(
                sct, SENZU_SLOT, assets["slot"], expected_type
            )
            if consume_accepted:
                print(
                    f"[SENZU] {_senzu_type_label(expected_type)} consume "
                    "accepted by the H-menu slot"
                )
            else:
                print(
                    "[SENZU] H-menu slot stayed loaded after three confirmed "
                    "digit attempts "
                    f"(score={consume_score:.3f}); closing once and using the "
                    "existing HP/inventory confirmation fallback"
                )
                # GC can refuse digit presses while H is confirmed open; save
                # the H region so the refusal state can be inspected.
                try:
                    cv2.imwrite(
                        os.path.join(DATA_DIR, "senzu_consume_refused.png"),
                        _grab_reference_box(sct, (1400, 850, 520, 230)),
                    )
                except Exception:
                    pass
            if _senzu_abort_requested():
                return False
            _tap_key_unchecked("h")
            time.sleep(0.25)
            if _senzu_abort_requested():
                return False

            # Refill during the recovery animation. Selecting Senzu in
            # Inventory clears GC's ghost slot, so an empty slot followed by a
            # confirmed assignment proves that this eat actually consumed one.
            if not _refill_senzu_slot(sct, assets, require_assignment=True):
                if _senzu_abort_requested():
                    return False
                if SENZU_STATUS == "empty":
                    return _resume_after_senzu_stock_empty(
                        sct,
                        assets,
                        bean_was_used=True,
                        bean_type=expected_type,
                    )
                if SENZU_STATUS == "not_consumed":
                    delayed_recovery = _recover_after_unconfirmed_senzu(
                        sct, assets, expected_type
                    )
                    if delayed_recovery is not None:
                        return delayed_recovery
                    if attempt == 1:
                        SENZU_STATUS = "eating"
                        print("[SENZU] HP is still red; retrying the eat once")
                        continue
                    _stop_for_senzu_failure(
                        "Senzu was not consumed after two confirmed slot attempts",
                        retryable=True,
                    )
                    return False
                _stop_for_senzu_failure(
                    "Senzu use was not confirmed or no replacement was available",
                    SENZU_STATUS,
                )
                return False
            TELEMETRY["senzu_eaten"] += 1
            print(
                f"[SENZU] {_senzu_type_label(expected_type)} consumption confirmed; "
                f"replacement is {_senzu_type_label(SENZU_ACTIVE_TYPE)}"
            )

            if _wait_for_green_hp(
                    sct, SENZU_RECOVERY_TIMEOUT_SEC, expected_type):
                if _senzu_abort_requested():
                    return False
                recovery_label = (
                    "Green HP" if expected_type == "full" else "HP above red"
                )
                print(f"[SENZU] {recovery_label} confirmed")
                if not _resume_training_after_senzu(sct, assets):
                    if _senzu_abort_requested():
                        return False
                    _stop_for_senzu_failure("Could not resume the active training category")
                    return False
                SENZU_STATUS = "ready"
                return True

            if _senzu_abort_requested():
                return False

            if attempt == 1:
                SENZU_STATUS = "eating"
                print(
                    f"[SENZU] HP not green after {SENZU_RECOVERY_TIMEOUT_SEC:g}s; "
                    "retrying once"
                )

        if _senzu_abort_requested():
            return False
        _stop_for_senzu_failure(
            f"HP was not green after two confirmed Senzu uses "
            f"({SENZU_RECOVERY_TIMEOUT_SEC:g}s each)",
            retryable=True,
        )
        return False


def display_set_resolution(width=1920, height=1080):
    """Switch Roblox's current display to width x height for this session only.
    Registry is untouched (flags=0), so Revert or a reboot restores the
    user's real mode — same model as the Windows Settings preview."""
    global _DISPLAY_RESTORE
    update_game_window()
    with _display_resolution_lock:
        if _DISPLAY_RESTORE is None:
            _DISPLAY_RESTORE = _load_display_restore()
        monitor = _current_game_monitor_info()
        if GAME_HWND is None or monitor is None:
            print("[display] Roblox monitor not found; resolution unchanged")
            return False, "Roblox monitor not found"
        device = monitor["device"]
        if _DISPLAY_RESTORE is not None and _DISPLAY_RESTORE["device"] != device:
            original_device = _DISPLAY_RESTORE["device"]
            print(f"[display] Revert {original_device} before changing {device}")
            return False, f"revert {original_device} first"
        try:
            original_mode = win32api.EnumDisplaySettings(
                device, win32con.ENUM_CURRENT_SETTINGS
            )
            target_mode = win32api.EnumDisplaySettings(
                device, win32con.ENUM_CURRENT_SETTINGS
            )
        except Exception as e:
            print(f"[display] Could not read {device}: {e}")
            return False, "display mode unavailable"
        target_mode.PelsWidth = int(width)
        target_mode.PelsHeight = int(height)
        # Assert ONLY width/height. The mode came from ENUM_CURRENT_SETTINGS, so its
        # Fields still flag the current DisplayFrequency/BitsPerPel; carrying those over
        # (|=) told Windows "1080p AT this monitor's exact refresh+bpp", which a secondary
        # monitor often can't do -> DISP_CHANGE_BADMODE (-2). Clearing to just W/H lets
        # Windows pick a supported frequency/bpp for that device.
        target_mode.Fields = win32con.DM_PELSWIDTH | win32con.DM_PELSHEIGHT
        result = win32api.ChangeDisplaySettingsEx(device, target_mode, 0)
        ok = (result == win32con.DISP_CHANGE_SUCCESSFUL)
        if ok and _DISPLAY_RESTORE is None:
            _DISPLAY_RESTORE = {"device": device, "mode": original_mode}
            try:
                _persist_display_restore(_DISPLAY_RESTORE)
            except OSError as error:
                print(f"[display] Could not save display restore state: {error}")
        print(
            f"[display] {device} -> {width}x{height}: "
            f"{'ok' if ok else f'failed (code {result})'}"
        )
        return ok, result


# ─── Resolution confirm / auto-revert ────────────────────────────────────────
# A bad mode can leave the screen unreadable, and then the user can't click
# "Revert" to undo it. So the timer lives here, not in the WebView: it still
# fires if the UI is unreachable. Windows' own display dialog works this way.
_display_confirm_lock = threading.Lock()
_display_confirm_timer = None
_display_confirm_deadline = 0.0


def _display_confirm_state():
    """Pending-confirmation state for /state. Seconds are clamped at 0."""
    with _display_confirm_lock:
        pending = _display_confirm_timer is not None
        remaining = max(0.0, _display_confirm_deadline - time.monotonic())
    return {
        "pending": pending,
        "seconds_remaining": round(remaining, 1) if pending else 0.0,
        "timeout_sec": DISPLAY_CONFIRM_TIMEOUT_SEC,
    }


def _cancel_display_confirm():
    """Stop a pending auto-revert. Safe to call when nothing is armed."""
    global _display_confirm_timer, _display_confirm_deadline
    with _display_confirm_lock:
        timer, _display_confirm_timer = _display_confirm_timer, None
        _display_confirm_deadline = 0.0
    if timer is not None:
        timer.cancel()
        return True
    return False


def _arm_display_confirm(timeout=None):
    global _display_confirm_timer, _display_confirm_deadline
    _cancel_display_confirm()
    seconds = float(DISPLAY_CONFIRM_TIMEOUT_SEC if timeout is None else timeout)

    def _expire():
        print("[display] No confirmation — reverting resolution.")
        _cancel_display_confirm()
        display_revert_resolution()
        update_game_window()

    timer = threading.Timer(seconds, _expire)
    timer.daemon = True
    with _display_confirm_lock:
        _display_confirm_timer = timer
        _display_confirm_deadline = time.monotonic() + seconds
    timer.start()
    print(f"[display] Awaiting confirmation; reverting in {seconds:.0f}s.")


def display_keep_resolution():
    """User confirmed the new mode — cancel the pending auto-revert."""
    if _cancel_display_confirm():
        print("[display] Resolution kept.")
        return True, "Resolution kept"
    return False, "Nothing waiting for confirmation"


def display_revert_resolution():
    """Restore the display and exact mode changed by Set, even after a move."""
    global _DISPLAY_RESTORE
    update_game_window()
    with _display_resolution_lock:
        if _DISPLAY_RESTORE is None:
            _DISPLAY_RESTORE = _load_display_restore()
        if _DISPLAY_RESTORE is not None:
            device = _DISPLAY_RESTORE["device"]
            original_mode = _DISPLAY_RESTORE["mode"]
        else:
            monitor = _current_game_monitor_info()
            if GAME_HWND is None or monitor is None:
                print("[display] Roblox monitor not found; resolution unchanged")
                return False, "Roblox monitor not found"
            device = monitor["device"]
            original_mode = None
        result = win32api.ChangeDisplaySettingsEx(device, original_mode, 0)
        ok = (result == win32con.DISP_CHANGE_SUCCESSFUL)
        if ok:
            _DISPLAY_RESTORE = None
            _clear_display_restore_file()
        print(
            f"[display] Revert {device}: "
            f"{'ok' if ok else f'failed (code {result})'}"
        )
        return ok, result


# ---------------- Button calibration ----------------

def _apply_button_overrides():
    """Apply user-calibrated button positions to the live BUTTONS dict, per stat."""
    global BUTTONS
    if not USER_BUTTON_OVERRIDES:
        return
    updated = dict(BUTTONS)
    for stat_name, pos in USER_BUTTON_OVERRIDES.items():
        if stat_name not in BUTTONS or not pos or len(pos) != 2:
            continue
        updated[stat_name] = (int(pos[0]), int(pos[1]))
    BUTTONS = updated


def _button_overrides_path():
    return os.path.join(JSON_DIR, "button_calibration.json")


def load_button_overrides():
    """Load saved per-stat button positions and apply them. Safe to call before JSON_DIR exists.

    Migration: legacy saves used keys 'health'/'physical'/'ki' (group names).
    On load, expand any legacy keys into the constituent stat names so old saves
    keep working until the user recalibrates."""
    global USER_BUTTON_OVERRIDES
    try:
        path = _button_overrides_path()
        if not os.path.exists(path):
            return
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return
        legacy_groups = {
            "health":   ["Health"],
            "physical": ["Agility", "Physical Damage"],
            "ki":       ["Ki Control", "Ki Damage"],
        }
        cleaned = {}
        for k, v in data.items():
            if not isinstance(v, (list, tuple)) or len(v) != 2:
                continue
            if k in BUTTONS:
                cleaned[k] = [int(v[0]), int(v[1])]
            elif k in legacy_groups:
                for stat_name in legacy_groups[k]:
                    cleaned[stat_name] = [int(v[0]), int(v[1])]
        USER_BUTTON_OVERRIDES = cleaned
        _apply_button_overrides()
        print(f"[calibrate] Loaded button overrides: {USER_BUTTON_OVERRIDES}")
    except Exception as e:
        print(f"[calibrate] load_button_overrides failed: {e}")


def save_button_overrides():
    try:
        os.makedirs(JSON_DIR, exist_ok=True)
        with open(_button_overrides_path(), "w", encoding="utf-8") as f:
            json.dump({
                "_coordinate_space": "reference-1920x1080",
                **USER_BUTTON_OVERRIDES,
            }, f, indent=2)
    except Exception as e:
        print(f"[calibrate] save_button_overrides failed: {e}")


def _calibration_monitor_geometry():
    """Return Tk geometry plus the live Roblox client rect.

    The overlay covers only the client, not its entire monitor. This keeps every
    captured point unambiguously client-local even on negative-origin displays.
    """
    update_game_window()
    if GAME_HWND is None or GAME_WIDTH <= 0 or GAME_HEIGHT <= 0:
        print("[calibrate] Roblox window not found; open Roblox before calibrating")
        return None, None
    client = {
        "left": int(GAME_OFFSET_X),
        "top": int(GAME_OFFSET_Y),
        "width": int(GAME_WIDTH),
        "height": int(GAME_HEIGHT),
    }
    geometry = (
        f"{client['width']}x{client['height']}"
        f"{client['left']:+d}{client['top']:+d}"
    )
    return geometry, client


def _capture_click_via_overlay(prompt_text):
    """Cover Roblox's client with a translucent overlay and capture one click. Returns
    canonical 1920x1080 client coordinates, or None if cancelled (Esc).

    Must be called from the thread that creates the Tk root (Tk isn't free-threaded).
    """
    try:
        import tkinter as tk
    except Exception as e:
        print(f"[calibrate] tkinter unavailable: {e}")
        return None

    geometry, client = _calibration_monitor_geometry()
    if geometry is None:
        return None
    result = {"pos": None}
    root = tk.Tk()
    try:
        root.overrideredirect(True)
        root.geometry(geometry)
        root.attributes("-alpha", 0.28)
        root.attributes("-topmost", True)
        root.config(cursor="crosshair", bg="black")

        # Prompt centered. Two labels so we can style the secondary line softer.
        wrap = tk.Frame(root, bg="black")
        wrap.place(relx=0.5, rely=0.5, anchor="center")
        tk.Label(wrap, text=prompt_text, fg="#e2e3e7", bg="black",
                 font=("Segoe UI", 22, "bold")).pack(pady=(0, 6))
        tk.Label(wrap, text="Click to capture  ·  Esc to cancel",
                 fg="#9a9da4", bg="black", font=("Segoe UI", 12)).pack()

        def on_click(event):
            result["pos"] = _screen_point_to_reference(
                root.winfo_pointerx(), root.winfo_pointery(), client
            )
            root.destroy()

        def on_esc(event):
            root.destroy()

        root.bind("<Button-1>", on_click)
        root.bind("<Escape>", on_esc)
        root.focus_force()
        root.mainloop()
    except Exception as e:
        print(f"[calibrate] overlay error: {e}")
        try:
            root.destroy()
        except Exception:
            pass
    return result["pos"]


def _run_button_calibration(stat_name, generation):
    """Worker thread: open the Tk overlay, save the captured coord."""
    global BUTTON_CALIBRATION_WAITING
    try:
        pos = _capture_click_via_overlay(f"Click the {stat_name} trait button in Roblox")
        if (pos
                and BUTTON_CALIBRATION_WAITING == stat_name
                and BUTTON_CALIBRATION_GENERATION == generation):
            USER_BUTTON_OVERRIDES[stat_name] = [int(pos[0]), int(pos[1])]
            _apply_button_overrides()
            save_button_overrides()
            print(f"[calibrate] {stat_name} -> {pos}")
    finally:
        if BUTTON_CALIBRATION_GENERATION == generation:
            BUTTON_CALIBRATION_WAITING = None


def _ui_calibrate_button_begin(stat_name):
    global BUTTON_CALIBRATION_WAITING, BUTTON_CALIBRATION_GENERATION
    if _ui_is_running():
        return False, "Stop the macro before calibrating."
    if stat_name not in BUTTONS:
        return False, f"Unknown stat: {stat_name}"
    if BUTTON_CALIBRATION_WAITING:
        return False, f"Already calibrating {BUTTON_CALIBRATION_WAITING}"
    BUTTON_CALIBRATION_GENERATION += 1
    generation = BUTTON_CALIBRATION_GENERATION
    BUTTON_CALIBRATION_WAITING = stat_name
    threading.Thread(
        target=_run_button_calibration,
        args=(stat_name, generation),
        daemon=True,
    ).start()
    return True, f"Waiting for click on {stat_name}"


def _ui_calibrate_button_cancel():
    # Tk overlay's Esc handler covers the user-facing cancel path. This is here so the UI
    # can clear the waiting state if it ever gets stuck — we just null the flag and let
    # the next click on the overlay get ignored (no-op since result wasn't recorded).
    global BUTTON_CALIBRATION_WAITING, BUTTON_CALIBRATION_GENERATION
    BUTTON_CALIBRATION_GENERATION += 1
    BUTTON_CALIBRATION_WAITING = None
    return True, "Calibration cancelled"


def _capture_region_via_overlay(prompt_text):
    """Overlay Roblox's client; user click-drags a rectangle. Returns a box in
    canonical 1920x1080 client coordinates, or None if cancelled (Esc) or if the
    rect is too small (< 4x4 live pixels is treated as an accidental click).
    """
    try:
        import tkinter as tk
    except Exception as e:
        print(f"[calibrate] tkinter unavailable: {e}")
        return None

    geometry, client = _calibration_monitor_geometry()
    if geometry is None:
        return None
    result = {"rect": None}
    state = {"start": None, "rect_id": None}
    root = tk.Tk()
    try:
        root.overrideredirect(True)
        root.geometry(geometry)
        root.attributes("-alpha", 0.28)
        root.attributes("-topmost", True)
        root.config(cursor="crosshair", bg="black")

        canvas = tk.Canvas(root, bg="black", highlightthickness=0, bd=0)
        canvas.pack(fill="both", expand=True)

        wrap = tk.Frame(root, bg="black")
        wrap.place(relx=0.5, rely=0.5, anchor="center")
        tk.Label(wrap, text=prompt_text, fg="#e2e3e7", bg="black",
                 font=("Segoe UI", 22, "bold")).pack(pady=(0, 6))
        tk.Label(wrap, text="Click and drag a rectangle  ·  Esc to cancel",
                 fg="#9a9da4", bg="black", font=("Segoe UI", 12)).pack()

        def on_down(event):
            state["start"] = (event.x_root, event.y_root)
            try:
                wrap.place_forget()
            except Exception:
                pass
            if state["rect_id"] is not None:
                canvas.delete(state["rect_id"])
            sx = event.x_root - client["left"]
            sy = event.y_root - client["top"]
            state["rect_id"] = canvas.create_rectangle(
                sx, sy, sx, sy, outline="#5fa8ff", width=2,
            )

        def on_drag(event):
            if state["start"] is None or state["rect_id"] is None:
                return
            sx_abs, sy_abs = state["start"]
            canvas.coords(
                state["rect_id"],
                sx_abs - client["left"],
                sy_abs - client["top"],
                event.x_root - client["left"],
                event.y_root - client["top"],
            )

        def on_up(event):
            if state["start"] is None:
                return
            sx, sy = state["start"]
            ex, ey = event.x_root, event.y_root
            left, top = min(sx, ex), min(sy, ey)
            width, height = abs(ex - sx), abs(ey - sy)
            if width >= 4 and height >= 4:
                result["rect"] = _screen_box_to_reference({
                    "top": int(top), "left": int(left),
                    "width": int(width), "height": int(height),
                }, client)
            root.destroy()

        def on_esc(event):
            root.destroy()

        # Bind on root so clicks on the prompt label (a child Frame sitting above the
        # canvas) still fire — Tk's child widgets would otherwise swallow the event.
        root.bind("<ButtonPress-1>", on_down)
        root.bind("<B1-Motion>", on_drag)
        root.bind("<ButtonRelease-1>", on_up)
        root.bind("<Escape>", on_esc)
        root.focus_force()
        root.mainloop()
    except Exception as e:
        print(f"[calibrate] overlay error: {e}")
        try:
            root.destroy()
        except Exception:
            pass
    return result["rect"]


def _apply_region_overrides():
    """Apply user-calibrated rectangles to HEALTH_BOX / AGILITY_BOX."""
    global HEALTH_BOX, AGILITY_BOX
    if not USER_REGION_OVERRIDES:
        return
    if "health_box" in USER_REGION_OVERRIDES:
        HEALTH_BOX = dict(USER_REGION_OVERRIDES["health_box"])
    if "agility_box" in USER_REGION_OVERRIDES:
        AGILITY_BOX = dict(USER_REGION_OVERRIDES["agility_box"])


def _region_overrides_path():
    return os.path.join(JSON_DIR, "region_calibration.json")


def load_region_overrides():
    global USER_REGION_OVERRIDES
    try:
        path = _region_overrides_path()
        if not os.path.exists(path):
            return
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            cleaned = {}
            for k, v in data.items():
                if k in REGION_NAMES and isinstance(v, dict):
                    if all(key in v for key in ("top", "left", "width", "height")):
                        cleaned[k] = {kk: int(v[kk]) for kk in ("top", "left", "width", "height")}
            USER_REGION_OVERRIDES = cleaned
            _apply_region_overrides()
            print(f"[calibrate] Loaded region overrides: {USER_REGION_OVERRIDES}")
    except Exception as e:
        print(f"[calibrate] load_region_overrides failed: {e}")


def save_region_overrides():
    try:
        os.makedirs(JSON_DIR, exist_ok=True)
        with open(_region_overrides_path(), "w", encoding="utf-8") as f:
            json.dump({
                "_coordinate_space": "reference-1920x1080",
                **USER_REGION_OVERRIDES,
            }, f, indent=2)
    except Exception as e:
        print(f"[calibrate] save_region_overrides failed: {e}")


def _run_region_calibration(region_key, generation):
    global REGION_CALIBRATION_WAITING
    try:
        label = REGION_NAMES.get(region_key, region_key)
        rect = _capture_region_via_overlay(f"Drag a rectangle over the {label} area")
        if (rect
                and REGION_CALIBRATION_WAITING == region_key
                and REGION_CALIBRATION_GENERATION == generation):
            USER_REGION_OVERRIDES[region_key] = rect
            _apply_region_overrides()
            save_region_overrides()
            print(f"[calibrate] {region_key} -> {rect}")
    finally:
        if REGION_CALIBRATION_GENERATION == generation:
            REGION_CALIBRATION_WAITING = None


def _ui_calibrate_region_begin(region_key):
    global REGION_CALIBRATION_WAITING, REGION_CALIBRATION_GENERATION
    if _ui_is_running():
        return False, "Stop the macro before calibrating."
    if region_key not in REGION_NAMES:
        return False, f"Unknown region: {region_key}"
    if REGION_CALIBRATION_WAITING:
        return False, f"Already calibrating {REGION_CALIBRATION_WAITING}"
    REGION_CALIBRATION_GENERATION += 1
    generation = REGION_CALIBRATION_GENERATION
    REGION_CALIBRATION_WAITING = region_key
    threading.Thread(
        target=_run_region_calibration,
        args=(region_key, generation),
        daemon=True,
    ).start()
    return True, f"Drag a rectangle for {region_key}"


def _ui_calibrate_region_cancel():
    global REGION_CALIBRATION_WAITING, REGION_CALIBRATION_GENERATION
    REGION_CALIBRATION_GENERATION += 1
    REGION_CALIBRATION_WAITING = None
    return True, "Region calibration cancelled"


class QuitException(Exception): pass


class SkipMinigameException(Exception):
    """Raised by check_exit() when MANUAL_NEXT_REQUESTED is set, so a minigame call
    deep in a green-observe wait can unwind immediately. The main loop catches it
    around minigame calls; the flag is NOT consumed here (the main loop's top-of-
    iteration handler consumes it and calls do_switch)."""
    pass


class SenzuControllerPause(SkipMinigameException):
    """Unwind stale main-controller work while Auto-Senzu owns the menus."""
    pass


def _destroy_cv_windows():
    """Close debug-HUD windows. No-op unless the HUD is on, and swallow errors:
    the frozen sidecar ships a headless OpenCV (no HighGUI), so an unconditional
    destroyAllWindows() raises 'The function is not implemented' — which is what
    crashed the macro on Stop."""
    if not SHOW_DEBUG_HUD:
        return
    try:
        cv2.destroyAllWindows()
    except Exception:
        pass


def check_exit():
    if _USER_STOP_LATCHED or UI_STOP_REQUESTED:
        # This flag is shared by the controller and the Auto-Senzu monitor.
        # Leave it set until the next Start so whichever thread observes Stop
        # first cannot consume it and allow the other thread to keep sending input.
        _record_run_outcome("stopped", "User requested stop")
        print("\n[STOP] UI requested stop. Input stopped.")
        _destroy_cv_windows()
        raise QuitException()
    if _controller_decisions_suspended():
        raise SenzuControllerPause()
    if MANUAL_NEXT_REQUESTED:
        # Don't consume the flag — let the main loop's top-of-iteration handler
        # call do_switch. We just need to unwind out of any inner minigame loop.
        raise SkipMinigameException()
    if PROGRESSION_COMPLETE_REQUESTED.is_set():
        # The main loop consumes this event and performs the category switch.
        # Raising here makes completion responsive inside blocking minigames.
        raise SkipMinigameException()
def safe_sleep(duration):
    """
    Sleep helper that stays responsive to configured stop/skip controls and keeps
    OpenCV windows alive.

    IMPORTANT: Use a small step for short sleeps so calls like safe_sleep(0.001)
    don't accidentally become ~50ms delays (which can make the Health bar feel late).
    """
    end = time.time() + max(0.0, float(duration))
    # Short sleeps need fine granularity; long sleeps can use coarser steps.
    step = 0.005 if duration <= 0.1 else 0.05
    while True:
        check_exit()
        if SHOW_DEBUG_HUD:
            cv2.waitKey(1)
        remaining = end - time.time()
        if remaining <= 0:
            break
        time.sleep(min(step, remaining))


def detect_yellow_progress(sct, state, geometry=None):
    """
    Returns: (yellow_pixels, raw_img_BGRA, mask_GRAY)
    """
    # Pixel-only sampling (1x1 grab)
    pixel = YELLOW_PIXEL_KI if "Ki" in state else YELLOW_PIXEL_STD
    raw = _grab_reference_box(
        sct,
        (int(pixel["x"]), int(pixel["y"]), 1, 1),
        geometry=geometry,
    )
    bgr = raw[:, :, :3]
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)

    # Pick HSV range based on game type (Ki can be darker due to overlay)
    if "Ki" in state and YELLOW_HSV_LOWER_KI is not None and YELLOW_HSV_UPPER_KI is not None:
        lower, upper = YELLOW_HSV_LOWER_KI, YELLOW_HSV_UPPER_KI
    else:
        lower, upper = YELLOW_HSV_LOWER_STD, YELLOW_HSV_UPPER_STD

    mask = cv2.inRange(hsv, lower, upper)

    return int(cv2.countNonZero(mask)), raw, mask


# --- Progression completion detector --------------------------------------
# We only need one authoritative fact: numerator == denominator. The old OCR
# templates were cropped from a scaled analysis sheet and scored ~0.2 on live
# GC, so they could neither report numbers nor switch safely. This detector
# finds the rendered (X/Y) glyphs and compares the two sides directly. It works
# for both 20/20 and 50/50 without guessing digit identities.
PROG_SEARCH_BOX = {"left": 700, "top": 300, "width": 700, "height": 500}
PROG_STABLE_COMPLETE_FRAMES = 3
PROG_POLL_INTERVAL_SEC = 0.25
PROG_AFTER_SWITCH_GRACE_SEC = 1.5
PROGRESSION_UI_LOST_TIMEOUT_SEC = 8.0
# Shorter settle window specifically for Auto-Senzu: long enough to skip the
# category-switch transition frames that spuriously fired Senzu on startup, but
# short enough that real low-HP recovery isn't noticeably delayed.
SENZU_AFTER_SWITCH_GRACE_SEC = 0.5


def _format_seconds(value):
    return f"{float(value):g}s"


def _progression_tracking_message(state):
    if NO_YELLOW_FALLBACK_ENABLED:
        return (
            f"[PROG] Tracking locked for {state}; "
            "yellow timeout fallback is no longer needed"
        )
    return (
        f"[PROG] Tracking locked for {state}; "
        "yellow timeout fallback remains disabled"
    )


def _first_yellow_message():
    if NO_YELLOW_FALLBACK_ENABLED:
        return (
            "[YELLOW] First yellow after switch; "
            f"{_format_seconds(NO_YELLOW_TIMEOUT_SEC)} fallback timeout available "
            "until progression tracking locks"
        )
    return "[YELLOW] First yellow after switch; fallback timeout is disabled"


def _training_order_result(skipped_stats):
    skipped = [str(stat) for stat in skipped_stats if str(stat)]
    if skipped:
        names = ", ".join(skipped)
        return "incomplete", f"Training order ended with skipped stats: {names}"
    return "completed", "Training order completed"


def _glyph_iou(mask, first, second):
    def normalized(component):
        x, y, w, h, _area = component
        glyph = mask[y:y + h, x:x + w]
        return cv2.resize(glyph, (32, 32), interpolation=cv2.INTER_NEAREST) > 0

    a = normalized(first)
    b = normalized(second)
    union = np.count_nonzero(a | b)
    return (np.count_nonzero(a & b) / union) if union else 0.0


def _progression_completion_from_frame(frame):
    """Return True for X/X, False for a detected incomplete label, else None."""
    bgr = frame[:, :, :3]
    channel_min = bgr.min(axis=2)
    channel_spread = bgr.max(axis=2) - channel_min
    mask = ((channel_min > 190) & (channel_spread < 45)).astype(np.uint8) * 255
    _count, _labels, stats, _centroids = cv2.connectedComponentsWithStats(mask, 8)
    components = []
    for x, y, w, h, area in stats[1:]:
        if 3 <= w <= 30 and 18 <= h <= 44 and 30 <= area <= 550:
            components.append((int(x), int(y), int(w), int(h), int(area)))

    parens = [c for c in components if c[3] >= 30 and c[2] <= 13]
    for opening in parens:
        ox, oy, ow, oh, _ = opening
        for closing in parens:
            cx, cy, _cw, ch, _ = closing
            inner_width = cx - (ox + ow)
            if not (55 <= inner_width <= 130):
                continue
            if abs(oy - cy) > 4 or abs(oh - ch) > 5:
                continue

            # Require the nearby "Progression" word so unrelated UI text such
            # as (0/20) cannot become an authoritative category switch.
            word_glyphs = [
                c for c in components
                if ox - 250 <= c[0] < ox - 5 and abs((c[1] + c[3]) - (oy + oh)) <= 10
            ]
            if len(word_glyphs) < 8:
                continue
            word_left = min(c[0] for c in word_glyphs)
            word_right = max(c[0] + c[2] for c in word_glyphs)
            if not 150 <= word_right - word_left <= 240:
                continue

            inside = [
                c for c in components
                if c not in (opening, closing)
                and c[0] >= ox + ow and c[0] + c[2] <= cx
            ]
            slashes = [c for c in inside if c[2] <= 10 and 21 <= c[3] <= 31]
            for slash in slashes:
                sx = slash[0]
                left = sorted((c for c in inside if c[0] + c[2] <= sx), key=lambda c: c[0])
                right = sorted((c for c in inside if c[0] > sx), key=lambda c: c[0])
                if not (1 <= len(left) <= 2 and 1 <= len(right) <= 2):
                    continue
                if len(left) != len(right):
                    return False
                return all(_glyph_iou(mask, a, b) >= 0.82 for a, b in zip(left, right))
    return None


def read_progression_completion(sct, geometry=None):
    return _progression_completion_from_frame(
        _grab_reference_box(sct, PROG_SEARCH_BOX, geometry=geometry)
    )


def _progression_ui_loss_state(
    current_state,
    tracked_state,
    completion,
    now,
    missing_since,
    suspended=False,
):
    """Track continuous loss only after progression was positively identified."""
    if (
        not current_state
        or tracked_state != current_state
        or completion is not None
        or suspended
    ):
        return None, False
    if missing_since is None:
        return now, False
    return (
        missing_since,
        now - missing_since >= PROGRESSION_UI_LOST_TIMEOUT_SEC,
    )


def _stop_for_training_ui_loss():
    global UI_STOP_REQUESTED
    reason = (
        "GC training interface disappeared; the character may have left "
        "the chamber or the session expired"
    )
    _record_run_outcome("error", reason, retryable=False)
    UI_STOP_REQUESTED = True
    print(f"[MONITOR] {reason}; stopping macro safely")


def _background_game_monitor(stop_event):
    """Continuously watch progression and critical HP during every minigame."""
    global PROGRESSION_TRACKED_STATE, PROGRESSION_COMPLETE
    global PROGRESSION_STATE_STARTED_AT

    tracked_state = None
    complete_streak = 0
    completion_sent = False
    red_streak = 0
    red_since = None
    red_handled = False
    death_streak = 0
    last_error_log = 0.0
    progression_missing_since = None

    with mss.MSS() as sct:
        while not stop_event.is_set():
            now = time.time()
            try:
                if not update_game_window():
                    stop_event.wait(PROG_POLL_INTERVAL_SEC)
                    continue
                game_geometry = _game_geometry()
                current_state = CURRENT_TRAINING_STATE
                try:
                    death_visible = bool(
                        current_state
                        and _gc_death_dialog_visible(sct, game_geometry)
                    )
                except Exception:
                    death_visible = False
                if death_visible:
                    death_streak += 1
                else:
                    death_streak = 0
                if death_streak >= 3:
                    _stop_for_game_death()
                    break
                if current_state != tracked_state:
                    tracked_state = current_state
                    complete_streak = 0
                    completion_sent = False
                    progression_missing_since = None
                    PROGRESSION_TRACKED_STATE = None
                    PROGRESSION_COMPLETE = None

                if (current_state
                        and not completion_sent
                        and now - PROGRESSION_STATE_STARTED_AT >= PROG_AFTER_SWITCH_GRACE_SEC):
                    completion = read_progression_completion(sct, game_geometry)
                    if completion is not None:
                        progression_missing_since = None
                        if PROGRESSION_TRACKED_STATE != current_state:
                            print(_progression_tracking_message(current_state))
                        PROGRESSION_TRACKED_STATE = current_state
                        PROGRESSION_COMPLETE = False
                        complete_streak = complete_streak + 1 if completion else 0
                        if complete_streak >= PROG_STABLE_COMPLETE_FRAMES:
                            PROGRESSION_COMPLETE = True
                            completion_sent = True
                            print(
                                f"[PROG] {current_state} complete "
                                f"({PROG_STABLE_COMPLETE_FRAMES} stable reads)"
                            )
                            PROGRESSION_COMPLETE_REQUESTED.set()
                    else:
                        complete_streak = 0
                        progression_missing_since, progression_ui_lost = (
                            _progression_ui_loss_state(
                                current_state,
                                PROGRESSION_TRACKED_STATE,
                                completion,
                                now,
                                progression_missing_since,
                                suspended=(
                                    TRAINING_MENU_VISIBLE
                                    or CONTROLLER_PAUSED
                                    or SENZU_CONTROLLER_ACTIVE.is_set()
                                    or SENZU_CONTROLLER_RESUME_REQUIRED.is_set()
                                ),
                            )
                        )
                        if progression_ui_lost:
                            _stop_for_training_ui_loss()
                            break

                # Post-switch settle window (like the progression check above): right
                # after a category starts the HP-fill box can misread as red while the
                # UI is mid-transition, which spuriously fired Auto-Senzu on startup
                # (the visible "it just presses Tab" symptom). Suppress the check for
                # SENZU_AFTER_SWITCH_GRACE_SEC; a real low-HP emergency still needs
                # 2 red frames + SENZU_DELAY_SEC, so it lands just after the window.
                if (SENZU_ENABLED and not SENZU_DISABLED_FOR_RUN and current_state
                        and now - PROGRESSION_STATE_STARTED_AT >= SENZU_AFTER_SWITCH_GRACE_SEC):
                    if _hp_bar_is_red(sct):
                        red_streak += 1
                        if red_since is None:
                            red_since = now
                    else:
                        red_streak = 0
                        red_since = None
                        red_handled = False
                    if (red_streak >= 2
                            and not red_handled
                            and red_since is not None
                            and now - red_since >= SENZU_DELAY_SEC):
                        red_handled = True
                        red_streak = 0
                        red_since = None
                        PROGRESSION_COMPLETE_REQUESTED.clear()
                        SENZU_CONTROLLER_ACTIVE.set()
                        SENZU_CONTROLLER_RESUME_REQUIRED.set()
                        try:
                            eat_senzu(sct)
                        except QuitException:
                            # User Stop is normal control flow. Let the outer
                            # handler end this monitor without turning the run
                            # into an Auto-Senzu error.
                            raise
                        except Exception as e:
                            _stop_for_senzu_failure(
                                f"Unexpected {e.__class__.__name__}: {e}"
                            )
                            raise
                        finally:
                            SENZU_CONTROLLER_ACTIVE.clear()
                        # Menu navigation invalidates any completion samples
                        # collected before recovery. Re-lock from fresh frames.
                        complete_streak = 0
                        completion_sent = False
                        PROGRESSION_TRACKED_STATE = None
                        PROGRESSION_COMPLETE = None
                        PROGRESSION_COMPLETE_REQUESTED.clear()
                        PROGRESSION_STATE_STARTED_AT = time.time()
                else:
                    red_streak = 0
                    red_since = None
                    red_handled = False
            except QuitException:
                break
            except Exception as e:
                # Capture can fail briefly while Roblox changes display mode. Keep
                # the monitor alive, but rate-limit the diagnostic.
                if now - last_error_log >= 5.0:
                    last_error_log = now
                    print(f"[MONITOR] {e.__class__.__name__}: {e}")

            stop_event.wait(PROG_POLL_INTERVAL_SEC)


def _start_background_game_monitor():
    global _background_monitor_stop, _background_monitor_thread
    global PROGRESSION_TRACKED_STATE, PROGRESSION_COMPLETE
    global _AFTER_ACTIONS_BLOCKED
    PROGRESSION_COMPLETE_REQUESTED.clear()
    PROGRESSION_TRACKED_STATE = None
    PROGRESSION_COMPLETE = None
    SENZU_CONTROLLER_ACTIVE.clear()
    SENZU_CONTROLLER_RESUME_REQUIRED.clear()
    # Reuse only within an active run. _run_macro_safe stops this thread when
    # the macro ends, so Idle can never inject game input.
    if _background_monitor_thread is not None and _background_monitor_thread.is_alive():
        if _background_monitor_stop is not None and _background_monitor_stop.is_set():
            _AFTER_ACTIONS_BLOCKED = True
            print("[SAFETY] Previous game-state monitor is still stopping")
            return False
        return True
    if _background_monitor_stop is not None:
        _background_monitor_stop.set()
    _background_monitor_stop = threading.Event()
    _background_monitor_thread = threading.Thread(
        target=_background_game_monitor,
        args=(_background_monitor_stop,),
        daemon=True,
        name="game-state-monitor",
    )
    _background_monitor_thread.start()
    return True


def _stop_background_game_monitor():
    global _background_monitor_stop, _background_monitor_thread
    global _AFTER_ACTIONS_BLOCKED
    stop_event = _background_monitor_stop
    monitor_thread = _background_monitor_thread
    if stop_event is not None:
        stop_event.set()
    if (monitor_thread is not None
            and monitor_thread is not threading.current_thread()
            and monitor_thread.is_alive()):
        monitor_thread.join(timeout=2.0)
    if monitor_thread is not None and monitor_thread.is_alive():
        _AFTER_ACTIONS_BLOCKED = True
        print("[SAFETY] Game-state monitor did not stop; after-run actions disabled")
        return False
    _background_monitor_stop = None
    _background_monitor_thread = None
    PROGRESSION_COMPLETE_REQUESTED.clear()
    return True


def detect_training_menu(sct, monitor, template):
    """Return whether the stable left-side Training Mode header is visible.

    This fixed UI header is unchanged across white HTC, blue/cloud HTC, and red
    GC. The result is shadow-only until live logs confirm the recorded behavior.
    """
    if template is None:
        return False, 0.0
    frame = _grab_reference_box(sct, TRAINING_MENU_BOX, geometry=monitor)
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    score = cv2.minMaxLoc(
        cv2.matchTemplate(gray, template, cv2.TM_CCOEFF_NORMED)
    )[1]
    return score >= TRAINING_MENU_MATCH_THRESHOLD, float(score)


def _normalize_gravity_target(value, *, strict=False):
    """Accept only the real GC steps. Invalid persisted values safely mean Off."""
    try:
        number = _finite_int(value)
    except (TypeError, ValueError, OverflowError):
        if strict:
            raise ValueError("GC gravity must be 0, 10, 20, ... 100")
        return 0
    if number not in range(0, 101, 10):
        if strict:
            raise ValueError("GC gravity must be 0, 10, 20, ... 100")
        return 0
    return number


def _normalize_after_run_game_action(value, *, strict=False):
    action = str(value or "none").strip().lower()
    if action not in AFTER_RUN_GAME_ACTIONS:
        if strict:
            choices = ", ".join(sorted(AFTER_RUN_GAME_ACTIONS))
            raise ValueError(f"After-run game action must be one of: {choices}")
        return "none"
    return action


def _normalize_auto_retry_recovery_mode(value, *, strict=False):
    mode = str(value or "reset").strip().lower()
    if mode not in AUTO_RETRY_RECOVERY_MODES:
        if strict:
            choices = ", ".join(sorted(AUTO_RETRY_RECOVERY_MODES))
            raise ValueError(f"Recovery mode must be one of: {choices}")
        return "reset"
    return mode


def _gravity_mask_from_frame(frame):
    bgr = frame[:, :, :3]
    channel_min = bgr.min(axis=2)
    channel_spread = bgr.max(axis=2) - channel_min
    return ((channel_min > 190) & (channel_spread < 55)).astype(np.uint8) * 255


def _gravity_control_glyph_count(mask):
    count, _labels, stats, _centroids = cv2.connectedComponentsWithStats(mask, 8)
    glyphs = []
    for x, y, w, h, area in stats[1:count]:
        if 2 <= w <= 22 and 10 <= h <= 24 and 20 <= area <= 320:
            glyphs.append((int(x), int(y), int(w), int(h), int(area)))
    if not 2 <= len(glyphs) <= 4:
        return 0
    baselines = [y + h for _x, y, _w, h, _area in glyphs]
    return len(glyphs) if max(baselines) - min(baselines) <= 4 else 0


def _load_gravity_templates():
    templates = {}
    for value in range(0, 101, 10):
        path = os.path.join(BASE_DIR, "gravity", f"gravity_{value}.png")
        template = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
        if template is None:
            return {}
        templates[value] = template > 0
    return templates


def _gravity_value_from_mask(mask, templates):
    observed = mask > 0
    if not _gravity_control_glyph_count(mask):
        return None, 0.0, 0.0
    scores = []
    for value, template in templates.items():
        if template.shape != observed.shape:
            continue
        union = np.count_nonzero(observed | template)
        score = np.count_nonzero(observed & template) / union if union else 0.0
        scores.append((float(score), int(value)))
    if not scores:
        return None, 0.0, 0.0
    scores.sort(reverse=True)
    best_score, best_value = scores[0]
    second_score = scores[1][0] if len(scores) > 1 else 0.0
    if not _gravity_match_is_reliable(best_score, second_score):
        return None, best_score, second_score
    return best_value, best_score, second_score


def _gravity_match_is_reliable(best_score, second_score):
    return bool(
        best_score >= GRAVITY_MATCH_MIN
        and best_score - second_score >= GRAVITY_MATCH_MARGIN_MIN
    )


def _gravity_advance_is_safe(current, observed, target):
    return bool(
        observed in range(0, 101, 10)
        and current < observed <= target
    )


def _read_gc_gravity(sct, geometry, templates):
    frame = _grab_reference_box(sct, GRAVITY_LABEL_BOX, geometry=geometry)
    mask = _gravity_mask_from_frame(frame)
    value, score, second = _gravity_value_from_mask(mask, templates)
    return value, score, second, _gravity_control_glyph_count(mask) > 0


def _raise_gc_gravity(sct, geometry, target):
    """Raise GC gravity to `target`; never lower, wrap, or click in HTC."""
    target = _normalize_gravity_target(target, strict=True)
    if target == 0:
        print("[GRAVITY] Auto-raise is off; leaving the current gravity unchanged")
        return True

    templates = _load_gravity_templates()
    if len(templates) != 11:
        print("[GRAVITY] Gravity label templates are missing; leaving gravity unchanged")
        return False

    robust_move(*_reference_point(*GRAVITY_POINTER_PARK_POINT, geometry))
    safe_sleep(0.10)
    current, score, second, control_present = _read_gc_gravity(
        sct, geometry, templates
    )
    if not control_present:
        print("[GRAVITY] GC control not found; HTC detected, leaving gravity unchanged")
        return True
    if current is None:
        print(
            "[GRAVITY] GC control is present but its value was not reliable "
            f"(best={score:.3f}, second={second:.3f}); leaving gravity unchanged"
        )
        return False
    if current >= target:
        print(f"[GRAVITY] Current {current}G already meets target {target}G; no clicks")
        return True

    start_value = current
    click_point = _reference_point(*GRAVITY_CLICK_POINT, geometry)
    park_point = _reference_point(*GRAVITY_POINTER_PARK_POINT, geometry)
    while current < target:
        expected = current + 10
        accepted = False
        for attempt in range(1, 4):
            check_exit()
            click_at(*click_point)
            robust_move(*park_point)
            deadline = time.time() + 0.8
            while time.time() < deadline:
                safe_sleep(0.05)
                observed, score, second, control_present = _read_gc_gravity(
                    sct, geometry, templates
                )
                if observed == expected:
                    current = observed
                    accepted = True
                    break
                if _gravity_advance_is_safe(current, observed, target):
                    print(
                        f"[GRAVITY] Delayed redraw skipped {expected}G but confirmed "
                        f"safe forward progress at {observed}G"
                    )
                    current = observed
                    accepted = True
                    break
                if observed not in (None, current):
                    print(
                        f"[GRAVITY] Expected {expected}G but read {observed}G; "
                        "refusing further clicks"
                    )
                    return False
            if accepted:
                break
            print(
                f"[GRAVITY] Click {current}G -> {expected}G not confirmed; "
                f"retrying ({attempt}/3)"
            )
        if not accepted:
            print(f"[GRAVITY] Could not confirm {expected}G; leaving it at {current}G")
            return False

    print(f"[GRAVITY] Raised {start_value}G -> {current}G; target confirmed")
    return True


def _cycle_gc_gravity_to_zero(sct, geometry, *, after_run=False):
    """Cycle the GC control forward until it wraps to 0G; never click in HTC."""
    if after_run:
        _ensure_after_run_active()
    templates = _load_gravity_templates()
    if len(templates) != 11:
        print("[GRAVITY] Gravity label templates are missing; cannot set 0G")
        return False

    park_at = _reference_point(*GRAVITY_POINTER_PARK_POINT, geometry)
    if after_run:
        with _input_lock:
            _ensure_after_run_active()
            _user32.SetCursorPos(*park_at)
        time.sleep(0.10)
        _ensure_after_run_active()
    else:
        robust_move(*park_at)
        safe_sleep(0.10)
    current, score, second, control_present = _read_gc_gravity(
        sct, geometry, templates
    )
    if not control_present:
        print("[GRAVITY] GC control not found; HTC detected, 0G fallback ignored")
        return True
    if current is None:
        print(
            "[GRAVITY] GC control is present but its value was not reliable "
            f"(best={score:.3f}, second={second:.3f}); cannot set 0G"
        )
        return False
    if current == 0:
        print("[GRAVITY] Gravity is already 0G")
        return True

    start_value = current
    click_point = _reference_point(*GRAVITY_CLICK_POINT, geometry)
    park_point = _reference_point(*GRAVITY_POINTER_PARK_POINT, geometry)
    while current != 0:
        expected = 0 if current == 100 else current + 10
        accepted = False
        for attempt in range(1, 4):
            if after_run:
                _ensure_after_run_active()
                with _input_lock:
                    _ensure_after_run_active()
                    _click_sendinput_abs(*click_point)
                    _ensure_after_run_active()
                    _user32.SetCursorPos(*park_point)
            else:
                check_exit()
                click_at(*click_point)
                robust_move(*park_point)
            deadline = time.time() + 0.8
            while time.time() < deadline:
                if after_run:
                    time.sleep(0.05)
                    _ensure_after_run_active()
                else:
                    safe_sleep(0.05)
                observed, score, second, control_present = _read_gc_gravity(
                    sct, geometry, templates
                )
                if observed == expected:
                    current = observed
                    accepted = True
                    break
                if observed not in (None, current):
                    print(
                        f"[GRAVITY] Expected {expected}G but read {observed}G; "
                        "refusing further clicks"
                    )
                    return False
            if accepted:
                break
            print(
                f"[GRAVITY] Click {current}G -> {expected}G not confirmed; "
                f"retrying ({attempt}/3)"
            )
        if not accepted:
            print(f"[GRAVITY] Could not confirm {expected}G; leaving it at {current}G")
            return False

    print(f"[GRAVITY] Lowered {start_value}G -> 0G")
    return True


def _hsv_bounds_from_sample(hsv):
    h, s, v = [int(x) for x in hsv]
    lower = np.array(
        [max(0, h - YELLOW_H_TOL), max(0, s - YELLOW_S_TOL), max(0, v - YELLOW_V_TOL)],
        dtype=np.uint8,
    )
    upper = np.array(
        [min(179, h + YELLOW_H_TOL), min(255, s + YELLOW_S_TOL), min(255, v + YELLOW_V_TOL)],
        dtype=np.uint8,
    )
    return lower, upper


## NOTE:
## Yellow calibration JSON was removed on purpose for distribution.
## Coordinates are hard-coded so they can't be changed by accident.


def load_master_config():
    """
    Loads user settings from `config/macro_config.json` (safe/optional).
    If missing, creates a default config file using the values in this script.
    """
    global START_DELAY, GC_GRAVITY_TARGET_G, PREVENT_SLEEP_WHILE_RUNNING
    global RESTORE_FULLSCREEN_ON_START, DISPLAY_CONFIRM_CHANGES
    global SHUTDOWN_PC_WHEN_FINISHED, AFTER_RUN_GAME_ACTION, AFTER_RUN_ON_FAILURE
    global AUTO_RETRY_ON_FAILURE, AUTO_RETRY_MAX_ATTEMPTS
    global AUTO_RETRY_RECOVERY_MODE, AUTO_RETRY_WALK_OUT, AUTO_RETRY_WALK_SECONDS
    global DIAGNOSTIC_MODE
    global NEW_GAME_WAIT, NO_YELLOW_TIMEOUT_SEC
    global MANUAL_NEXT_KEY, START_STOP_HOTKEY
    global HEALTH_HIT_COOLDOWN_SEC, HEALTH_MODE
    global KEY_PRESS_DELAY, STABILIZE_DELAY, POST_COMBO_DELAY
    global TRAINING_ORDER_CUSTOM, AGILITY_MODE
    global AGILITY_GREEN_OBSERVE_SEC, AGILITY_INTER_STRING_WAIT_SEC, AGILITY_AFTER_GREEN_SETTLE_SEC

    def _apply(data):
        # IMPORTANT: this is a nested function; we must declare globals here
        # or assignments will create locals and the config won't actually apply.
        global START_DELAY, GC_GRAVITY_TARGET_G, PREVENT_SLEEP_WHILE_RUNNING
        global RESTORE_FULLSCREEN_ON_START, DISPLAY_CONFIRM_CHANGES
        global SHUTDOWN_PC_WHEN_FINISHED, AFTER_RUN_GAME_ACTION, AFTER_RUN_ON_FAILURE
        global AUTO_RETRY_ON_FAILURE, AUTO_RETRY_MAX_ATTEMPTS
        global AUTO_RETRY_RECOVERY_MODE, AUTO_RETRY_WALK_OUT, AUTO_RETRY_WALK_SECONDS
        global DIAGNOSTIC_MODE
        global NEW_GAME_WAIT, NO_YELLOW_TIMEOUT_SEC
        global MANUAL_NEXT_KEY, START_STOP_HOTKEY, PAUSE_HOTKEY
        global HEALTH_HIT_COOLDOWN_SEC, HEALTH_MODE
        global KEY_PRESS_DELAY, STABILIZE_DELAY, POST_COMBO_DELAY
        global TRAINING_ORDER_CUSTOM, AGILITY_MODE
        global AGILITY_GREEN_OBSERVE_SEC, AGILITY_INTER_STRING_WAIT_SEC, AGILITY_AFTER_GREEN_SETTLE_SEC

        if not isinstance(data, dict):
            return
        # Preferred (clear) keys
        if "start_delay_sec" in data:
            START_DELAY = _bounded_float(data["start_delay_sec"], 0.0, 30.0)
        if "gc_gravity_target_g" in data:
            GC_GRAVITY_TARGET_G = _normalize_gravity_target(
                data["gc_gravity_target_g"]
            )
        if "prevent_sleep_while_running" in data:
            PREVENT_SLEEP_WHILE_RUNNING = _ui_bool(
                data["prevent_sleep_while_running"]
            )
        if "restore_fullscreen_on_start" in data:
            RESTORE_FULLSCREEN_ON_START = _ui_bool(
                data["restore_fullscreen_on_start"]
            )
        if "display_confirm_changes" in data:
            DISPLAY_CONFIRM_CHANGES = _ui_bool(data["display_confirm_changes"])
        if "shutdown_pc_when_finished" in data:
            SHUTDOWN_PC_WHEN_FINISHED = _ui_bool(
                data["shutdown_pc_when_finished"]
            )
        if "after_run_game_action" in data:
            AFTER_RUN_GAME_ACTION = _normalize_after_run_game_action(
                data["after_run_game_action"]
            )
        if "after_run_on_failure" in data:
            AFTER_RUN_ON_FAILURE = _ui_bool(data["after_run_on_failure"])
        if "auto_retry_on_failure" in data:
            AUTO_RETRY_ON_FAILURE = _ui_bool(data["auto_retry_on_failure"])
        if "auto_retry_max_attempts" in data:
            AUTO_RETRY_MAX_ATTEMPTS = _bounded_int(
                data["auto_retry_max_attempts"], 1, 10
            )
        if "auto_retry_recovery_mode" in data:
            AUTO_RETRY_RECOVERY_MODE = _normalize_auto_retry_recovery_mode(
                data["auto_retry_recovery_mode"]
            )
        if "auto_retry_walk_out" in data:
            AUTO_RETRY_WALK_OUT = _ui_bool(data["auto_retry_walk_out"])
        if "auto_retry_walk_seconds" in data:
            AUTO_RETRY_WALK_SECONDS = _bounded_float(
                data["auto_retry_walk_seconds"], 0.5, 10.0
            )
        if "diagnostic_mode" in data:
            DIAGNOSTIC_MODE = _ui_bool(data["diagnostic_mode"])
        if "after_switch_wait_sec" in data:
            NEW_GAME_WAIT = _bounded_float(data["after_switch_wait_sec"], 0.0, 10.0)
        if "no_yellow_timeout_sec" in data:
            NO_YELLOW_TIMEOUT_SEC = _bounded_float(data["no_yellow_timeout_sec"], 1.0, 300.0)
        if "no_yellow_fallback_enabled" in data:
            global NO_YELLOW_FALLBACK_ENABLED
            NO_YELLOW_FALLBACK_ENABLED = _ui_bool(data["no_yellow_fallback_enabled"])

        if "manual_next_key" in data:
            MANUAL_NEXT_KEY = _normalize_hotkey_name(data["manual_next_key"]) or MANUAL_NEXT_KEY
        if "start_stop_hotkey" in data:
            START_STOP_HOTKEY = _normalize_hotkey_name(data["start_stop_hotkey"]) or START_STOP_HOTKEY
        if "pause_hotkey" in data:
            PAUSE_HOTKEY = _normalize_hotkey_name(data["pause_hotkey"]) or PAUSE_HOTKEY

        if "health_hit_cooldown_sec" in data:
            HEALTH_HIT_COOLDOWN_SEC = _bounded_float(data["health_hit_cooldown_sec"], 0.0, 5.0)
        if "health_mode" in data:
            HEALTH_MODE = str(data["health_mode"]) if data["health_mode"] in ("v1_legacy", "v2_track") else "v2_track"
        if "wasd_key_press_delay_sec" in data:
            KEY_PRESS_DELAY = _bounded_float(data["wasd_key_press_delay_sec"], 0.0, 1.0)
        elif "wsad_key_press_delay_sec" in data:
            KEY_PRESS_DELAY = _bounded_float(data["wsad_key_press_delay_sec"], 0.0, 1.0)
        if "wasd_stabilize_delay_sec" in data:
            STABILIZE_DELAY = _bounded_float(data["wasd_stabilize_delay_sec"], 0.0, 5.0)
        elif "wsad_stabilize_delay_sec" in data:
            STABILIZE_DELAY = _bounded_float(data["wsad_stabilize_delay_sec"], 0.0, 5.0)
        if "wasd_post_burst_delay_sec" in data:
            POST_COMBO_DELAY = _bounded_float(data["wasd_post_burst_delay_sec"], 0.0, 5.0)
        elif "wsad_post_burst_delay_sec" in data:
            POST_COMBO_DELAY = _bounded_float(data["wsad_post_burst_delay_sec"], 0.0, 5.0)
        if "agility_mode" in data:
            AGILITY_MODE = str(data["agility_mode"]) if data["agility_mode"] in ("v1", "v2") else "v2"
        if "agility_green_observe_sec" in data:
            AGILITY_GREEN_OBSERVE_SEC = _bounded_float(data["agility_green_observe_sec"], 0.1, 5.0)
        if "agility_inter_string_wait_sec" in data:
            AGILITY_INTER_STRING_WAIT_SEC = _bounded_float(data["agility_inter_string_wait_sec"], 0.1, 10.0)
        if "agility_after_green_settle_sec" in data:
            AGILITY_AFTER_GREEN_SETTLE_SEC = _bounded_float(data["agility_after_green_settle_sec"], 0.0, 5.0)
        if "training_order" in data:
            TRAINING_ORDER_CUSTOM = _sanitize_training_order(data["training_order"])
        if "ki_v8_click_delay_sec" in data:
            global KI_V8_CLICK_DELAY_SEC
            KI_V8_CLICK_DELAY_SEC = _bounded_float(data["ki_v8_click_delay_sec"], 0.0, 0.7)
        if "ki_v8_mode" in data:
            global KI_V8_MODE
            v = str(data["ki_v8_mode"])
            if v in ("v1_time", "v2_ring"):
                KI_V8_MODE = v
        if "ki_v8_v2_target_r_factor" in data:
            global KI_V8_V2_TARGET_R_FACTOR
            KI_V8_V2_TARGET_R_FACTOR = min(
                3.0, max(KI_V8_V2_R_MIN_FACTOR, float(data["ki_v8_v2_target_r_factor"]))
            )
        if "ki_v8_v2_brightness_threshold" in data:
            global KI_V8_V2_BRIGHTNESS_THRESHOLD
            KI_V8_V2_BRIGHTNESS_THRESHOLD = _bounded_int(
                data["ki_v8_v2_brightness_threshold"], 50, 255
            )
        if "ki_v8_v2_bright_count_threshold" in data:
            global KI_V8_V2_BRIGHT_COUNT_THRESHOLD
            KI_V8_V2_BRIGHT_COUNT_THRESHOLD = _bounded_int(
                data["ki_v8_v2_bright_count_threshold"], 1, 32
            )
        if "ki_latency_comp_ms" in data:
            global KI_LATENCY_COMP_MS
            KI_LATENCY_COMP_MS = min(250, max(0, int(data["ki_latency_comp_ms"])))
        if "senzu_enabled" in data:
            global SENZU_ENABLED
            SENZU_ENABLED = bool(data["senzu_enabled"])
        if "senzu_slot" in data:
            global SENZU_SLOT
            SENZU_SLOT = min(4, max(1, int(data["senzu_slot"])))
        if "senzu_delay_sec" in data:
            global SENZU_DELAY_SEC
            SENZU_DELAY_SEC = _bounded_float(data["senzu_delay_sec"], 0.0, 30.0)
        if "senzu_recovery_timeout_sec" in data:
            global SENZU_RECOVERY_TIMEOUT_SEC
            SENZU_RECOVERY_TIMEOUT_SEC = _bounded_float(
                data["senzu_recovery_timeout_sec"], 1.0, 30.0
            )
        if "senzu_preference_mode" in data:
            global SENZU_PREFERENCE_MODE
            new_preference = _normalize_senzu_preference(
                data["senzu_preference_mode"]
            )
            if new_preference != SENZU_PREFERENCE_MODE:
                _invalidate_senzu_row_cache()
            SENZU_PREFERENCE_MODE = new_preference
        if "senzu_zero_gravity_on_empty" in data:
            global SENZU_ZERO_GRAVITY_ON_EMPTY
            SENZU_ZERO_GRAVITY_ON_EMPTY = _ui_bool(
                data["senzu_zero_gravity_on_empty"]
            )

        # Back-compat: older uppercase keys from previous builds
        if "START_DELAY" in data:
            START_DELAY = _bounded_float(data["START_DELAY"], 0.0, 30.0)
        if "NEW_GAME_WAIT" in data:
            NEW_GAME_WAIT = _bounded_float(data["NEW_GAME_WAIT"], 0.0, 10.0)

        # Back-compat: older yellow keys
        if "YELLOW_TIMEOUT_SEC" in data and "no_yellow_timeout_sec" not in data:
            NO_YELLOW_TIMEOUT_SEC = _bounded_float(data["YELLOW_TIMEOUT_SEC"], 1.0, 300.0)
        if "YELLOW_MISSING_TIMEOUT_SEC" in data and "no_yellow_timeout_sec" not in data:
            NO_YELLOW_TIMEOUT_SEC = _bounded_float(data["YELLOW_MISSING_TIMEOUT_SEC"], 1.0, 300.0)

        # Back-compat: older timing names
        if "MANUAL_NEXT_KEY" in data:
            MANUAL_NEXT_KEY = str(data["MANUAL_NEXT_KEY"]).lower()[:1] or MANUAL_NEXT_KEY
        if "AGILITY_KEY_HOLD" in data:
            KEY_PRESS_DELAY = _bounded_float(data["AGILITY_KEY_HOLD"], 0.0, 1.0)
        if "AGILITY_STABILIZE" in data:
            STABILIZE_DELAY = _bounded_float(data["AGILITY_STABILIZE"], 0.0, 5.0)
        if "AGILITY_POST_BURST" in data:
            POST_COMBO_DELAY = _bounded_float(data["AGILITY_POST_BURST"], 0.0, 5.0)

        # Back-compat: prior "agility_*" keys
        if ("wasd_key_press_delay_sec" not in data) and ("wsad_key_press_delay_sec" not in data) and ("agility_key_press_delay_sec" in data):
            KEY_PRESS_DELAY = _bounded_float(data["agility_key_press_delay_sec"], 0.0, 1.0)
        if ("wasd_stabilize_delay_sec" not in data) and ("wsad_stabilize_delay_sec" not in data) and ("agility_stabilize_delay_sec" in data):
            STABILIZE_DELAY = _bounded_float(data["agility_stabilize_delay_sec"], 0.0, 5.0)
        if ("wasd_post_burst_delay_sec" not in data) and ("wsad_post_burst_delay_sec" not in data) and ("agility_post_burst_delay_sec" in data):
            POST_COMBO_DELAY = _bounded_float(data["agility_post_burst_delay_sec"], 0.0, 5.0)

    try:
        try:
            os.makedirs(JSON_DIR, exist_ok=True)
        except Exception:
            pass

        # Create default config if missing (migrate legacy files if present)
        if not os.path.exists(MACRO_CONFIG_FILE):
            legacy = None
            legacy_candidates = [LEGACY_MACRO_CONFIG_FILE, LEGACY_SETTINGS_FILE]
            for candidate in legacy_candidates:
                if os.path.exists(candidate):
                    try:
                        with open(candidate, "r", encoding="utf-8") as f:
                            legacy = json.load(f)
                        break
                    except Exception:
                        legacy = None

            if isinstance(legacy, dict):
                _apply(legacy)
                save_master_config()
                print(f"[OK] Migrated legacy settings.json -> {MACRO_CONFIG_FILE}")
                return

            save_master_config()
            print(f"[OK] Created default config: {MACRO_CONFIG_FILE}")
            return

        with open(MACRO_CONFIG_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)

        _apply(data)
        # Rewrite once with the supported schema. This drops old internal/debug
        # fields that are intentionally no longer user-configurable.
        save_master_config()
        print(f"[OK] Loaded settings from {MACRO_CONFIG_FILE}")
    except Exception as e:
        print(f"[WARN] Could not load {MACRO_CONFIG_FILE}: {e}")


def _master_config_snapshot():
    return {
        "start_delay_sec": float(START_DELAY),
        "gc_gravity_target_g": int(GC_GRAVITY_TARGET_G),
        "prevent_sleep_while_running": bool(PREVENT_SLEEP_WHILE_RUNNING),
        "restore_fullscreen_on_start": bool(RESTORE_FULLSCREEN_ON_START),
        "display_confirm_changes": bool(DISPLAY_CONFIRM_CHANGES),
        "shutdown_pc_when_finished": bool(SHUTDOWN_PC_WHEN_FINISHED),
        "after_run_game_action": AFTER_RUN_GAME_ACTION,
        "after_run_on_failure": bool(AFTER_RUN_ON_FAILURE),
        "auto_retry_on_failure": bool(AUTO_RETRY_ON_FAILURE),
        "auto_retry_max_attempts": int(AUTO_RETRY_MAX_ATTEMPTS),
        "auto_retry_recovery_mode": str(AUTO_RETRY_RECOVERY_MODE),
        "auto_retry_walk_out": bool(AUTO_RETRY_WALK_OUT),
        "auto_retry_walk_seconds": float(AUTO_RETRY_WALK_SECONDS),
        "diagnostic_mode": bool(DIAGNOSTIC_MODE),
        "after_switch_wait_sec": float(NEW_GAME_WAIT),
        "no_yellow_timeout_sec": float(NO_YELLOW_TIMEOUT_SEC),
        "no_yellow_fallback_enabled": bool(NO_YELLOW_FALLBACK_ENABLED),
        "manual_next_key": str(MANUAL_NEXT_KEY),
        "start_stop_hotkey": str(START_STOP_HOTKEY),
        "pause_hotkey": str(PAUSE_HOTKEY),
        "health_hit_cooldown_sec": float(HEALTH_HIT_COOLDOWN_SEC),
        "health_mode": str(HEALTH_MODE),
        "wasd_key_press_delay_sec": float(KEY_PRESS_DELAY),
        "wasd_stabilize_delay_sec": float(STABILIZE_DELAY),
        "wasd_post_burst_delay_sec": float(POST_COMBO_DELAY),
        "agility_mode": str(AGILITY_MODE),
        "agility_green_observe_sec": float(AGILITY_GREEN_OBSERVE_SEC),
        "agility_inter_string_wait_sec": float(AGILITY_INTER_STRING_WAIT_SEC),
        "agility_after_green_settle_sec": float(AGILITY_AFTER_GREEN_SETTLE_SEC),
        "training_order": list(_sanitize_training_order(TRAINING_ORDER_CUSTOM)),
        "ki_v8_mode": str(KI_V8_MODE),
        "ki_v8_click_delay_sec": float(KI_V8_CLICK_DELAY_SEC),
        "ki_v8_v2_target_r_factor": float(KI_V8_V2_TARGET_R_FACTOR),
        "ki_v8_v2_brightness_threshold": int(KI_V8_V2_BRIGHTNESS_THRESHOLD),
        "ki_v8_v2_bright_count_threshold": int(KI_V8_V2_BRIGHT_COUNT_THRESHOLD),
        "ki_latency_comp_ms": int(KI_LATENCY_COMP_MS),
        "senzu_enabled": bool(SENZU_ENABLED),
        "senzu_slot": int(SENZU_SLOT),
        "senzu_delay_sec": float(SENZU_DELAY_SEC),
        "senzu_recovery_timeout_sec": float(SENZU_RECOVERY_TIMEOUT_SEC),
        "senzu_preference_mode": str(SENZU_PREFERENCE_MODE),
        "senzu_zero_gravity_on_empty": bool(SENZU_ZERO_GRAVITY_ON_EMPTY),
    }


def save_master_config():
    try:
        os.makedirs(JSON_DIR, exist_ok=True)
    except Exception:
        pass
    # Atomic write: a crash or a concurrent save mid-write would otherwise leave
    # a truncated macro_config.json that fails to parse on next launch. Write to
    # a temp file in the same dir, then os.replace() (atomic on Windows + POSIX).
    tmp_path = MACRO_CONFIG_FILE + ".tmp"
    with _config_lock:
        data = _master_config_snapshot()
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, MACRO_CONFIG_FILE)


def factory_reset_configuration():
    """Restore shipped macro, calibration, and save-location defaults.

    Session logs and exported files are deliberately preserved.
    """
    global BUTTONS, HEALTH_BOX, AGILITY_BOX
    global USER_BUTTON_OVERRIDES, USER_REGION_OVERRIDES
    global SAVE_DIR

    reset_user_settings_to_defaults()
    BUTTONS = dict(DEFAULT_BUTTONS)
    HEALTH_BOX = dict(DEFAULT_HEALTH_BOX)
    AGILITY_BOX = dict(DEFAULT_AGILITY_BOX)
    USER_BUTTON_OVERRIDES = {}
    USER_REGION_OVERRIDES = {}
    SAVE_DIR = os.path.join(DATA_DIR, "saves")

    for path in (
        _button_overrides_path(),
        _region_overrides_path(),
        os.path.join(JSON_DIR, "pixel_calibration.json"),
        _save_dir_pref_file(),
    ):
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
    # Re-seed and apply the shipped calibration immediately. The hard-coded
    # fallback coordinates intentionally remain only a last resort; using them
    # until the next app restart made Factory Reset temporarily misclick.
    seed_defaults()
    load_button_overrides()
    load_region_overrides()
    save_master_config()


def hardware_click():
    """Click at the current cursor position. Used after a separate move call.
    Prefer click_at(x, y) when you have target coords — it's one call with the
    move baked in, which Roblox accepts more reliably."""
    check_exit()
    pos = win32api.GetCursorPos()
    click_at(pos[0], pos[1])
    check_exit()

def burst_click(count=3):
    for _ in range(count):
        hardware_click()
        safe_sleep(0.02)

def _tap_key_unchecked(key):
    # pydirectinput.press() with PAUSE=0 releases too quickly for Roblox to register
    # reliably. A 30ms hold matches a short physical key tap.
    with _stop_input_gate:
        if _USER_STOP_LATCHED:
            return False
        pydirectinput.keyDown(key)
        time.sleep(0.030)
        pydirectinput.keyUp(key)
    time.sleep(KEY_PRESS_DELAY)
    return True


def hardware_tap(key):
    check_exit()
    with _input_lock:
        # Auto-Senzu may have started while this thread waited for the lock.
        check_exit()
        _tap_key_unchecked(key)
    check_exit()

# ─── Mouse input — absolute-coordinate SendInput ──────────────────────────────
# Roblox anti-cheat silently drops mouse_event() clicks unless they're preceded
# by an absolute-coord SendInput MOVE:
#   SetCursorPos → 20ms wait → SendInput MOVE (ABSOLUTE) → 10ms → SendInput MOVE
#   again → 10ms → SendInput LEFTDOWN+LEFTUP (ABSOLUTE).
# The double MOVE is non-obvious but Roblox-specific: one MOVE isn't always
# registered by the in-game cursor layer.
import ctypes as _ctypes

_user32 = _ctypes.windll.user32

class _MOUSEINPUT(_ctypes.Structure):
    _fields_ = [
        ("dx",          _ctypes.c_long),
        ("dy",          _ctypes.c_long),
        ("mouseData",   _ctypes.c_ulong),
        ("dwFlags",     _ctypes.c_ulong),
        ("time",        _ctypes.c_ulong),
        ("dwExtraInfo", _ctypes.POINTER(_ctypes.c_ulong)),
    ]

class _INPUT(_ctypes.Structure):
    class _IN(_ctypes.Union):
        _fields_ = [("mi", _MOUSEINPUT)]
    _anonymous_ = ("_in",)
    _fields_    = [("type", _ctypes.c_ulong), ("_in", _IN)]

_MOUSEEVENTF_MOVE     = 0x0001
_MOUSEEVENTF_LEFTDOWN = 0x0002
_MOUSEEVENTF_LEFTUP   = 0x0004
_MOUSEEVENTF_VIRTUALDESK = 0x4000
_MOUSEEVENTF_ABSOLUTE = 0x8000

_SM_XVIRTUALSCREEN = 76
_SM_YVIRTUALSCREEN = 77
_SM_CXVIRTUALSCREEN = 78
_SM_CYVIRTUALSCREEN = 79

def _make_mouse_input(flags, dx=0, dy=0):
    inp            = _INPUT()
    inp.type       = 0
    inp.mi.dx      = dx
    inp.mi.dy      = dy
    inp.mi.dwFlags = flags
    return inp

# Configurable so the Dev Box can A/B test methods live.
MOUSE_METHOD = "sendinput_abs"  # "sendinput_abs" (default) | "setcursor_mouseevent" (legacy) | "setcursor_only" (debug: cursor moves but no click)


def _virtual_desktop_bounds():
    """Return (left, top, width, height) across every attached display."""
    left = int(_user32.GetSystemMetrics(_SM_XVIRTUALSCREEN))
    top = int(_user32.GetSystemMetrics(_SM_YVIRTUALSCREEN))
    width = int(_user32.GetSystemMetrics(_SM_CXVIRTUALSCREEN))
    height = int(_user32.GetSystemMetrics(_SM_CYVIRTUALSCREEN))
    if width <= 0 or height <= 0:
        return (0, 0, 1920, 1080)
    return (left, top, width, height)


def _absolute_virtual_coords(x, y, bounds=None):
    """Map virtual-desktop pixels to SendInput's inclusive 0..65535 space."""
    left, top, width, height = bounds or _virtual_desktop_bounds()
    max_x = max(1, int(width) - 1)
    max_y = max(1, int(height) - 1)
    rel_x = min(max(int(x) - int(left), 0), max_x)
    rel_y = min(max(int(y) - int(top), 0), max_y)
    return (
        int(round(rel_x * 65535 / max_x)),
        int(round(rel_y * 65535 / max_y)),
    )


def _click_sendinput_abs_unchecked(x, y):
    """Absolute-coordinate SendInput click, mapped across the complete Windows virtual desktop."""
    nx, ny = _absolute_virtual_coords(x, y)
    absolute_flags = _MOUSEEVENTF_ABSOLUTE | _MOUSEEVENTF_VIRTUALDESK

    _user32.SetCursorPos(int(x), int(y))
    time.sleep(0.02)

    # A genuine RELATIVE nudge (+1px then -1px, no ABSOLUTE flag) forces raw-input
    # consumers like Roblox's cursor tracking to register a real HID delta. The
    # absolute MOVEs below can be zero-delta since SetCursorPos already parked the
    # cursor at the target, and a raw-input cursor ignores zero-delta moves until a
    # real hardware event arrives — which is why clicks previously didn't land until
    # the user physically wiggled the mouse. This nudge is that wiggle, in software.
    nudge_out = (_INPUT * 1)(_make_mouse_input(_MOUSEEVENTF_MOVE, 1, 0))
    nudge_back = (_INPUT * 1)(_make_mouse_input(_MOUSEEVENTF_MOVE, -1, 0))
    _user32.SendInput(1, nudge_out, _ctypes.sizeof(_INPUT))
    time.sleep(0.005)
    _user32.SendInput(1, nudge_back, _ctypes.sizeof(_INPUT))
    time.sleep(0.01)

    move = (_INPUT * 1)(_make_mouse_input(_MOUSEEVENTF_MOVE | absolute_flags, nx, ny))
    _user32.SendInput(1, move, _ctypes.sizeof(_INPUT))
    time.sleep(0.01)
    _user32.SendInput(1, move, _ctypes.sizeof(_INPUT))
    time.sleep(0.01)

    # The absolute MOVE establishes the cursor position. Keep the button packets
    # button-only: Roblox can ignore coordinate-bearing LEFTDOWN/LEFTUP packets
    # when the target is on a monitor with negative virtual-desktop coordinates.
    click = (_INPUT * 2)(
        _make_mouse_input(_MOUSEEVENTF_LEFTDOWN),
        _make_mouse_input(_MOUSEEVENTF_LEFTUP),
    )
    _user32.SendInput(2, click, _ctypes.sizeof(_INPUT))


def _click_sendinput_abs(x, y):
    with _stop_input_gate:
        if _USER_STOP_LATCHED:
            return False
        _click_sendinput_abs_unchecked(x, y)
    return True


def _click_setcursor_mouseevent(x, y):
    """Legacy: SetCursorPos + mouse_event with relative (0,0). What this build used before — Roblox often drops these silently."""
    win32api.SetCursorPos((int(x), int(y)))
    time.sleep(0.01)
    win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, 0, 0)
    time.sleep(0.005)
    win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, 0, 0)


def _click_setcursor_only(x, y):
    """Debug: move only, no click. Use to see if the cursor actually goes where expected."""
    win32api.SetCursorPos((int(x), int(y)))


def click_at(x, y, method=None):
    """Dispatch a click at screen-space (x, y) via the configured method."""
    method = method or MOUSE_METHOD
    with _input_lock:
        if _controller_decisions_suspended():
            raise SenzuControllerPause()
        with _stop_input_gate:
            if _USER_STOP_LATCHED:
                raise QuitException()
            if method == "sendinput_abs":
                _click_sendinput_abs(x, y)
            elif method == "setcursor_mouseevent":
                _click_setcursor_mouseevent(x, y)
            elif method == "setcursor_only":
                _click_setcursor_only(x, y)
            else:
                # Unknown method — fall back to the K pattern, don't silently no-op.
                _click_sendinput_abs(x, y)


def robust_move(x, y):
    """Move the cursor without clicking. Uses SendInput-absolute so Roblox notices the move."""
    check_exit()
    with _stop_input_gate:
        if _USER_STOP_LATCHED:
            raise QuitException()
        nx, ny = _absolute_virtual_coords(x, y)
        _user32.SetCursorPos(int(x), int(y))
        time.sleep(0.01)
        move = (_INPUT * 1)(_make_mouse_input(
            _MOUSEEVENTF_MOVE | _MOUSEEVENTF_ABSOLUTE | _MOUSEEVENTF_VIRTUALDESK,
            nx,
            ny,
        ))
        _user32.SendInput(1, move, _ctypes.sizeof(_INPUT))
    time.sleep(0.005)
    check_exit()

def preprocess_solid(img_bgr):
    gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
    thresh = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                   cv2.THRESH_BINARY, 11, 2)
    kernel = np.ones((2, 2), np.uint8)
    dilated = cv2.dilate(thresh, kernel, iterations=1)
    proc = cv2.GaussianBlur(dilated, (3, 3), 0)
    return proc

def update_debug_hud(img_raw, img_proc, match_score, status_text):
    if not SHOW_DEBUG_HUD:
        return
    img_raw_bgr = img_raw[:, :, :3]
    img_proc_bgr = cv2.cvtColor(img_proc, cv2.COLOR_GRAY2BGR)

    if img_raw_bgr.shape[:2] != img_proc_bgr.shape[:2]:
        img_raw_bgr = cv2.resize(img_raw_bgr, (img_proc_bgr.shape[1], img_proc_bgr.shape[0]))

    combined = np.vstack((img_raw_bgr, img_proc_bgr))
    h, w, _ = combined.shape

    color = (0, 255, 0) if match_score > CONFIDENCE_TEXT else (0, 0, 255)
    cv2.rectangle(combined, (0, 0), (w, 5), color, -1)

    font_scale = 0.5 if w > 100 else 0.3
    # Show the OCR/template readout so you can verify what's being detected
    if status_text:
        cv2.rectangle(combined, (0, 5), (w, 26), (0, 0, 0), -1)
        cv2.putText(
            combined,
            str(status_text)[:80],
            (5, 20),
            cv2.FONT_HERSHEY_SIMPLEX,
            font_scale,
            (255, 255, 255),
            1,
            cv2.LINE_AA,
        )
    cv2.putText(combined, f"{int(match_score*100)}%", (5, h - 5), cv2.FONT_HERSHEY_SIMPLEX, font_scale, (0, 255, 0), 1)
    cv2.imshow("BOT VISION HUD", combined)
    cv2.waitKey(1)

# --- LOGIC ---
def _health_v1_probe(monitor):
    """Scale the original narrow 1920x1080 target probe to the live game window.
    Trim top/bottom so red floor or feedback text cannot enter the sample."""
    box = _reference_box(HEALTH_BOX, monitor)
    trim = int(round(box["height"] * 0.18))
    box["top"] += trim
    box["height"] = max(4, box["height"] - 2 * trim)
    return box


def _health_red_mask(hsv, saturation=100):
    return cv2.inRange(hsv, np.array([0, saturation, 100]), np.array([10, 255, 255])) + \
           cv2.inRange(hsv, np.array([160, saturation, 100]), np.array([180, 255, 255]))


def _logic_health_v1(sct, monitor):
    """Original behavior: any solid red entering the narrow green-zone probe fires F."""
    box = _health_v1_probe(monitor)
    img_bgr = np.array(sct.grab(box))[:, :, :3]
    hsv = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2HSV)
    red_count = cv2.countNonZero(_health_red_mask(hsv))
    threshold = max(50, int(box["width"] * box["height"] * 0.005))
    if red_count > threshold:
        print(f">>> [HEALTH v1] Hit! red={red_count} threshold={threshold} box={box}")
        hardware_tap('f')
        TELEMETRY["health_hits"] += 1
        safe_sleep(HEALTH_HIT_COOLDOWN_SEC)


def _logic_health_v2(sct, monitor):
    """Find the Health UI, remember the full green target span, and fire when the
    moving red marker's center enters the middle of that span. Red replaces green
    on contact, so intersecting the two color masks directly can never work."""
    geometry = (monitor["left"], monitor["top"], monitor["width"], monitor["height"])
    if _HEALTH_V2_STATE["geometry"] != geometry:
        _HEALTH_V2_STATE.update({
            "geometry": geometry,
            "target": None,
            "armed": True,
            "last_hit": 0.0,
        })

    roi_left = monitor["left"] + int(monitor["width"] * 0.68)
    roi_top = monitor["top"] + int(monitor["height"] * 0.42)
    roi = {
        "left": roi_left,
        "top": roi_top,
        "width": max(8, monitor["width"] - (roi_left - monitor["left"])),
        "height": max(8, int(monitor["height"] * 0.26)),
    }
    img_bgr = np.array(sct.grab(roi))[:, :, :3]
    hsv = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2HSV)
    green = cv2.inRange(hsv, np.array([40, 120, 120]), np.array([80, 255, 255]))
    contours, _ = cv2.findContours(green, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    candidates = []
    for contour in contours:
        x, y, w, h = cv2.boundingRect(contour)
        if w * h >= 300 and h >= 20:
            candidates.append((w * h, x, y, w, h))
    if candidates:
        _, gx, gy, gw, gh = max(candidates)
        current = _HEALTH_V2_STATE["target"]
        min_full_target_width = max(30, int(monitor["width"] * 0.035))
        if gw >= min_full_target_width and (current is None or gw > current[2]):
            _HEALTH_V2_STATE["target"] = (gx, gy, gw, gh)

    target = _HEALTH_V2_STATE["target"]
    if target is None:
        return
    gx, gy, gw, gh = target
    strip = hsv[max(0, gy):min(hsv.shape[0], gy + gh), :]
    if strip.size == 0:
        return
    red = _health_red_mask(strip)
    column_counts = np.count_nonzero(red, axis=0)
    red_columns = np.flatnonzero(column_counts >= max(5, gh * 0.35))
    if not red_columns.size:
        return

    runs = np.split(red_columns, np.where(np.diff(red_columns) > 1)[0] + 1)
    runs = [
        run for run in runs
        if run[0] > 2 and run[-1] < roi["width"] - 3
        and 12 <= len(run) <= int(roi["width"] * 0.20)
    ]
    if not runs:
        return
    marker = max(runs, key=lambda run: float(column_counts[run].mean()))
    marker_left, marker_right = int(marker[0]), int(marker[-1])
    marker_center = (marker_left + marker_right) / 2.0

    # Re-arm after the marker has clearly left the target. Fire near the middle,
    # which is less timing-sensitive than firing on the first touching pixel.
    if marker_center < gx - gw * 0.25 or marker_center > gx + gw * 1.25:
        _HEALTH_V2_STATE["armed"] = True
    hit_left = gx + gw * 0.30
    hit_right = gx + gw * 0.60
    now = time.time()
    if (_HEALTH_V2_STATE["armed"] and hit_left <= marker_center <= hit_right
            and now - _HEALTH_V2_STATE["last_hit"] >= HEALTH_HIT_COOLDOWN_SEC):
        print(">>> [HEALTH v2] Hit")
        hardware_tap('f')
        TELEMETRY["health_hits"] += 1
        _HEALTH_V2_STATE["armed"] = False
        _HEALTH_V2_STATE["last_hit"] = now


def logic_health(sct, monitor):
    try:
        if HEALTH_MODE == "v2_track":
            _logic_health_v2(sct, monitor)
        else:
            _logic_health_v1(sct, monitor)
    except (QuitException, SkipMinigameException):
        raise
    except Exception:
        pass

def scan_for_letters(sct, monitor_area, templates):
    try:
        img_bgr = _grab_reference_box(sct, monitor_area)
        img_gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
    except: return []
    raw_matches = []
    for tpl, char in templates:
        if tpl.shape[0] > img_gray.shape[0]:
            continue
        res = cv2.matchTemplate(img_gray, tpl, cv2.TM_CCOEFF_NORMED)
        loc = np.where(res >= AGILITY_CONFIDENCE)
        for pt in zip(*loc[::-1]):
            cx = pt[0] + tpl.shape[1] // 2
            cy = pt[1] + tpl.shape[0] // 2
            raw_matches.append((pt[0], pt[1], char, float(res[pt[1], pt[0]]), cx, cy))
    raw_matches.sort(key=lambda x: x[3], reverse=True)
    final_unique = []
    for match in raw_matches:
        mx, my, mchar, mconf, mcx, mcy = match
        is_unique = True
        for valid in final_unique:
            if math.hypot(mcx - valid[4], mcy - valid[5]) < 20:
                is_unique = False
                break
        if is_unique:
            final_unique.append(match)
    final_unique.sort(key=lambda x: x[0])
    clean_batch = []
    for target in final_unique:
        mx, my, mchar, mconf, mcx, mcy = target
        if not is_area_green(img_bgr, mcx, mcy):
            clean_batch.append(mchar)
    return clean_batch

def logic_agility_v1(sct, templates):
    first_look = scan_for_letters(sct, AGILITY_BOX, templates)
    if first_look:
        safe_sleep(STABILIZE_DELAY)
        final_batch = scan_for_letters(sct, AGILITY_BOX, templates)
        if final_batch:
            combo_str = " - ".join([k.upper() for k in final_batch])
            print(f">>> [WASD] Burst: {combo_str}")
            for key in final_batch: hardware_tap(key)
            safe_sleep(POST_COMBO_DELAY)


def _scan_letters_with_pos(sct, monitor_area, templates):
    """Like scan_for_letters but returns (char, cx, cy) with positions relative to grab area."""
    try:
        img_bgr = _grab_reference_box(sct, monitor_area)
        img_gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
    except Exception:
        return []
    raw = []
    for tpl, char in templates:
        if tpl.shape[0] > img_gray.shape[0]:
            continue
        res = cv2.matchTemplate(img_gray, tpl, cv2.TM_CCOEFF_NORMED)
        loc = np.where(res >= AGILITY_CONFIDENCE)
        for pt in zip(*loc[::-1]):
            cx = pt[0] + tpl.shape[1] // 2
            cy = pt[1] + tpl.shape[0] // 2
            raw.append((pt[0], pt[1], char, float(res[pt[1], pt[0]]), cx, cy))
    raw.sort(key=lambda x: x[3], reverse=True)
    unique = []
    for m in raw:
        mx, my, mchar, mconf, mcx, mcy = m
        dup = False
        for v in unique:
            if math.hypot(mcx - v[4], mcy - v[5]) < 20:
                dup = True
                break
        if not dup:
            unique.append(m)
    unique.sort(key=lambda x: x[0])
    result = []
    for mx, my, mchar, mconf, mcx, mcy in unique:
        # Only return truly-pending (white) letters. Green = already done,
        # red = currently failed mid-recovery — neither should trigger a press.
        if is_area_green(img_bgr, mcx, mcy):
            continue
        if is_area_red(img_bgr, mcx, mcy):
            continue
        result.append((mchar, mcx, mcy))
    return result


def _is_letter_green_now(sct, monitor_area, cx, cy):
    """Re-grab monitor_area and check if position (cx, cy) turned green."""
    try:
        img_bgr = _grab_reference_box(sct, monitor_area)
        return is_area_green(img_bgr, cx, cy)
    except Exception:
        return False


_agility_chain_yellow_ts = 0.0  # last yellow seen inside a chain loop; the outer
                                # loop folds this in so its "NO YELLOW for Ns"
                                # duration stays honest after a long chain.


def _agility_yellow_gone(sct, state, absent_since):
    """Between-sequence progression check for the Agility chain loops.

    The game keeps serving WASD prompts even after the set completes (seen live
    at Progression 50/50), so a chain loop that only exits on empty-scan or red
    can run forever and the outer loop's no-yellow switch never gets a turn.
    Returns the updated absent_since marker, or raises StopIteration semantics
    via a (gone, absent_since) tuple: gone=True means the caller should return.
    """
    global _agility_chain_yellow_ts
    y_count, _, _ = detect_yellow_progress(sct, state)
    if y_count > 0:
        _agility_chain_yellow_ts = time.time()
        return False, None
    now = time.time()
    if absent_since is None:
        return False, now
    if now - absent_since >= NO_YELLOW_TIMEOUT_SEC:
        print(f"[WASD] No yellow for {now - absent_since:.1f}s between sequences — set complete, yielding for switch")
        return True, absent_since
    return False, absent_since


def logic_agility_v2(sct, templates, state="Agility"):
    """Sequential per-letter green-gated press.

    For each letter in the sequence, in order:
      1. Tap the key.
      2. Wait up to AGILITY_GREEN_OBSERVE_SEC (default 700ms) for that letter's
         slot to turn green.
      3. Green → advance to the next letter.
      4. Red → wrong key was registered; abandon the sequence, sleep
         AGILITY_FAIL_BACKOFF_SEC for the in-game timeout, then return so the
         main loop picks up the next (multiplier-reset) sequence.
      5. Still white after the timeout → tap again. Capped at 3 attempts per
         letter to avoid spam if input is being eaten by the game.
    """
    letters = _scan_letters_with_pos(sct, AGILITY_BOX, templates)
    if not letters:
        return

    MAX_ATTEMPTS = 3
    POLL_INTERVAL = 0.02  # 20ms — balance between responsiveness and CPU
    yellow_absent_since = None

    while True:
        check_exit()
        combo = " - ".join(k.upper() for k, _, _ in letters)
        print(f">>> [WASD v2] Sequence: {combo}")
        TELEMETRY["wasd_sequences"] += 1

        sequence_failed = False
        for char, cx, cy in letters:
            check_exit()
            attempt = 1
            hardware_tap(char)
            t_press = time.time()
            t_window_start = t_press
            confirmed = False

            while True:
                check_exit()
                st = _letter_state_at(sct, AGILITY_BOX, cx, cy)
                if st == 'green':
                    confirmed = True
                    TELEMETRY["wasd_greens"] += 1
                    total_ms = (time.time() - t_press) * 1000.0
                    extra = f" (attempt {attempt})" if attempt > 1 else ""
                    print(f"    [WASD v2] {char.upper()} green in {total_ms:.0f}ms{extra}")
                    break
                if st == 'red':
                    TELEMETRY["wasd_reds"] += 1
                    print(f"    [WASD v2] FAIL — {char.upper()} red, abandoning sequence")
                    sequence_failed = True
                    break

                elapsed = time.time() - t_window_start
                if elapsed >= AGILITY_GREEN_OBSERVE_SEC:
                    if attempt >= MAX_ATTEMPTS:
                        TELEMETRY["wasd_unconfirmed"] += 1
                        # Diagnostic: sample the patch we've been watching so we can tell
                        # input-bug ("game is green at cx,cy but our check missed it") apart
                        # from detection-bug ("center pixel is dark stroke, not green bg").
                        try:
                            sample = _grab_reference_box(sct, AGILITY_BOX)
                            patch = sample[max(0, cy-18):cy+18, max(0, cx-18):cx+18]
                            avg_b = int(patch[:, :, 0].mean()) if patch.size else 0
                            avg_g = int(patch[:, :, 1].mean()) if patch.size else 0
                            avg_r = int(patch[:, :, 2].mean()) if patch.size else 0
                            print(f"    [WASD v2] {char.upper()} never confirmed after {attempt} attempts (avgRGB=({avg_r},{avg_g},{avg_b}) at slot=({cx},{cy}))")
                        except Exception:
                            print(f"    [WASD v2] {char.upper()} never confirmed after {attempt} attempts, moving on")
                        break
                    attempt += 1
                    hardware_tap(char)
                    t_window_start = time.time()
                time.sleep(POLL_INTERVAL)

            if sequence_failed:
                break

        if sequence_failed:
            safe_sleep(AGILITY_FAIL_BACKOFF_SEC)
            return

        if AGILITY_AFTER_GREEN_SETTLE_SEC > 0:
            safe_sleep(AGILITY_AFTER_GREEN_SETTLE_SEC)

        gone, yellow_absent_since = _agility_yellow_gone(sct, state, yellow_absent_since)
        if gone:
            return

        next_letters = _stable_scan_letters(sct, templates, AGILITY_INTER_STRING_WAIT_SEC)
        if not next_letters:
            return
        letters = next_letters


def logic_agility_v3(sct, templates, state="Agility"):
    """Sequential per-letter, green-gated press. Matches wiki + video-confirmed mechanics:
    each letter must be pressed in order, and pressing the WRONG letter turns the
    expected one RED (fail — wait out the timeout, do not keep pressing).

    Press flow per letter:
      1. Tap the key
      2. Watch that letter's screen position
      3. If it turns GREEN within AGILITY_PER_LETTER_TIMEOUT_SEC, advance to next letter
      4. If it turns RED (or any other letter in the row is red), abandon — wait
         AGILITY_FAIL_BACKOFF_SEC for the fail-state to clear, then return
      5. If still white at timeout, re-press once and continue waiting (handles dropped input)
    """
    letters = _stable_scan_letters(sct, templates, 0.5)
    if not letters:
        return

    yellow_absent_since = None
    while True:
        check_exit()
        combo = " - ".join(k.upper() for k, _, _ in letters)
        print(f">>> [WASD v3] Sequence: {combo}")

        for idx, (char, cx, cy) in enumerate(letters):
            check_exit()
            hardware_tap(char)
            t0 = time.time()
            confirmed = False
            repressed = False
            while time.time() - t0 < AGILITY_PER_LETTER_TIMEOUT_SEC * 2:
                check_exit()
                st = _letter_state_at(sct, AGILITY_BOX, cx, cy)
                if st == 'green':
                    confirmed = True
                    break
                if st == 'red':
                    print(f"    [WASD v3] FAIL — {char.upper()} red")
                    safe_sleep(AGILITY_FAIL_BACKOFF_SEC)
                    return
                # Halfway through the window, try one re-press in case the
                # first tap was dropped by the OS/game.
                if not repressed and (time.time() - t0) > AGILITY_PER_LETTER_TIMEOUT_SEC:
                    hardware_tap(char)
                    repressed = True
                time.sleep(0.005)

            if not confirmed:
                print(f"    [WASD v3] {char.upper()} never confirmed green, continuing")
            else:
                ms = (time.time() - t0) * 1000.0
                print(f"    [WASD v3] {char.upper()} green in {ms:.0f}ms")

        # Chain into next sequence.
        gone, yellow_absent_since = _agility_yellow_gone(sct, state, yellow_absent_since)
        if gone:
            return
        next_letters = _stable_scan_letters(sct, templates, AGILITY_INTER_STRING_WAIT_SEC)
        if not next_letters:
            return
        letters = next_letters


def _ki_v8_check_dark_border(img_bgr, cx, cy, dot_r):
    """Sample points on a ring at radius (dot_r + 3) — the thin black border just
    outside the orange disc. Real Ki dots have a thick black border around the orange;
    most other orange shapes (HUD, outfit, halos) don't.

    24 samples evenly around the circle. Out-of-bounds samples are excluded — when
    the dot is near the screen edge we don't penalize it, just evaluate what's visible.
    """
    h, w = img_bgr.shape[:2]
    n = 24
    angles = np.linspace(0.0, 2.0 * np.pi, n, endpoint=False)
    border_r = max(dot_r + 3, dot_r + int(dot_r * 0.10))
    in_bounds = 0
    dark = 0
    for a in angles:
        sx = int(cx + border_r * np.cos(a))
        sy = int(cy + border_r * np.sin(a))
        if not (0 <= sx < w and 0 <= sy < h):
            continue
        in_bounds += 1
        b, g, r = img_bgr[sy, sx]
        # "Dark" = max channel below threshold, NOT luminance. GC's solid red
        # (0,0,190) has luminance ~57 (< KI_V8_DARK_THRESH), so a grayscale test
        # counted the red background as a black border and let any orange blob on
        # red pass. max(B,G,R)=190 is correctly not dark; a true black outline is.
        if max(int(b), int(g), int(r)) < KI_V8_DARK_THRESH:
            dark += 1
    if in_bounds < 6:
        return False
    return (dark / in_bounds) >= KI_V8_BORDER_DARK_FRAC_MIN


def _ki_v8_check_vertical_one(img_bgr, cx, cy, dot_r):
    """The "1" digit inside the Ki dot is a tall narrow vertical black stroke.
    Scan a central column band (±dot_r/3 wide, ~1.3× dot_r tall) for columns
    where ≥45% of pixels are dark — those are the digit's vertical stroke.
    Require 1-8 such columns total (a thin connected stroke, not a wide dark block).
    """
    crop_h = max(8, int(dot_r * 1.3))
    crop_w = max(6, int(dot_r * 0.6))
    h, w = img_bgr.shape[:2]
    y0 = max(0, cy - crop_h // 2)
    y1 = min(h, cy + crop_h // 2)
    x0 = max(0, cx - crop_w // 2)
    x1 = min(w, cx + crop_w // 2)
    patch = img_bgr[y0:y1, x0:x1]
    if patch.shape[0] < 8 or patch.shape[1] < 4:
        return False
    # max-channel "dark" (same reason as _ki_v8_check_dark_border): keeps the GC
    # red background from registering as the black "1" stroke.
    dark_mask = (patch.max(axis=2) < KI_V8_DARK_THRESH).astype(np.uint8)
    col_sums = dark_mask.sum(axis=0)
    min_h = int(patch.shape[0] * KI_V8_DIGIT_DARK_FRAC_MIN)
    n_dark_cols = int((col_sums >= min_h).sum())
    return KI_V8_DIGIT_WIDTH_MIN <= n_dark_cols <= KI_V8_DIGIT_WIDTH_MAX


_ki_v8_no_dot_last_log_ts = 0.0


def _ki_v8_count_bright_at_radius(img_bgr, cx, cy, radius, n_samples=32, brightness_threshold=210):
    """For each of n_samples angles around the circle, count it if ANY pixel in the
    radial BAND [radius - KI_V8_V2_BAND_OFFSET, radius + KI_V8_V2_BAND_OFFSET] is
    BRIGHT *and* close to GRAYSCALE. The band catches the actual game ring even
    when it's only 2-3 px wide — a single-radius sample lands in the dark gap and
    misses. Grayscale filter excludes saturated background (red walls, orange dot)."""
    h, w = img_bgr.shape[:2]
    angles = np.linspace(0.0, 2.0 * np.pi, n_samples, endpoint=False)
    cos_a = np.cos(angles)
    sin_a = np.sin(angles)
    band = int(KI_V8_V2_BAND_OFFSET)
    hit_mask = np.zeros(n_samples, dtype=bool)
    for d_r in range(-band, band + 1):
        r = radius + d_r
        if r < 1:
            continue
        xs = (cx + r * cos_a).astype(np.int32)
        ys = (cy + r * sin_a).astype(np.int32)
        in_bounds = (xs >= 0) & (xs < w) & (ys >= 0) & (ys < h)
        xs_clip = np.clip(xs, 0, w - 1)
        ys_clip = np.clip(ys, 0, h - 1)
        pixels = img_bgr[ys_clip, xs_clip].astype(np.int16)
        v_max = pixels.max(axis=1)
        v_min = pixels.min(axis=1)
        is_bright = v_max >= brightness_threshold
        is_grayscale = (v_max - v_min) <= 80
        layer_hits = is_bright & is_grayscale & in_bounds
        hit_mask |= layer_hits
    return int(hit_mask.sum())


def _ki_v8_local_contrast_profile(img_bgr, cx, cy, radii, n_samples=32, contrast_delta=8):
    """Count locally brighter ring samples for every candidate radius.

    The shrinking Ki ring is compared with pixels six places inward and outward
    at the same angle. Lab L keeps the comparison about lightness, so a global
    red, blue, or yellow cast does not need its own environment threshold.
    This runs beside the neutral-bright profile and becomes the tracked radius
    when its qualified peak is stronger.
    """
    if not radii:
        return []
    h, w = img_bgr.shape[:2]
    lab_l = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2LAB)[:, :, 0].astype(np.int16)
    angles = np.linspace(0.0, 2.0 * np.pi, n_samples, endpoint=False)
    cos_a = np.cos(angles)
    sin_a = np.sin(angles)
    counts = []

    for radius in radii:
        ring_l = np.full(n_samples, -999, dtype=np.int16)
        ring_valid = np.zeros(n_samples, dtype=bool)
        for d_r in (-1, 0, 1):
            r = radius + d_r
            xs = (cx + r * cos_a).astype(np.int32)
            ys = (cy + r * sin_a).astype(np.int32)
            valid = (xs >= 0) & (xs < w) & (ys >= 0) & (ys < h)
            values = lab_l[np.clip(ys, 0, h - 1), np.clip(xs, 0, w - 1)]
            ring_l = np.maximum(ring_l, np.where(valid, values, -999))
            ring_valid |= valid

        background_l = np.full(n_samples, -999, dtype=np.int16)
        background_valid = np.ones(n_samples, dtype=bool)
        for d_r in (-6, 6):
            r = radius + d_r
            xs = (cx + r * cos_a).astype(np.int32)
            ys = (cy + r * sin_a).astype(np.int32)
            valid = (xs >= 0) & (xs < w) & (ys >= 0) & (ys < h)
            values = lab_l[np.clip(ys, 0, h - 1), np.clip(xs, 0, w - 1)]
            background_l = np.maximum(background_l, np.where(valid, values, -999))
            background_valid &= valid

        hits = ring_valid & background_valid & ((ring_l - background_l) >= int(contrast_delta))
        counts.append(int(hits.sum()))
    return counts


def _ki_v8_clamped_grab(sct, center_x_abs, center_y_abs, half):
    """Grab a square ROI around an absolute screen point, clamped to the virtual
    screen. Returns (img_bgr, local_cx, local_cy). When the dot sits near a screen
    edge the rectangle shrinks instead of shifting, so local_cx/local_cy stay the
    dot's true position inside the returned image (the old code pinned the origin
    at 0 but kept assuming the dot was at the center)."""
    screen = sct.monitors[0]
    left = max(screen["left"], center_x_abs - half)
    top = max(screen["top"], center_y_abs - half)
    right = min(screen["left"] + screen["width"], center_x_abs + half)
    bottom = min(screen["top"] + screen["height"], center_y_abs + half)
    grab = {"left": left, "top": top,
            "width": max(1, right - left), "height": max(1, bottom - top)}
    img = np.array(sct.grab(grab))[:, :, :3]
    return img, center_x_abs - left, center_y_abs - top


def _ki_v8_track_update(track, ring_r, target_r, lead_px=0.0):
    """Advance one detector method's motion state and return True when that
    method's own evidence authorizes a click (radius at/below target after
    confirmed shrinking motion). Each method gets its own track dict — sharing
    prev_r/streak/saw_above_target across methods let one method's history
    authorize another method's peak.

    lead_px shifts only the firing test (ring_r - lead_px <= target_r), never
    the motion state: on a slow machine or high ping the ring keeps shrinking
    between the captured frame and the game registering the click, so firing
    on the raw observed radius lands late. Motion/saw_above stay on raw radii
    so prediction can't fabricate shrink evidence."""
    if ring_r is None:
        return False
    if ring_r > target_r:
        track["saw_above_target"] = True
    if track["prev_r"] is not None and ring_r < track["prev_r"]:
        track["streak"] += 1
    elif track["prev_r"] is not None and ring_r > track["prev_r"] + 2:
        track["streak"] = 1
    track["prev_r"] = ring_r
    if ring_r - lead_px > target_r:
        return False
    return track["saw_above_target"] or track["streak"] >= int(KI_V8_V2_MOTION_STREAK_MIN)


def _ki_v8_shrink_velocity_px_s(track_history):
    """Median shrink speed (px/s, positive = shrinking) from the last few
    detected radii of ONE method's history [(t_ms, ring_r, count), ...].
    None until 3 recent samples exist — prediction stays off while evidence
    is thin, so a lone noisy pair can't produce a huge phantom velocity."""
    recent = [(t_ms, r) for t_ms, r, _ in track_history[-8:] if r is not None][-5:]
    if len(recent) < 3:
        return None
    speeds = []
    for (t0, r0), (t1, r1) in zip(recent, recent[1:]):
        dt_s = (t1 - t0) / 1000.0
        if dt_s > 0:
            speeds.append((r0 - r1) / dt_s)
    if not speeds:
        return None
    speeds.sort()
    return speeds[len(speeds) // 2]


def _ki_v8_lead_px(track_history):
    """Radius lead to subtract for latency compensation: shrink velocity times
    the user-configured KI_LATENCY_COMP_MS. 0 when compensation is off (the
    default) or velocity isn't established, i.e. exactly the old behavior."""
    if KI_LATENCY_COMP_MS <= 0:
        return 0.0
    velocity = _ki_v8_shrink_velocity_px_s(track_history)
    if velocity is None or velocity <= 0:
        return 0.0
    return velocity * (KI_LATENCY_COMP_MS / 1000.0)


def _ki_v8_save_live_debug(small, local_cx, local_cy, dot_r, target_r, r_min, r_max,
                            profile, detected_r):
    """Write the current scan state to bot/python/json/ki_debug/v2/live.png so the user
    can open it with an auto-refreshing image viewer and see, frame by frame, what the
    macro is detecting. Includes the radial brightness-count profile drawn as a bar
    chart along the bottom — peak = where the ring was found this frame."""
    if not KI_V8_V2_DEBUG_IMAGE:
        return
    try:
        H, W = small.shape[:2]
        # Add ~40 px of profile-bar real estate below the image
        bar_h = 40
        canvas = np.zeros((H + bar_h, W, 3), dtype=np.uint8)
        canvas[:H] = small
        # Indicators
        cv2.circle(canvas, (local_cx, local_cy), int(r_min), (60, 60, 60), 1)
        cv2.circle(canvas, (local_cx, local_cy), int(r_max), (60, 60, 60), 1)
        cv2.circle(canvas, (local_cx, local_cy), int(target_r), (0, 255, 0), 1)
        cv2.circle(canvas, (local_cx, local_cy), int(dot_r), (0, 140, 255), 2)
        if detected_r is not None:
            cv2.circle(canvas, (local_cx, local_cy), int(detected_r), (255, 255, 255), 2)
        # Profile bar
        if profile:
            peak = max(max(profile), 1)
            for i, n in enumerate(profile):
                x = int(i * W / max(1, len(profile)))
                bar = int((n / peak) * (bar_h - 4))
                color = (0, 255, 255) if i == max(range(len(profile)), key=lambda k: profile[k]) else (90, 180, 180)
                cv2.line(canvas, (x, H + bar_h - 2), (x, H + bar_h - 2 - bar), color, 1)
        # Caption
        caption = f"r_min={r_min} r_max={r_max} target={target_r} detected={detected_r}"
        cv2.putText(canvas, caption, (4, H + 12), cv2.FONT_HERSHEY_SIMPLEX, 0.35,
                    (0, 0, 0), 3, cv2.LINE_AA)
        cv2.putText(canvas, caption, (4, H + 12), cv2.FONT_HERSHEY_SIMPLEX, 0.35,
                    (255, 255, 255), 1, cv2.LINE_AA)
        out_dir = os.path.join(JSON_DIR, "ki_debug", "v2")
        os.makedirs(out_dir, exist_ok=True)
        cv2.imwrite(os.path.join(out_dir, "live.png"), canvas)
    except Exception:
        pass


def _ki_v8_grade_perfect(sct, monitor, cx, cy, dot_r):
    """After a click, sleep briefly then sample a 4×dot_r region around the dot looking
    for the yellow "Perfect!" text overlay that the game draws on a successful click.
    Updates TELEMETRY and prints the grade. Disabled by default because synchronous
    grading delays the next target and the async experiment produced stale results."""
    if not KI_V8_V2_GRADE_OUTCOMES:
        return
    try:
        safe_sleep(float(KI_V8_V2_GRADE_DELAY_SEC))
        half = max(40, 2 * int(dot_r))
        img, _local_cx, _local_cy = _ki_v8_clamped_grab(
            sct, monitor["left"] + cx, monitor["top"] + cy, half
        )
        hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
        # Yellow/gold "Perfect!" text — narrow hue band starting at 23, ABOVE the orange
        # dot's hue band (~10-22). The previous range 18-38 was counting the orange dot
        # itself as "yellow text" — 277 yellow_px every click was exactly the dot's pixel
        # count, not the Perfect text. Hue ≥ 23 cleanly separates gold from orange.
        yellow = (
            (hsv[:, :, 0] >= 23) & (hsv[:, :, 0] <= 38) &
            (hsv[:, :, 1] >= 140) & (hsv[:, :, 2] >= 170)
        )
        yellow_count = int(yellow.sum())
        TELEMETRY["ki_graded"] += 1
        if yellow_count >= int(KI_V8_V2_GRADE_YELLOW_MIN_PX):
            TELEMETRY["ki_perfect"] += 1
            grade = "Perfect"
        else:
            grade = "non-Perfect"
        p = TELEMETRY["ki_perfect"]
        g = TELEMETRY["ki_graded"]
        rate = (100.0 * p / g) if g else 0.0
        print(f"[KI v8] Grade: {grade} (yellow_px={yellow_count})  rolling: {p}/{g} = {rate:.0f}% Perfect")
    except (QuitException, SkipMinigameException):
        raise
    except Exception:
        pass
_ki_v8_v2_debug_counter = 0

def _ki_v8_save_v2_debug_image(small, local_cx, local_cy, dot_r, target_r, r_min, r_max, history, decision):
    """Save an annotated PNG of the local grab so the user can SEE what v2 detected.
    Overlays: orange dot, gray scan-range bounds, green target_r, white last-detected
    ring, blue trajectory dots for every detected radius in history, plus caption.
    Rotates through KI_V8_V2_DEBUG_BUFFER files in bot/python/json/ki_debug/v2/."""
    global _ki_v8_v2_debug_counter
    if not KI_V8_V2_DEBUG_IMAGE:
        return
    try:
        out = small.copy()
        # Scan bounds (dim gray) so user sees where v2 was looking
        cv2.circle(out, (local_cx, local_cy), int(r_min), (60, 60, 60), 1)
        cv2.circle(out, (local_cx, local_cy), int(r_max), (60, 60, 60), 1)
        # Trajectory of every detected radius in history (dim blue arc segments) so user
        # can see the ring's shrink path
        for (_t, r, _n) in history:
            if r is not None:
                cv2.circle(out, (local_cx, local_cy), int(r), (120, 60, 30), 1)
        # Target circle (green)
        cv2.circle(out, (local_cx, local_cy), int(target_r), (0, 255, 0), 1)
        # Dot (orange)
        cv2.circle(out, (local_cx, local_cy), int(dot_r), (0, 140, 255), 2)
        # Final detected ring radius (white, thick) so it's obvious
        last_found = next((r for (_t, r, _n) in reversed(history) if r is not None), None)
        if last_found is not None:
            cv2.circle(out, (local_cx, local_cy), int(last_found), (255, 255, 255), 2)
        # Caption
        cv2.putText(out, decision, (5, 14), cv2.FONT_HERSHEY_SIMPLEX, 0.38, (0, 0, 0), 3, cv2.LINE_AA)
        cv2.putText(out, decision, (5, 14), cv2.FONT_HERSHEY_SIMPLEX, 0.38, (255, 255, 255), 1, cv2.LINE_AA)
        out_dir = os.path.join(JSON_DIR, "ki_debug", "v2")
        os.makedirs(out_dir, exist_ok=True)
        idx = _ki_v8_v2_debug_counter % max(1, int(KI_V8_V2_DEBUG_BUFFER))
        _ki_v8_v2_debug_counter += 1
        cv2.imwrite(os.path.join(out_dir, f"click_{idx:02d}.png"), out)
        # Also overwrite "last.png" as a convenience shortcut
        cv2.imwrite(os.path.join(out_dir, "last.png"), out)
    except Exception:
        pass


def _ki_v8_log_v2_history(history, label="v2 history"):
    """Print a compact summary of a v2 scan run so the user can see what was tracked.
    history is a list of (t_ms, ring_r_or_None, count) tuples — one per scan iteration.
    Sampled (not every entry) to keep the log line readable."""
    if not VERBOSE_DETECTOR_LOGS:
        return
    if not history:
        print(f"[KI v8] {label}: (empty)")
        return
    found = [(t, r, n) for (t, r, n) in history if r is not None]
    if not found:
        # Print first/middle/last to show what counts we DID see (helps tune brightness)
        sample = [history[0]]
        if len(history) >= 3:
            sample.append(history[len(history) // 2])
        sample.append(history[-1])
        bits = " ".join(f"t={t}ms r=- n={n}" for (t, _r, n) in sample)
        print(f"[KI v8] {label} (no ring found, {len(history)} iters): {bits}")
        return
    # Print first, every ~4th, and last detection
    step = max(1, len(found) // 6)
    sample = found[::step] + ([found[-1]] if found[-1] not in found[::step] else [])
    bits = " ".join(f"t={t}ms r={r} n={n}" for (t, r, n) in sample)
    print(f"[KI v8] {label} ({len(found)}/{len(history)} hits): {bits}")


def _ki_v8_find_ring_radius(img_bgr, cx, cy, dot_r, brightness_threshold, count_threshold):
    """Compute a brightness-profile (count vs radius) across [r_min, r_max] and return
    the radius with the MAXIMUM count, provided it exceeds count_threshold. The ring
    creates a clear local peak in the profile — even when occluded or thin, the peak
    is sharply higher than surrounding noise. Returns (radius, count) or (None, 0)."""
    r_min = int(dot_r * KI_V8_V2_R_MIN_FACTOR)
    r_max = int(dot_r * KI_V8_V2_R_MAX_FACTOR)
    step = max(1, int(KI_V8_V2_SCAN_STEP_PX))
    radii = list(range(r_min, r_max + 1, step))
    if not radii:
        return None, 0
    profile = [
        _ki_v8_count_bright_at_radius(
            img_bgr, cx, cy, r,
            n_samples=KI_V8_V2_SAMPLE_COUNT,
            brightness_threshold=brightness_threshold,
        )
        for r in radii
    ]
    peak_idx = max(range(len(profile)), key=lambda i: profile[i])
    peak_n = profile[peak_idx]
    if peak_n < count_threshold:
        return None, 0
    return radii[peak_idx], peak_n


def logic_ki_v8(sct, monitor):
    """Ki minigame v8 — tight detection + two click-timing modes.

    Detection layer (always runs):
      - Orange HSV blob, area in [400, 8000], aspect close to 1
      - Area-ratio (contour_area / enclosing-circle area) ≥ 0.55 — tolerates
        partial occlusion by blue HUD elements
      - Thick black border around the orange (24-point sample at dot_r + 3)
      - Vertical "1" stroke in the center (column-darkness scan, 1-12 thin columns)
      - 2-frame position stability before clicking
      - Post-click cooldown of 400ms before re-detecting
      - On "no dot" failure, saves `ki_debug/last_*.png` with reject reason

    Click-timing layer (selected by KI_V8_MODE):
      "v1_time" — wait KI_V8_CLICK_DELAY_SEC after stable detect, click. Simple
                  and reliable. Adjust delay if clicks land too early/late.
      "v2_ring" — radial-scan tracker. Scan radii outer→inner each frame, locate
                  the shrinking ring's current radius, fire when it crosses
                  target_r = dot_r * KI_V8_V2_TARGET_R_FACTOR. Gated by "must
                  have seen ring at r > target on a prior frame" so static bright
                  background pixels can't trigger an instant click. Falls back
                  to immediate click on timeout.
    """
    global _ki_v8_no_dot_last_log_ts
    try:
        check_exit()
        now = time.time()
        if now - _ki_v8_state["last_click_at"] < KI_V8_POST_CLICK_COOLDOWN_SEC:
            time.sleep(0.05)
            return

        img = np.array(sct.grab(monitor))
        img_bgr = img[:, :, :3]
        hsv = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2HSV)

        orange = cv2.inRange(hsv, LOWER_ORANGE, UPPER_ORANGE)
        kernel = np.ones((3, 3), np.uint8)
        orange = cv2.morphologyEx(orange, cv2.MORPH_OPEN, kernel)
        contours, _ = cv2.findContours(orange, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        candidates = []  # (score, cx, cy, r)
        n_pass_area = n_pass_shape = n_pass_border = n_pass_one = 0
        best_reject = None  # (reason, cx, cy, r) — for diagnostic

        for c in contours:
            check_exit()
            area = cv2.contourArea(c)
            if not (KI_V8_AREA_MIN < area < KI_V8_AREA_MAX):
                continue
            n_pass_area += 1
            x, y, w_, h_ = cv2.boundingRect(c)
            if h_ == 0 or w_ == 0:
                continue
            aspect = float(w_) / h_
            if abs(1.0 - aspect) > KI_V8_ASPECT_TOL:
                continue
            (ex, ey), er = cv2.minEnclosingCircle(c)
            er = max(er, 1.0)
            area_ratio = area / (np.pi * er * er)
            if area_ratio < KI_V8_AREA_RATIO_MIN:
                continue
            n_pass_shape += 1
            cx, cy, r = int(ex), int(ey), int(er)
            if not _ki_v8_check_dark_border(img_bgr, cx, cy, r):
                if best_reject is None:
                    best_reject = ("border", cx, cy, r)
                continue
            n_pass_border += 1
            if not _ki_v8_check_vertical_one(img_bgr, cx, cy, r):
                if best_reject is None or best_reject[0] == "border":
                    best_reject = ("digit", cx, cy, r)
                continue
            n_pass_one += 1
            score = area_ratio + (1.0 - abs(1.0 - aspect))
            candidates.append((score, cx, cy, r))

        if not candidates:
            now2 = time.time()
            if now2 - _ki_v8_no_dot_last_log_ts >= KI_NO_DOT_LOG_INTERVAL_SEC:
                _ki_v8_no_dot_last_log_ts = now2
                orange_px = int((orange > 0).sum())
                br_str = ""
                if best_reject is not None:
                    rs, bcx, bcy, br_ = best_reject
                    br_str = f" reject={rs} at=({bcx+monitor['left']},{bcy+monitor['top']}) r={br_}"
                print(f"[KI v8] No dot — orange_px={orange_px} contours={len(contours)} "
                      f"pass_area={n_pass_area} shape={n_pass_shape} "
                      f"border={n_pass_border} one={n_pass_one}{br_str}")
                try:
                    dbg = os.path.join(JSON_DIR, "ki_debug")
                    os.makedirs(dbg, exist_ok=True)
                    cv2.imwrite(os.path.join(dbg, "last_frame.png"), img_bgr)
                    cv2.imwrite(os.path.join(dbg, "last_orange_mask.png"), orange)
                    if best_reject is not None:
                        _, bcx, bcy, br_ = best_reject
                        pad = max(80, br_ * 3)
                        x0c = max(0, bcx - pad); y0c = max(0, bcy - pad)
                        x1c = min(img_bgr.shape[1], bcx + pad)
                        y1c = min(img_bgr.shape[0], bcy + pad)
                        cv2.imwrite(os.path.join(dbg, "last_best_candidate.png"),
                                    img_bgr[y0c:y1c, x0c:x1c])
                except Exception:
                    pass
            _ki_v8_state["consecutive_seen"] = 0
            return

        candidates.sort(key=lambda c: c[0], reverse=True)
        _, cx, cy, dot_r = candidates[0]

        # Multi-frame stability: only click after the same dot has been detected
        # at consistent coords across N consecutive calls. Filters out transient
        # detections (single-frame noise).
        last = _ki_v8_state["last_dot"]
        if last is not None:
            lcx, lcy, lt = last
            if (abs(cx - lcx) <= KI_V8_STABLE_POS_TOL_PX and
                abs(cy - lcy) <= KI_V8_STABLE_POS_TOL_PX and
                (now - lt) <= 1.5):
                _ki_v8_state["consecutive_seen"] += 1
            else:
                _ki_v8_state["consecutive_seen"] = 1
        else:
            _ki_v8_state["consecutive_seen"] = 1
        _ki_v8_state["last_dot"] = (cx, cy, now)

        if _ki_v8_state["consecutive_seen"] < KI_V8_STABLE_FRAMES_REQUIRED:
            return

        TELEMETRY["ki_dots_found"] += 1
        screen_cx = cx + monitor["left"]
        screen_cy = cy + monitor["top"]

        def _commit_click(label):
            print(f">>> [KI v8] Click ({label}): ({screen_cx}, {screen_cy}) r={dot_r}")
            _ki_click(screen_cx, screen_cy)
            _ki_v8_state["last_click_at"] = time.time()
            _ki_v8_state["consecutive_seen"] = 0
            _ki_v8_state["last_dot"] = None
            TELEMETRY["ki_clicks"] += 1

        # Dispatch on mode.
        if KI_V8_MODE == "v2_ring":
            # ── v2: outer→inner scan + motion gate ────────────────────────────
            # Find outermost bright circle in [r_min, r_max]. r_min is set above the
            # dot's static outline so we can't lock onto it. Motion gate requires the
            # detected radius to have actually been DECREASING (or have been above
            # target_r at some earlier point) before firing — this kills any false
            # fires on static structure that happens to live in the scan range.
            target_r = max(2, int(dot_r * KI_V8_V2_TARGET_R_FACTOR))
            count_thresh = int(KI_V8_V2_BRIGHT_COUNT_THRESHOLD)
            r_min_scan = int(dot_r * KI_V8_V2_R_MIN_FACTOR)
            r_max_scan = int(dot_r * KI_V8_V2_R_MAX_FACTOR)
            streak_min = int(KI_V8_V2_MOTION_STREAK_MIN)
            grab_half = r_max_scan + 10

            # Adaptive brightness threshold: sample background well outside the ring's
            # possible position. Set threshold to bg_max + 10 so the floor (whatever
            # color it is — HTC gray-cyan, GC dark, etc.) is just barely excluded.
            # Falls back to the static configured threshold if the sample is unreliable.
            bg_max_sampled = None
            try:
                _bg_img, _bg_cx, _bg_cy = _ki_v8_clamped_grab(
                    sct, monitor["left"] + cx, monitor["top"] + cy, grab_half,
                )
                _bg_r = max(int(dot_r * 3.0), r_max_scan + 5)
                _bg_angles = np.linspace(0, 2 * np.pi, 24, endpoint=False)
                _bx = np.clip((_bg_cx + _bg_r * np.cos(_bg_angles)).astype(np.int32), 0, _bg_img.shape[1] - 1)
                _by = np.clip((_bg_cy + _bg_r * np.sin(_bg_angles)).astype(np.int32), 0, _bg_img.shape[0] - 1)
                _bg_max_each = _bg_img[_by, _bx].astype(np.int16).max(axis=1)
                bg_max_sampled = int(np.median(_bg_max_each))
                # +10 — restored after +5 caused detection failures on bright HTC zones
                # (bg_max=216 → threshold=221, but many ring pixels were below 221 on those
                # frames, leaving NO ring detected for those dots). +10 is safer because the
                # static brightness floor (220) acts as a low bound — when bg is dark, threshold
                # stays at 220. When bg is bright, threshold ratchets above it with proper margin.
                brightness_thresh = max(int(KI_V8_V2_BRIGHTNESS_THRESHOLD), bg_max_sampled + 10)
                brightness_thresh = min(255, brightness_thresh)
            except Exception:
                brightness_thresh = int(KI_V8_V2_BRIGHTNESS_THRESHOLD)

            if VERBOSE_DETECTOR_LOGS:
                print(
                    f"[KI v8] v2 scan begin dot@({cx},{cy}) dot_r={dot_r} "
                    f"target_r={target_r} range=[{r_min_scan},{r_max_scan}] "
                    f"bright>={brightness_thresh} (bg_max={bg_max_sampled}) "
                    f"count>={count_thresh} streak>={streak_min} "
                    f"contrast>={KI_V8_V2_CONTRAST_DELTA}"
                )

            history = []  # (t_ms, ring_r, count)
            contrast_history = []  # shadow detector only; never controls clicks
            last_small = None
            # Independent motion state per detector method. Bright keeps click
            # authority; contrast runs the same gate logic as a shadow so live
            # logs show when it WOULD have fired and how that compares.
            bright_track = {"prev_r": None, "streak": 1, "saw_above_target": False}
            contrast_track = {"prev_r": None, "streak": 1, "saw_above_target": False}
            contrast_shadow_fire_ms = None
            t_start = time.time()
            while time.time() - t_start < KI_V8_V2_TIMEOUT_SEC:
                check_exit()
                small, local_cx, local_cy = _ki_v8_clamped_grab(
                    sct, monitor["left"] + cx, monitor["top"] + cy, grab_half,
                )
                last_small = small
                # Inline the scan so we can also capture the full profile for the live
                # debug image. find_ring_radius does this internally but throws away
                # the profile — duplicate the logic here to keep the profile around.
                radii = list(range(r_min_scan, r_max_scan + 1, max(1, int(KI_V8_V2_SCAN_STEP_PX))))
                profile = [
                    _ki_v8_count_bright_at_radius(
                        small, local_cx, local_cy, r,
                        n_samples=KI_V8_V2_SAMPLE_COUNT,
                        brightness_threshold=brightness_thresh,
                    )
                    for r in radii
                ]
                contrast_profile = _ki_v8_local_contrast_profile(
                    small, local_cx, local_cy, radii,
                    n_samples=KI_V8_V2_SAMPLE_COUNT,
                    contrast_delta=KI_V8_V2_CONTRAST_DELTA,
                )
                if profile:
                    peak_i = max(range(len(profile)), key=lambda i: profile[i])
                    ring_count = profile[peak_i]
                    if profile[peak_i] >= count_thresh:
                        ring_r = radii[peak_i]
                    else:
                        ring_r = None
                else:
                    ring_r = None
                    ring_count = 0
                if not history and KI_V8_V2_DEBUG_IMAGE:
                    out_dir = os.path.join(JSON_DIR, "ki_debug", "v2")
                    os.makedirs(out_dir, exist_ok=True)
                    cv2.imwrite(os.path.join(out_dir, "scan_start.png"), small)
                _ki_v8_save_live_debug(
                    small, local_cx, local_cy, dot_r, target_r,
                    r_min_scan, r_max_scan, profile, ring_r,
                )
                t_ms = int((time.time() - t_start) * 1000)
                history.append((t_ms, ring_r, ring_count))
                if contrast_profile:
                    contrast_peak_i = max(range(len(contrast_profile)), key=lambda i: contrast_profile[i])
                    contrast_count = contrast_profile[contrast_peak_i]
                    if contrast_profile[contrast_peak_i] >= count_thresh:
                        contrast_r = radii[contrast_peak_i]
                    else:
                        contrast_r = None
                else:
                    contrast_r = None
                    contrast_count = 0
                contrast_history.append((t_ms, contrast_r, contrast_count))

                # Contrast runs the identical gate on its own independent track,
                # never sharing motion state with bright. With contrast_click off
                # it only logs when it would have fired (shadow mode).
                contrast_lead_px = _ki_v8_lead_px(contrast_history)
                if contrast_shadow_fire_ms is None and _ki_v8_track_update(
                        contrast_track, contrast_r, target_r, contrast_lead_px):
                    contrast_shadow_fire_ms = t_ms
                    if KI_V8_V2_CONTRAST_CLICK:
                        gate_reason = ("saw>target" if contrast_track["saw_above_target"]
                                       else f"streak={contrast_track['streak']}")
                        lead_note = f" lead={contrast_lead_px:.1f}px" if contrast_lead_px > 0 else ""
                        decision = (
                            f"v2 converged[contrast] r={contrast_r} target={target_r} "
                            f"count={contrast_count}/{KI_V8_V2_SAMPLE_COUNT} "
                            f"after {t_ms}ms ({gate_reason}){lead_note}"
                        )
                        _commit_click(decision)
                        _ki_v8_log_v2_history(history)
                        _ki_v8_log_v2_history(contrast_history, "v2 contrast")
                        _ki_v8_save_v2_debug_image(
                            small, local_cx, local_cy, dot_r, target_r,
                            r_min_scan, r_max_scan, contrast_history, decision,
                        )
                        _ki_v8_grade_perfect(sct, monitor, cx, cy, dot_r)
                        return
                    if VERBOSE_DETECTOR_LOGS:
                        print(
                            f"[KI v8] SHADOW contrast would fire r={contrast_r} "
                            f"target={target_r} count={contrast_count}/{KI_V8_V2_SAMPLE_COUNT} "
                            f"after {t_ms}ms"
                        )

                bright_lead_px = _ki_v8_lead_px(history)
                if _ki_v8_track_update(bright_track, ring_r, target_r, bright_lead_px):
                    gate_reason = ("saw>target" if bright_track["saw_above_target"]
                                   else f"streak={bright_track['streak']}")
                    lead_note = f" lead={bright_lead_px:.1f}px" if bright_lead_px > 0 else ""
                    decision = (
                        f"v2 converged r={ring_r} target={target_r} "
                        f"bright={ring_count}/{KI_V8_V2_SAMPLE_COUNT} "
                        f"after {t_ms}ms ({gate_reason}){lead_note}"
                    )
                    if contrast_shadow_fire_ms is not None:
                        decision += f" [shadow-contrast fired at {contrast_shadow_fire_ms}ms]"
                    _commit_click(decision)
                    _ki_v8_log_v2_history(history)
                    _ki_v8_log_v2_history(contrast_history, "v2 contrast")
                    _ki_v8_save_v2_debug_image(
                        small, local_cx, local_cy, dot_r, target_r,
                        r_min_scan, r_max_scan, history, decision,
                    )
                    _ki_v8_grade_perfect(sct, monitor, cx, cy, dot_r)
                    return
                time.sleep(0.005)

            # Timeout — ring never crossed target with motion confirmed.
            TELEMETRY["ki_timeouts"] += 1
            decision = (
                f"v2-timeout target_r={target_r} "
                f"saw_above={bright_track['saw_above_target']} "
                f"max_streak={bright_track['streak']}"
                + (f" [shadow-contrast fired at {contrast_shadow_fire_ms}ms]"
                   if contrast_shadow_fire_ms is not None else "")
            )
            _commit_click(decision)
            _ki_v8_log_v2_history(history)
            _ki_v8_log_v2_history(contrast_history, "v2 contrast")
            if last_small is not None:
                _ki_v8_save_v2_debug_image(
                    last_small, local_cx, local_cy, dot_r, target_r,
                    r_min_scan, r_max_scan, history, decision,
                )
            _ki_v8_grade_perfect(sct, monitor, cx, cy, dot_r)
            return

        # ── v1: time-based delay (default) ─────────────────────────────────────
        # Click after KI_V8_CLICK_DELAY_SEC seconds. Default 0 = immediate, which
        # matched the user's pass-5 testing where every circle got clicked Perfectly.
        delay = max(0.0, float(KI_V8_CLICK_DELAY_SEC))
        if delay > 0:
            safe_sleep(delay)
        _commit_click(f"v1 +{int(delay*1000)}ms")
    except (QuitException, SkipMinigameException):
        raise
    except Exception as e:
        import traceback
        print(f"[KI v8] Error: {e}")
        for ln in traceback.format_exc().splitlines()[-4:]:
            if ln.strip(): print(f"[KI v8] {ln}")


def _ki_click(screen_cx, screen_cy):
    # One-call atomic click: SendInput pipeline (move + click) at the target coords.
    # Previously was two phases (SetCursorPos then mouse_event(0,0)); Roblox often
    # accepted the cursor move but silently dropped the click.
    click_at(int(screen_cx), int(screen_cy))
    safe_sleep(0.05)


## Progress template/OCR progression was removed (yellow pixel progression only).
def is_area_green(img_bgr, center_x, center_y, box_size=18):
    # Was box_size=5 (11x11 patch). Bumped to 18 (37x37) because letter centroids
    # sometimes land on the dark stroke of the letter itself (D has a thick vertical
    # bar at its centroid). With 11x11, the patch could miss the green background
    # entirely; 37x37 always covers the slot's green halo.
    y_min = max(0, center_y - box_size)
    y_max = min(img_bgr.shape[0], center_y + box_size)
    x_min = max(0, center_x - box_size)
    x_max = min(img_bgr.shape[1], center_x + box_size)
    patch = img_bgr[y_min:y_max, x_min:x_max]
    if patch.size == 0: return False
    b, g, r = patch[:, :, 0], patch[:, :, 1], patch[:, :, 2]
    green_mask = (g > 100) & (g.astype(np.int16) > (r.astype(np.int16) + 30)) & (g.astype(np.int16) > (b.astype(np.int16) + 30))
    return np.any(green_mask)


def is_area_red(img_bgr, center_x, center_y, box_size=18):
    """Mirror of is_area_green: returns True if the patch around (cx,cy) has any dominantly red pixels."""
    y_min = max(0, center_y - box_size)
    y_max = min(img_bgr.shape[0], center_y + box_size)
    x_min = max(0, center_x - box_size)
    x_max = min(img_bgr.shape[1], center_x + box_size)
    patch = img_bgr[y_min:y_max, x_min:x_max]
    if patch.size == 0: return False
    b, g, r = patch[:, :, 0], patch[:, :, 1], patch[:, :, 2]
    red_mask = (r > 120) & (r.astype(np.int16) > (g.astype(np.int16) + 40)) & (r.astype(np.int16) > (b.astype(np.int16) + 40))
    return np.any(red_mask)


def _letter_state_at(sct, monitor_area, cx, cy):
    """One grab, two checks. Returns 'green', 'red', or 'pending'."""
    try:
        img_bgr = _grab_reference_box(sct, monitor_area)
    except Exception:
        return 'pending'
    if is_area_green(img_bgr, cx, cy):
        return 'green'
    if is_area_red(img_bgr, cx, cy):
        return 'red'
    return 'pending'


def _scans_match(a, b, tol_px=10):
    """Two letter-scan lists (already x-sorted) describe the same on-screen state."""
    if not a or not b: return False
    if len(a) != len(b): return False
    for (ac, ax, ay), (bc, bx, by) in zip(a, b):
        if ac != bc: return False
        if abs(ax - bx) > tol_px or abs(ay - by) > tol_px: return False
    return True


def _stable_scan_letters(sct, templates, max_wait_sec, settle_ms=25):
    """Wait until two consecutive scans of AGILITY_BOX return matching letters.
    Filters out transient white-flash states between letters/strings.
    Returns the matched scan, or [] if max_wait_sec elapses."""
    t0 = time.time()
    prev = []
    gap = settle_ms / 1000.0
    while time.time() - t0 < max_wait_sec:
        check_exit()
        scan = _scan_letters_with_pos(sct, AGILITY_BOX, templates)
        if scan and _scans_match(scan, prev):
            return scan
        prev = scan
        time.sleep(gap)
    return []

# --- MAIN LOOP ---
def run_master_controller():
    global CONTROLLER_PAUSED, CURRENT_TRAINING_STATE, PROGRESSION_STATE_STARTED_AT
    global TRAINING_MENU_VISIBLE
    check_exit()
    if _stop_if_starting_on_death_screen():
        return
    CONTROLLER_PAUSED = False
    TRAINING_MENU_VISIBLE = False
    print("[XynMacro] macro loop started")

    # Settings are loaded before the UI server starts and live changes already
    # update these globals. Reloading here can overwrite a just-saved UI change.
    if _start_background_game_monitor() is False:
        raise RuntimeError(
            "Previous game-state monitor is still stopping. Wait a moment and try Start again."
        )

    agility_templates = []
    for char in ['w', 'a', 's', 'd']:
        tpl_path = os.path.join(BASE_DIR, f"tpl_{char}.png")
        if os.path.exists(tpl_path):
            base = cv2.imread(tpl_path, cv2.IMREAD_GRAYSCALE)
            for s in SCALES:
                agility_templates.append((cv2.resize(base, (int(base.shape[1]*s), int(base.shape[0]*s))), char))

    training_menu_template = cv2.imread(
        os.path.join(BASE_DIR, "tpl_training_mode.png"), cv2.IMREAD_GRAYSCALE
    )
    if training_menu_template is None:
        print("[WARN] Training menu template missing; menu shadow detector disabled.")

    runtime_order = _sanitize_training_order(TRAINING_ORDER_CUSTOM)
    if not runtime_order:
        raise RuntimeError("Training Order is empty; add at least one stat before Start")

    current_idx = 0
    state = runtime_order[current_idx]
    skipped_stats = []

    if SHOW_DEBUG_HUD:
        cv2.namedWindow("BOT VISION HUD", cv2.WINDOW_NORMAL)
        cv2.resizeWindow("BOT VISION HUD", 400, 200)

    print(f"STARTING: {state}")
    print(f"[ORDER] {' -> '.join(runtime_order)}")
    if START_DELAY > 0:
        print(f"Starting in {_format_seconds(START_DELAY)}... (focus the game window)")
        safe_sleep(START_DELAY)

    def _build_monitor_from_game():
        """Build a capture rect only from a confirmed live Roblox client."""
        return _confirmed_game_capture_rect(), "game-window"

    with mss.MSS() as sct:
        monitor, monitor_source = _build_monitor_from_game()
        print(f"[CAPTURE] Region: ({monitor['left']},{monitor['top']}) {monitor['width']}x{monitor['height']} [{monitor_source}]")
        if DIAGNOSTIC_MODE:
            report = _diagnostic_report()
            print(f"[DIAGNOSTIC] {report.get('summary', 'Unavailable')}")
            print(
                f"[DIAGNOSTIC] foreground={report.get('foreground')} "
                f"dpi={report.get('dpi')} templates={report.get('template_scores')}"
            )
            print(f"[DIAGNOSTIC] {report.get('capture_note', '')}")
            for issue in report.get("issues", []):
                print(f"[DIAGNOSTIC WARN] {issue}")
        window_last_refresh = time.time()

        # Yellow tracking state
        yellow_last_poll = 0.0
        yellow_last_seen_ts = time.time()
        last_yellow_log_ts = 0.0
        awaiting_first_yellow = True  # load grace: first yellow after start/switch
        menu_match_streak = 0
        menu_shadow_visible = None
        def _menu_stably_visible():
            """Fresh N-sample check that the Training Mode header is on screen.
            Only trusted in the 'visible' direction: it needs every sample at
            score >= 0.90, and live data puts the open menu at 0.9998 with
            nothing else observed above 0.421."""
            if training_menu_template is None:
                return False
            for i in range(TRAINING_MENU_STABLE_FRAMES):
                match, _score = detect_training_menu(sct, monitor, training_menu_template)
                if not match:
                    return False
                if i < TRAINING_MENU_STABLE_FRAMES - 1:
                    safe_sleep(0.05)
            return True

        def _click_trait_and_confirm(category, log_prefix):
            """Click a trait and require the menu to disappear stably."""
            nonlocal monitor, monitor_source
            for attempt in range(1, 4):
                monitor, monitor_source = _build_monitor_from_game()
                bx, by = _button_screen_point(category, monitor)
                focus_game_window()
                print(
                    f"{log_prefix} Clicking trait: {category} @ "
                    f"({int(bx)}, {int(by)}) attempt {attempt}/3"
                )
                click_at(int(bx), int(by))
                hidden_streak = 0
                deadline = time.time() + 1.25
                while time.time() < deadline:
                    visible, _score = detect_training_menu(
                        sct, monitor, training_menu_template
                    )
                    hidden_streak = 0 if visible else hidden_streak + 1
                    if hidden_streak >= TRAINING_MENU_STABLE_FRAMES:
                        print(f"{log_prefix} {category} selection confirmed")
                        return True
                    safe_sleep(0.05)
                print(f"{log_prefix} Menu stayed open; retrying trait click")
            return False

        # Initial Start is allowed from the open trait menu, Game Menu, or normal
        # gameplay. Confirm the menu before selecting the first trait so a blind
        # Health click can never land inside the game area.
        if state in BUTTONS:
            if _menu_stably_visible():
                print("[INIT MENU] Already open — selecting first trait")
            elif training_menu_template is None:
                print("[ERROR] Cannot confirm the Training Mode menu: template missing.")
                _record_run_outcome("error", "Training Mode template is missing", state)
                return
            else:
                restored_from_game_menu = False
                if SENZU_ENABLED:
                    senzu_assets = _senzu_assets()
                    game_menu_visible = _wait_for_senzu_screen(
                        sct,
                        senzu_assets["game_menu"],
                        (0, 340, 220, 80),
                        timeout=0.35,
                    )[0] is not None
                    if game_menu_visible:
                        print("[INIT MENU] Game Menu visible — restoring Training Mode first")
                        restored_from_game_menu = _close_inventory_to_training(sct, senzu_assets)
                        if not restored_from_game_menu:
                            print("[ERROR] Could not safely close Game Menu; Start cancelled.")
                            _record_run_outcome(
                                "error", "Could not safely close Game Menu during startup", state
                            )
                            return

                if not restored_from_game_menu:
                    print("[INIT MENU] Not visible — pressing Tab")
                    focus_game_window()
                    hardware_tap('tab')
                    t_tab = time.time()
                    while time.time() - t_tab < 2.0:
                        match, _score = detect_training_menu(sct, monitor, training_menu_template)
                        if match and _menu_stably_visible():
                            print(f"[INIT MENU] Open after {int((time.time()-t_tab)*1000)}ms")
                            break
                        safe_sleep(0.1)
                    else:
                        print("[ERROR] Training Mode menu was not confirmed after Tab; Start cancelled.")
                        _record_run_outcome(
                            "error", "Training Mode menu was not confirmed during startup", state
                        )
                        return

            if SENZU_ENABLED:
                # Before the first trait is active, GC can make M reopen the
                # Training Mode panel instead of the Game Menu. That makes an
                # inventory preflight ambiguous and used to cancel an otherwise
                # valid Start. Defer slot loading to the first red-HP recovery,
                # where eat_senzu() exits the active minigame before navigating
                # H -> Inventory. It already handles a missing slot, empty stock,
                # the ghost-slot redraw, and the configured 0G fallback.
                print(
                    "[SENZU] Startup inventory check deferred until the first "
                    "critical-HP recovery"
                )

            if SENZU_DISABLED_FOR_RUN and SENZU_ZERO_GRAVITY_ON_EMPTY:
                print(
                    "[GRAVITY] Auto-raise skipped because allowed Senzu stock is empty "
                    "and the 0G fallback is enabled"
                )
            elif not _raise_gc_gravity(sct, monitor, GC_GRAVITY_TARGET_G):
                print("[ERROR] GC gravity target could not be confirmed; Start cancelled.")
                _record_run_outcome(
                    "error", "GC gravity target could not be confirmed", state
                )
                return

            if not _click_trait_and_confirm(state, "[INIT]"):
                raise RuntimeError(f"Could not confirm {state} trait selection after 3 clicks")
            PROGRESSION_STATE_STARTED_AT = time.time()
            if "Ki" in state:
                safe_sleep(0.1)
            else:
                safe_sleep(0.5)
                safe_sleep(NEW_GAME_WAIT)
            # Do not let the background progression watcher interrupt startup's
            # post-selection settle wait. It begins tracking once startup is done.
            PROGRESSION_COMPLETE_REQUESTED.clear()
            CURRENT_TRAINING_STATE = state

        # Restamp after startup menu handling so startup time cannot count as
        # "no yellow" and skip the first category.
        yellow_last_seen_ts = time.time()
        awaiting_first_yellow = True

        if NO_YELLOW_FALLBACK_ENABLED:
            print(
                "[OK] Progression: label completion detector; "
                f"yellow timeout is a {NO_YELLOW_TIMEOUT_SEC:.1f}s fallback until tracking locks"
            )
        else:
            print(
                "[OK] Progression: label completion detector; "
                "no-yellow fallback switch is disabled (L skips manually)"
            )

        # Manual skip debounce (hotkey flag is in MANUAL_NEXT_REQUESTED)
        last_manual_next_ts = 0.0

        # Pause state (hotkey flag is in PAUSE_TOGGLE_REQUESTED)
        paused = False

        def do_switch(reason, *, current_completed=True):
            nonlocal state, current_idx, yellow_last_poll, yellow_last_seen_ts
            nonlocal menu_match_streak, menu_shadow_visible, awaiting_first_yellow
            global CURRENT_TRAINING_STATE, PROGRESSION_STATE_STARTED_AT
            print(reason)
            # Pause progression and Auto-Senzu decisions while menus are changing.
            CURRENT_TRAINING_STATE = None
            TELEMETRY["switches"] += 1
            if not current_completed and state not in skipped_stats:
                skipped_stats.append(state)
            next_idx = current_idx + 1
            if next_idx >= len(runtime_order):
                outcome, outcome_reason = _training_order_result(skipped_stats)
                print("ALL DONE!" if outcome == "completed" else outcome_reason.upper())
                _record_run_outcome(outcome, outcome_reason, state)
                return True
            # Menu handshake: Tab TOGGLES the menu, so pressing it while the menu
            # is already open (game kicked us back, or a prior Tab landed) would
            # close it and the trait click would hit nothing.
            if _menu_stably_visible():
                print("[MENU] Already open — skipping Tab")
            else:
                focus_game_window()
                hardware_tap('tab')
                if training_menu_template is not None:
                    t_tab = time.time()
                    while time.time() - t_tab < 2.0:
                        match, _s = detect_training_menu(sct, monitor, training_menu_template)
                        if match and _menu_stably_visible():
                            print(f"[MENU] Open after {int((time.time()-t_tab)*1000)}ms")
                            break
                        safe_sleep(0.1)
                    else:
                        raise RuntimeError(
                            "Training Mode menu was not confirmed after Tab; switch cancelled"
                        )
                else:
                    safe_sleep(0.5)
            next_cat = runtime_order[next_idx]
            if not _click_trait_and_confirm(next_cat, "[SWITCH]"):
                raise RuntimeError(
                    f"Could not confirm {next_cat} trait selection after 3 clicks"
                )
            state = next_cat; current_idx = next_idx
            PROGRESSION_STATE_STARTED_AT = time.time()
            PROGRESSION_COMPLETE_REQUESTED.clear()
            print(f"NEW STATE: {state}")
            if "Ki" in state:
                safe_sleep(0.1)
            else:
                safe_sleep(0.5)
                safe_sleep(NEW_GAME_WAIT)
            PROGRESSION_COMPLETE_REQUESTED.clear()
            CURRENT_TRAINING_STATE = state

            # reset yellow tracking
            yellow_last_poll = 0.0
            yellow_last_seen_ts = time.time()
            awaiting_first_yellow = True
            menu_match_streak = 0
            menu_shadow_visible = None
            return False

        global MANUAL_NEXT_REQUESTED, PAUSE_TOGGLE_REQUESTED
        while True:
            if SENZU_CONTROLLER_ACTIVE.is_set():
                time.sleep(0.05)
                continue
            if SENZU_CONTROLLER_RESUME_REQUIRED.is_set():
                # Discard decisions sampled before Auto-Senzu changed menus,
                # then restart all timing from the restored active category.
                PROGRESSION_COMPLETE_REQUESTED.clear()
                yellow_last_poll = 0.0
                yellow_last_seen_ts = time.time()
                awaiting_first_yellow = True
                menu_match_streak = 0
                menu_shadow_visible = None
                SENZU_CONTROLLER_RESUME_REQUIRED.clear()
                continue
            # Pause + manual-skip flag handling happens BEFORE check_exit, because
            # check_exit raises SkipMinigameException when MANUAL_NEXT_REQUESTED is set
            # (so it can unwind out of deep minigame loops). If we let that raise here,
            # the consumer block below would never run.
            if PAUSE_TOGGLE_REQUESTED:
                PAUSE_TOGGLE_REQUESTED = False
                paused = not paused
                CONTROLLER_PAUSED = paused
                if paused:
                    print("\n[PAUSED] Press U to resume")
                else:
                    yellow_last_seen_ts = time.time()
                    yellow_last_poll = 0.0
                    print("\n[RESUMED]")
            if paused:
                # Completion/manual-next events intentionally unwind blocking
                # minigame loops. While paused, preserve them for resume instead
                # of letting that unwind terminate the whole macro.
                try:
                    safe_sleep(0.1)
                except SkipMinigameException:
                    pass
                continue

            if MANUAL_NEXT_REQUESTED:
                MANUAL_NEXT_REQUESTED = False
                if (time.time() - last_manual_next_ts) >= MANUAL_NEXT_DEBOUNCE:
                    last_manual_next_ts = time.time()
                    try:
                        if do_switch(
                            f"[SKIP] Manual next ({MANUAL_NEXT_KEY.upper()})",
                            current_completed=False,
                        ):
                            break
                    except SenzuControllerPause:
                        pass
                    except QuitException:
                        raise
                continue  # restart loop after switch — don't fall into a stale minigame

            if PROGRESSION_COMPLETE_REQUESTED.is_set():
                PROGRESSION_COMPLETE_REQUESTED.clear()
                try:
                    if do_switch(f"[PROG] {state} reached its displayed target -> Switching..."):
                        break
                except SenzuControllerPause:
                    pass
                continue

            try:
                check_exit()

                CURRENT_TRAINING_STATE = state

                # --- DYNAMIC CAPTURE REFRESH ---
                # The Roblox window can move between monitors (user dragging, alt-tab,
                # etc.). Refresh the capture rect every ~2s so a stale region doesn't
                # trap us looking at a static piece of wallpaper.
                if time.time() - window_last_refresh > 2.0:
                    window_last_refresh = time.time()
                    new_monitor, new_source = _build_monitor_from_game()
                    if (new_monitor["left"], new_monitor["top"], new_monitor["width"], new_monitor["height"]) != \
                       (monitor["left"], monitor["top"], monitor["width"], monitor["height"]):
                        monitor = new_monitor
                        print(f"[CAPTURE] Region moved: ({monitor['left']},{monitor['top']}) {monitor['width']}x{monitor['height']} [{new_source}]")
                elif GAME_HWND is not None:
                    # High-frequency reference-box captures can notice a moved
                    # window before this loop's slower discovery pass. Adopt that
                    # already-refreshed geometry without another Win32 search.
                    live_geometry = {
                        "left": int(GAME_OFFSET_X),
                        "top": int(GAME_OFFSET_Y),
                        "width": int(GAME_WIDTH),
                        "height": int(GAME_HEIGHT),
                    }
                    if live_geometry != monitor:
                        monitor = live_geometry
                        monitor_source = "game-window"

                # --- PROGRESSION YELLOW CHECK (pixel sampling, non-blocking) ---
                now = time.time()
                if now - yellow_last_poll >= YELLOW_SAMPLE_INTERVAL_SEC:
                    yellow_last_poll = now
                    # The agility chain loop samples yellow itself while it holds
                    # the thread; fold that in so missing_for stays truthful.
                    yellow_last_seen_ts = max(yellow_last_seen_ts, _agility_chain_yellow_ts)
                    y_count, y_raw, y_mask = detect_yellow_progress(sct, state, monitor)
                    present = (y_count > 0)
                    if present:
                        yellow_last_seen_ts = now
                        if awaiting_first_yellow:
                            awaiting_first_yellow = False
                            print(_first_yellow_message())
                    missing_for = (now - yellow_last_seen_ts)

                    if SHOW_DEBUG_HUD:
                        score = max(0.0, min(1.0, 1.0 - (missing_for / max(0.1, NO_YELLOW_TIMEOUT_SEC))))
                        update_debug_hud(y_raw, y_mask, score, f"YEL {y_count} pres {present} miss {missing_for:.1f}s")

                    if YELLOW_DEBUG_LOG and (now - last_yellow_log_ts) >= YELLOW_DEBUG_LOG_INTERVAL:
                        last_yellow_log_ts = now
                        print(f"[YELLOW] {state}: cnt={y_count} pres={present} miss={missing_for:.1f}s")

                    # Slow loads/ping: before the first yellow of a stat, allow the
                    # longer load grace so a loading screen can't burn the timeout.
                    switch_after = (max(NO_YELLOW_TIMEOUT_SEC, TRAINING_LOAD_GRACE_SEC)
                                    if awaiting_first_yellow else NO_YELLOW_TIMEOUT_SEC)
                    progression_is_tracked = PROGRESSION_TRACKED_STATE == state
                    if (NO_YELLOW_FALLBACK_ENABLED
                            and not progression_is_tracked
                            and not present
                            and missing_for >= switch_after):
                        if do_switch(f"[OK] NO YELLOW for {missing_for:.1f}s -> Switching..."):
                            break

                    if training_menu_template is not None:
                        menu_match, menu_score = detect_training_menu(
                            sct, monitor, training_menu_template
                        )
                        menu_match_streak = menu_match_streak + 1 if menu_match else 0
                        stable_visible = menu_match_streak >= TRAINING_MENU_STABLE_FRAMES
                        TRAINING_MENU_VISIBLE = stable_visible
                        if menu_shadow_visible is None or stable_visible != menu_shadow_visible:
                            menu_shadow_visible = stable_visible
                            label = "visible" if stable_visible else "hidden"
                            print(
                                f"[MENU shadow] {label} score={menu_score:.3f} "
                                f"streak={menu_match_streak}/{TRAINING_MENU_STABLE_FRAMES}"
                            )

                if TRAINING_MENU_VISIBLE:
                    # The user may press Tab, or the game may return here on its
                    # own. Never keep firing minigame input through an open menu.
                    # Give the progression watcher one beat to classify a truly
                    # completed stat before resuming an incomplete one.
                    time.sleep(0.25)
                    if PROGRESSION_COMPLETE_REQUESTED.is_set():
                        continue
                    if not _click_trait_and_confirm(state, "[MENU RECOVERY]"):
                        raise RuntimeError(
                            f"Training Mode is open and {state} could not be resumed"
                        )
                    TRAINING_MENU_VISIBLE = False
                    menu_match_streak = 0
                    menu_shadow_visible = False
                    yellow_last_seen_ts = time.time()
                    awaiting_first_yellow = True
                    PROGRESSION_STATE_STARTED_AT = time.time()
                    PROGRESSION_COMPLETE_REQUESTED.clear()
                    print(f"[MENU RECOVERY] Resumed incomplete stat: {state}")
                    continue

                # --- MINIGAME LOGIC --- (wrapped so L key during a green-observe wait
                # unwinds back to the loop top, where MANUAL_NEXT_REQUESTED gets consumed
                # on the next iteration).
                try:
                    if state == "Health" and ENABLE_HEALTH_MINIGAME:
                        logic_health(sct, monitor)
                    elif state in ["Agility", "Physical Damage"] and ENABLE_PHYSICAL_MINIGAME:
                        if AGILITY_MODE == "v3":
                            logic_agility_v3(sct, agility_templates, state)
                        elif AGILITY_MODE == "v2":
                            logic_agility_v2(sct, agility_templates, state)
                        else:
                            logic_agility_v1(sct, agility_templates)
                    elif state in ["Ki Control", "Ki Damage"] and ENABLE_KI_MINIGAME:
                        logic_ki_v8(sct, monitor)
                except SkipMinigameException:
                    pass

                safe_sleep(0.001)

            except QuitException:
                # Propagate configured Stop immediately (no return-to-menu prompts).
                raise
            except SkipMinigameException:
                # Race: flag re-set between consumer and check_exit. Loop will catch it next iter.
                pass
            except Exception as e:
                print(f"Error: {e}")
                _record_run_outcome(
                    "error", f"Controller error: {e.__class__.__name__}: {e}", state
                )
                break

    _destroy_cv_windows()

def _ui_bool(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on", "y")
    return bool(value)


def _ui_is_running():
    return MACRO_THREAD is not None and MACRO_THREAD.is_alive()


def _get_screen_info():
    """Return bounds and active mode for the display containing Roblox."""
    unavailable = {
        "width": 0,
        "height": 0,
        "hz": 0,
        "left": 0,
        "top": 0,
        "right": 0,
        "bottom": 0,
        "device": "",
        "primary": False,
        "source": "unavailable",
    }
    monitor = _current_game_monitor_info()
    if GAME_HWND is None or monitor is None:
        return unavailable
    try:
        mode = win32api.EnumDisplaySettings(
            monitor["device"], win32con.ENUM_CURRENT_SETTINGS
        )
        width = int(getattr(mode, "PelsWidth", 0)) or monitor["width"]
        height = int(getattr(mode, "PelsHeight", 0)) or monitor["height"]
        hz = int(getattr(mode, "DisplayFrequency", 0))
    except Exception:
        width = monitor["width"]
        height = monitor["height"]
        hz = 0
    return {
        "width": width,
        "height": height,
        "hz": hz,
        "left": monitor["left"],
        "top": monitor["top"],
        "right": monitor["right"],
        "bottom": monitor["bottom"],
        "device": monitor["device"],
        "primary": monitor["primary"],
        "source": "game-monitor",
    }


def _ui_config_snapshot():
    """Return only supported user-facing settings and their normalized values."""
    return {
        "start_delay_sec": START_DELAY,
        "gc_gravity_target_g": GC_GRAVITY_TARGET_G,
        "prevent_sleep_while_running": PREVENT_SLEEP_WHILE_RUNNING,
        "restore_fullscreen_on_start": RESTORE_FULLSCREEN_ON_START,
        "display_confirm_changes": DISPLAY_CONFIRM_CHANGES,
        "shutdown_pc_when_finished": SHUTDOWN_PC_WHEN_FINISHED,
        "after_run_game_action": AFTER_RUN_GAME_ACTION,
        "after_run_on_failure": AFTER_RUN_ON_FAILURE,
        "auto_retry_on_failure": AUTO_RETRY_ON_FAILURE,
        "auto_retry_max_attempts": AUTO_RETRY_MAX_ATTEMPTS,
        "auto_retry_recovery_mode": AUTO_RETRY_RECOVERY_MODE,
        "auto_retry_walk_out": AUTO_RETRY_WALK_OUT,
        "auto_retry_walk_seconds": AUTO_RETRY_WALK_SECONDS,
        "diagnostic_mode": DIAGNOSTIC_MODE,
        "after_switch_wait_sec": NEW_GAME_WAIT,
        "no_yellow_timeout_sec": NO_YELLOW_TIMEOUT_SEC,
        "no_yellow_fallback_enabled": NO_YELLOW_FALLBACK_ENABLED,
        "manual_next_key": MANUAL_NEXT_KEY,
        "start_stop_hotkey": START_STOP_HOTKEY,
        "pause_hotkey": PAUSE_HOTKEY,
        "health_hit_cooldown_sec": HEALTH_HIT_COOLDOWN_SEC,
        "health_mode": HEALTH_MODE,
        "wasd_key_press_delay_sec": KEY_PRESS_DELAY,
        "wasd_stabilize_delay_sec": STABILIZE_DELAY,
        "wasd_post_burst_delay_sec": POST_COMBO_DELAY,
        "agility_mode": AGILITY_MODE,
        "agility_green_observe_sec": AGILITY_GREEN_OBSERVE_SEC,
        "agility_inter_string_wait_sec": AGILITY_INTER_STRING_WAIT_SEC,
        "agility_after_green_settle_sec": AGILITY_AFTER_GREEN_SETTLE_SEC,
        "training_order": list(_sanitize_training_order(TRAINING_ORDER_CUSTOM)),
        "ki_v8_mode": KI_V8_MODE,
        "ki_v8_click_delay_sec": KI_V8_CLICK_DELAY_SEC,
        "ki_v8_v2_target_r_factor": KI_V8_V2_TARGET_R_FACTOR,
        "ki_v8_v2_brightness_threshold": KI_V8_V2_BRIGHTNESS_THRESHOLD,
        "ki_v8_v2_bright_count_threshold": KI_V8_V2_BRIGHT_COUNT_THRESHOLD,
        "ki_latency_comp_ms": KI_LATENCY_COMP_MS,
        "senzu_enabled": SENZU_ENABLED,
        "senzu_slot": SENZU_SLOT,
        "senzu_delay_sec": SENZU_DELAY_SEC,
        "senzu_recovery_timeout_sec": SENZU_RECOVERY_TIMEOUT_SEC,
        "senzu_preference_mode": SENZU_PREFERENCE_MODE,
        "senzu_zero_gravity_on_empty": SENZU_ZERO_GRAVITY_ON_EMPTY,
    }


def _ui_state_snapshot():
    # Re-detect game window each poll so the UI reflects live state (user toggling
    # Roblox between windowed and fullscreen, moving the window, etc.). Skip when
    # macro is running so coords don't shift mid-run.
    running = _ui_is_running()
    if not running:
        update_game_window()
    with _run_result_lock:
        last_run = None if LAST_RUN_RESULT is None else dict(LAST_RUN_RESULT)
        if last_run is not None:
            last_run["telemetry"] = dict(last_run.get("telemetry", {}))
    return {
        "version": APP_VERSION,
        "running": running,
        "current_state": CURRENT_TRAINING_STATE,
        "training_menu_visible": bool(running and TRAINING_MENU_VISIBLE),
        "controller_paused_for_senzu": SENZU_CONTROLLER_ACTIVE.is_set(),
        "controller_paused": CONTROLLER_PAUSED,
        "stop_requested": bool(running and UI_STOP_REQUESTED),
        "last_run": last_run,
        "progression": None,
        "senzu_remaining": SENZU_REMAINING,
        "senzu_active_type": SENZU_ACTIVE_TYPE,
        "senzu_status": SENZU_STATUS,
        "senzu_disabled_for_run": SENZU_DISABLED_FOR_RUN,
        "progression_status": (
            "complete" if running and PROGRESSION_TRACKED_STATE == CURRENT_TRAINING_STATE and PROGRESSION_COMPLETE
            else "tracking" if running and CURRENT_TRAINING_STATE and PROGRESSION_TRACKED_STATE == CURRENT_TRAINING_STATE
            else None
        ),
        "error_count": MACRO_ERROR_COUNT,
        "last_error": MACRO_LAST_ERROR,
        "config": _ui_config_snapshot(),
        "available_stats": list(BUTTONS.keys()),
        "screen": _get_screen_info(),
        "display_restore_pending": bool(
            _DISPLAY_RESTORE is not None or os.path.isfile(_display_restore_path())
        ),
        "display_confirm": _display_confirm_state(),
        "game_window": {
            "found": GAME_HWND is not None,
            "minimized": GAME_WINDOW_MINIMIZED,
            "fullscreen": game_window_is_fullscreen(),
            "x": GAME_OFFSET_X,
            "y": GAME_OFFSET_Y,
            "width": GAME_WIDTH,
            "height": GAME_HEIGHT,
        },
        "button_calibration": {
            "waiting": BUTTON_CALIBRATION_WAITING,
            "overrides": {k: list(v) for k, v in USER_BUTTON_OVERRIDES.items()},
            "current": {k: list(v) for k, v in BUTTONS.items()},
        },
        "region_calibration": {
            "waiting": REGION_CALIBRATION_WAITING,
            "overrides": {k: dict(v) for k, v in USER_REGION_OVERRIDES.items()},
            "current": {
                "health_box":  dict(HEALTH_BOX),
                "agility_box": dict(AGILITY_BOX),
            },
        },
        "started_at": MACRO_STARTED_AT if _ui_is_running() else 0,
        "telemetry": dict(TELEMETRY),
    }


def _health_snapshot():
    return {"ok": True, "pid": os.getpid(), "version": APP_VERSION}


def _set_thread_sleep_hold(active):
    """Keep Windows and the display awake from the macro worker thread only."""
    if os.name != "nt":
        return True
    import ctypes
    es_continuous = 0x80000000
    flags = es_continuous
    if active:
        flags |= 0x00000001  # ES_SYSTEM_REQUIRED
        flags |= 0x00000002  # ES_DISPLAY_REQUIRED; capture needs visible frames
    result = ctypes.windll.kernel32.SetThreadExecutionState(flags)
    if not result:
        print(
            f"[POWER] Could not {'enable' if active else 'release'} the sleep hold"
        )
        return False
    print("[POWER] PC and display sleep blocked for this run" if active
          else "[POWER] Sleep hold released")
    return True


def _should_run_after_actions(outcome):
    """Manual Stop is never eligible, even when error handling is enabled."""
    if _USER_STOP_LATCHED or _AFTER_ACTIONS_BLOCKED:
        return False
    if outcome == "completed":
        return True
    return outcome == "error" and AFTER_RUN_ON_FAILURE


def _should_shutdown_pc(outcome):
    return bool(SHUTDOWN_PC_WHEN_FINISHED and _should_run_after_actions(outcome))


class AfterRunCancelled(RuntimeError):
    pass


def _ensure_after_run_active():
    if _USER_STOP_LATCHED:
        raise AfterRunCancelled("cancelled by Manual Stop")


def _after_run_tap(key):
    """Send a bounded cleanup key even when the controller's stop flag is set."""
    _ensure_after_run_active()
    with _input_lock:
        _ensure_after_run_active()
        _tap_key_unchecked(key)


def _after_run_click_reference(x, y, geometry):
    _ensure_after_run_active()
    screen_x, screen_y = _reference_point(x, y, geometry)
    with _input_lock:
        _ensure_after_run_active()
        _click_sendinput_abs(screen_x, screen_y)


def _after_run_wait_training_menu(sct, geometry, template, visible, timeout=2.0):
    deadline = time.time() + timeout
    stable = 0
    while time.time() < deadline:
        if _USER_STOP_LATCHED:
            return False
        matched, _score = detect_training_menu(sct, geometry, template)
        if matched == visible:
            stable += 1
            if stable >= 2:
                return True
        else:
            stable = 0
        time.sleep(0.08)
    return False


def _after_run_wait_template(sct, template, box, visible=True, timeout=3.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _USER_STOP_LATCHED:
            return False
        matched, _score, _location = _template_in_reference_box(
            sct, template, box, threshold=0.82
        )
        if matched == visible:
            return True
        time.sleep(0.08)
    return False


def _after_run_wait_game_menu(sct, template, visible=True, timeout=3.0):
    return _after_run_wait_template(
        sct, template, (0, 340, 220, 80), visible=visible, timeout=timeout
    )


def _after_run_open_training_menu(sct, geometry):
    template = _senzu_assets()["training"]
    if template is None:
        print("[AFTER RUN] Training Mode template is missing")
        return False
    if _after_run_wait_training_menu(sct, geometry, template, True, timeout=0.25):
        return True
    _after_run_tap("tab")
    if _after_run_wait_training_menu(sct, geometry, template, True):
        return True
    print("[AFTER RUN] Training Mode could not be confirmed")
    return False


def _after_run_go_to_main_menu():
    _ensure_after_run_active()
    if not focus_game_window():
        print("[AFTER RUN] Roblox could not be focused for Main Menu")
        return False
    try:
        geometry = _confirmed_game_capture_rect()
    except RuntimeError as error:
        print(f"[AFTER RUN] Main Menu skipped: {error}")
        return False

    assets = _senzu_assets()
    game_menu_template = assets["game_menu"]
    training_template = assets["training"]
    inventory_template = assets["inventory"]
    if (game_menu_template is None or training_template is None
            or inventory_template is None):
        print("[AFTER RUN] Menu templates are missing")
        return False

    with mss.MSS() as sct:
        inventory_open = _after_run_wait_template(
            sct, inventory_template, (200, 340, 280, 80), timeout=0.25
        )
        if inventory_open:
            _after_run_click_reference(80, 377, geometry)
        elif not _after_run_wait_game_menu(sct, game_menu_template, timeout=0.25):
            _after_run_tap("m")
            inventory_open = _after_run_wait_template(
                sct, inventory_template, (200, 340, 280, 80), timeout=0.5
            )
            if inventory_open:
                _after_run_click_reference(80, 377, geometry)
        if not _after_run_wait_game_menu(sct, game_menu_template, timeout=1.5):
            # M can be ignored from a minigame. Exit through Training Mode, then
            # open Game Menu from normal gameplay.
            if not _after_run_wait_training_menu(
                sct, geometry, training_template, True, timeout=0.25
            ):
                _after_run_tap("tab")
                _after_run_wait_training_menu(
                    sct, geometry, training_template, True, timeout=2.0
                )
            if _after_run_wait_training_menu(
                sct, geometry, training_template, True, timeout=0.25
            ):
                _after_run_tap("tab")
                _after_run_wait_training_menu(
                    sct, geometry, training_template, False, timeout=2.0
                )
            _after_run_tap("m")
            inventory_open = _after_run_wait_template(
                sct, inventory_template, (200, 340, 280, 80), timeout=0.6
            )
            if inventory_open:
                _after_run_click_reference(80, 377, geometry)
        if not _after_run_wait_game_menu(sct, game_menu_template, timeout=3.0):
            print("[AFTER RUN] Game Menu could not be confirmed; no menu clicks sent")
            return False

        # Same confirmed DBOG route used by DBOG Daily Claimer.
        before = _grab_reference_box(sct, (120, 360, 555, 130), geometry)
        _after_run_click_reference(84, 564, geometry)
        deadline = time.time() + 5.0
        travel_open = False
        while time.time() < deadline:
            _ensure_after_run_active()
            after = _grab_reference_box(sct, (120, 360, 555, 130), geometry)
            difference = float(np.mean(cv2.absdiff(before, after)))
            if difference >= 6.0:
                travel_open = True
                break
            time.sleep(0.1)
        if not travel_open:
            print("[AFTER RUN] Travel menu change was not confirmed; Main Menu click cancelled")
            return False
        _after_run_click_reference(330, 423, geometry)
        time.sleep(1.0)
    print("[AFTER RUN] Main Menu requested")
    return True


def _after_run_close_game():
    _ensure_after_run_active()
    if not update_game_window() or GAME_HWND is None:
        print("[AFTER RUN] Roblox is already closed")
        return True
    hwnd = GAME_HWND
    if not _is_supported_roblox_window(hwnd):
        print("[AFTER RUN] Close cancelled: target is not a verified Roblox client")
        return False
    _ensure_after_run_active()
    print("[AFTER RUN] Closing Roblox")
    _user32.PostMessageW(hwnd, win32con.WM_CLOSE, 0, 0)
    deadline = time.time() + 5.0
    while time.time() < deadline:
        if _USER_STOP_LATCHED:
            print("[AFTER RUN] Close wait cancelled by Manual Stop")
            return False
        if not _user32.IsWindow(hwnd):
            print("[AFTER RUN] Roblox closed")
            return True
        time.sleep(0.2)
    print("[AFTER RUN] Roblox did not close; no other Roblox processes were terminated")
    return False


def _after_run_set_zero_gravity():
    _ensure_after_run_active()
    if not focus_game_window():
        print("[AFTER RUN] Roblox could not be focused for 0G")
        return False
    try:
        geometry = _confirmed_game_capture_rect()
    except RuntimeError as error:
        print(f"[AFTER RUN] 0G skipped: {error}")
        return False
    with mss.MSS() as sct:
        if not _after_run_open_training_menu(sct, geometry):
            return False
        return _cycle_gc_gravity_to_zero(sct, geometry, after_run=True)


def _perform_after_run_game_action(action):
    handlers = {
        "main_menu": _after_run_go_to_main_menu,
        "close_game": _after_run_close_game,
        "zero_gravity": _after_run_set_zero_gravity,
    }
    handler = handlers.get(action)
    if handler is None:
        return action == "none"
    original_cursor = (
        win32api.GetCursorPos()
        if action in {"main_menu", "zero_gravity"}
        else None
    )
    try:
        return bool(handler())
    except Exception as error:
        print(f"[AFTER RUN] {action} failed: {type(error).__name__}: {error}")
        return False
    finally:
        # After-run handlers temporarily park the pointer over GC controls and
        # menus. Put it back even when detection refuses an uncertain action.
        if original_cursor is not None:
            with _input_lock:
                _user32.SetCursorPos(*original_cursor)


def _diagnostic_report():
    """Describe the exact capture/input environment for remote support."""
    if GAME_HWND is None:
        update_game_window()
    if GAME_HWND is None:
        return {
            "ok": False,
            "summary": "Roblox window not found",
            "issues": ["Open DBOG before collecting diagnostics."],
        }

    geometry = _game_geometry()
    left, top, width, height = geometry
    monitor = _current_game_monitor_info() or {}
    monitor_width = int(monitor.get("width", 0))
    monitor_height = int(monitor.get("height", 0))
    mode = "borderless/fullscreen" if (
        left == int(monitor.get("left", left))
        and top == int(monitor.get("top", top))
        and width == monitor_width
        and height == monitor_height
    ) else "windowed"
    aspect = width / max(1, height)
    issues = []
    if _user32.IsIconic(GAME_HWND):
        issues.append("Roblox is minimized; screen capture cannot see the game.")
    if abs(aspect - (16 / 9)) > 0.035:
        issues.append(
            f"Roblox client is not 16:9 ({width}x{height}); detection is scaled but may be less reliable."
        )
    if width < 1280 or height < 720:
        issues.append("Roblox client is below 1280x720; small templates may lose detail.")
    training_order = list(_sanitize_training_order(TRAINING_ORDER_CUSTOM))
    if not training_order:
        issues.append("Training Order is empty.")
    invalid_buttons = [
        name for name, (x, y) in BUTTONS.items()
        if not (0 <= int(x) < GAME_REFERENCE_WIDTH and 0 <= int(y) < GAME_REFERENCE_HEIGHT)
    ]
    if invalid_buttons:
        issues.append(f"Trait calibration is outside the game client: {', '.join(invalid_buttons)}")
    invalid_regions = []
    for name, box in (("Health", HEALTH_BOX), ("Agility", AGILITY_BOX)):
        if (box["left"] < 0 or box["top"] < 0
                or box["left"] + box["width"] > GAME_REFERENCE_WIDTH
                or box["top"] + box["height"] > GAME_REFERENCE_HEIGHT):
            invalid_regions.append(name)
    if invalid_regions:
        issues.append(f"Scan calibration is outside the game client: {', '.join(invalid_regions)}")
    required_templates = (
        "tpl_training_mode.png", "tpl_game_menu.png", "tpl_inventory_menu.png",
        "tpl_senzu_bean.png", "tpl_slot_senzu.png",
    )
    missing_templates = [
        name for name in required_templates if not os.path.isfile(os.path.join(BASE_DIR, name))
    ]
    if missing_templates:
        issues.append(f"Required vision files are missing: {', '.join(missing_templates)}")

    try:
        dpi = int(_user32.GetDpiForWindow(GAME_HWND))
    except Exception:
        dpi = 0
    foreground = _user32.GetForegroundWindow() == GAME_HWND

    menu_scores = {"training": None, "game": None}
    try:
        assets = _senzu_assets()
        with mss.MSS() as sct:
            _visible, training_score = detect_training_menu(
                sct, _confirmed_game_capture_rect(), assets["training"]
            )
            _visible, game_score, _location = _template_in_reference_box(
                sct, assets["game_menu"], (0, 340, 220, 80), threshold=0.82
            )
        menu_scores = {
            "training": round(float(training_score), 3),
            "game": round(float(game_score), 3),
        }
    except Exception as error:
        issues.append(f"Live template probe failed: {type(error).__name__}: {error}")

    return {
        "ok": True,
        "summary": f"Roblox {width}x{height} at ({left}, {top}), {mode}",
        "client": {"left": left, "top": top, "width": width, "height": height},
        "monitor": monitor,
        "window_mode": mode,
        "dpi": dpi,
        "foreground": foreground,
        "minimized": bool(_user32.IsIconic(GAME_HWND)),
        "template_scores": menu_scores,
        "settings": {
            "training_order": training_order,
            "gravity_target_g": GC_GRAVITY_TARGET_G,
            "health_mode": HEALTH_MODE,
            "agility_mode": AGILITY_MODE,
            "ki_mode": KI_V8_MODE,
            "senzu_enabled": SENZU_ENABLED,
        },
        "calibration": {
            "trait_buttons": {name: list(point) for name, point in BUTTONS.items()},
            "health_box": dict(HEALTH_BOX),
            "agility_box": dict(AGILITY_BOX),
        },
        "capture_note": (
            "Coordinates use the Roblox client area, so a visible taskbar does not shift scan boxes. "
            "Minimizing or covering Roblox can still break capture."
        ),
        "issues": issues,
    }


def _diagnostic_overlay_frame(sct, geometry):
    """Return a labelled, scaled view of the exact client-relative scan areas."""
    raw = np.array(sct.grab(geometry))[:, :, :3]
    frame = raw.copy()
    scale_x = frame.shape[1] / GAME_REFERENCE_WIDTH
    scale_y = frame.shape[0] / GAME_REFERENCE_HEIGHT

    def draw_box(name, box, color):
        x = int(box["left"] * scale_x)
        y = int(box["top"] * scale_y)
        right = int((box["left"] + box["width"]) * scale_x)
        bottom = int((box["top"] + box["height"]) * scale_y)
        cv2.rectangle(frame, (x, y), (right, bottom), color, 2)
        cv2.putText(
            frame, name, (x + 4, max(18, y + 18)), cv2.FONT_HERSHEY_SIMPLEX,
            0.55, color, 2, cv2.LINE_AA,
        )

    draw_box("Training menu", TRAINING_MENU_BOX, (255, 190, 40))
    draw_box("Health", HEALTH_BOX, (50, 220, 80))
    draw_box("WASD", AGILITY_BOX, (50, 180, 255))
    draw_box("HP", SENZU_HP_FILL_BOX, (50, 50, 255))
    draw_box("Gravity", GRAVITY_LABEL_BOX, (230, 80, 230))
    cv2.putText(
        frame, "Ki: scans full Roblox client", (20, frame.shape[0] - 22),
        cv2.FONT_HERSHEY_SIMPLEX, 0.65, (0, 230, 255), 2, cv2.LINE_AA,
    )
    max_width = 960
    if frame.shape[1] > max_width:
        target_height = max(1, int(frame.shape[0] * max_width / frame.shape[1]))
        frame = cv2.resize(frame, (max_width, target_height), interpolation=cv2.INTER_AREA)
    return frame


def _run_after_actions(outcome, reason):
    if not _should_run_after_actions(outcome):
        return False
    action = _normalize_after_run_game_action(AFTER_RUN_GAME_ACTION)
    if action != "none":
        _perform_after_run_game_action(action)
    # PC shutdown is independent: a failed game action must not suppress an
    # explicitly enabled shutdown.
    _schedule_pc_shutdown(outcome, reason)
    return action != "none" or bool(SHUTDOWN_PC_WHEN_FINISHED)


def _schedule_pc_shutdown(outcome, reason):
    """Schedule a cancellable Windows shutdown for an eligible run outcome."""
    if not _should_shutdown_pc(outcome):
        return False
    if os.name != "nt":
        print("[POWER] PC shutdown is only supported on Windows")
        return False

    import subprocess

    outcome_label = "finished" if outcome == "completed" else "failed"
    comment = f"XynMacro {outcome_label}: {str(reason)[:160]}"
    shutdown_executable = os.path.join(
        os.environ.get("SystemRoot", r"C:\Windows"), "System32", "shutdown.exe"
    )
    command = [
        shutdown_executable, "/s", "/t", str(PC_SHUTDOWN_DELAY_SEC),
        "/c", comment,
    ]
    try:
        subprocess.Popen(
            command,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except OSError as error:
        print(f"[POWER] Could not schedule PC shutdown: {error}")
        return False

    print(
        f"[POWER] PC shutdown scheduled in {PC_SHUTDOWN_DELAY_SEC}s "
        "(run 'shutdown /a' to cancel)"
    )
    return True


def _finalize_run_result():
    global LAST_RUN_RESULT, MACRO_ERROR_COUNT, MACRO_LAST_ERROR
    ended_at = time.time()
    started_at = MACRO_STARTED_AT or ended_at
    elapsed = max(0.0, ended_at - started_at)
    with _run_result_lock:
        outcome = _CURRENT_RUN_OUTCOME or "error"
        reason = _CURRENT_RUN_REASON or "Controller ended without an explicit result"
        category = _CURRENT_RUN_CATEGORY or CURRENT_TRAINING_STATE
        final_telemetry = dict(TELEMETRY)
        LAST_RUN_RESULT = {
            "outcome": outcome,
            "reason": reason,
            "category": category,
            "started_at": started_at,
            "ended_at": ended_at,
            "elapsed_sec": round(elapsed, 3),
            "telemetry": final_telemetry,
        }
        if outcome == "error":
            MACRO_ERROR_COUNT += 1
            MACRO_LAST_ERROR = reason
    reason_log = reason.replace("\n", " ").replace("\r", " ")
    telemetry_log = json.dumps(final_telemetry, separators=(",", ":"))
    print(
        f"[RUN] outcome={outcome} reason={reason_log!r} "
        f"category={category or '-'} elapsed={elapsed:.1f}s telemetry={telemetry_log}"
    )
    _run_after_actions(outcome, reason)


def _auto_retry_can_run(retries_used):
    with _run_result_lock:
        outcome = _CURRENT_RUN_OUTCOME
        retryable = _CURRENT_RUN_RETRYABLE
    return bool(
        AUTO_RETRY_ON_FAILURE
        and outcome == "error"
        and retryable
        and not _USER_STOP_LATCHED
        and not _AFTER_ACTIONS_BLOCKED
        and int(retries_used) < int(AUTO_RETRY_MAX_ATTEMPTS)
    )


def _clear_attempt_outcome():
    global _CURRENT_RUN_OUTCOME, _CURRENT_RUN_REASON, _CURRENT_RUN_CATEGORY
    global _CURRENT_RUN_RETRYABLE
    global MACRO_LAST_ERROR
    with _run_result_lock:
        _CURRENT_RUN_OUTCOME = None
        _CURRENT_RUN_REASON = None
        _CURRENT_RUN_CATEGORY = None
        _CURRENT_RUN_RETRYABLE = False
        MACRO_LAST_ERROR = None


def _auto_retry_cancelled():
    """User Stop is permanent for this run, including recovery handoffs."""
    return bool(_USER_STOP_LATCHED)


def _auto_retry_wait(duration, step=0.05):
    deadline = time.time() + max(0.0, float(duration))
    while time.time() < deadline:
        if _auto_retry_cancelled():
            return False
        time.sleep(min(step, max(0.0, deadline - time.time())))
    return not _auto_retry_cancelled()


def _gc_death_dialog_visible(sct, geometry):
    """Match DBOG's centered You Died/Respawn dialog by its three blue bands."""
    for box in GC_DEATH_DIALOG_BANDS:
        frame = _grab_reference_box(sct, box, geometry=geometry)[:, :, :3]
        dark_blue = (
            (frame[:, :, 0] > 35) & (frame[:, :, 0] < 120)
            & (frame[:, :, 1] > 20) & (frame[:, :, 1] < 100)
            & (frame[:, :, 2] < 100)
        )
        bright_text = np.min(frame, axis=2) > 190
        if float(np.mean(dark_blue)) < 0.60 or float(np.mean(bright_text)) < 0.008:
            return False
    return True


def _auto_retry_wait_for_death_dialog(timeout):
    stable = 0
    deadline = time.time() + timeout
    with mss.MSS() as sct:
        while time.time() < deadline:
            if _auto_retry_cancelled():
                return False
            try:
                geometry = _confirmed_game_capture_rect()
                visible = _gc_death_dialog_visible(sct, geometry)
            except Exception:
                stable = 0
                if not _auto_retry_wait(0.25):
                    return False
                continue
            stable = stable + 1 if visible else 0
            if stable >= 2:
                return True
            if not _auto_retry_wait(0.25):
                return False
    return False


def _stop_if_starting_on_death_screen():
    """Fail safely before startup navigation if GC is already awaiting Respawn."""
    if not _auto_retry_wait_for_death_dialog(0.75):
        check_exit()
        return False
    print("[RECOVERY] Macro started on GC's death screen")
    _stop_for_game_death()
    return True


def _auto_retry_click_respawn(timeout=15.0):
    if _auto_retry_cancelled() or not focus_game_window():
        return False
    if _auto_retry_cancelled():
        return False
    with mss.MSS() as sct:
        geometry = _confirmed_game_capture_rect()
        if not _gc_death_dialog_visible(sct, geometry):
            return False
        click_point = _reference_point(*GC_RESPAWN_POINT, geometry)
        with _input_lock:
            if _auto_retry_cancelled():
                return False
            geometry = _confirmed_game_capture_rect()
            if not _gc_death_dialog_visible(sct, geometry):
                return False
            if _auto_retry_cancelled():
                return False
            click_point = _reference_point(*GC_RESPAWN_POINT, geometry)
            if not _click_sendinput_abs(*click_point):
                return False
    print("[RECOVERY] Confirmed GC death dialog; clicked Respawn")

    stable_hidden = 0
    deadline = time.time() + timeout
    with mss.MSS() as sct:
        while time.time() < deadline:
            if _auto_retry_cancelled():
                return False
            try:
                geometry = _confirmed_game_capture_rect()
                visible = _gc_death_dialog_visible(sct, geometry)
            except Exception:
                stable_hidden = 0
                if not _auto_retry_wait(0.25):
                    return False
                continue
            stable_hidden = 0 if visible else stable_hidden + 1
            if stable_hidden >= 4:
                print("[RECOVERY] Respawn dialog closed")
                return _auto_retry_wait(4.0)
            if not _auto_retry_wait(0.25):
                return False
    print("[RECOVERY] Respawn click was not confirmed")
    return False


def _auto_retry_wait_for_death(timeout=180.0):
    print(f"[RECOVERY] Waiting up to {timeout:g}s for GC death")
    if not _auto_retry_wait_for_death_dialog(timeout):
        print("[RECOVERY] Timed out waiting for the GC death dialog")
        return False
    return _auto_retry_click_respawn()


def _auto_retry_reset_character():
    """Send Roblox's bounded reset sequence without clicking an unverified menu."""
    if _auto_retry_cancelled() or not focus_game_window():
        print("[RECOVERY] Roblox could not be focused for reset")
        return False
    if _auto_retry_cancelled():
        return False
    with _input_lock:
        # Tab may release a DBOG minigame that suppresses Roblox's reset chord.
        for key in ("tab", "esc", "r", "enter"):
            if _auto_retry_cancelled():
                return False
            if not _tap_key_unchecked(key):
                return False
            if key != "enter" and not _auto_retry_wait(0.35):
                return False
    print("[RECOVERY] Reset requested with Esc, R, Enter")
    if not _auto_retry_wait_for_death_dialog(15.0):
        print("[RECOVERY] GC death dialog was not confirmed after reset")
        return False
    return _auto_retry_click_respawn()


def _auto_retry_walk_forward():
    if not AUTO_RETRY_WALK_OUT:
        return True
    if _auto_retry_cancelled() or not focus_game_window():
        print("[RECOVERY] Roblox could not be focused for the walk-out step")
        return False
    if _auto_retry_cancelled():
        return False
    duration = _bounded_float(AUTO_RETRY_WALK_SECONDS, 0.5, 10.0)
    print(f"[RECOVERY] Walking forward for {duration:g}s")
    with _input_lock:
        if _auto_retry_cancelled():
            return False
        key_is_down = False
        try:
            with _stop_input_gate:
                if _auto_retry_cancelled():
                    return False
                pydirectinput.keyDown("w")
                key_is_down = True
            deadline = time.time() + duration
            while time.time() < deadline:
                if _auto_retry_cancelled():
                    return False
                time.sleep(min(0.1, max(0.0, deadline - time.time())))
        finally:
            if key_is_down:
                pydirectinput.keyUp("w")
    return True


def _auto_retry_recover():
    if _auto_retry_cancelled():
        return False
    if AUTO_RETRY_RECOVERY_MODE == "wait_for_death":
        ready = _auto_retry_wait_for_death()
    else:
        ready = _auto_retry_reset_character()
    if not ready or _auto_retry_cancelled():
        return False
    return _auto_retry_walk_forward()


def _prepare_controller_attempt():
    global UI_STOP_REQUESTED, CURRENT_TRAINING_STATE
    global MANUAL_NEXT_REQUESTED, PAUSE_TOGGLE_REQUESTED, TRAINING_MENU_VISIBLE
    global CONTROLLER_PAUSED, SENZU_DISABLED_FOR_RUN, SENZU_STATUS
    global SENZU_ACTIVE_TYPE, SENZU_REMAINING
    if _auto_retry_cancelled():
        UI_STOP_REQUESTED = True
        return False
    UI_STOP_REQUESTED = False
    MANUAL_NEXT_REQUESTED = False
    PAUSE_TOGGLE_REQUESTED = False
    CONTROLLER_PAUSED = False
    CURRENT_TRAINING_STATE = None
    TRAINING_MENU_VISIBLE = False
    SENZU_DISABLED_FOR_RUN = False
    SENZU_STATUS = "idle"
    SENZU_ACTIVE_TYPE = None
    SENZU_REMAINING = None
    PROGRESSION_COMPLETE_REQUESTED.clear()
    SENZU_CONTROLLER_ACTIVE.clear()
    SENZU_CONTROLLER_RESUME_REQUIRED.clear()
    _invalidate_senzu_row_cache()
    _ki_v8_state["last_dot"] = None
    _ki_v8_state["consecutive_seen"] = 0
    _ki_v8_state["last_click_at"] = 0.0
    return True


def _runtime_error_is_retryable(message):
    message = str(message)
    return bool(
        (
            message.startswith("Could not confirm ")
            and message.endswith(" trait selection after 3 clicks")
        )
        or message == "Training Mode menu was not confirmed after Tab; switch cancelled"
        or (
            message.startswith("Training Mode is open and ")
            and message.endswith(" could not be resumed")
        )
    )


def _run_macro_safe():
    global CURRENT_TRAINING_STATE, TRAINING_MENU_VISIBLE, CONTROLLER_PAUSED
    global SENZU_STATUS, SENZU_ACTIVE_TYPE
    sleep_hold_active = bool(PREVENT_SLEEP_WHILE_RUNNING)
    if sleep_hold_active:
        _set_thread_sleep_hold(True)
    retries_used = 0
    try:
        while True:
            if not _prepare_controller_attempt():
                _record_run_outcome("stopped", "User requested stop")
                break
            if _auto_retry_cancelled():
                _record_run_outcome("stopped", "User requested stop")
                break
            try:
                run_master_controller()
            except QuitException:
                _record_run_outcome("stopped", "Run stopped")
            except SkipMinigameException:
                print("[UI] Manual skip raised during startup — ignored. Press Start again.")
                _record_run_outcome("error", "Manual skip interrupted startup")
            except RuntimeError as e:
                _record_run_outcome(
                    "error",
                    f"RuntimeError: {e}",
                    retryable=_runtime_error_is_retryable(e),
                )
                print(f"[UI] Macro stopped safely: {e}")
            except Exception as e:
                import traceback
                _record_run_outcome("error", f"{e.__class__.__name__}: {e}")
                print(f"[UI] Macro thread error ({e.__class__.__name__}): {e}")
                for ln in traceback.format_exc().splitlines()[-6:]:
                    if ln.strip():
                        print(f"[UI] {ln}")

            monitor_stopped = _stop_background_game_monitor()
            if not monitor_stopped or not _auto_retry_can_run(retries_used):
                break

            retries_used += 1
            TELEMETRY["recovery_attempts"] += 1
            with _run_result_lock:
                failed_reason = _CURRENT_RUN_REASON or "Unknown controller error"
            print(
                f"[RECOVERY] Attempt {retries_used}/{AUTO_RETRY_MAX_ATTEMPTS} "
                f"after: {failed_reason}"
            )
            if not _auto_retry_recover():
                if _auto_retry_cancelled():
                    _record_run_outcome("stopped", "User requested stop")
                    break
                _record_run_outcome(
                    "error", f"Recovery attempt {retries_used} could not be confirmed"
                )
                break
            if _auto_retry_cancelled():
                _record_run_outcome("stopped", "User requested stop")
                break
            _clear_attempt_outcome()
            print(f"[RECOVERY] Restarting selected training plan ({retries_used})")
    finally:
        _stop_background_game_monitor()
        if sleep_hold_active:
            _set_thread_sleep_hold(False)
        _finalize_run_result()
        CURRENT_TRAINING_STATE = None
        TRAINING_MENU_VISIBLE = False
        CONTROLLER_PAUSED = False
        PROGRESSION_COMPLETE_REQUESTED.clear()
        SENZU_STATUS = "idle"
        SENZU_ACTIVE_TYPE = None


def _finite_float(value, minimum=0.0):
    """Coerce to float, rejecting NaN/inf and clamping below `minimum`. A
    non-finite or negative timing/threshold value must never reach the macro
    loops or get persisted — it stalls or destabilises detection."""
    f = float(value)
    if not math.isfinite(f):
        raise ValueError("value must be a finite number")
    return max(minimum, f)


def _finite_int(value, minimum=0):
    f = float(value)
    if not math.isfinite(f):
        raise ValueError("value must be a finite number")
    return max(minimum, int(f))


def _bounded_float(value, minimum, maximum):
    return min(float(maximum), _finite_float(value, float(minimum)))


def _bounded_int(value, minimum, maximum):
    return min(int(maximum), _finite_int(value, int(minimum)))


# Serialises live setting changes + the config write so concurrent /command
# requests (Flask is threaded) can't interleave and lose updates or corrupt the
# file. Re-entrant because _ui_apply_setting holds it across save_master_config.
_config_lock = threading.RLock()


def _ui_apply_setting_unlocked(key, value):
    global START_DELAY, GC_GRAVITY_TARGET_G, PREVENT_SLEEP_WHILE_RUNNING
    global RESTORE_FULLSCREEN_ON_START, DISPLAY_CONFIRM_CHANGES
    global SHUTDOWN_PC_WHEN_FINISHED, AFTER_RUN_GAME_ACTION, AFTER_RUN_ON_FAILURE
    global AUTO_RETRY_ON_FAILURE, AUTO_RETRY_MAX_ATTEMPTS
    global AUTO_RETRY_RECOVERY_MODE, AUTO_RETRY_WALK_OUT, AUTO_RETRY_WALK_SECONDS
    global DIAGNOSTIC_MODE
    global NEW_GAME_WAIT, NO_YELLOW_TIMEOUT_SEC
    global MANUAL_NEXT_KEY, HEALTH_HIT_COOLDOWN_SEC, HEALTH_MODE, START_STOP_HOTKEY, PAUSE_HOTKEY
    global KEY_PRESS_DELAY, STABILIZE_DELAY, POST_COMBO_DELAY
    global TRAINING_ORDER_CUSTOM, AGILITY_MODE
    global AGILITY_GREEN_OBSERVE_SEC, AGILITY_INTER_STRING_WAIT_SEC, AGILITY_AFTER_GREEN_SETTLE_SEC

    if key == "start_delay_sec":
        START_DELAY = _bounded_float(value, 0.0, 30.0)
    elif key == "gc_gravity_target_g":
        GC_GRAVITY_TARGET_G = _normalize_gravity_target(value, strict=True)
    elif key == "prevent_sleep_while_running":
        PREVENT_SLEEP_WHILE_RUNNING = _ui_bool(value)
    elif key == "restore_fullscreen_on_start":
        RESTORE_FULLSCREEN_ON_START = _ui_bool(value)
    elif key == "display_confirm_changes":
        DISPLAY_CONFIRM_CHANGES = _ui_bool(value)
    elif key == "shutdown_pc_when_finished":
        SHUTDOWN_PC_WHEN_FINISHED = _ui_bool(value)
    elif key == "after_run_game_action":
        AFTER_RUN_GAME_ACTION = _normalize_after_run_game_action(value, strict=True)
    elif key == "after_run_on_failure":
        AFTER_RUN_ON_FAILURE = _ui_bool(value)
    elif key == "auto_retry_on_failure":
        AUTO_RETRY_ON_FAILURE = _ui_bool(value)
    elif key == "auto_retry_max_attempts":
        AUTO_RETRY_MAX_ATTEMPTS = _bounded_int(value, 1, 10)
    elif key == "auto_retry_recovery_mode":
        AUTO_RETRY_RECOVERY_MODE = _normalize_auto_retry_recovery_mode(
            value, strict=True
        )
    elif key == "auto_retry_walk_out":
        AUTO_RETRY_WALK_OUT = _ui_bool(value)
    elif key == "auto_retry_walk_seconds":
        AUTO_RETRY_WALK_SECONDS = _bounded_float(value, 0.5, 10.0)
    elif key == "diagnostic_mode":
        DIAGNOSTIC_MODE = _ui_bool(value)
    elif key == "after_switch_wait_sec":
        NEW_GAME_WAIT = _bounded_float(value, 0.0, 10.0)
    elif key == "no_yellow_fallback_enabled":
        global NO_YELLOW_FALLBACK_ENABLED
        NO_YELLOW_FALLBACK_ENABLED = _ui_bool(value)
    elif key == "no_yellow_timeout_sec":
        NO_YELLOW_TIMEOUT_SEC = _bounded_float(value, 1.0, 300.0)
    elif key == "manual_next_key":
        new_key = _normalize_hotkey_name(value) or "l"
        if new_key in (START_STOP_HOTKEY, PAUSE_HOTKEY):
            raise ValueError("That hotkey is already assigned.")
        if new_key != MANUAL_NEXT_KEY:
            MANUAL_NEXT_KEY = new_key
            register_manual_next_hotkey()
    elif key == "start_stop_hotkey":
        new_key = _normalize_hotkey_name(value) or "f6"
        if new_key in (MANUAL_NEXT_KEY, PAUSE_HOTKEY):
            raise ValueError("That hotkey is already assigned.")
        if new_key != START_STOP_HOTKEY:
            START_STOP_HOTKEY = new_key
            register_start_stop_hotkey()
    elif key == "pause_hotkey":
        new_key = _normalize_hotkey_name(value) or "u"
        if new_key in (START_STOP_HOTKEY, MANUAL_NEXT_KEY):
            raise ValueError("That hotkey is already assigned.")
        if new_key != PAUSE_HOTKEY:
            PAUSE_HOTKEY = new_key
            register_pause_hotkey()
    elif key == "health_hit_cooldown_sec":
        HEALTH_HIT_COOLDOWN_SEC = _bounded_float(value, 0.0, 5.0)
    elif key == "health_mode":
        if value not in ("v1_legacy", "v2_track"):
            raise ValueError("Health mode must be v1_legacy or v2_track")
        HEALTH_MODE = str(value)
    elif key == "wasd_key_press_delay_sec":
        KEY_PRESS_DELAY = _bounded_float(value, 0.0, 1.0)
    elif key == "wasd_stabilize_delay_sec":
        STABILIZE_DELAY = _bounded_float(value, 0.0, 5.0)
    elif key == "wasd_post_burst_delay_sec":
        POST_COMBO_DELAY = _bounded_float(value, 0.0, 5.0)
    elif key == "agility_mode":
        if value not in ("v1", "v2"):
            raise ValueError("Agility mode must be v1 or v2")
        AGILITY_MODE = str(value)
    elif key == "agility_green_observe_sec":
        AGILITY_GREEN_OBSERVE_SEC = _bounded_float(value, 0.1, 5.0)
    elif key == "agility_inter_string_wait_sec":
        AGILITY_INTER_STRING_WAIT_SEC = _bounded_float(value, 0.1, 10.0)
    elif key == "agility_after_green_settle_sec":
        AGILITY_AFTER_GREEN_SETTLE_SEC = _bounded_float(value, 0.0, 5.0)
    elif key == "training_order":
        TRAINING_ORDER_CUSTOM = _sanitize_training_order(value)
    elif key == "ki_v8_click_delay_sec":
        global KI_V8_CLICK_DELAY_SEC
        KI_V8_CLICK_DELAY_SEC = _bounded_float(value, 0.0, 0.7)
    elif key == "ki_v8_mode":
        global KI_V8_MODE
        if value not in ("v1_time", "v2_ring"):
            raise ValueError("Ki mode must be v1_time or v2_ring")
        KI_V8_MODE = str(value)
    elif key == "ki_v8_v2_target_r_factor":
        global KI_V8_V2_TARGET_R_FACTOR
        KI_V8_V2_TARGET_R_FACTOR = min(
            3.0, max(KI_V8_V2_R_MIN_FACTOR, _finite_float(value))
        )
    elif key == "ki_v8_v2_brightness_threshold":
        global KI_V8_V2_BRIGHTNESS_THRESHOLD
        KI_V8_V2_BRIGHTNESS_THRESHOLD = _bounded_int(value, 50, 255)
    elif key == "ki_v8_v2_bright_count_threshold":
        global KI_V8_V2_BRIGHT_COUNT_THRESHOLD
        KI_V8_V2_BRIGHT_COUNT_THRESHOLD = _bounded_int(value, 1, 32)
    elif key == "ki_latency_comp_ms":
        global KI_LATENCY_COMP_MS
        KI_LATENCY_COMP_MS = min(250, max(0, _finite_int(value)))
    elif key == "senzu_enabled":
        global SENZU_ENABLED
        SENZU_ENABLED = _ui_bool(value)
    elif key == "senzu_slot":
        global SENZU_SLOT
        SENZU_SLOT = min(4, max(1, _finite_int(value)))
    elif key == "senzu_delay_sec":
        global SENZU_DELAY_SEC
        SENZU_DELAY_SEC = _bounded_float(value, 0.0, 30.0)
    elif key == "senzu_recovery_timeout_sec":
        global SENZU_RECOVERY_TIMEOUT_SEC
        SENZU_RECOVERY_TIMEOUT_SEC = _bounded_float(value, 1.0, 30.0)
    elif key == "senzu_preference_mode":
        global SENZU_PREFERENCE_MODE
        new_preference = _normalize_senzu_preference(value, strict=True)
        if new_preference != SENZU_PREFERENCE_MODE:
            _invalidate_senzu_row_cache()
        SENZU_PREFERENCE_MODE = new_preference
    elif key == "senzu_zero_gravity_on_empty":
        global SENZU_ZERO_GRAVITY_ON_EMPTY
        SENZU_ZERO_GRAVITY_ON_EMPTY = _ui_bool(value)
    else:
        raise ValueError(f"Unknown key: {key}")


def _ui_apply_setting(key, value):
    # Keep the global mutation and its persisted snapshot in one critical
    # section. Otherwise two quick UI changes can save in reverse order and
    # restore a stale value on the next launch.
    with _config_lock:
        previous_settings = _ui_config_snapshot()
        _ui_apply_setting_unlocked(key, value)
        try:
            save_master_config()
        except Exception:
            # Keep the live state consistent with the UI and the last complete
            # config when persistence fails.
            _ui_apply_setting_unlocked(key, previous_settings[key])
            raise


_macro_start_lock = threading.Lock()


def _pid_alive(pid):
    """True if a process with this PID is still running (Windows: not a zombie)."""
    if os.name == "nt":
        import ctypes
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        handle = ctypes.windll.kernel32.OpenProcess(
            PROCESS_QUERY_LIMITED_INFORMATION, False, pid
        )
        if not handle:
            return False
        # Also check exit code — handle may be valid for a zombie process
        exit_code = ctypes.c_ulong(0)
        STILL_ACTIVE = 259
        ok = ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))
        ctypes.windll.kernel32.CloseHandle(handle)
        return not (ok and exit_code.value != STILL_ACTIVE)
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _cleanup_stale_port_files(own_launcher_pid):
    """Delete port_<pid>.json files left behind by launchers that no longer exist.

    The Rust shell deletes its own file on clean close. The launcher PID in each
    filename lets crash leftovers be removed without touching an active instance."""
    import glob
    import re
    for path in glob.glob(os.path.join(DATA_DIR, "port_*.json")):
        m = re.fullmatch(r"port_(\d+)\.json", os.path.basename(path))
        if not m:
            continue
        pid = int(m.group(1))
        if pid == own_launcher_pid:
            continue
        try:
            if not _pid_alive(pid):
                os.remove(path)
                print(f"[sidecar] Removed stale port file: {os.path.basename(path)}")
        except OSError:
            pass  # locked or already gone — another instance may own it


def _start_parent_watchdog(parent_pid):
    """Background thread that monitors the Tauri launcher PID. When the launcher
    process disappears (closed window, crash, or terminated dev command), this
    sidecar exits immediately so an orphan cannot retain global hotkeys.

    No-op if parent_pid is missing (developer ran the script directly without --pid)."""
    if not parent_pid or int(parent_pid) == os.getpid():
        return

    def _watch():
        pid = int(parent_pid)
        while True:
            time.sleep(0.25)
            try:
                alive = _pid_alive(pid)
            except Exception:
                # If the check itself blows up, don't kill ourselves over it
                continue
            if not alive:
                print(f"[sidecar] Tauri launcher (PID {pid}) is gone. Exiting to avoid orphaning.")
                try:
                    os.remove(os.path.join(DATA_DIR, f"port_{pid}.json"))
                except OSError:
                    pass
                # os._exit skips atexit/finalizers — important because the macro thread
                # is daemon and we just want to vanish fast, not run cleanup.
                os._exit(0)

    threading.Thread(target=_watch, daemon=True, name="parent-watchdog").start()


def _ui_start_macro():
    global MACRO_THREAD, UI_STOP_REQUESTED, MACRO_STARTED_AT, MACRO_LAST_ERROR
    # Lock around the entire check+assign — without it, rapid F6 presses or the
    # `keyboard` lib delivering duplicate events can race past _ui_is_running()
    # and both spawn a MACRO_THREAD, leaving multiple macro loops clicking in
    # parallel and only one of them seeing the stop signal.
    with _macro_start_lock:
        if _ui_is_running():
            return True, "Already running"
        if not _sanitize_training_order(TRAINING_ORDER_CUSTOM):
            return False, "Add at least one stat to Training Order."
        # Refuse before the countdown or worker starts. Waiting until the first
        # capture makes Start look successful for several seconds and risks any
        # future startup input landing in whichever window currently has focus.
        # A minimized Roblox has no client rect; restore it instead of refusing.
        if not update_game_window() and not restore_game_window():
            return False, "Open Roblox before starting XynMacro."
        if GAME_HWND is None:
            return False, "Open Roblox before starting XynMacro."
        # Do this before the countdown so the scan regions are measured against
        # the window the run will actually use.
        if RESTORE_FULLSCREEN_ON_START:
            ensure_game_fullscreen()
        if (
            _background_monitor_thread is not None
            and _background_monitor_thread.is_alive()
            and _background_monitor_stop is not None
            and _background_monitor_stop.is_set()
        ):
            return False, "Previous game monitor is still stopping. Try Start again shortly."
        UI_STOP_REQUESTED = False
        MACRO_LAST_ERROR = None
        _begin_run_result()
        MACRO_STARTED_AT = time.time()
        _telemetry_reset()
        MACRO_THREAD = threading.Thread(target=_run_macro_safe, daemon=True)
        MACRO_THREAD.start()
    return True, "Started"


def _ui_stop_macro():
    global UI_STOP_REQUESTED, _USER_STOP_LATCHED
    with _stop_input_gate:
        _USER_STOP_LATCHED = True
        UI_STOP_REQUESTED = True
    if _ui_is_running():
        _record_run_outcome("stopped", "User requested stop")
    return True, "Stop requested"


def _hotkey_manual_next():
    global MANUAL_NEXT_REQUESTED
    MANUAL_NEXT_REQUESTED = True


def _hotkey_pause_toggle():
    global PAUSE_TOGGLE_REQUESTED
    PAUSE_TOGGLE_REQUESTED = True


def register_manual_next_hotkey():
    """(Re)register the manual-next global hotkey to match MANUAL_NEXT_KEY.
    Called at startup and whenever the user changes the keybind in the UI."""
    global _manual_next_hotkey_handle
    if _manual_next_hotkey_handle is not None:
        try:
            keyboard.remove_hotkey(_manual_next_hotkey_handle)
        except Exception:
            pass
        _manual_next_hotkey_handle = None
    try:
        _manual_next_hotkey_handle = keyboard.add_hotkey(MANUAL_NEXT_KEY, _hotkey_manual_next)
        print(f"[sidecar] Registered global hotkey: {MANUAL_NEXT_KEY.upper()} (manual next)")
    except Exception as e:
        print(f"[sidecar] Manual-next hotkey registration failed: {e}")


def register_pause_hotkey():
    global _pause_hotkey_handle
    if _pause_hotkey_handle is not None:
        try:
            keyboard.remove_hotkey(_pause_hotkey_handle)
        except Exception:
            pass
        _pause_hotkey_handle = None
    try:
        _pause_hotkey_handle = keyboard.add_hotkey(PAUSE_HOTKEY, _hotkey_pause_toggle)
        print(f"[sidecar] Registered global hotkey: {PAUSE_HOTKEY.upper()} (pause toggle)")
    except Exception as e:
        print(f"[sidecar] Pause hotkey registration failed: {e}")


_start_stop_hotkey_handle = None


def _hotkey_toggle_macro():
    if _ui_is_running():
        _ui_stop_macro()
    else:
        _ui_start_macro()


def register_start_stop_hotkey():
    global _start_stop_hotkey_handle
    if _start_stop_hotkey_handle is not None:
        try:
            keyboard.remove_hotkey(_start_stop_hotkey_handle)
        except Exception:
            pass
        _start_stop_hotkey_handle = None
    try:
        _start_stop_hotkey_handle = keyboard.add_hotkey(START_STOP_HOTKEY, _hotkey_toggle_macro)
        print(f"[sidecar] Registered global hotkey: {START_STOP_HOTKEY.upper()} (start/stop)")
    except Exception as e:
        print(f"[sidecar] Start/stop hotkey registration failed: {e}")


def _validated_auth_token(auth_token, frozen=None):
    """Return the configured launch token, or allow an unauthenticated source run.

    Installed sidecars must always be launched by Rust with a per-launch token.
    Keeping the fallback limited to an unfrozen Python process lets developers run
    this file directly without weakening the packaged app.
    """
    token = str(auth_token or "").strip()
    if token:
        return token
    is_frozen = getattr(sys, "frozen", False) if frozen is None else bool(frozen)
    if is_frozen:
        raise ValueError("--auth-token is required in packaged sidecar builds")
    return None


def _loopback_request_denial(host_header, origin, presented_token, auth_token):
    """Return ``(message, status)`` when a sidecar HTTP request must be denied."""
    raw_host = host_header or ""
    if raw_host.startswith("[") and "]" in raw_host:
        host = raw_host[:raw_host.index("]") + 1]
    elif raw_host.count(":") == 1:
        host = raw_host.rsplit(":", 1)[0]
    else:
        host = raw_host
    allowed_hosts = {"127.0.0.1", "localhost", "[::1]", "::1"}
    if host not in allowed_hosts:
        return "forbidden host", 403
    if origin is not None:
        return "cross-origin request blocked", 403
    if auth_token is not None:
        candidate = presented_token or ""
        if not hmac.compare_digest(candidate, auth_token):
            return "unauthorized", 401
    return None


def run_ui_server(sidecar_pid=None, auth_token=None):
    """Headless Flask backend for the Tauri shell.

    sidecar_pid is required. The chosen port is written to
    ``port_<sidecar_pid>.json`` next to this script so the Rust launcher
    can read it and proxy HTTP requests to /state, /command, /logs, etc.
    """
    from flask import Flask, request, jsonify, send_from_directory

    import sys as _sys
    if not isinstance(_sys.stdout, _TeeBuffer):
        session_log = _open_session_log_file()
        _sys.stdout = _TeeBuffer(_sys.stdout, _ui_log_ring, file_handle=session_log)
        if session_log is not None:
            try:
                # Banner so the file is identifiable
                print(f"[sidecar] Session log: {session_log.name}")
            except Exception:
                pass

    ui_dir = os.path.join(BASE_DIR, "ui")
    app = Flask(__name__, static_folder=ui_dir, static_url_path="")

    # This API drives raw mouse/keyboard injection and listens on a predictable
    # loopback port, so lock it to same-origin local callers. The only legit
    # client is the Tauri Rust proxy (server-to-server: Host 127.0.0.1, no
    # Origin header). Rejecting foreign Hosts blocks DNS-rebinding, and
    # rejecting any Origin blocks a browser page from POSTing /command to start
    # the macro. (Previously the app used a wildcard CORS(app), which allowed
    # exactly that cross-origin access.)
    @app.before_request
    def _guard_loopback():
        denial = _loopback_request_denial(
            request.host,
            request.headers.get("Origin"),
            request.headers.get("X-XynMacro-Token"),
            auth_token,
        )
        if denial is not None:
            message, status = denial
            return jsonify({"ok": False, "error": message}), status

    @app.route("/")
    def index():
        return send_from_directory(ui_dir, "index.html")

    @app.route("/<path:path>")
    def static_files(path):
        return send_from_directory(ui_dir, path)

    @app.route("/state", methods=["GET"])
    def state():
        snap = _ui_state_snapshot()
        snap["save_dir"] = SAVE_DIR
        snap["config_dir"] = JSON_DIR
        return jsonify(snap)

    @app.route("/health", methods=["GET"])
    def health():
        return jsonify(_health_snapshot())

    @app.route("/diagnostics", methods=["GET"])
    def diagnostics():
        return jsonify(_diagnostic_report())

    @app.route("/logs", methods=["GET"])
    def logs():
        since = request.args.get("since", type=float, default=0)
        entries = [e for e in _ui_log_ring if e["t"] > since]
        return jsonify(entries)

    @app.route("/preview", methods=["GET"])
    def preview():
        """Return a base64-PNG snapshot of a named region.
        Usage: /preview?region=health_box | agility_box | game_window | diagnostics
        Used by the Calibration tab to show users exactly what the macro sees."""
        region = (request.args.get("region") or "").strip().lower()
        try:
            with mss.MSS() as _sct:
                if region == "health_box":
                    box = _reference_box(HEALTH_BOX)
                elif region == "agility_box":
                    box = _reference_box(AGILITY_BOX)
                elif region == "game_window":
                    update_game_window()
                    if GAME_HWND is None:
                        return jsonify({"ok": False, "msg": "Roblox window not found"})
                    box = {"left": GAME_OFFSET_X, "top": GAME_OFFSET_Y,
                           "width": GAME_WIDTH, "height": GAME_HEIGHT}
                elif region == "diagnostics":
                    if GAME_HWND is None:
                        update_game_window()
                    if GAME_HWND is None:
                        return jsonify({"ok": False, "msg": "Roblox window not found"})
                    box = _confirmed_game_capture_rect()
                    bgr = _diagnostic_overlay_frame(_sct, box)
                else:
                    return jsonify({"ok": False, "msg": f"Unknown region: {region}"})

                if region != "diagnostics":
                    raw = np.array(_sct.grab(box))
                    bgr = raw[:, :, :3]
                ok, buf = cv2.imencode(".png", bgr)
                if not ok:
                    return jsonify({"ok": False, "msg": "Encode failed"})
                import base64
                b64 = base64.b64encode(buf.tobytes()).decode("ascii")
                return jsonify({
                    "ok": True,
                    "image": "data:image/png;base64," + b64,
                    "width": int(box["width"]),
                    "height": int(box["height"]),
                    "left": int(box["left"]),
                    "top": int(box["top"]),
                })
        except Exception as e:
            return jsonify({"ok": False, "msg": str(e)})

    @app.route("/save_log", methods=["POST"])
    def save_log():
        try:
            import shutil
            import time as _t
            os.makedirs(SAVE_DIR, exist_ok=True)
            stamp = _t.strftime("%Y%m%d-%H%M%S")
            path = os.path.join(SAVE_DIR, f"log-{stamp}.txt")
            session_handle = getattr(_sys.stdout, "_file", None)
            source_path = getattr(session_handle, "name", None)
            if session_handle is not None and source_path and os.path.isfile(source_path):
                session_handle.flush()
                shutil.copy2(source_path, path)
                with open(path, "r", encoding="utf-8", errors="replace") as saved:
                    line_count = sum(1 for _ in saved)
                return jsonify({
                    "ok": True,
                    "path": path,
                    "count": line_count,
                    "full_session": True,
                })

            # Fallback for an unusual stdout wrapper that has no session file.
            with open(path, "w", encoding="utf-8") as saved:
                for entry in _ui_log_ring:
                    ts = _t.strftime("%H:%M:%S", _t.localtime(entry["t"]))
                    saved.write(f"{ts}  {entry['msg']}\n")
            return jsonify({
                "ok": True,
                "path": path,
                "count": len(_ui_log_ring),
                "full_session": False,
            })
        except Exception as e:
            return jsonify({"ok": False, "msg": str(e)})

    @app.route("/open_save_dir", methods=["POST"])
    def open_save_dir():
        try:
            os.makedirs(SAVE_DIR, exist_ok=True)
            os.startfile(SAVE_DIR)  # opens the folder in Explorer (Windows)
            return jsonify({"ok": True, "path": SAVE_DIR})
        except Exception as e:
            return jsonify({"ok": False, "msg": str(e)})

    @app.route("/open_config_dir", methods=["POST"])
    def open_config_dir():
        try:
            os.makedirs(JSON_DIR, exist_ok=True)
            os.startfile(JSON_DIR)  # settings + calibration live here
            return jsonify({"ok": True, "path": JSON_DIR})
        except Exception as e:
            return jsonify({"ok": False, "msg": str(e)})

    @app.route("/pick_save_dir", methods=["POST"])
    def pick_save_dir():
        try:
            import tkinter as tk
            from tkinter import filedialog
            root = tk.Tk()
            root.withdraw()
            root.attributes("-topmost", True)
            chosen = filedialog.askdirectory(initialdir=SAVE_DIR, title="Choose where saves go")
            root.destroy()
            if chosen:
                _set_save_dir(chosen)
                return jsonify({"ok": True, "path": SAVE_DIR})
            return jsonify({"ok": False, "msg": "cancelled"})
        except Exception as e:
            return jsonify({"ok": False, "msg": str(e)})

    @app.route("/command", methods=["POST"])
    def command():
        data = request.get_json() or {}
        action = (data.get("action") or "").strip().lower()
        value = data.get("value")

        try:
            if action == "start":
                ok, msg = _ui_start_macro()
                return jsonify({"ok": ok, "msg": msg})
            if action == "stop":
                ok, msg = _ui_stop_macro()
                return jsonify({"ok": ok, "msg": msg})
            if action == "set":
                key = value.get("key") if isinstance(value, dict) else None
                val = value.get("value") if isinstance(value, dict) else value
                if key:
                    _ui_apply_setting(key, val)
                    normalized = _ui_config_snapshot().get(key)
                    return jsonify({
                        "ok": True,
                        "msg": f"{key} updated",
                        "value": normalized,
                    })
                return jsonify({"ok": False, "msg": "Missing key"})
            if action == "calibrate_button_begin":
                stat = (value or {}).get("stat") if isinstance(value, dict) else value
                # Back-compat for any old UI calls that still send {group: 'health'}.
                if not stat and isinstance(value, dict):
                    legacy = {"health": "Health", "physical": "Agility", "ki": "Ki Control"}
                    stat = legacy.get(value.get("group"))
                if not stat:
                    return jsonify({"ok": False, "msg": "Missing stat"})
                ok, msg = _ui_calibrate_button_begin(str(stat))
                return jsonify({"ok": ok, "msg": msg})
            if action == "calibrate_button_cancel":
                ok, msg = _ui_calibrate_button_cancel()
                return jsonify({"ok": ok, "msg": msg})
            if action == "calibrate_region_begin":
                region = (value or {}).get("region") if isinstance(value, dict) else value
                if not region:
                    return jsonify({"ok": False, "msg": "Missing region"})
                ok, msg = _ui_calibrate_region_begin(str(region))
                return jsonify({"ok": ok, "msg": msg})
            if action == "calibrate_region_cancel":
                ok, msg = _ui_calibrate_region_cancel()
                return jsonify({"ok": ok, "msg": msg})
            if action == "reset_defaults":
                if _ui_is_running():
                    return jsonify({"ok": False, "msg": "Stop the macro before resetting settings"})
                with _config_lock:
                    reset_user_settings_to_defaults()
                    save_master_config()
                register_start_stop_hotkey()
                register_manual_next_hotkey()
                register_pause_hotkey()
                return jsonify({"ok": True, "msg": "Defaults restored"})
            if action == "factory_reset":
                if _ui_is_running():
                    return jsonify({"ok": False, "msg": "Stop the macro before factory reset"})
                with _config_lock:
                    factory_reset_configuration()
                register_start_stop_hotkey()
                register_manual_next_hotkey()
                register_pause_hotkey()
                return jsonify({
                    "ok": True,
                    "msg": "Macro settings, calibration, and save location restored",
                })
            if action == "display_set_1080":
                if _ui_is_running():
                    return jsonify({
                        "ok": False,
                        "msg": "Stop the macro before changing display resolution",
                    })
                ok, code = display_set_resolution(1920, 1080)
                update_game_window()
                if ok and DISPLAY_CONFIRM_CHANGES:
                    _arm_display_confirm()
                    return jsonify({
                        "ok": True,
                        "msg": f"Display set to 1920x1080 — confirm within "
                               f"{int(DISPLAY_CONFIRM_TIMEOUT_SEC)}s or it reverts",
                        "confirm": _display_confirm_state(),
                    })
                return jsonify({"ok": ok, "msg": "Display set to 1920x1080"
                                if ok else f"Display change failed (code {code})"})
            if action == "display_keep":
                ok, msg = display_keep_resolution()
                return jsonify({"ok": ok, "msg": msg})
            if action == "display_revert":
                if _ui_is_running():
                    return jsonify({
                        "ok": False,
                        "msg": "Stop the macro before reverting display resolution",
                    })
                _cancel_display_confirm()
                ok, code = display_revert_resolution()
                update_game_window()
                return jsonify({"ok": ok, "msg": "Display reverted to your Windows setting"
                                if ok else f"Revert failed (code {code})"})
        except Exception as e:
            return jsonify({"ok": False, "msg": str(e)})

        return jsonify({"ok": False, "msg": f"Unknown action: {action}"})

    port = 8765
    for _ in range(50):
        try:
            import socket
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.bind(("127.0.0.1", port))
            s.close()
            break
        except OSError:
            port += 1
    else:
        port = 8765

    url = f"http://127.0.0.1:{port}"

    if sidecar_pid is not None:
        # Headless: write port file in the writable data dir, then run Flask blocking.
        import logging as _logging
        _logging.getLogger('werkzeug').setLevel(_logging.ERROR)
        # Silence Flask's own startup banner (" * Serving Flask app", " * Debug
        # mode: off", the dev-server warning) — it's framework noise, not our log.
        import flask.cli as _flask_cli
        _flask_cli.show_server_banner = lambda *a, **k: None
        os.makedirs(DATA_DIR, exist_ok=True)
        _cleanup_stale_port_files(int(sidecar_pid))
        port_file = os.path.join(DATA_DIR, f"port_{sidecar_pid}.json")
        try:
            with open(port_file, "w", encoding="utf-8") as f:
                json.dump({"port": port, "pid": os.getpid()}, f)
            print(f"[sidecar] Wrote port file: {port_file} (port={port})")
        except Exception as e:
            print(f"[sidecar] Failed to write port file: {e}")

        # Parent-PID watchdog — if the Tauri launcher dies (closed, crashed, or
        # killed via Ctrl+C without a clean shutdown), this sidecar would otherwise
        # become an orphan that keeps its F6 hotkey hook registered. When the user
        # later presses F6, every orphan plus the active sidecar all fire their
        # macros and click in parallel. Polling the launcher PID lets us self-exit
        # cleanly when our parent goes away.
        _start_parent_watchdog(sidecar_pid)

        # Global hotkeys (OS-level via the `keyboard` lib) — fire even when the
        # game window has focus, unlike the Tauri WebView's local keys.
        register_start_stop_hotkey()
        register_manual_next_hotkey()
        register_pause_hotkey()

        print(f"[sidecar] Flask listening on {url}")
        app.run(host="127.0.0.1", port=port, threaded=True, use_reloader=False)
        return

    raise RuntimeError("run_ui_server requires sidecar_pid; the Tauri shell owns the window now")


def seed_defaults():
    """Copy bundled default calibration into the writable JSON_DIR on a fresh install.

    A user's first launch has an empty app-data dir, so without this the macro
    falls back to bare code defaults and clicks the wrong spots. We copy each file
    only if it's missing, so the user's own recalibrations are never overwritten.
    No-op in dev, where JSON_DIR already holds the live calibration."""
    defaults_dir = os.path.join(BASE_DIR, "defaults")
    if not os.path.isdir(defaults_dir):
        return
    os.makedirs(JSON_DIR, exist_ok=True)
    for name in ("button_calibration.json", "region_calibration.json"):
        src = os.path.join(defaults_dir, name)
        dst = os.path.join(JSON_DIR, name)
        if not os.path.exists(src) or os.path.exists(dst):
            continue
        try:
            with open(src, "r", encoding="utf-8") as f:
                content = f.read()
            with open(dst, "w", encoding="utf-8") as f:
                f.write(content)
            print(f"[seed] Seeded default {name}")
        except Exception as e:
            print(f"[seed] Failed to seed {name}: {e}")


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="XynMacro Python sidecar for the Tauri shell")
    ap.add_argument("--sidecar", action="store_true",
                    help="Headless Flask backend (the only supported mode in this build)")
    ap.add_argument("--pid", type=int, default=None,
                    help="Launcher PID. Port is written to port_<pid>.json in --data-dir.")
    ap.add_argument("--data-dir", type=str, default=None,
                    help="Writable runtime dir for port file, config, saved logs. "
                         "Defaults to the script's own directory (dev mode).")
    ap.add_argument("--app-version", type=str, default=None,
                    help="Authoritative Tauri package version reported by /state and /health.")
    ap.add_argument("--auth-token", type=str, default=None,
                    help="Per-launch token supplied by the Tauri launcher.")
    args = ap.parse_args()
    if not args.sidecar:
        ap.error("--sidecar is required. This is XynMacro's backend; the desktop app "
                 "launches it, so it isn't meant to be run directly.")
    if args.pid is None:
        args.pid = os.getpid()
    if not args.app_version:
        ap.error("--app-version is required when the Tauri shell launches the sidecar")
    try:
        auth_token = _validated_auth_token(args.auth_token)
    except ValueError as error:
        ap.error(str(error))
    if auth_token is None:
        print("[sidecar] Authentication disabled for direct source development launch")
    set_app_version(args.app_version)
    if args.data_dir:
        DATA_DIR = os.path.abspath(args.data_dir)
        JSON_DIR = os.path.join(DATA_DIR, "json")
        MACRO_CONFIG_FILE = os.path.join(JSON_DIR, "macro_config.json")
        os.makedirs(JSON_DIR, exist_ok=True)
        print(f"[sidecar] Data dir: {DATA_DIR}")
    SAVE_DIR = os.path.join(DATA_DIR, "saves")
    _load_save_dir()
    seed_defaults()
    load_master_config()
    load_button_overrides()
    load_region_overrides()
    # One-time detection at startup so the UI can show whether DBOG is found.
    # The macro re-runs this on Start in case the window has moved.
    update_game_window()
    run_ui_server(sidecar_pid=args.pid, auth_token=auth_token)
