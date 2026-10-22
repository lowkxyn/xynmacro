# XynMacro Python sidecar.
# Screen capture, game-state detection, input injection, and a loopback HTTP API
# the Tauri shell calls. Launched headless by the desktop app; not run directly.
import ctypes as _ctypes

_user32 = _ctypes.windll.user32


def _set_process_dpi_awareness():
    # Geometry, MSS captures and injected mouse coordinates all use physical
    # pixels. Set the process mode before any native library can read geometry.
    set_context = getattr(_user32, "SetProcessDpiAwarenessContext", None)
    if set_context is not None:
        set_context.argtypes = [_ctypes.c_void_p]
        set_context.restype = _ctypes.c_bool
        if set_context(_ctypes.c_void_p(-4)):  # PER_MONITOR_AWARE_V2
            return

    shcore = _ctypes.windll.shcore
    awareness = _ctypes.c_int()
    if (shcore.GetProcessDpiAwareness(None, _ctypes.byref(awareness)) == 0
            and awareness.value == 2):
        return  # A manifest or earlier initializer already selected per-monitor.
    # Don't kill the sidecar over this: a manifest may already have locked a
    # different mode, and the display-scaling warning still covers that case.
    if shcore.SetProcessDpiAwareness(2) != 0:
        print(f"[WARN] Could not enable per-monitor DPI awareness (current mode {awareness.value}).")


_set_process_dpi_awareness()

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
import re as _re
re = _re
import threading
import queue
import hmac as _hmac
hmac = _hmac
import subprocess
import tempfile
import notifications
import settings_schema
# Retain the core facade so existing callers and patched state share the split helpers.
import window_geometry as _window_geometry
_window_process_executable = _window_geometry._window_process_executable
_monitor_info_for_window = _window_geometry._monitor_info_for_window
import image_analysis as _image_analysis
_hp_fill_is_critical = _image_analysis._hp_fill_is_critical
_hp_fill_is_green = _image_analysis._hp_fill_is_green
_read_inventory_count = _image_analysis._read_inventory_count
_glyph_iou = _image_analysis._glyph_iou
_progression_completion_from_frame = _image_analysis._progression_completion_from_frame
_gravity_mask_from_frame = _image_analysis._gravity_mask_from_frame
_gravity_control_glyph_count = _image_analysis._gravity_control_glyph_count
preprocess_solid = _image_analysis.preprocess_solid
_ki_v8_local_contrast_profile = _image_analysis._ki_v8_local_contrast_profile
_ki_v8_clamped_grab = _image_analysis._ki_v8_clamped_grab
_ki_v8_shrink_velocity_px_s = _image_analysis._ki_v8_shrink_velocity_px_s
is_area_green = _image_analysis.is_area_green
is_area_red = _image_analysis.is_area_red
_scans_match = _image_analysis._scans_match
import runtime_diagnostics as _runtime_diagnostics
_read_log_tail = _runtime_diagnostics._read_log_tail
_scrub_user_paths = _runtime_diagnostics._scrub_user_paths
_log_entry_line = _runtime_diagnostics._log_entry_line
_shareable_log = _runtime_diagnostics._shareable_log
_registry_value = _runtime_diagnostics._registry_value
_system_profile = _runtime_diagnostics._system_profile
_pid_alive = _runtime_diagnostics._pid_alive
import numeric_helpers as _numeric_helpers
_finite_float = _numeric_helpers._finite_float
_finite_int = _numeric_helpers._finite_int
_bounded_float = _numeric_helpers._bounded_float
_bounded_int = _numeric_helpers._bounded_int
import gc_recovery
import majin_vision
import training_ui
from contextlib import contextmanager
from urllib.parse import urlencode as _urlencode
urlencode = _urlencode

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
_config_backup_required = False

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
    with open(_save_dir_pref_file(), "w", encoding="utf-8") as f:
        f.write(path)
    SAVE_DIR = path

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
# Bring Roblox to the front when the start countdown ends, so the app window
# can't be left covering the game.
SWITCH_TO_GAME_ON_START = True
# Open Roblox chat pops its whole box up whenever the pointer passes over it,
# covering the gravity button and Ki circles, so Start closes it.
HIDE_ROBLOX_CHAT_ON_START = True
# Optional: once Ki Control is finished this run, stop charging so the other
# minigames aren't interrupted for it. Healing still steps out.
STOP_CHARGING_AFTER_KI_CONTROL = False
# Ki HP checks. "fixed": every 30 s (sooner only once two checks in this stat show
# HP falling). "adaptive": also learns the drain outside Ki and checks sooner after deaths.
KI_HP_CHECK_MODE = "adaptive"
# Roblox restored from the taskbar comes back windowed even when it was fullscreen,
# which moves every scan region. Put it back to fullscreen before a run.
RESTORE_FULLSCREEN_ON_START = True
# Size Roblox to a 1920x1080 window instead of changing the display resolution.
# Off by default: it only fits on displays larger than 1080p.
WINDOWED_MODE_ON_START = False
# Why the last ensure_game_windowed() refused, when the reason is one the UI can
# offer a way past. Only "windowed_too_small" is meaningful; everything else clears it.
WINDOWED_BLOCK_CODE = None
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

# 3. PROGRESSION
NO_YELLOW_TIMEOUT_SEC = 5.0           # seconds without yellow before switching; numeric progression OCR is not reliable enough to replace this
NO_YELLOW_FALLBACK_ENABLED = False    # Progression tracking is the primary completion signal. The
                                      # no-yellow switch is an opt-in fallback; when off, an untracked
                                      # stat keeps training until tracking locks or a manual skip.
PROGRESSION_STALL_TIMEOUT_SEC = 120.0  # Points Left can cap a readable count below its displayed target.
YELLOW_SAMPLE_INTERVAL_SEC = 0.25     # seconds between samples (lower = faster response, more CPU)
TRAINING_LOAD_GRACE_SEC = 12.0        # after a switch/start, allow this long for the FIRST yellow
                                      # (slow load/ping) before the no-yellow switch can fire

# Manual override: use the configured hotkey to skip to the next category immediately
MANUAL_NEXT_KEY = "l"

# Global hotkey that toggles macro start/stop (any keyboard-lib-compatible name).
START_STOP_HOTKEY = "f6"
PAUSE_HOTKEY = "u"
CHARGE_KEY = "shift"
MAJIN_PANEL_KEY = "g"
MAJIN_REGEN_KEYS = tuple(str(slot) for slot in range(1, 9))
_MACRO_INPUT_KEYS = frozenset({
    "w", "a", "s", "d", "f", "tab", "h", "m", "esc", "escape",
    "r", "enter", "return", "alt", "f11", CHARGE_KEY, MAJIN_PANEL_KEY,
    *MAJIN_REGEN_KEYS,
})


def _normalize_hotkey_name(value):
    """Coerce a hotkey value into a string the `keyboard` library accepts.
    Accepts strings like 'l', 'F6', 'shift+s'. Returns lowercased+stripped,
    or empty string if unusable."""
    if value is None:
        return ""
    s = str(value).strip().lower()
    return s


def _validate_hotkey_input_conflict(key):
    if not key:
        raise ValueError("Choose a non-empty hotkey.")
    emitted_codes = {code for name in _MACRO_INPUT_KEYS
                     for code in keyboard.key_to_scan_codes(name)}
    steps = keyboard.parse_hotkey_combinations(key)
    if any(code in emitted_codes for step in steps for chord in step for code in chord):
        raise ValueError("Hotkey uses a key XynMacro sends to Roblox. Choose another key.")


def _loaded_hotkey(value, fallback):
    key = _normalize_hotkey_name(value) or fallback
    try:
        _validate_hotkey_input_conflict(key)
        keyboard.parse_hotkey_combinations(key)
    except ValueError as error:
        print(f"[settings] Ignoring hotkey {key}: {error}")
        return fallback
    return key


def _hotkeys_overlap(first, second):
    first_steps = keyboard.parse_hotkey_combinations(first)
    second_steps = keyboard.parse_hotkey_combinations(second)

    def chords_overlap(first_chord, second_chord):
        first_keys, second_keys = set(first_chord), set(second_chord)
        if first_keys <= second_keys:
            return (all(keyboard.is_modifier(code) for code in first_keys)
                    or not any(keyboard.is_modifier(code) for code in second_keys - first_keys))
        if second_keys <= first_keys:
            return (all(keyboard.is_modifier(code) for code in second_keys)
                    or not any(keyboard.is_modifier(code) for code in first_keys - second_keys))
        return False

    def sequence_contains(shorter, longer):
        step = 0
        for long_step in longer:
            if any(chords_overlap(short_chord, long_chord)
                   for short_chord in shorter[step] for long_chord in long_step):
                step += 1
                if step == len(shorter):
                    return True
        return False

    # The hook ignores unrelated ordinary keys while waiting for a sequence,
    # so a shorter binding can fire inside a longer one, including its middle.
    # Modifier-only prefixes can fire before the remaining modifiers go down.
    # Complete chords with different modifiers still remain distinct bindings.
    return (sequence_contains(first_steps, second_steps)
            or sequence_contains(second_steps, first_steps))

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

# The game replaced its training screens in October 2026. "legacy" keeps the
# previous readers (and the tests built on their screenshots) selectable; the
# layout's coordinates are applied by _apply_game_ui_layout() below.
LEGACY_UI_LAYOUT = "legacy"
GAME_UI_LAYOUT = "2026-10"


def _new_game_ui():
    return GAME_UI_LAYOUT != LEGACY_UI_LAYOUT


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
GRAVITY_POINTER_PARK_POINT = (1200, 540)
GC_RUN_CONFIRMED = False
GRAVITY_MATCH_MIN = 0.80
GRAVITY_MATCH_MARGIN_MIN = 0.035
# How often the label is re-read after a gravity click.
GRAVITY_POLL_SEC = 0.05
# Least time between two gravity clicks. Spam-clicking by hand (120 fps capture,
# 20 Oct 2026) never moved the label faster than 133 ms apart, and 150 ms still
# dropped clicks live; the game's button takes no faster input than this.
GRAVITY_CLICK_GAP_SEC = 0.25
# How long the label may stay unreadable after a click before giving up. Timed,
# not counted: three 25 ms reads (75 ms) gave up on a redraw live.
GRAVITY_MISSING_SEC = 0.3
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
    "robloxplayer.exe",
    "roblox.exe",
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
BUTTON_CALIBRATION_ERROR = None
BUTTON_CALIBRATION_THREAD = None

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
REGION_CALIBRATION_ERROR = None
REGION_CALIBRATION_THREAD = None

# Menu and utility keys need to span several game frames.
UTILITY_KEY_HOLD_SEC = 0.100
UTILITY_KEY_GAP_SEC = 0.040

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

TRAIT_CLICK_APPROACH_ENABLED = False   # opt-in: approach before the first trait click; retries always approach
TRAIT_CLICK_APPROACH_PAUSE_SEC = 0.04  # settle time at each step of the retry approach move

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
# Adaptive brightness (opt-in). KI_V8_DARK_THRESH is an absolute value, and on a
# stock display the "1" stroke only just clears it — measured across known-good
# frames, the first column qualifies at exactly 89-90. A 2% brightness lift
# anywhere in the display pipeline (BIOS "game mode"/vibrance profiles, HDR, a
# monitor ICC profile, Night Light) pushes the stroke above 90 and the digit
# check rejects every dot, while the border check is unaffected — the exact
# `border=1 one=0 reject=digit` signature reported from the field.
# The stroke-to-dot brightness RATIO stays put at ~0.50 through those shifts, so
# when enabled the threshold is derived from the dot's own brightness instead.
# KI_V8_DARK_THRESH remains the floor so a genuinely dark scene can't drive it down.
KI_V8_ADAPTIVE_BRIGHTNESS = False     # opt-in: derive the digit threshold from the dot instead of a fixed value
KI_V8_ADAPTIVE_DARK_FRAC = 0.55       # threshold = crop_median_brightness × this (0.50 is the measured stroke ratio)
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
HEAL_METHOD = "senzu"
MAJIN_REGEN_SLOT = 3
MAJIN_REGEN_SENZU_RESERVE = True
# Auto: look for Super Regeneration in the G panel (any slot) and fall back to
# the normal heal method once a run proves this character doesn't have it.
MAJIN_REGEN_AUTO = True
MAJIN_REGEN_UNAVAILABLE_FOR_RUN = False
MAJIN_REGEN_READY_AT = 0.0             # Monotonic, retained across Stop/Start within this process.
MAJIN_REGEN_CHANNEL_UNTIL = 0.0
MAJIN_CONTROLLER_ACTIVE = False
HEALING_ACTION = None
SENZU_ENABLED = True                  # Autofeed: eat a senzu (H + slot key) when the HP bar turns red.
SENZU_SLOT = 1                        # Item slot the senzu is assigned to (1-4).
SENZU_SLOT_MATCH_MIN = 0.74
SENZU_DELAY_SEC = 0.0                 # Extra wait between detecting red HP and eating.
SENZU_RECOVERY_TIMEOUT_SEC = 7.0      # Wait for green HP before one confirmed retry.
SENZU_STUCK_RESET_LIMIT = 1
SENZU_STUCK_RESETS_USED = 0
SENZU_STUCK_RESET_REQUESTED = False
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


def _normalize_heal_method(value, *, strict=False):
    if value in ("senzu", "majin_regen"):
        return value
    if strict:
        raise ValueError("Healing method must be senzu or majin_regen")
    return "senzu"


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
SENZU_TRAIT_RESELECT_DEFERRED = False
_manual_next_hotkey_handle = None       # keyboard.hook remover for re-registration on rebind
_pause_hotkey_handle = None
CURRENT_TRAINING_STATE = None
TRAINING_MENU_VISIBLE = False
MACRO_THREAD = None
MACRO_STARTED_AT = 0.0
MACRO_STARTED_MONOTONIC = None

# A separate capture thread owns progression and senzu checks so blocking
# minigame handlers cannot starve either one. Events are reset for every run.
PROGRESSION_COMPLETE_REQUESTED = threading.Event()
PROGRESSION_GENERATION = 0
PROGRESSION_COMPLETION = None
PROGRESSION_STALL_RESET_REQUESTED = threading.Event()
PROGRESSION_TRACKED_STATE = None
PROGRESSION_COMPLETE = None
PROGRESSION_CAPPED_STATE = None
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
    "majin_regens":     0,
    "majin_regen_failures": 0,
    "switches":         0,
    "recovery_attempts": 0,
    "hit_restarts": 0,
}

def _telemetry_reset():
    LOOP_METRICS.update(loops_per_second=0.0, last_cycle_ms=0.0)
    for k in list(TELEMETRY.keys()):
        TELEMETRY[k] = 0


def _begin_run_result():
    global _CURRENT_RUN_OUTCOME, _CURRENT_RUN_REASON, _CURRENT_RUN_CATEGORY
    global _CURRENT_RUN_RETRYABLE
    global _USER_STOP_LATCHED, _AFTER_ACTIONS_BLOCKED
    global GC_RUN_CONFIRMED, _GAME_COVERED_SINCE
    GC_RUN_CONFIRMED = False
    _GAME_COVERED_SINCE = None
    with _run_result_lock:
        _CURRENT_RUN_OUTCOME = None
        _CURRENT_RUN_REASON = None
        _CURRENT_RUN_CATEGORY = None
        _CURRENT_RUN_RETRYABLE = False
        _USER_STOP_LATCHED = False
        _AFTER_ACTIONS_BLOCKED = False


def _run_elapsed_seconds(ended_at=None):
    if MACRO_STARTED_MONOTONIC is not None:
        return max(0.0, time.monotonic() - MACRO_STARTED_MONOTONIC)
    ended_at = time.time() if ended_at is None else ended_at
    return max(0.0, ended_at - (MACRO_STARTED_AT or ended_at))


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
_ui_log_lock = threading.Lock()
_ui_log_sequence = 0


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
            global _ui_log_sequence
            with _ui_log_lock:
                _ui_log_sequence += 1
                self._ring.append({"t": time.time(), "seq": _ui_log_sequence,
                                   "msg": s.rstrip("\n")})
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


def _session_marker_path():
    return os.path.join(JSON_DIR, "last_session.json")


# Filled in at boot when the previous sidecar never got to mark itself clean.
# None means the last shutdown was orderly (or this is a first run).
PREVIOUS_SESSION_CRASH = None


def _claim_session_marker(log_path):
    """Record this session as in-progress and report on how the last one ended.

    A file is the only thing that survives the process it describes. The marker is
    written unclean and only flipped to clean on the way out, so anything that stops
    the sidecar without running that code — an unhandled exception, a kill, a power
    cut — leaves the evidence behind for the next launch to find.
    """
    global PREVIOUS_SESSION_CRASH
    PREVIOUS_SESSION_CRASH = None
    path = _session_marker_path()
    try:
        with open(path, "r", encoding="utf-8") as fh:
            previous = json.load(fh)
        if not isinstance(previous, dict):
            previous = None
        elif (not isinstance(previous.get("pid"), (int, str))
              or isinstance(previous.get("pid"), bool)
              or int(previous["pid"]) <= 0
              or not isinstance(previous.get("clean"), bool)
              or not isinstance(previous.get("version"), str)
              or not isinstance(previous.get("started_at"), (int, float))
              or isinstance(previous.get("started_at"), bool)
              or not isinstance(previous.get("log"), (str, type(None)))):
            previous = None
    except Exception:
        previous = None
    # A live PID means a second copy is running, not a crash — don't cry wolf.
    if (previous and not previous["clean"]
            and not _pid_alive(int(previous["pid"]))):
        PREVIOUS_SESSION_CRASH = {
            "version": previous.get("version"),
            "started_at": previous.get("started_at"),
            "log": previous.get("log"),
            "log_tail": _read_log_tail(previous.get("log")),
        }
    try:
        os.makedirs(JSON_DIR, exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({
                "pid": os.getpid(),
                "version": APP_VERSION,
                "started_at": time.time(),
                "log": log_path,
                "clean": False,
            }, fh)
    except Exception as e:
        print(f"[sidecar] Could not write the session marker: {e}")


def _release_session_marker():
    """Mark this session as having shut down on purpose."""
    path = _session_marker_path()
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:
        return
    if int(data.get("pid") or 0) != os.getpid():
        return  # a newer sidecar owns the marker now; leave its state alone
    data["clean"] = True
    data["ended_at"] = time.time()
    try:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
    except Exception:
        pass


def _start_session_logging():
    """Tee stdout into the UI log ring and this session's log file (once).

    Startup calls this before loading settings: the packaged sidecar has no
    console, so a "could not load settings / preserved original" warning printed
    earlier went nowhere and the reset looked silent."""
    if isinstance(sys.stdout, _TeeBuffer):
        return
    session_log = _open_session_log_file()
    sys.stdout = _TeeBuffer(sys.stdout, _ui_log_ring, file_handle=session_log)
    if session_log is not None:
        try:
            # Banner so the file is identifiable
            print(f"[sidecar] Session log: {session_log.name}")
        except Exception:
            pass
    _claim_session_marker(getattr(session_log, "name", None))
    if PREVIOUS_SESSION_CRASH:
        print("[sidecar] The previous session ended without shutting down cleanly.")


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
DEFAULT_USER_SETTINGS = settings_schema.default_settings(globals())


COMPATIBILITY_DEFAULTS = {
    "mouse_click_button": "left",
    "respawn_settle_sec": 1.0,
    "scan_rate_limit_hz": 0,
    "restart_health_after_hit": False,
    "restart_ki_after_hit": False,
    "restart_after_hit_delay_sec": 0.2,
    # Charge Ki with Shift outside minigames, up to the second or third layer.
    "charge_hold_mode": "off",
    # Safety nets that users may switch off; both default on.
    "progression_stall_enabled": True,
    "auto_retry_walk_fallback": True,
}
CHARGE_HOLD_MODES = ("off", "second", "third")
# Older builds held Shift during minigames; anyone who had that on wanted charge.
LEGACY_CHARGE_HOLD_MODES = {"whole_run": "third", "until_ki_control": "third"}
COMPATIBILITY_SETTINGS = dict(COMPATIBILITY_DEFAULTS)
DEFAULT_USER_SETTINGS.update(COMPATIBILITY_DEFAULTS)
LOOP_METRICS = {"loops_per_second": 0.0, "last_cycle_ms": 0.0}


def _set_compatibility_setting(key, value):
    if key == "mouse_click_button":
        if value not in ("left", "right", "windows"):
            raise ValueError("Click button must be left, right or windows")
    elif key in ("restart_health_after_hit", "restart_ki_after_hit",
                 "progression_stall_enabled", "auto_retry_walk_fallback"):
        value = _ui_bool(value)
    elif key == "respawn_settle_sec":
        value = _bounded_float(value, 0.0, 30.0)
    elif key == "restart_after_hit_delay_sec":
        value = _bounded_float(value, 0.05, 2.0)
    elif key == "scan_rate_limit_hz":
        value = _bounded_int(value, 0, 240)
        if 0 < value < 10:
            raise ValueError("Scan limit must be 0 (unlimited) or 10–240 Hz")
    elif key == "charge_hold_mode":
        value = LEGACY_CHARGE_HOLD_MODES.get(value, value)
        if value not in CHARGE_HOLD_MODES:
            raise ValueError("Charge must be off, second or third")
    else:
        raise ValueError(f"Unknown compatibility setting: {key}")
    with _stop_input_gate:
        COMPATIBILITY_SETTINGS[key] = value
        if key == "charge_hold_mode" and _CHARGE_HELD:
            _set_charge_hold(True)


def _game_has_focus():
    return bool(GAME_HWND and not GAME_WINDOW_MINIMIZED
                and _user32.GetForegroundWindow() == GAME_HWND)


_focus_failure_last_log_ts = None


def _log_game_focus_failure():
    global _focus_failure_last_log_ts
    now = time.monotonic()
    if _focus_failure_last_log_ts is None or now - _focus_failure_last_log_ts >= 5.0:
        _focus_failure_last_log_ts = now
        print("[window] Roblox is not foreground; game input skipped")


def _ensure_game_focus_for_input():
    _wait_while_game_covered()
    if _game_has_focus():
        return True
    focus_game_window()
    if _game_has_focus():
        return True
    _log_game_focus_failure()
    return False


def _reset_minigame_detection():
    _reset_ki_v9()
    _ki_v8_state.update(last_dot=None, consecutive_seen=0, last_click_at=0.0)
    _HEALTH_V2_STATE.update(geometry=None, target=None, armed=True, last_hit=0.0)


def _restart_minigame_after_hit(category, menu_visible, reselect):
    """Restart only through a confirmed menu; completion and Stop always win."""
    def interrupt_check():
        check_exit()
        if PAUSE_TOGGLE_REQUESTED:
            raise SkipMinigameException()
        if not _game_has_focus():
            raise RuntimeError("Restart cancelled: Roblox lost focus")

    with _input_lock:
        interrupt_check()
        PROGRESSION_STALL_RESET_REQUESTED.set()
        deadline = time.monotonic() + COMPATIBILITY_SETTINGS["restart_after_hit_delay_sec"]
        while time.monotonic() < deadline:
            interrupt_check()
            safe_sleep(min(0.025, max(0.0, deadline - time.monotonic())))
        interrupt_check()
        _set_training_state(None)
        if not menu_visible():
            hardware_tap("tab")
        deadline = time.monotonic() + 3.0
        while not menu_visible():
            interrupt_check()
            if time.monotonic() >= deadline:
                raise RuntimeError("Restart cancelled: training menu did not open")
            safe_sleep(0.05)
        interrupt_check()
        if not reselect(category, "[RESTART]", interrupt_check=interrupt_check):
            raise RuntimeError("Restart cancelled: trait could not be reselected")
        _set_training_state(category)
        _reset_minigame_detection()
        TELEMETRY["hit_restarts"] += 1


def _finish_minigame_cycle(started):
    limit = COMPATIBILITY_SETTINGS["scan_rate_limit_hz"]
    elapsed = time.perf_counter() - started
    safe_sleep(max(0.001, (1.0 / limit - elapsed) if limit else 0.001))
    elapsed = max(0.000001, time.perf_counter() - started)
    previous = LOOP_METRICS["last_cycle_ms"]
    smoothed = elapsed * 1000 if not previous else previous * 0.8 + elapsed * 200
    LOOP_METRICS.update(last_cycle_ms=smoothed, loops_per_second=1000 / smoothed)
    notifications.progress(_run_elapsed_seconds(), CURRENT_TRAINING_STATE, _notification_extras)


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
    global START_DELAY, GC_GRAVITY_TARGET_G, PREVENT_SLEEP_WHILE_RUNNING, SWITCH_TO_GAME_ON_START
    global RESTORE_FULLSCREEN_ON_START, WINDOWED_MODE_ON_START, DISPLAY_CONFIRM_CHANGES
    global HIDE_ROBLOX_CHAT_ON_START, STOP_CHARGING_AFTER_KI_CONTROL, KI_HP_CHECK_MODE
    global SHUTDOWN_PC_WHEN_FINISHED, AFTER_RUN_GAME_ACTION, AFTER_RUN_ON_FAILURE
    global AUTO_RETRY_ON_FAILURE, AUTO_RETRY_MAX_ATTEMPTS
    global AUTO_RETRY_RECOVERY_MODE, AUTO_RETRY_WALK_OUT, AUTO_RETRY_WALK_SECONDS
    global DIAGNOSTIC_MODE
    global NEW_GAME_WAIT, NO_YELLOW_TIMEOUT_SEC, NO_YELLOW_FALLBACK_ENABLED
    global PROGRESSION_STALL_TIMEOUT_SEC
    global MANUAL_NEXT_KEY, START_STOP_HOTKEY, PAUSE_HOTKEY
    global HEALTH_HIT_COOLDOWN_SEC, HEALTH_MODE
    global KEY_PRESS_DELAY, STABILIZE_DELAY, POST_COMBO_DELAY
    global TRAINING_ORDER_CUSTOM, AGILITY_MODE
    global AGILITY_GREEN_OBSERVE_SEC, AGILITY_INTER_STRING_WAIT_SEC, AGILITY_AFTER_GREEN_SETTLE_SEC

    COMPATIBILITY_SETTINGS.update(COMPATIBILITY_DEFAULTS)
    START_DELAY = float(DEFAULT_USER_SETTINGS["start_delay_sec"])
    GC_GRAVITY_TARGET_G = int(DEFAULT_USER_SETTINGS["gc_gravity_target_g"])
    PREVENT_SLEEP_WHILE_RUNNING = bool(
        DEFAULT_USER_SETTINGS["prevent_sleep_while_running"]
    )
    SWITCH_TO_GAME_ON_START = bool(DEFAULT_USER_SETTINGS["switch_to_game_on_start"])
    HIDE_ROBLOX_CHAT_ON_START = bool(DEFAULT_USER_SETTINGS["hide_roblox_chat_on_start"])
    STOP_CHARGING_AFTER_KI_CONTROL = bool(DEFAULT_USER_SETTINGS["stop_charging_after_ki_control"])
    KI_HP_CHECK_MODE = str(DEFAULT_USER_SETTINGS["ki_hp_check_mode"])
    RESTORE_FULLSCREEN_ON_START = bool(
        DEFAULT_USER_SETTINGS["restore_fullscreen_on_start"]
    )
    WINDOWED_MODE_ON_START = bool(
        DEFAULT_USER_SETTINGS["windowed_mode_on_start"]
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
    PROGRESSION_STALL_TIMEOUT_SEC = float(DEFAULT_USER_SETTINGS["progression_stall_timeout_sec"])
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
    global KI_LATENCY_COMP_MS, KI_V8_ADAPTIVE_BRIGHTNESS, TRAIT_CLICK_APPROACH_ENABLED
    global SENZU_ENABLED, SENZU_SLOT, SENZU_DELAY_SEC, SENZU_RECOVERY_TIMEOUT_SEC
    global SENZU_PREFERENCE_MODE
    global SENZU_ZERO_GRAVITY_ON_EMPTY
    global HEAL_METHOD, MAJIN_REGEN_SLOT, MAJIN_REGEN_SENZU_RESERVE, MAJIN_REGEN_AUTO
    KI_V8_CLICK_DELAY_SEC = float(DEFAULT_USER_SETTINGS.get("ki_v8_click_delay_sec", 0.29))
    KI_V8_MODE = str(DEFAULT_USER_SETTINGS.get("ki_v8_mode", "v2_ring"))
    KI_V8_V2_TARGET_R_FACTOR = min(
        3.0, max(KI_V8_V2_R_MIN_FACTOR, float(DEFAULT_USER_SETTINGS.get("ki_v8_v2_target_r_factor", 1.40)))
    )
    KI_V8_V2_BRIGHTNESS_THRESHOLD = int(DEFAULT_USER_SETTINGS.get("ki_v8_v2_brightness_threshold", 220))
    KI_V8_V2_BRIGHT_COUNT_THRESHOLD = int(DEFAULT_USER_SETTINGS.get("ki_v8_v2_bright_count_threshold", 6))
    KI_LATENCY_COMP_MS = min(250, max(0, int(DEFAULT_USER_SETTINGS.get("ki_latency_comp_ms", 0))))
    KI_V8_ADAPTIVE_BRIGHTNESS = bool(DEFAULT_USER_SETTINGS.get("ki_adaptive_brightness", False))
    TRAIT_CLICK_APPROACH_ENABLED = bool(DEFAULT_USER_SETTINGS.get("trait_click_approach", False))
    HEAL_METHOD = _normalize_heal_method(DEFAULT_USER_SETTINGS.get("heal_method", "senzu"))
    MAJIN_REGEN_SLOT = min(8, max(1, int(DEFAULT_USER_SETTINGS.get("majin_regen_slot", 3))))
    MAJIN_REGEN_SENZU_RESERVE = bool(DEFAULT_USER_SETTINGS.get("majin_regen_senzu_reserve", True))
    MAJIN_REGEN_AUTO = bool(DEFAULT_USER_SETTINGS.get("majin_regen_auto", True))
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


def _is_supported_roblox_window(hwnd):
    """Reject title-only matches such as browsers showing a Roblox page."""
    executable = _window_process_executable(hwnd)
    if executable is not None:
        return executable in SUPPORTED_ROBLOX_EXECUTABLES
    if os.name == "nt" and hwnd:
        try:
            import ctypes
            buf = ctypes.create_unicode_buffer(256)
            if ctypes.windll.user32.GetClassNameW(int(hwnd), buf, 256):
                cls = buf.value.lower()
                if cls in {"windowsclient", "robloxplayerbetawindowclass"}:
                    return True
        except Exception:
            pass
    return False

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


def _game_window_frame_padding():
    """Return (width, height) of Roblox's non-client frame, or None.

    Measured as window rect minus client rect rather than derived from
    AdjustWindowRect: DWM, the Windows version and the DPI all change the real
    title-bar and border sizes, and a wrong guess offsets every scan region.
    """
    if os.name != "nt" or GAME_HWND is None:
        return None
    try:
        import ctypes
        user32 = ctypes.windll.user32

        class _RECT(ctypes.Structure):
            _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                        ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

        window = _RECT()
        client = _RECT()
        if not user32.GetWindowRect(int(GAME_HWND), ctypes.byref(window)):
            return None
        if not user32.GetClientRect(int(GAME_HWND), ctypes.byref(client)):
            return None
        frame_width = (window.right - window.left) - (client.right - client.left)
        frame_height = (window.bottom - window.top) - (client.bottom - client.top)
        if frame_width < 0 or frame_height < 0:
            return None
        return frame_width, frame_height
    except Exception as e:
        print(f"[window] frame measurement failed: {e}")
        return None


def _move_game_window(x, y, width, height):
    """Move and resize the Roblox window. Returns True when Windows accepted it."""
    if os.name != "nt" or GAME_HWND is None:
        return False
    try:
        import ctypes
        user32 = ctypes.windll.user32
        if user32.IsZoomed(int(GAME_HWND)) or user32.IsIconic(int(GAME_HWND)):
            user32.ShowWindow(int(GAME_HWND), 9)  # SW_RESTORE
        SWP_NOZORDER = 0x0004
        SWP_NOACTIVATE = 0x0010
        return bool(user32.SetWindowPos(
            int(GAME_HWND), 0, int(x), int(y), int(width), int(height),
            SWP_NOZORDER | SWP_NOACTIVATE,
        ))
    except Exception as e:
        print(f"[window] move failed: {e}")
        return False


def _windowed_target_bounds(monitor, frame_width, frame_height,
                            client_width=GAME_REFERENCE_WIDTH,
                            client_height=GAME_REFERENCE_HEIGHT):
    """Where to put a windowed Roblox so its client area is exactly the reference size.

    Returns ``(x, y, width, height)`` for the whole window (client + frame), or None
    when the display cannot fit it. The scan regions are authored against a
    1920x1080 client, so a smaller client would have to be scaled and a non-16:9 one
    would skew every template — refusing beats silently scanning the wrong pixels.

    Centring uses the work area, not the raw monitor rect, so the window never ends
    up under the taskbar where the macro's own clicks would land on it.
    """
    if not monitor:
        return None
    left = int(monitor.get("work_left", monitor["left"]))
    top = int(monitor.get("work_top", monitor["top"]))
    width = int(monitor.get("work_width", monitor["width"]))
    height = int(monitor.get("work_height", monitor["height"]))
    window_width = int(client_width) + max(0, int(frame_width))
    window_height = int(client_height) + max(0, int(frame_height))
    if window_width > width or window_height > height:
        return None
    return (
        left + (width - window_width) // 2,
        top + (height - window_height) // 2,
        window_width,
        window_height,
    )


def _windowed_fallback_bounds(monitor, frame_width, frame_height):
    """The biggest window this display can actually hold, centred in the work area.

    Only reached once the user has accepted the warning: the client ends up smaller
    than 1920x1080, so every region is scaled, and unless the leftover space happens
    to be 16:9 the X and Y scales differ and the templates skew. Returns
    ``(x, y, width, height)`` or None when there is no usable space at all.
    """
    if not monitor:
        return None
    left = int(monitor.get("work_left", monitor["left"]))
    top = int(monitor.get("work_top", monitor["top"]))
    width = int(monitor.get("work_width", monitor["width"]))
    height = int(monitor.get("work_height", monitor["height"]))
    window_width = min(int(GAME_REFERENCE_WIDTH) + max(0, int(frame_width)), width)
    window_height = min(int(GAME_REFERENCE_HEIGHT) + max(0, int(frame_height)), height)
    if window_width - max(0, int(frame_width)) < 1 or window_height - max(0, int(frame_height)) < 1:
        return None
    return (
        left + (width - window_width) // 2,
        top + (height - window_height) // 2,
        window_width,
        window_height,
    )


def _client_fits_work_area(monitor):
    """True when the client is exactly 1920x1080 and fully inside the work area."""
    if (GAME_WIDTH, GAME_HEIGHT) != (GAME_REFERENCE_WIDTH, GAME_REFERENCE_HEIGHT):
        return False
    left = int(monitor.get("work_left", monitor["left"]))
    top = int(monitor.get("work_top", monitor["top"]))
    right = left + int(monitor.get("work_width", monitor["width"]))
    bottom = top + int(monitor.get("work_height", monitor["height"]))
    return (GAME_OFFSET_X >= left and GAME_OFFSET_Y >= top
            and GAME_OFFSET_X + GAME_WIDTH <= right
            and GAME_OFFSET_Y + GAME_HEIGHT <= bottom)


def ensure_game_windowed(wait=2.0, allow_fallback=False, keep_position=False):
    """Size and centre Roblox so its client area is exactly 1920x1080. Returns (ok, message).

    Roblox has to leave fullscreen first (F11 is a toggle, so this only presses it
    when the client really does cover the monitor), and the frame is measured rather
    than assumed: the title bar height depends on the Windows version and DPI, and
    getting it wrong shifts every scan region by exactly that many pixels.

    ``allow_fallback`` accepts an undersized window on a display that cannot hold a
    1920x1080 one. It is opt-in per call because the result scales (and usually
    skews) every scan region, so the user has to have agreed to it first.
    """
    global WINDOWED_BLOCK_CODE
    WINDOWED_BLOCK_CODE = None
    if not update_game_window():
        return False, "Open Roblox before using Windowed Mode."
    if GAME_HWND is None:
        return False, "Open Roblox before using Windowed Mode."

    monitor = _monitor_info_for_window(GAME_HWND)
    if monitor is None:
        return False, "Could not identify the display Roblox is on."

    if game_window_is_fullscreen():
        if not focus_game_window():
            return False, "Could not focus Roblox to leave fullscreen."
        print("[window] Leaving fullscreen with F11 before sizing the window.")
        try:
            pydirectinput.keyDown("f11")
            time.sleep(UTILITY_KEY_HOLD_SEC)
        finally:
            pydirectinput.keyUp("f11")
        time.sleep(UTILITY_KEY_GAP_SEC)
        deadline = time.monotonic() + wait
        while time.monotonic() < deadline:
            time.sleep(0.15)
            update_game_window()
            if game_window_is_fullscreen() is False:
                break
        if game_window_is_fullscreen() is not False:
            return False, "Roblox did not leave fullscreen (F11 not accepted)."

    # Players often drag a correctly sized window aside while the macro is
    # stopped; snapping it back to centre on every start is annoying. Only leave
    # it where it is when the whole client is still on-screen, since pixels off
    # the work area can't be captured or clicked. The manual Windowed button
    # still recentres, since that's the one way to ask for it.
    if (keep_position and game_window_is_fullscreen() is False
            and _client_fits_work_area(monitor)):
        print(f"[window] Roblox already {GAME_WIDTH}x{GAME_HEIGHT}, leaving it where it is.")
        return True, f"Roblox already {GAME_WIDTH}x{GAME_HEIGHT}."

    frame = _game_window_frame_padding()
    if frame is None:
        return False, "Could not measure the Roblox window frame."
    frame_width, frame_height = frame
    bounds = _windowed_target_bounds(monitor, frame_width, frame_height)
    undersized = bounds is None
    if undersized:
        if not allow_fallback:
            # The caller has to be able to tell "wrong size display" from "Windows
            # refused" so it can offer the warning instead of a dead end. The frame
            # can only be measured after leaving fullscreen, so this cannot be
            # predicted before the attempt.
            WINDOWED_BLOCK_CODE = "windowed_too_small"
            return False, (
                f"This display is {monitor['width']}x{monitor['height']} — too small for a "
                "1920x1080 window. Use Fullscreen On Start or Set 1080p instead."
            )
        bounds = _windowed_fallback_bounds(monitor, frame_width, frame_height)
        if bounds is None:
            return False, (
                f"This display is {monitor['width']}x{monitor['height']} — there is no room "
                "for a Roblox window at all."
            )

    x, y, window_width, window_height = bounds
    if not _move_game_window(x, y, window_width, window_height):
        return False, "Windows refused to move the Roblox window."

    deadline = time.monotonic() + wait
    while time.monotonic() < deadline:
        time.sleep(0.15)
        update_game_window()
        if (GAME_WIDTH, GAME_HEIGHT) == (GAME_REFERENCE_WIDTH, GAME_REFERENCE_HEIGHT):
            print(f"[window] Roblox windowed at {GAME_WIDTH}x{GAME_HEIGHT}, centred on screen.")
            return True, f"Roblox set to {GAME_WIDTH}x{GAME_HEIGHT}, centred."
        if undersized and (GAME_WIDTH, GAME_HEIGHT) == (
            window_width - frame_width, window_height - frame_height
        ):
            print(f"[window] Roblox windowed at {GAME_WIDTH}x{GAME_HEIGHT} (undersized, accepted).")
            return True, (
                f"Roblox set to {GAME_WIDTH}x{GAME_HEIGHT} — smaller than 1920x1080, "
                "so detection is scaled."
            )
    return False, (
        f"Roblox ended up {GAME_WIDTH}x{GAME_HEIGHT} instead of "
        f"{GAME_REFERENCE_WIDTH}x{GAME_REFERENCE_HEIGHT}."
    )


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
    try:
        pydirectinput.keyDown("f11")
        time.sleep(UTILITY_KEY_HOLD_SEC)
    finally:
        pydirectinput.keyUp("f11")
    time.sleep(UTILITY_KEY_GAP_SEC)
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
                # Skip tiny windows (launcher/loading dialogs are typically <400x300,
                # the actual game is ≥400x300 in any reasonable mode).
                if w < 400 or h < 300:
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
        # Alt only unlocks Windows foreground activation; it is not a game tap.
        win32api.keybd_event(win32con.VK_MENU, 0, 0, 0)
        try:
            user32.SetForegroundWindow(GAME_HWND)
        finally:
            win32api.keybd_event(win32con.VK_MENU, 0, win32con.KEYEVENTF_KEYUP, 0)
    time.sleep(0.15)
    focused = user32.GetForegroundWindow() == GAME_HWND
    if not focused:
        _log_game_focus_failure()
    return focused


def _windows_above_game():
    import win32gui
    from ctypes import wintypes

    hwnd = win32gui.GetTopWindow(0)
    seen = set()
    while hwnd and hwnd != GAME_HWND and hwnd not in seen:
        seen.add(hwnd)
        try:
            if win32gui.IsWindowVisible(hwnd) and not win32gui.IsIconic(hwnd):
                cloaked = wintypes.DWORD()
                _ctypes.windll.dwmapi.DwmGetWindowAttribute(
                    _ctypes.c_void_p(hwnd), 14, _ctypes.byref(cloaked), 4)
                style = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
                invisible_overlay = False
                if style & win32con.WS_EX_LAYERED:
                    try:
                        _, alpha, flags = win32gui.GetLayeredWindowAttributes(hwnd)
                        invisible_overlay = bool(flags & win32con.LWA_ALPHA and alpha == 0)
                    except win32gui.error:
                        pass  # Per-pixel layered windows have no uniform alpha setting.
                # Click-through overlays (NVIDIA's "Instant Replay saved!", Discord,
                # Steam) span the whole screen but only draw a corner toast and take no
                # input; live, one stopped a run as "covering Roblox".
                click_through = bool(style & win32con.WS_EX_TRANSPARENT and style & win32con.WS_EX_LAYERED)
                if not cloaked.value and not invisible_overlay and not click_through:
                    yield win32gui.GetWindowRect(hwnd), win32gui.GetWindowText(hwnd)
            hwnd = win32gui.GetWindow(hwnd, win32con.GW_HWNDNEXT)
        except win32gui.error:
            # The z-order can change while an overlay is closing.
            break


def _game_detection_regions():
    if _new_game_ui():
        ui = training_ui
        boxes = [ui.HEALTH_BAR_BOX, ui.AGILITY_LETTER_BOX, ui.ROUND_TIMER_BOX,
                 *ui.PROGRESSION_LABEL_BOXES.values(), TRAINING_MENU_BOX, GRAVITY_LABEL_BOX,
                 SENZU_HP_FILL_BOX, ui.HUD_LABELS_BOX, ui.GC_BANNER_BOX, ui.TRAINING_POINTS_BOX,
                 ui.MajinTransform.PANEL_BOX, ui.SenzuMenus.HEADING_BOX, ui.SenzuMenus.LIST_BOX]
    else:
        boxes = [HEALTH_BOX, AGILITY_BOX, TRAINING_MENU_BOX, GRAVITY_LABEL_BOX,
                 SENZU_HP_FILL_BOX, PROG_SEARCH_BOX, TRAINING_POINTS_BOX,
                 majin_vision.PANEL_BOX, majin_vision.ENTER_PROMPT_BOX,
                 (0, 340, 650, 460), (1400, 850, 520, 230)]
    if CURRENT_TRAINING_STATE and "Ki" in CURRENT_TRAINING_STATE:
        boxes.append((0, 200, 1920, 880))
    boxes.extend((x - 10, y - 10, 20, 20) for x, y in BUTTONS.values())
    regions = []
    for box in boxes:
        if isinstance(box, dict):
            x, y, width, height = (box[key] for key in ("left", "top", "width", "height"))
        else:
            x, y, width, height = box
        regions.append((GAME_OFFSET_X + x * GAME_WIDTH / 1920,
                        GAME_OFFSET_Y + y * GAME_HEIGHT / 1080,
                        GAME_OFFSET_X + (x + width) * GAME_WIDTH / 1920,
                        GAME_OFFSET_Y + (y + height) * GAME_HEIGHT / 1080))
    return regions


def _game_window_covering_title():
    if GAME_HWND is None or GAME_WIDTH <= 0 or GAME_HEIGHT <= 0:
        return None
    regions = _game_detection_regions()
    for (left, top, right, bottom), title in _windows_above_game():
        if any(left < x2 and right > x1 and top < y2 and bottom > y1
               for x1, y1, x2, y2 in regions):
            return title or "untitled window"
    return None


# A notification toast or the taskbar can pop up over fullscreen Roblox for a
# while. Input waits for it to go instead of ending the run, and only gives up
# if something stays on top this long.
GAME_COVERED_GIVE_UP_SEC = 120.0
_GAME_COVERED_SINCE = None
_game_covered_lock = threading.Lock()


def _set_game_covered(covered):
    global _GAME_COVERED_SINCE
    with _game_covered_lock:
        if covered == (_GAME_COVERED_SINCE is not None):
            return
        _GAME_COVERED_SINCE = time.monotonic() if covered else None
    if covered:
        _set_charge_hold(False)
        print("[SAFETY] Another window is over Roblox; input paused until it goes")
    else:
        print("[SAFETY] Roblox is clear again; carrying on")


def _watch_game_covered():
    """Monitor-thread check. Returns True once the run has been stopped."""
    if not _game_window_covering_title():
        _set_game_covered(False)
        return False
    _set_game_covered(True)
    since = _GAME_COVERED_SINCE
    if since is not None and time.monotonic() - since >= GAME_COVERED_GIVE_UP_SEC:
        return _stop_if_game_covered()
    return False


def _wait_while_game_covered():
    """Hold input while another window is over Roblox.

    A minigame input that had to wait is dropped rather than sent late: the
    frame it was aimed at is gone. Menu and healing input carries on."""
    if _GAME_COVERED_SINCE is None:
        return
    while _game_window_covering_title():
        if _USER_STOP_LATCHED or UI_STOP_REQUESTED:
            check_exit()
        since = _GAME_COVERED_SINCE
        if since is not None and time.monotonic() - since >= GAME_COVERED_GIVE_UP_SEC:
            _stop_if_game_covered()
            check_exit()
        time.sleep(0.1)
    _set_game_covered(False)
    # After-run cleanup runs once the monitor has stopped; it only waits.
    monitor = _background_monitor_thread
    if (CURRENT_TRAINING_STATE is not None and monitor is not None
            and threading.current_thread() is not monitor):
        raise SkipMinigameException()


def _stop_if_game_covered():
    global UI_STOP_REQUESTED, _AFTER_ACTIONS_BLOCKED
    covering = _game_window_covering_title()
    if not covering:
        return False
    # Cleanup actions are input too, and may hit the covering window.
    _AFTER_ACTIONS_BLOCKED = True
    UI_STOP_REQUESTED = True
    # Titles can name private browser tabs or documents, and run outcomes and the
    # session log both end up in shared reports. Only the decision is recorded.
    _record_run_outcome("error", "Another window is covering Roblox")
    _release_macro_input()
    print("[SAFETY] Another application window is covering a game reader or input region; stopped")
    return True


def _new_ui_hp_fraction(sct):
    """HP bar fill share, or None while the HUD is hidden (menus, Ki minigames)."""
    if not training_ui.hud_visible(_grab_reference_box(sct, training_ui.HUD_LABELS_BOX)):
        return None
    return training_ui.hp_fill_fraction(_grab_reference_box(sct, SENZU_HP_FILL_BOX))


def _hp_above_red(sct):
    if _new_game_ui():
        fraction = _new_ui_hp_fraction(sct)
        return fraction is not None and fraction > training_ui.HP_CRITICAL_FRACTION
    return _hp_fill_is_above_red(_grab_reference_box(sct, SENZU_HP_FILL_BOX))


def _hp_fill_is_above_red(raw):
    """Accept a visibly filled orange or green HP patch, never an empty one."""
    if _new_game_ui():
        fraction = training_ui.hp_fill_fraction(raw)
        return fraction is not None and fraction > training_ui.HP_CRITICAL_FRACTION
    hsv = cv2.cvtColor(raw[:, :, :3], cv2.COLOR_BGR2HSV)
    vivid = (hsv[:, :, 1] > 140) & (hsv[:, :, 2] > 140)
    vivid_count = int(np.count_nonzero(vivid))
    if vivid_count < int(raw.shape[0] * raw.shape[1] * 0.45):
        return False
    hue = hsv[:, :, 0]
    recovered = int(np.count_nonzero((hue >= 6) & (hue <= 75) & vivid))
    return recovered >= int(vivid_count * 0.82)


def _hp_bar_is_red(sct):
    """True only when the fixed left-side HP fill patch is critical red."""
    if _new_game_ui():
        fraction = _new_ui_hp_fraction(sct)
        return fraction is not None and fraction <= training_ui.HP_CRITICAL_FRACTION
    raw = _grab_reference_box(sct, SENZU_HP_FILL_BOX)
    return _hp_fill_is_critical(raw)


def _hp_bar_is_green(sct):
    """True only when the fixed left-side HP fill patch is restored green."""
    if _new_game_ui():
        fraction = _new_ui_hp_fraction(sct)
        return fraction is not None and fraction >= training_ui.HP_HEALED_FRACTION
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
    if _new_game_ui():
        return {**_SENZU_ASSET_CACHE, "training": _training_menu_template()}
    return _SENZU_ASSET_CACHE


_TRAINING_MENU_TEMPLATES = {}


def _training_menu_template():
    """The "Training Mode" heading for the active UI layout (None if missing)."""
    if GAME_UI_LAYOUT not in _TRAINING_MENU_TEMPLATES:
        _TRAINING_MENU_TEMPLATES[GAME_UI_LAYOUT] = (
            training_ui.load_menu_heading() if _new_game_ui()
            else cv2.imread(os.path.join(BASE_DIR, "tpl_training_mode.png"), cv2.IMREAD_GRAYSCALE)
        )
    return _TRAINING_MENU_TEMPLATES[GAME_UI_LAYOUT]


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
    deadline = time.monotonic() + timeout
    best_score = 0.0
    while time.monotonic() < deadline and not _senzu_abort_requested():
        visible, score, location = _template_in_reference_box(
            sct, template, box, threshold
        )
        best_score = max(best_score, score)
        if _senzu_abort_requested() or time.monotonic() >= deadline:
            break
        if visible:
            return location, score
        time.sleep(min(0.05, max(0.0, deadline - time.monotonic())))
    return None, best_score


def _wait_for_senzu_inventory_or_game_menu(sct, assets, timeout=1.8):
    """Poll both M-menu routes instead of timing out on the absent one first."""
    deadline = time.monotonic() + timeout
    inventory_score = game_menu_score = 0.0
    previous_menu = None
    stable = 0
    while time.monotonic() < deadline and not _senzu_abort_requested():
        menu = None
        for name, box in (
                ("inventory", (200, 340, 280, 80)),
                ("game_menu", (0, 340, 220, 80))):
            visible, score, _ = _template_in_reference_box(sct, assets[name], box)
            if name == "inventory":
                inventory_score = max(inventory_score, score)
            else:
                game_menu_score = max(game_menu_score, score)
            if visible:
                menu = name
                break
        if _senzu_abort_requested() or time.monotonic() >= deadline:
            break
        stable = stable + 1 if menu and menu == previous_menu else int(menu is not None)
        previous_menu = menu
        if stable >= 2:
            return menu, game_menu_score, inventory_score
        time.sleep(min(0.05, max(0.0, deadline - time.monotonic())))
    return None, game_menu_score, inventory_score


def _wait_for_training_menu_for_senzu(
        sct, training_template, timeout=1.5, *, allow_gameplay=False):
    """Confirm two Training frames; None means confirmed gameplay when allowed."""
    deadline = time.monotonic() + timeout
    stable = 0
    gameplay_stable = 0
    best_score = 0.0
    assets = _senzu_assets() if allow_gameplay else None
    while time.monotonic() < deadline and not _senzu_abort_requested():
        visible, score = _training_menu_visible_for_senzu(sct, training_template)
        best_score = max(best_score, score)
        stable = stable + 1 if visible else 0
        gameplay = allow_gameplay and not visible and _senzu_gameplay_visible(sct, assets)
        gameplay_stable = gameplay_stable + 1 if gameplay else 0
        if _senzu_abort_requested() or time.monotonic() >= deadline:
            break
        if stable >= 2:
            return True, best_score
        if gameplay_stable >= 2:
            return None, best_score
        time.sleep(min(0.05, max(0.0, deadline - time.monotonic())))
    return False, best_score


def _senzu_gameplay_visible(sct, assets):
    if not _majin_enter_prompt(sct):
        return False
    # The world prompt can remain visible behind Inventory or Game Menu.
    for name, box in (("inventory", (200, 340, 280, 80)),
                      ("game_menu", (0, 340, 220, 80))):
        visible, _, _ = _template_in_reference_box(sct, assets[name], box)
        if visible:
            return False
    return True


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
        or (MAJIN_CONTROLLER_ACTIVE and (PAUSE_TOGGLE_REQUESTED or CONTROLLER_PAUSED))
    )


def _ensure_training_menu_for_senzu(sct, assets):
    if _senzu_abort_requested():
        return False
    training_template = assets["training"]
    visible, _ = _training_menu_visible_for_senzu(sct, training_template)
    if visible:
        return True
    if _senzu_gameplay_visible(sct, assets):
        training_visible, _ = _wait_for_training_menu_for_senzu(
            sct, training_template, timeout=0.35, allow_gameplay=True
        )
        if training_visible is None:
            return _open_training_menu_from_gameplay(sct, assets)
        if training_visible:
            return True

    # Red HP can be detected while the player already has Game Menu or
    # Inventory open. Route those known screens back to Training Mode instead
    # of blindly toggling Tab from an unknown menu state.
    menu, _, _ = _wait_for_senzu_inventory_or_game_menu(sct, assets, timeout=0.35)
    if menu is not None:
        return _close_inventory_to_training(sct, assets)

    if not _focus_game_for_senzu():
        return False
    _tap_key_unchecked("tab")
    deadline = time.monotonic() + 1.8
    stable = 0
    while time.monotonic() < deadline and not _senzu_abort_requested():
        visible, _ = _training_menu_visible_for_senzu(sct, training_template)
        stable = stable + 1 if visible else 0
        if stable >= 2:
            return True
        time.sleep(min(0.05, max(0.0, deadline - time.monotonic())))
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
        sct, slot, slot_template, samples=3, *, hotbar=False, bean_type="full",
        timeout=None):
    """Require agreeing frames; with a timeout, wait for a loaded slot to appear."""
    if samples < 1:
        return None, 0.0
    states = []
    best_score = 0.0
    deadline = None if timeout is None else time.monotonic() + timeout
    while deadline is None or time.monotonic() < deadline:
        if _senzu_abort_requested():
            return None, best_score
        assigned, score = _senzu_slot_has_bean(
            sct, slot, slot_template, hotbar=hotbar, bean_type=bean_type
        )
        states.append(assigned)
        states = states[-samples:]
        best_score = max(best_score, score)
        if len(states) == samples and (deadline is None or all(states)):
            break
        pause = 0.05 if deadline is None else min(
            0.05, max(0.0, deadline - time.monotonic())
        )
        time.sleep(pause)
    if _senzu_abort_requested() or len(states) < samples:
        return None, best_score
    if all(states):
        return True, best_score
    if not any(states):
        return False, best_score
    return None, best_score


def _wait_for_hotbar_slot_clear(
        sct, slot, slot_template, bean_type, timeout=0.77):
    """Confirm two clear H-menu frames after one consume keypress."""
    clear_streak = 0
    best_score = 0.0
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
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
        time.sleep(min(0.05, max(0.0, deadline - time.monotonic())))
    return False, best_score


def _consume_open_senzu_slot(
        sct, slot, slot_template, bean_type, max_key_attempts=3):
    """Activate the open H-menu slot until GC confirms that row cleared.

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
            return None, best_score
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
    return False, best_score


INVENTORY_DIGIT_MIN_IOU = 0.55
INVENTORY_DIGIT_MIN_MARGIN = 0.04
_INVENTORY_GLYPH_SIZE = (16, 22)


def _normalized_glyph(mask):
    ys, xs = np.where(mask)
    cropped = mask[ys.min():ys.max() + 1, xs.min():xs.max() + 1].astype(np.uint8) * 255
    return cv2.resize(cropped, _INVENTORY_GLYPH_SIZE, interpolation=cv2.INTER_AREA) > 127


def _mask_iou(first, second):
    union = np.count_nonzero(first | second)
    return np.count_nonzero(first & second) / union if union else 0.0


def _inventory_digit_glyphs(digit_sheet):
    return [
        _normalized_glyph(digit_sheet[:, digit * 12:(digit + 1) * 12] > 0)
        for digit in range(10)
    ]


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


def _wait_for_inventory_quantity_red(sct, row_y, max_samples=8, *, deadline=float("inf")):
    """Require two consecutive red quantity frames before slot assignment."""
    red_streak = 0
    for index in range(max_samples):
        if _senzu_abort_requested() or time.monotonic() >= deadline:
            return False
        red_streak = red_streak + 1 if _inventory_quantity_is_red(sct, row_y) else 0
        if _senzu_abort_requested() or time.monotonic() >= deadline:
            return False
        if red_streak >= 2:
            return True
        if index + 1 < max_samples:
            time.sleep(min(0.05, max(0.0, deadline - time.monotonic())))
    return False


def _safe_get_cursor_pos():
    try:
        return win32api.GetCursorPos()
    except Exception:
        return None


def _park_menu_cursor(geometry=None):
    with _input_lock, _stop_input_gate:
        if _USER_STOP_LATCHED:
            return
        _user32.SetCursorPos(*_reference_point(*GRAVITY_POINTER_PARK_POINT, geometry))
        _nudge_pointer()


def _restore_menu_cursor(original_cursor, geometry=None):
    left, top, width, height = _game_geometry(geometry)
    if original_cursor is not None:
        x, y = original_cursor
        over_chat = (0 <= x - left <= 480 * width / GAME_REFERENCE_WIDTH
                     and 0 <= y - top <= 420 * height / GAME_REFERENCE_HEIGHT)
    else:
        over_chat = True
    # Restoring a chat hover would immediately cover the next menu/control again.
    target = (_reference_point(*GRAVITY_POINTER_PARK_POINT, geometry)
              if over_chat else original_cursor)
    with _input_lock, _stop_input_gate:
        # After Stop or with another app in front, leave the pointer alone.
        if _USER_STOP_LATCHED or not _game_has_focus():
            return
        _user32.SetCursorPos(*target)
        _nudge_pointer()


@contextmanager
def _menu_cursor_guard(geometry=None):
    geometry = _game_geometry(geometry)
    original_cursor = _safe_get_cursor_pos()
    _park_menu_cursor(geometry)
    try:
        yield
    finally:
        _restore_menu_cursor(original_cursor, geometry)


def _find_senzu_row_in_frame(frame, assets, bean_type, *, row_y=None):
    """Read an exact bean label/count without scrolling or moving the pointer."""
    template = assets["bean"]
    if template is None:
        return None, None, 0.0
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    band_top = 0
    if row_y is not None:
        expected_top = int(row_y) - 395 - template.shape[0] // 2
        band_top = max(0, expected_top - 4)
        band_bottom = min(gray.shape[0], expected_top + template.shape[0] + 4)
        gray = gray[band_top:band_bottom]
    if gray.shape[0] < template.shape[0] or gray.shape[1] < template.shape[1]:
        return None, None, 0.0
    scores = cv2.matchTemplate(gray, template, cv2.TM_CCOEFF_NORMED)
    _, best_score, _, _ = cv2.minMaxLoc(scores)
    matches_y, matches_x = np.where(scores >= 0.80)
    # Half contains the same suffix farther right. Preserve the exact type split.
    type_matches = [
        (float(scores[y, x]), int(y))
        for y, x in zip(matches_y, matches_x)
        if (int(x) <= 30) == (bean_type == "full")
    ]
    if not type_matches:
        return None, None, float(best_score)
    score, match_y = max(type_matches)
    row_top = 395 + band_top + match_y
    count = _read_inventory_count(frame, row_top, assets["digits"])
    return row_top + template.shape[0] // 2, count, score


def _wait_for_senzu_assignment(sct, assets, row_y, count, bean_type, timeout):
    """Keep the pointer at the slot until its label or exact count drop confirms."""
    deadline = time.monotonic() + timeout
    loaded_streak = 0
    best_score = 0.0
    remaining = None
    while time.monotonic() < deadline and not _senzu_abort_requested():
        assigned, score = _senzu_slot_has_bean(
            sct, SENZU_SLOT, assets["slot"], bean_type=bean_type
        )
        best_score = max(best_score, score)
        if _senzu_abort_requested() or time.monotonic() >= deadline:
            return False, best_score, None
        inventory_visible, _, _ = _template_in_reference_box(
            sct, assets["inventory"], (200, 340, 280, 80)
        )
        loaded_streak = loaded_streak + 1 if assigned and inventory_visible else 0
        remaining = None
        if inventory_visible and time.monotonic() < deadline:
            _, remaining, _ = _find_senzu_row_in_frame(
                _grab_reference_box(sct, (0, 395, 650, 335)),
                assets, bean_type, row_y=row_y,
            )
        count_confirmed = count is not None and remaining == count - 1
        if _senzu_abort_requested():
            return False, best_score, remaining
        # Keep the existing three-frame slot guard and exact decrement fallback.
        if loaded_streak >= 3 or count_confirmed:
            return True, best_score, remaining
        time.sleep(min(0.05, max(0.0, deadline - time.monotonic())))
    return False, best_score, remaining


def _find_senzu_row(sct, assets, bean_type="full", *, deadline=None):
    """Search the Items list for one exact Senzu type."""
    deadline = float("inf") if deadline is None else deadline

    def expired():
        return _senzu_abort_requested() or time.monotonic() >= deadline

    def wait(duration):
        until = min(deadline, time.monotonic() + duration)
        while time.monotonic() < until:
            if expired():
                return False
            time.sleep(min(0.05, max(0.0, until - time.monotonic())))
        return not expired()

    if expired():
        return None, None, 0.0
    # Without the Inventory open, the wheel below would zoom the game camera and a
    # false row match could click gameplay. A short wait rides out a redraw frame.
    inventory_location, _ = _wait_for_senzu_screen(
        sct, assets["inventory"], (200, 340, 280, 80), timeout=min(0.4, max(0.0, deadline - time.monotonic()))
    )
    if inventory_location is None or expired():
        return None, None, 0.0
    list_box = (0, 395, 650, 335)
    # GC normally reopens the same Items view. Read it before any pointer travel
    # or scroll reset, including the first refill when there is no row cache.
    visible_deadline = min(deadline, time.monotonic() + 0.30)
    while not expired():
        row_y, count, score = _find_senzu_row_in_frame(
            _grab_reference_box(sct, list_box), assets, bean_type
        )
        if expired():
            return None, None, 0.0
        if row_y is not None:
            _remember_senzu_row(bean_type, row_y)
            return row_y, count, score
        if time.monotonic() >= visible_deadline:
            break
        wait(min(0.05, max(0.0, visible_deadline - time.monotonic())))
    if expired():
        return None, None, 0.0
    best_frame = None
    last_frame = None
    best_score = 0.0
    # Legacy mouse_event wheel packets were ignored by Roblox on this setup.
    # Use the same SendInput path as reliable clicks, first forcing the custom
    # list to its top so remembered Inventory scroll state cannot matter.
    # Position in the empty right margin of the list so the cursor does not hover
    # over item text or icons during scrolling.
    cursor_x, cursor_y = _reference_point(550, 560)
    robust_move(cursor_x, cursor_y)

    def _send_wheel(notches):
        wheel = (_INPUT * 1)(_make_mouse_input(0x0800))  # MOUSEEVENTF_WHEEL
        wheel[0].mi.mouseData = int(notches * 120) & 0xFFFFFFFF
        with _stop_input_gate:
            if _USER_STOP_LATCHED or expired():
                return False
            _user32.SendInput(1, wheel, _ctypes.sizeof(_INPUT))
        return True

    for _ in range(15):
        if expired():
            return None, None, 0.0
        if not _send_wheel(4) or not wait(0.01):
            return None, None, 0.0
    if not wait(0.20):
        return None, None, 0.0

    previous_gray = None
    unchanged_after_scroll = 0
    for _ in range(40):
        if expired():
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
        row_y, count, score = _find_senzu_row_in_frame(frame, assets, bean_type)
        if score > best_score:
            best_score = score
            best_frame = frame.copy()
        if expired():
            return None, None, 0.0
        if row_y is not None:
            _remember_senzu_row(bean_type, row_y)
            return row_y, count, score
        if not _send_wheel(-4) or not wait(0.16):
            return None, None, best_score
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
        if (int(x) <= 30) == (bean_type == "full")
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
        if len(priority) == 1:
            return unreadable_candidate
        bean_type = unreadable_candidate[3]
        row_y, count, score = _find_senzu_row(sct, assets, bean_type)
        if row_y is not None and count != 0:
            return row_y, count, score, bean_type
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
    # Returns as soon as it matches; the longer ceiling covers an Inventory that
    # is still sliding in on slower PCs (a live run left it open at 0.35s).
    menu, _, _ = _wait_for_senzu_inventory_or_game_menu(sct, assets, timeout=1.85)
    inventory_visible = menu == "inventory"
    game_menu_visible = menu == "game_menu"

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
            sct, assets["training"], timeout=1.8, allow_gameplay=True
        )
        if training_visible:
            return True
        if training_visible is None:
            print("[SENZU] Inventory closed to gameplay; reopening Training Mode")
            return _open_training_menu_from_gameplay(sct, assets)
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
        _park_menu_cursor()
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
        sct, assets["training"], timeout=1.8, allow_gameplay=True
    )
    if not training_visible:
        print("[SENZU] Game Menu closed to gameplay; reopening Training Mode")
        return _open_training_menu_from_gameplay(sct, assets)
    return True


def _refill_senzu_slot(
        sct, assets, require_assignment=False, _inventory_reopen_attempted=False):
    global SENZU_REMAINING, SENZU_STATUS, SENZU_ACTIVE_TYPE
    SENZU_STATUS = "refilling"
    _park_menu_cursor()
    if not _focus_game_for_senzu():
        print("[SENZU] Roblox focus not confirmed before opening Game Menu")
        SENZU_STATUS = "error"
        return False
    _tap_key_unchecked("m")
    menu, game_menu_score, inventory_score = _wait_for_senzu_inventory_or_game_menu(
        sct, assets, timeout=1.8
    )
    inventory_open = menu == "inventory"
    if menu is None:
        print(
            "[SENZU] Neither Game Menu nor Inventory confirmed after M "
            f"(menu={game_menu_score:.3f}, inventory={inventory_score:.3f})"
        )
        SENZU_STATUS = "error"
        _close_inventory_to_training(sct, assets)
        return False

    if not inventory_open:
        # The menu poll confirms two frames; a rejected click is retried only
        # while the same Game Menu is still visually confirmed.
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
                # The Game Menu left, so the click landed. On slower PCs the
                # Inventory is still sliding in at 1.2s (live capture: 0.659, then
                # 0.95 once settled); give it longer before calling it a failure.
                inventory_location, settled_score = _wait_for_senzu_screen(
                    sct, assets["inventory"], (200, 340, 280, 80), timeout=2.0
                )
                inventory_score = max(inventory_score, settled_score)
                if inventory_location is not None:
                    print(f"[SENZU] Inventory settled late (score={settled_score:.3f})")
                    break
                print(
                    "[SENZU] Screen changed after Inventory click; refusing a blind retry "
                    f"(inventory={inventory_score:.3f}, menu={game_menu_score:.3f})"
                )
                break
            print(
                f"[SENZU] Inventory click not accepted; retrying "
                f"({click_attempt}/3, score={inventory_score:.3f}, "
                f"target=({x},{y}), cursor={_safe_get_cursor_pos()})"
            )
        if inventory_location is None:
            print(f"[SENZU] Inventory not confirmed (score={inventory_score:.3f})")
            SENZU_STATUS = "error"
            _close_inventory_to_training(sct, assets)
            return False
    else:
        print("[SENZU] M reopened Inventory directly")

    # Scan the current view first even when there is no valid cache. Inventory
    # often already shows Items, so only a missing allowed row needs a tab click.
    row_y, count, score, selected_type = _find_preferred_senzu_row(sct, assets)
    if _senzu_abort_requested():
        return False
    if row_y is None:
        x, y = _reference_point(414, 758)
        click_at(x, y)
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
            x, y = _reference_point(414, 758)
            click_at(x, y)
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
    _park_menu_cursor()
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
        assignment_deadline = time.monotonic() + (
            SENZU_RECOVERY_TIMEOUT_SEC if require_assignment else 0.5
        )
        assignment_attempt = 0
        while time.monotonic() < assignment_deadline:
            if _senzu_abort_requested():
                return False
            assignment_attempt += 1
            x, y = _reference_point(1720, slot_y)
            click_at(x, y)
            confirmed, confirm_score, remaining_after = _wait_for_senzu_assignment(
                sct, assets, row_y, count, selected_type,
                timeout=min(1.2, max(0.0, assignment_deadline - time.monotonic())),
            )
            assigned_score = max(assigned_score, confirm_score)
            if _senzu_abort_requested():
                return False
            if confirmed:
                assigned = True
                if count is not None and remaining_after == count - 1:
                    print(
                        f"[SENZU] Replacement confirmed by inventory count "
                        f"({count} -> {remaining_after})"
                    )
                break
            if not require_assignment or time.monotonic() >= assignment_deadline:
                break

            # Only a failed confirmation window can justify moving back left.
            # Recheck while finding the row so a late redraw cannot double-assign.
            delayed_confirmed, delayed_score, remaining_after = _wait_for_senzu_assignment(
                sct, assets, row_y, count, selected_type,
                timeout=min(0.24, max(0.0, assignment_deadline - time.monotonic())),
            )
            assigned_score = max(assigned_score, delayed_score)
            if _senzu_abort_requested():
                return False
            if delayed_confirmed:
                assigned = True
                break
            retry_row_y, remaining_after, retry_score = _find_senzu_row(
                sct, assets, selected_type, deadline=assignment_deadline
            )
            if _senzu_abort_requested():
                return False
            if count is not None and remaining_after == count - 1:
                assigned = True
                break
            if time.monotonic() >= assignment_deadline:
                break
            if retry_row_y is None or (
                    remaining_after is not None and remaining_after <= 0):
                assigned_score = max(assigned_score, retry_score)
                break
            print(
                f"[SENZU] Replacement not ready during recovery; "
                f"retrying assignment ({assignment_attempt})"
            )
            row_y = retry_row_y
            x, y = _reference_point(311, row_y)
            click_at(x, y)
            _park_menu_cursor()
            _wait_for_inventory_quantity_red(sct, row_y, deadline=assignment_deadline)
    if _senzu_abort_requested():
        return False
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
        # The confirmation poll read stock in place. An unreadable quantity
        # must not send the pointer back left after the slot is already loaded.
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
    global SENZU_TRAIT_RESELECT_DEFERRED
    if _senzu_abort_requested():
        return False
    if CURRENT_TRAINING_STATE not in BUTTONS:
        return True
    visible, _ = _training_menu_visible_for_senzu(sct, assets["training"])
    if not visible:
        return False
    if _senzu_abort_requested():
        return False
    if CONTROLLER_PAUSED:
        SENZU_TRAIT_RESELECT_DEFERRED = True
        return True
    # A run started mid-minigame never saw the menu, so Points Left is unknown
    # and the stat could train on with no points. The menu is open now; read it.
    # Points Left only refines completion, so a bad read must never block the resume.
    # It also re-reads a known cap, since the panel is the only reliable view of
    # the count when the minigame label sits on a pale sky.
    cap = PROGRESSION_TARGETS.get(CURRENT_TRAINING_STATE)
    if cap is None or (cap[0] < cap[1] and CURRENT_TRAINING_STATE not in _POINTS_CAP_REACHED):
        try:
            _record_points_target(CURRENT_TRAINING_STATE, _capture_points_panels(sct, CURRENT_TRAINING_STATE))
        except Exception as error:
            print(f"[PROG] Points Left read failed: {error}")
    bx, by = _button_screen_point(CURRENT_TRAINING_STATE)
    click_at(int(bx), int(by))
    _park_menu_cursor()
    hidden = 0
    deadline = time.monotonic() + 1.5
    while time.monotonic() < deadline and not _senzu_abort_requested():
        visible, _ = _training_menu_visible_for_senzu(sct, assets["training"])
        hidden = 0 if visible else hidden + 1
        if hidden >= 2:
            return True
        time.sleep(min(0.05, max(0.0, deadline - time.monotonic())))
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
    if _new_game_ui():
        return True  # loaded on demand by _eat_senzu_v2, outside Training Mode
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
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and not _senzu_abort_requested():
        recovered = (_hp_above_red(sct)
                     if bean_type == "half" else _hp_bar_is_green(sct))
        recovered_streak = recovered_streak + 1 if recovered else 0
        if recovered_streak >= 2:
            return True
        time.sleep(min(0.05, max(0.0, deadline - time.monotonic())))
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


def _request_stuck_senzu_recovery():
    global SENZU_STUCK_RESETS_USED, SENZU_STUCK_RESET_REQUESTED, UI_STOP_REQUESTED
    if _senzu_abort_requested() or _USER_STOP_LATCHED:
        return False
    if SENZU_STUCK_RESETS_USED >= SENZU_STUCK_RESET_LIMIT:
        _stop_for_senzu_failure(
            "Eat key is still ignored with a loaded slot; the one-reset-per-run "
            "limit was reached"
        )
        return False
    SENZU_STUCK_RESETS_USED += 1
    SENZU_STUCK_RESET_REQUESTED = True
    print(
        "[SENZU] Eat key ignored with a loaded slot; resetting the "
        "character to clear the game's stuck item state"
    )
    # Hand recovery to the run owner after both training and healing release input.
    _record_run_outcome("error", "Senzu eat key ignored with a confirmed loaded slot")
    UI_STOP_REQUESTED = True
    return False


def _client_frame(sct):
    return _grab_reference_box(sct, (0, 0, GAME_REFERENCE_WIDTH, GAME_REFERENCE_HEIGHT))


def _wait_for_frame(sct, condition, timeout, settle=2):
    """Poll whole client frames until condition(frame) holds `settle` times in a row."""
    deadline = time.monotonic() + timeout
    streak = 0
    while time.monotonic() < deadline and not _senzu_abort_requested():
        streak = streak + 1 if condition(_client_frame(sct)) else 0
        if streak >= settle:
            return True
        time.sleep(0.05)
    return False


def _leave_training_to_gameplay(sct):
    """Tab until the GC banner says "open training". From a running minigame
    the first Tab stops it and shows Training Mode; the second closes that."""
    open_prompt = lambda frame: training_ui.open_training_prompt(
        training_ui.crop(frame, training_ui.OPEN_TRAINING_BOX))
    for _tab in range(2):
        if open_prompt(_client_frame(sct)):
            return True
        if not _focus_game_for_senzu():
            return False
        _tap_key_unchecked("tab")
        if _wait_for_frame(sct, open_prompt, timeout=0.8):
            return True
    return _wait_for_frame(sct, open_prompt, timeout=0.6)


def _close_side_menu(sct):
    menus = training_ui.SenzuMenus
    if menus.side_menu_state(_client_frame(sct)) != "open":
        return True
    if not _focus_game_for_senzu():
        return False
    _tap_key_unchecked("m")
    return _wait_for_frame(sct, lambda frame: menus.side_menu_state(frame) == "closed", timeout=1.2)


def _scroll_side_list(notches):
    """Wheel the side-menu list (SendInput; Roblox ignored mouse_event wheels)."""
    x, y = _reference_point(training_ui.SenzuMenus.LIST_ROW_X, 600)
    robust_move(x, y)
    wheel = (_INPUT * 1)(_make_mouse_input(0x0800))  # MOUSEEVENTF_WHEEL
    wheel[0].mi.mouseData = int(notches * 120) & 0xFFFFFFFF
    with _stop_input_gate:
        if _USER_STOP_LATCHED or _senzu_abort_requested():
            return False
        _user32.SendInput(1, wheel, _ctypes.sizeof(_INPUT))
    return True


def _refill_senzu_slot_v2(sct):
    """M -> Customize -> Items -> Senzu Bean -> slot. Each click waits for its page."""
    global SENZU_STATUS, SENZU_ACTIVE_TYPE
    menus = training_ui.SenzuMenus
    SENZU_STATUS = "refilling"
    _park_menu_cursor()
    if not _focus_game_for_senzu():
        print("[SENZU] Roblox focus not confirmed before opening the menu")
        SENZU_STATUS = "error"
        return False
    if menus.side_menu_state(_client_frame(sct)) != "open":
        _tap_key_unchecked("m")
        if not _wait_for_frame(sct, lambda frame: menus.side_menu_state(frame) == "open", timeout=1.5):
            print("[SENZU] Side menu not confirmed after M")
            SENZU_STATUS = "error"
            return False

    def page(name, timeout=1.2):
        return _wait_for_frame(sct, lambda frame: menus.heading(frame) == name, timeout=timeout)

    def click_reference(x, y):
        if _senzu_abort_requested():
            return False
        screen_x, screen_y = _reference_point(x, y)
        click_at(screen_x, screen_y)
        _park_menu_cursor()
        return True

    # M reopens the last page; the Customize icon always resets to its root.
    if menus.heading(_client_frame(sct)) != "items":
        if not click_reference(*menus.CUSTOMIZE_ICON) or not page("customize"):
            print("[SENZU] Customize page not confirmed")
            SENZU_STATUS = "error"
            _close_side_menu(sct)
            return False
        if not click_reference(*menus.CUSTOMIZE_ITEMS_ROW) or not page("items"):
            print("[SENZU] Items page not confirmed")
            SENZU_STATUS = "error"
            _close_side_menu(sct)
            return False
    # The list draws a beat after its heading; one early read called a full
    # stock empty live (2026-10-14).
    row_y = None
    deadline = time.monotonic() + 1.2
    while row_y is None and time.monotonic() < deadline and not _senzu_abort_requested():
        row_y = menus.senzu_row_y(_client_frame(sct))
        if row_y is None:
            time.sleep(0.08)
    # Only five rows show; with more items Senzu can sit below the fold.
    scrolls = 0
    while row_y is None and scrolls < 6 and not _senzu_abort_requested():
        scrolls += 1
        if not _scroll_side_list(-2):
            break
        time.sleep(0.25)
        row_y = menus.senzu_row_y(_client_frame(sct))
    if row_y is None:
        print("[SENZU] No Senzu Bean row in Items; stock is empty")
        SENZU_STATUS = "empty"
        _close_side_menu(sct)
        return False
    if not click_reference(menus.LIST_ROW_X, row_y) or not page("senzu_bean"):
        print("[SENZU] Senzu Bean slot picker not confirmed")
        SENZU_STATUS = "error"
        _close_side_menu(sct)
        return False
    if not click_reference(menus.SLOT_ROW_X, menus.slot_row_y(SENZU_SLOT)) or not page("items", 1.5):
        print(f"[SENZU] Slot {SENZU_SLOT} assignment not confirmed")
        SENZU_STATUS = "error"
        _close_side_menu(sct)
        return False
    if not _close_side_menu(sct):
        print("[SENZU] Side menu did not close after the refill")
        SENZU_STATUS = "error"
        return False
    TELEMETRY["senzu_refills"] += 1
    SENZU_STATUS = "ready"
    SENZU_ACTIVE_TYPE = "full"
    print(f"[SENZU] Slot {SENZU_SLOT} refilled with a Senzu Bean")
    return True


def _hotbar_slot_loaded(sct):
    """Open the H list if needed and report whether the Senzu slot is loaded."""
    menus = training_ui.SenzuMenus
    if not menus.hotbar_visible(_client_frame(sct)):
        if not _focus_game_for_senzu():
            return None
        _tap_key_unchecked("h")
        if not _wait_for_frame(sct, menus.hotbar_visible, timeout=0.8):
            return None
    return _wait_for_frame(sct, lambda frame: menus.hotbar_slot_has_senzu(frame, SENZU_SLOT),
                           timeout=0.7)


def _close_hotbar(sct):
    menus = training_ui.SenzuMenus
    if menus.hotbar_visible(_client_frame(sct)) and _focus_game_for_senzu():
        _tap_key_unchecked("h")
        _wait_for_frame(sct, lambda frame: not menus.hotbar_visible(frame), timeout=0.6)


def _resume_after_empty_v2(sct, assets):
    """The refill leaves us in the room; the shared empty-stock path expects
    Training Mode open (HP is already handled by the caller)."""
    if not _ensure_training_menu_for_senzu(sct, assets):
        if _senzu_abort_requested():
            return False
        _disable_senzu_for_run("No Senzu Bean stock remains")
        _stop_for_senzu_failure("Could not reopen Training Mode after Senzu ran out")
        return False
    return _resume_after_senzu_stock_empty(sct, assets, bean_was_used=False)


def _eat_senzu_v2(sct):
    """October 2026 UI: the HUD and H list hide behind Training Mode, so eat out
    in the room: leave training, H + slot digit, refill, wait for HP, resume."""
    global SENZU_STATUS
    assets = _senzu_assets()
    menus = training_ui.SenzuMenus
    SENZU_STATUS = "eating"
    if "full" not in _senzu_type_priority():
        # Only "Senzu Bean" has been seen in the new menus; never spend a
        # full bean the user excluded.
        _disable_senzu_for_run("Half-only Senzu isn't supported in the new game UI yet")
        return True
    if not _leave_training_to_gameplay(sct):
        if _senzu_abort_requested():
            return False
        _stop_for_senzu_failure("Could not leave Training Mode to eat")
        return False
    loaded = _hotbar_slot_loaded(sct)
    if loaded is None:
        if _senzu_abort_requested():
            return False
        _stop_for_senzu_failure("Items list (H) not confirmed")
        return False
    if not loaded:
        print(f"[SENZU] Slot {SENZU_SLOT} is empty; loading it first")
        _close_hotbar(sct)
        if not _refill_senzu_slot_v2(sct):
            if _senzu_abort_requested():
                return False
            if SENZU_STATUS == "empty":
                return _resume_after_empty_v2(sct, assets)
            _stop_for_senzu_failure("No Senzu could be loaded", SENZU_STATUS)
            return False
        loaded = _hotbar_slot_loaded(sct)
        if not loaded:
            if _senzu_abort_requested():
                return False
            _stop_for_senzu_failure(f"Senzu slot {SENZU_SLOT} not confirmed after the refill")
            return False
    print(f"[SENZU] Critical HP: eating the Senzu Bean in slot {SENZU_SLOT}")
    if not _focus_game_for_senzu():
        _stop_for_senzu_failure("Roblox focus not confirmed before the eat digit")
        return False
    _tap_key_unchecked(str(SENZU_SLOT))
    consumed = _wait_for_frame(
        sct, lambda frame: not menus.hotbar_slot_has_senzu(frame, SENZU_SLOT), timeout=0.8)
    if not consumed:
        if _senzu_abort_requested():
            return False
        return _request_stuck_senzu_recovery()
    TELEMETRY["senzu_eaten"] += 1
    _close_hotbar(sct)
    # Refill while the bean's heal plays (~3 s live), so the next eat is instant.
    refilled = _refill_senzu_slot_v2(sct)
    if not refilled and _senzu_abort_requested():
        return False
    if not _wait_for_green_hp(sct, SENZU_RECOVERY_TIMEOUT_SEC):
        if _senzu_abort_requested():
            return False
        # Resuming here would put a low-HP character back under gravity.
        _stop_for_senzu_failure("HP was not confirmed healed after the Senzu Bean",
                                retryable=True)
        return False
    if not refilled and SENZU_STATUS == "empty":
        return _resume_after_empty_v2(sct, assets)
    if not refilled:
        _stop_for_senzu_failure("Could not refill the Senzu slot after eating", SENZU_STATUS)
        return False
    if not _ensure_training_menu_for_senzu(sct, assets) or not _resume_training_after_senzu(sct, assets):
        if _senzu_abort_requested():
            return False
        _stop_for_senzu_failure("Could not resume the active training category")
        return False
    SENZU_STATUS = "ready"
    return True


def eat_senzu(sct, *, exit_active_minigame=True):
    """Return to Training, eat/refill, wait for green HP, then resume."""
    global SENZU_STATUS
    if _new_game_ui():
        with _input_lock, _menu_cursor_guard():
            return _eat_senzu_v2(sct)
    assets = _senzu_assets()
    with _input_lock, _menu_cursor_guard():
        SENZU_STATUS = "eating"
        # The Training menu stays on screen while Agility/Ki minigames run, so
        # a visible menu does NOT mean the minigame stopped. An active minigame
        # swallows the digit consume keys. Mirror the manual flow exactly:
        # Tab out of the minigame first, then H, 1.
        if exit_active_minigame and CURRENT_TRAINING_STATE in BUTTONS:
            if not _focus_game_for_senzu():
                if _senzu_abort_requested():
                    return False
                _stop_for_senzu_failure("Roblox focus not confirmed before using Senzu")
                return False
            print("[SENZU] Exiting the active minigame with Tab before H")
            _tap_key_unchecked("tab")
            _wait_for_training_menu_for_senzu(sct, assets["training"], timeout=0.40)
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
                timeout=0.46,
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
                if not _focus_game_for_senzu():
                    if _senzu_abort_requested():
                        return False
                    _stop_for_senzu_failure("Roblox focus not confirmed before using Senzu")
                    return False
                _tap_key_unchecked("h")
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
                expected_type = SENZU_ACTIVE_TYPE or _senzu_type_priority()[0]

                def refilled_slot_state():
                    return _stable_senzu_slot_state(
                        sct,
                        SENZU_SLOT,
                        assets["slot"],
                        hotbar=True,
                        bean_type=expected_type,
                        timeout=0.46,
                    )

                # H toggles, and the Items list is sometimes already up when
                # Inventory closes; a blind H would hide it again. Look first.
                assigned, assigned_score = refilled_slot_state()
                if assigned is not True and not _senzu_abort_requested():
                    _tap_key_unchecked("h")
                    assigned, assigned_score = refilled_slot_state()
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
                if not _focus_game_for_senzu():
                    if _senzu_abort_requested():
                        return False
                    _stop_for_senzu_failure("Roblox focus not confirmed before using Senzu")
                    return False
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
            if _senzu_abort_requested():
                return False
            if consume_accepted is None:
                _stop_for_senzu_failure("Roblox focus not confirmed before the eat digit")
                return False
            if consume_accepted:
                print(
                    f"[SENZU] {_senzu_type_label(expected_type)} consume "
                    "accepted by the H-menu slot"
                )
            else:
                loaded, _ = _stable_senzu_slot_state(
                    sct, SENZU_SLOT, assets["slot"], hotbar=True, bean_type=expected_type
                )
                if loaded is True:
                    return _request_stuck_senzu_recovery()
                print(
                    "[SENZU] Eat digit acceptance was unclear "
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
            if not _focus_game_for_senzu():
                if _senzu_abort_requested():
                    return False
                _stop_for_senzu_failure("Roblox focus not confirmed before using Senzu")
                return False
            _tap_key_unchecked("h")
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


class _MajinPaused(Exception):
    """Unwind the held G chord before waiting for Resume."""


def _majin_check():
    if (_USER_STOP_LATCHED or UI_STOP_REQUESTED
            or (_background_monitor_stop is not None and _background_monitor_stop.is_set())):
        raise QuitException()
    if PAUSE_TOGGLE_REQUESTED or CONTROLLER_PAUSED:
        raise _MajinPaused()


def _majin_wait(duration):
    deadline = time.monotonic() + duration
    while True:
        _majin_check()
        if not _game_has_focus():
            raise RuntimeError("Roblox lost focus during regeneration")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        time.sleep(min(0.05, remaining))


def _majin_wait_for(read_state, expected, timeout=1.8):
    deadline = time.monotonic() + timeout
    stable = 0
    while time.monotonic() < deadline:
        _majin_check()
        if not _game_has_focus():
            raise RuntimeError("Roblox lost focus during regeneration")
        stable = stable + 1 if read_state() == expected else 0
        _majin_check()
        if stable >= 2:
            return True
        _majin_wait(min(0.05, max(0.0, deadline - time.monotonic())))
    return False


def _majin_tap(key):
    _majin_check()
    if not _ensure_game_focus_for_input():
        raise RuntimeError("Roblox focus not confirmed for regeneration")
    with _stop_input_gate:
        _majin_check()
        if not _tap_key_unchecked(key, wait_for_cover=False):
            raise QuitException()


def _majin_enter_prompt(sct):
    if _new_game_ui():
        # "Press TAB to open training." (out of Training Mode); the in-training
        # "Press TAB to stop training." scores well below the threshold.
        return training_ui.open_training_prompt(
            _grab_reference_box(sct, training_ui.OPEN_TRAINING_BOX))
    return majin_vision.enter_prompt_visible(
        _grab_reference_box(sct, majin_vision.ENTER_PROMPT_BOX)
    )


def _majin_panel_vision():
    return training_ui.MajinTransform if _new_game_ui() else majin_vision


@contextmanager
def _hold_majin_panel():
    with _input_lock:
        _majin_check()
        if not _ensure_game_focus_for_input():
            raise RuntimeError("Roblox focus not confirmed before G")
        g_held = False
        try:
            with _stop_input_gate:
                _majin_check()
                g_held = True
                pydirectinput.keyDown(MAJIN_PANEL_KEY)
            yield
        finally:
            if g_held:
                with _stop_input_gate:
                    pydirectinput.keyUp(MAJIN_PANEL_KEY)


def _attempt_majin_regen(sct):
    global MAJIN_REGEN_READY_AT, MAJIN_REGEN_CHANNEL_UNTIL, MAJIN_REGEN_UNAVAILABLE_FOR_RUN
    _majin_check()
    # From an active minigame the first Tab only opens the Training Mode menu;
    # a second Tab leaves Training Mode. Tab from the menu goes straight out.
    training_template = _senzu_assets()["training"]
    for _tab in range(2):
        if _majin_enter_prompt(sct):
            break
        print("[MAJIN] Leaving Training Mode with Tab")
        _majin_tap("tab")
        _majin_wait_for(
            lambda: _majin_enter_prompt(sct)
            or _training_menu_visible_for_senzu(sct, training_template)[0],
            True)
    if not _majin_wait_for(lambda: _majin_enter_prompt(sct), True):
        raise RuntimeError("Enter Training Mode prompt not confirmed after Tab")

    cast_started = False
    recovered = False
    slot = MAJIN_REGEN_SLOT
    try:
        with _hold_majin_panel():
            vision = _majin_panel_vision()
            read_panel = lambda: _grab_reference_box(sct, vision.PANEL_BOX)
            panel_seen = _majin_wait_for(lambda: vision.panel_visible(read_panel()), True)
            if MAJIN_REGEN_AUTO:
                if not panel_seen and _new_game_ui():
                    # Not seeing the panel says nothing about the character;
                    # don't give up on Majin for the rest of the run over it.
                    print("[MAJIN] Transform panel not seen while holding G; trying again next time")
                    return "unseen"
                found = vision.find_regen_slot(read_panel()) if panel_seen else None
                if found is None:
                    MAJIN_REGEN_UNAVAILABLE_FOR_RUN = True
                    print("[MAJIN] Auto: Super Regeneration is not in the G panel; "
                          "using the normal heal method for the rest of this run")
                    return "unavailable"
                slot = found
            elif not panel_seen:
                raise RuntimeError("Transformations panel not confirmed while holding G")
            # The panel fades in; its row text is unreadable for a frame or two.
            _majin_wait_for(lambda: vision.slot_state(read_panel(), slot) in ("ready", "cooldown"),
                            True, timeout=0.8)
            state = vision.slot_state(read_panel(), slot)
            if state not in ("ready", "cooldown") or not _majin_wait_for(
                    lambda: vision.slot_state(read_panel(), slot), state, timeout=0.6):
                raise RuntimeError(f"Transformation slot {slot} is empty or unreadable")
            if state == "cooldown":
                MAJIN_REGEN_READY_AT = time.monotonic() + 60.0
                print(f"[MAJIN] Slot {slot} is grey; checking again in 60s")
                return "cooldown"
            _majin_check()
            # Until HP proves recovery, an interrupted cast needs the longer timer.
            cast_started = True
            MAJIN_REGEN_READY_AT = time.monotonic() + 180.0
            hp_before = _new_ui_hp_fraction(sct) if _new_game_ui() else None
            print(f"[MAJIN] Using Super Regeneration in slot {slot}")
            _majin_tap(MAJIN_REGEN_KEYS[slot - 1])
            MAJIN_REGEN_CHANNEL_UNTIL = time.monotonic() + 5.3
        if _new_game_ui():
            # Ki checks heal at 50%, already "above red": wait out the channel
            # (no menu input during it), then require HP to have actually risen.
            _majin_wait_for(lambda: False, True, timeout=5.3)
            hp_after = _new_ui_hp_fraction(sct)
            recovered = hp_after is not None and (
                hp_after >= training_ui.HP_HEALED_FRACTION
                or (hp_before is not None and hp_after >= hp_before + 0.05))
        else:
            recovered = _majin_wait_for(
                lambda: _hp_above_red(sct),
                True, timeout=5.3,
            )
        if recovered:
            MAJIN_REGEN_CHANNEL_UNTIL = 0.0
            MAJIN_REGEN_READY_AT = time.monotonic() + 60.0
            TELEMETRY["majin_regens"] += 1
            print("[MAJIN] HP above critical confirmed; cooldown 60s")
            return "success"
        return "failed"
    finally:
        if cast_started and not recovered:
            MAJIN_REGEN_READY_AT = time.monotonic() + 180.0
            TELEMETRY["majin_regen_failures"] += 1
            print("[MAJIN] Regeneration failed or was interrupted; cooldown 180s")


def _consume_pause_toggle():
    global PAUSE_TOGGLE_REQUESTED, CONTROLLER_PAUSED
    with _stop_input_gate:
        if not PAUSE_TOGGLE_REQUESTED:
            return False
        PAUSE_TOGGLE_REQUESTED = False
        CONTROLLER_PAUSED = not CONTROLLER_PAUSED
        PROGRESSION_STALL_RESET_REQUESTED.set()
        _set_charge_hold(False)
        return True


def _majin_wait_for_resume():
    global PAUSE_TOGGLE_REQUESTED, CONTROLLER_PAUSED
    # The main loop cannot consume Pause while this thread owns the controller.
    while True:
        if (_USER_STOP_LATCHED or UI_STOP_REQUESTED
                or (_background_monitor_stop is not None and _background_monitor_stop.is_set())):
            raise QuitException()
        if _consume_pause_toggle():
            print("[MAJIN] Paused" if CONTROLLER_PAUSED else "[MAJIN] Resumed")
        if not CONTROLLER_PAUSED:
            return
        time.sleep(0.05)


def _majin_restore_training_menu(sct, assets):
    _majin_check()
    if not _ensure_training_menu_for_senzu(sct, assets):
        _majin_check()
        raise RuntimeError("Could not restore Training Mode after regeneration")
    _majin_check()


def use_majin_regen(sct):
    """Use the shared healing ownership; recover menus before reselecting a stat."""
    global MAJIN_CONTROLLER_ACTIVE, MAJIN_REGEN_CHANNEL_UNTIL, HEALING_ACTION
    with _input_lock, _menu_cursor_guard():
        assets = _senzu_assets()
        MAJIN_CONTROLLER_ACTIVE = True
        HEALING_ACTION = "majin_regen"
        restore_needed = False
        outcome = None
        try:
            while True:
                try:
                    _majin_check()
                    # Pause may unwind verification while the game keeps channeling.
                    # A quick Resume must not send Tab or a reserve bean into it.
                    if time.monotonic() < MAJIN_REGEN_CHANNEL_UNTIL:
                        _majin_wait_for(
                            (lambda: False) if _new_game_ui() else (lambda: _hp_above_red(sct)),
                            True, timeout=MAJIN_REGEN_CHANNEL_UNTIL - time.monotonic(),
                        )
                        MAJIN_REGEN_CHANNEL_UNTIL = 0.0
                    if outcome is None:
                        if time.monotonic() < MAJIN_REGEN_READY_AT:
                            outcome = "cooldown"
                            HEALING_ACTION = "majin_cooldown"
                            print("[MAJIN] Internal cooldown active")
                        else:
                            restore_needed = True
                            outcome = _attempt_majin_regen(sct)
                            if outcome == "cooldown":
                                HEALING_ACTION = "majin_cooldown"

                    if (outcome != "success" and MAJIN_REGEN_SENZU_RESERVE
                            and SENZU_ENABLED and not SENZU_DISABLED_FOR_RUN
                            and _hp_bar_is_red(sct)):
                        exit_active = not restore_needed
                        if restore_needed:
                            _majin_restore_training_menu(sct, assets)
                        restore_needed = True
                        print("[MAJIN] HP still critical; using reserve Auto-Senzu")
                        HEALING_ACTION = "reserve_senzu"
                        result = eat_senzu(sct, exit_active_minigame=exit_active)
                        _majin_check()
                        return result

                    if restore_needed:
                        _majin_restore_training_menu(sct, assets)
                        if not _resume_training_after_senzu(sct, assets):
                            _majin_check()
                            raise RuntimeError("Could not reselect the current training stat")
                        _majin_check()
                        print("[MAJIN] Resumed the current training stat")
                    return True
                except _MajinPaused:
                    _majin_wait_for_resume()
        finally:
            MAJIN_CONTROLLER_ACTIVE = False
            HEALING_ACTION = None


# Ki Control/Damage hide the HUD for the whole minigame; live, HP fell from
# 100% to 64% in 60 s of Ki Damage at 10G and the character died twice.
KI_HP_CHECK_SEC = 30.0
KI_HP_HEAL_FRACTION = 0.50
_KI_HP_READINGS = []  # (time, fraction) from the last checks of this stat
# HP drain learned while the HUD is up (Health, Agility, Physical Damage), so the
# first check of a Ki stat isn't a blind 30 s. Drain depends on gravity and level.
HP_DRAIN_SAMPLE_SEC = 5.0
_HP_LAST_SEEN = None      # (time, fraction)
_HP_DRAIN_RATE = None     # fraction per second, smoothed
# Each death during a Ki stat brings later checks forward, for this app session.
_KI_CHECK_SCALE = 1.0
KI_CHECK_SCALE_STEP = 0.75
KI_CHECK_SCALE_MIN = 0.4


def _note_hp_reading(fraction, now=None):
    """Learn the drain from successive HP readings; a rise (healing) restarts it."""
    global _HP_LAST_SEEN, _HP_DRAIN_RATE
    now = time.monotonic() if now is None else now
    if _HP_LAST_SEEN is not None:
        t0, f0 = _HP_LAST_SEEN
        if f0 - fraction > 0.005 and now - t0 >= 1.0:
            rate = (f0 - fraction) / (now - t0)
            _HP_DRAIN_RATE = rate if _HP_DRAIN_RATE is None else _HP_DRAIN_RATE * 0.6 + rate * 0.4
    _HP_LAST_SEEN = (now, fraction)


def _note_ki_death():
    global _KI_CHECK_SCALE
    if KI_HP_CHECK_MODE != "adaptive":
        return
    _KI_CHECK_SCALE = max(KI_CHECK_SCALE_MIN, _KI_CHECK_SCALE * KI_CHECK_SCALE_STEP)
    print(f"[HP] Died during a Ki stat; HP checks now come {1 - _KI_CHECK_SCALE:.0%} sooner")


def _ki_hp_interval(now=None):
    """Seconds until the next Ki HP check: 30 at most, sooner when HP is falling
    fast enough (higher gravity) to reach critical, and sooner after deaths."""
    now = time.monotonic() if now is None else now
    critical = training_ui.HP_CRITICAL_FRACTION
    interval = KI_HP_CHECK_SEC
    if len(_KI_HP_READINGS) >= 2:
        (t0, f0), (t1, f1) = _KI_HP_READINGS[-2:]
        rate = (f0 - f1) / max(1.0, t1 - t0)
        if rate > 0:
            interval = max(8.0, min(KI_HP_CHECK_SEC, (f1 - critical) / rate * 0.6))
    elif KI_HP_CHECK_MODE == "adaptive" and _HP_DRAIN_RATE and _HP_LAST_SEEN is not None:
        # Project the last HP seen forward with the drain learned outside Ki.
        seen_at, fraction = _HP_LAST_SEEN
        estimate = fraction - _HP_DRAIN_RATE * max(0.0, now - seen_at)
        interval = max(8.0, min(KI_HP_CHECK_SEC, (estimate - critical) / _HP_DRAIN_RATE * 0.6))
    if KI_HP_CHECK_MODE != "adaptive":
        return interval
    return max(5.0, interval * _KI_CHECK_SCALE)


HTC_LEVEL_TOTAL = 50  # GC stats run to 20 (training_ui.POINTS_TOTALS)


def _in_htc(stat):
    """True when the stat's Training Progression total says HTC, where HP barely
    drains, so stepping out of Ki training to read it only costs time. Unread
    totals or any sign of GC keep the checks on."""
    target = PROGRESSION_TARGETS.get(stat)
    return bool(target and target[1] == HTC_LEVEL_TOTAL and not GC_RUN_CONFIRMED)


def _ki_hp_check(sct):
    """Step out of a Ki minigame, read HP, heal if low, and resume training."""
    assets = _senzu_assets()
    with _input_lock, _menu_cursor_guard():
        if not _leave_training_to_gameplay(sct):
            if _senzu_abort_requested():
                return False  # Stop arrived mid-check
            raise RuntimeError("Could not leave Ki training to read HP")
        fraction = None
        deadline = time.monotonic() + 0.8
        while fraction is None and time.monotonic() < deadline and not _senzu_abort_requested():
            fraction = _new_ui_hp_fraction(sct)
            if fraction is None:
                time.sleep(0.05)
        print("[HP] Ki check: " + ("HUD not visible" if fraction is None else f"HP {fraction:.0%}"))
        healed = fraction is not None and fraction <= KI_HP_HEAL_FRACTION
        if fraction is not None:
            _KI_HP_READINGS[:] = [] if healed else (_KI_HP_READINGS + [(time.monotonic(), fraction)])[-2:]
            _note_hp_reading(fraction)
        if healed:
            if HEALING_ACTION == "majin_regen":
                use_majin_regen(sct)
            else:
                eat_senzu(sct)
        else:
            charge_ki_bar(sct, "Ki check", only_if_drained=True, owner="healing")
        if _senzu_abort_requested():
            return healed
        # Healing resumes the stat itself; a skipped heal (or a Majin cooldown)
        # leaves us out in the room.
        if training_ui.open_training_prompt(_grab_reference_box(sct, training_ui.OPEN_TRAINING_BOX)):
            if not (_ensure_training_menu_for_senzu(sct, assets)
                    and _resume_training_after_senzu(sct, assets)):
                raise RuntimeError("Could not resume Ki training after the HP check")
        return healed


def _mid_stat_recharge(sct):
    """Step out of the current minigame, charge Ki back up, and resume the stat.

    Returns whether Shift was actually held."""
    assets = _senzu_assets()
    with _input_lock, _menu_cursor_guard():
        if not _leave_training_to_gameplay(sct):
            if _senzu_abort_requested():
                return
            raise RuntimeError("Could not leave training to charge Ki")
        charged = charge_ki_bar(sct, "Mid-stat", owner="healing")
        if _senzu_abort_requested():
            return charged
        if not (_ensure_training_menu_for_senzu(sct, assets)
                and _resume_training_after_senzu(sct, assets)):
            raise RuntimeError("Could not resume training after charging Ki")
        return charged


def _claim_healing(token, for_charge=False):
    global HEALING_ACTION
    with _stop_input_gate:
        if (token != _progression_token() or token[1] is None
                or not (for_charge or _healing_monitor_enabled())
                or _USER_STOP_LATCHED or UI_STOP_REQUESTED
                or SENZU_CONTROLLER_ACTIVE.is_set() or SENZU_CONTROLLER_RESUME_REQUIRED.is_set()):
            return False
        _set_charge_hold(False)
        _set_training_state(CURRENT_TRAINING_STATE)
        HEALING_ACTION = _effective_heal_method()
        SENZU_CONTROLLER_ACTIVE.set()
        SENZU_CONTROLLER_RESUME_REQUIRED.set()
        return True


def _effective_heal_method():
    if MAJIN_REGEN_AUTO and not MAJIN_REGEN_UNAVAILABLE_FOR_RUN:
        return "majin_regen"
    return HEAL_METHOD


def _healing_monitor_enabled():
    if _effective_heal_method() == "majin_regen":
        return not (CONTROLLER_PAUSED or PAUSE_TOGGLE_REQUESTED)
    return SENZU_ENABLED and not SENZU_DISABLED_FOR_RUN


def _stop_for_majin_failure(message):
    global UI_STOP_REQUESTED
    _record_run_outcome("error", f"Majin regeneration: {message}")
    UI_STOP_REQUESTED = True
    print(f"[MAJIN] {message}; stopping macro safely")


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
_display_confirm_generation = 0


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
    global _display_confirm_timer, _display_confirm_deadline, _display_confirm_generation
    with _display_confirm_lock:
        timer, _display_confirm_timer = _display_confirm_timer, None
        _display_confirm_deadline = 0.0
        _display_confirm_generation += 1
    if timer is not None:
        timer.cancel()
        return True
    return False


def _arm_display_confirm(timeout=None):
    global _display_confirm_timer, _display_confirm_deadline, _display_confirm_generation
    _cancel_display_confirm()
    seconds = float(DISPLAY_CONFIRM_TIMEOUT_SEC if timeout is None else timeout)

    def _expire():
        global _display_confirm_timer, _display_confirm_deadline, _display_confirm_generation
        with _display_confirm_lock:
            if generation != _display_confirm_generation or _display_confirm_timer is not timer:
                return
            _display_confirm_timer = None
            _display_confirm_deadline = 0.0
            _display_confirm_generation += 1
            print("[display] No confirmation — reverting resolution.")
            display_revert_resolution()
            update_game_window()

    timer = threading.Timer(seconds, _expire)
    timer.daemon = True
    with _display_confirm_lock:
        _display_confirm_generation += 1
        generation = _display_confirm_generation
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

def _validated_button_position(pos):
    if not isinstance(pos, (list, tuple)) or len(pos) != 2:
        raise ValueError("button position needs two coordinates")
    x, y = (int(value) for value in pos)
    if not (0 <= x < GAME_REFERENCE_WIDTH and 0 <= y < GAME_REFERENCE_HEIGHT):
        raise ValueError("button position is outside the Roblox client")
    return [x, y]


def _validated_region_box(box):
    if not isinstance(box, dict):
        raise ValueError("region needs a box")
    rect = {key: int(box[key]) for key in ("top", "left", "width", "height")}
    if (rect["top"] < 0 or rect["left"] < 0
            or rect["width"] <= 0 or rect["height"] <= 0
            or rect["left"] + rect["width"] > GAME_REFERENCE_WIDTH
            or rect["top"] + rect["height"] > GAME_REFERENCE_HEIGHT):
        raise ValueError("region is outside the Roblox client or has no area")
    return rect


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
        saved_layout = data.get("_ui_layout", LEGACY_UI_LAYOUT)
        if saved_layout != GAME_UI_LAYOUT:
            # Old-menu positions would click the wrong stat in the new menu.
            print(f"[calibrate] Ignored button calibration from the {saved_layout} game menu")
            return
        legacy_groups = {
            "health":   ["Health"],
            "physical": ["Agility", "Physical Damage"],
            "ki":       ["Ki Control", "Ki Damage"],
        }
        cleaned = {}
        for k, v in data.items():
            if k not in BUTTONS and k not in legacy_groups:
                continue
            try:
                pos = _validated_button_position(v)
            except (ValueError, TypeError):
                print(f"[calibrate] Ignored invalid button override: {k}")
                continue
            if k in BUTTONS:
                cleaned[k] = pos
            else:
                for stat_name in legacy_groups[k]:
                    cleaned[stat_name] = pos
        USER_BUTTON_OVERRIDES = cleaned
        _apply_button_overrides()
        print(f"[calibrate] Loaded button overrides: {USER_BUTTON_OVERRIDES}")
    except Exception as e:
        print(f"[calibrate] load_button_overrides failed: {e}")


def save_button_overrides():
    _save_calibration_overrides(_button_overrides_path(), USER_BUTTON_OVERRIDES)


def _save_calibration_overrides(path, overrides):
    os.makedirs(JSON_DIR, exist_ok=True)
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=JSON_DIR,
                                         prefix="calibration-", suffix=".tmp", delete=False) as f:
            temp_path = f.name
            json.dump({"_coordinate_space": "reference-1920x1080",
                       "_ui_layout": GAME_UI_LAYOUT, **overrides}, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp_path, path)
    finally:
        if temp_path is not None and os.path.exists(temp_path):
            os.remove(temp_path)


def _calibration_is_active():
    return bool(
        BUTTON_CALIBRATION_WAITING or REGION_CALIBRATION_WAITING
        or (BUTTON_CALIBRATION_THREAD is not None and BUTTON_CALIBRATION_THREAD.is_alive())
        or (REGION_CALIBRATION_THREAD is not None and REGION_CALIBRATION_THREAD.is_alive())
    )


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
        f"+{client['left']}+{client['top']}"
    )
    return geometry, client


def _capture_click_via_overlay(prompt_text):
    """Cover Roblox's client with a translucent overlay and capture one click. Returns
    canonical 1920x1080 client coordinates, or None if cancelled (Esc).

    Must be called from the thread that creates the Tk root (Tk isn't free-threaded).
    """
    generation = BUTTON_CALIBRATION_GENERATION
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

        def check_cancelled():
            if (BUTTON_CALIBRATION_GENERATION != generation
                    or BUTTON_CALIBRATION_WAITING is None):
                root.destroy()
            else:
                root.after(100, check_cancelled)

        root.bind("<Button-1>", on_click)
        root.bind("<Escape>", on_esc)
        root.focus_force()
        root.after(100, check_cancelled)
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
    global BUTTON_CALIBRATION_WAITING, BUTTON_CALIBRATION_ERROR, BUTTONS
    try:
        pos = _capture_click_via_overlay(f"Click the {stat_name} trait button in Roblox")
        if (pos
                and BUTTON_CALIBRATION_WAITING == stat_name
                and BUTTON_CALIBRATION_GENERATION == generation):
            previous = USER_BUTTON_OVERRIDES.get(stat_name)
            previous_buttons = BUTTONS
            try:
                USER_BUTTON_OVERRIDES[stat_name] = _validated_button_position(pos)
            except (ValueError, TypeError) as e:
                BUTTON_CALIBRATION_ERROR = f"{stat_name}: {e}"
                print(f"[calibrate] {BUTTON_CALIBRATION_ERROR}")
                return
            _apply_button_overrides()
            try:
                save_button_overrides()
            except Exception as e:
                if previous is None:
                    USER_BUTTON_OVERRIDES.pop(stat_name, None)
                else:
                    USER_BUTTON_OVERRIDES[stat_name] = previous
                BUTTONS = previous_buttons
                BUTTON_CALIBRATION_ERROR = f"{stat_name}: save failed ({e})"
                print(f"[calibrate] {BUTTON_CALIBRATION_ERROR}")
                return
            print(f"[calibrate] {stat_name} -> {pos}")
    finally:
        if BUTTON_CALIBRATION_GENERATION == generation:
            BUTTON_CALIBRATION_WAITING = None


def _capture_region_via_overlay(prompt_text):
    """Overlay Roblox's client; user click-drags a rectangle. Returns a box in
    canonical 1920x1080 client coordinates, or None if cancelled (Esc) or if the
    rect is too small (< 4x4 live pixels is treated as an accidental click).
    """
    generation = REGION_CALIBRATION_GENERATION
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

        def check_cancelled():
            if (REGION_CALIBRATION_GENERATION != generation
                    or REGION_CALIBRATION_WAITING is None):
                root.destroy()
            else:
                root.after(100, check_cancelled)

        # Bind on root so clicks on the prompt label (a child Frame sitting above the
        # canvas) still fire — Tk's child widgets would otherwise swallow the event.
        root.bind("<ButtonPress-1>", on_down)
        root.bind("<B1-Motion>", on_drag)
        root.bind("<ButtonRelease-1>", on_up)
        root.bind("<Escape>", on_esc)
        root.focus_force()
        root.after(100, check_cancelled)
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
                if k in REGION_NAMES:
                    try:
                        cleaned[k] = _validated_region_box(v)
                    except (ValueError, TypeError, KeyError):
                        print(f"[calibrate] Ignored invalid region override: {k}")
            USER_REGION_OVERRIDES = cleaned
            _apply_region_overrides()
            print(f"[calibrate] Loaded region overrides: {USER_REGION_OVERRIDES}")
    except Exception as e:
        print(f"[calibrate] load_region_overrides failed: {e}")


def save_region_overrides():
    _save_calibration_overrides(_region_overrides_path(), USER_REGION_OVERRIDES)


def _run_region_calibration(region_key, generation):
    global REGION_CALIBRATION_WAITING, REGION_CALIBRATION_ERROR, HEALTH_BOX, AGILITY_BOX
    try:
        label = REGION_NAMES.get(region_key, region_key)
        rect = _capture_region_via_overlay(f"Drag a rectangle over the {label} area")
        if (rect
                and REGION_CALIBRATION_WAITING == region_key
                and REGION_CALIBRATION_GENERATION == generation):
            previous = USER_REGION_OVERRIDES.get(region_key)
            previous_health_box, previous_agility_box = HEALTH_BOX, AGILITY_BOX
            try:
                USER_REGION_OVERRIDES[region_key] = _validated_region_box(rect)
            except (ValueError, TypeError, KeyError) as e:
                REGION_CALIBRATION_ERROR = f"{region_key}: {e}"
                print(f"[calibrate] {REGION_CALIBRATION_ERROR}")
                return
            _apply_region_overrides()
            try:
                save_region_overrides()
            except Exception as e:
                if previous is None:
                    USER_REGION_OVERRIDES.pop(region_key, None)
                else:
                    USER_REGION_OVERRIDES[region_key] = previous
                HEALTH_BOX, AGILITY_BOX = previous_health_box, previous_agility_box
                REGION_CALIBRATION_ERROR = f"{region_key}: save failed ({e})"
                print(f"[calibrate] {REGION_CALIBRATION_ERROR}")
                return
            print(f"[calibrate] {region_key} -> {rect}")
    finally:
        if REGION_CALIBRATION_GENERATION == generation:
            REGION_CALIBRATION_WAITING = None


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


def _progression_token():
    with _stop_input_gate:
        return PROGRESSION_GENERATION, CURRENT_TRAINING_STATE, PROGRESSION_STATE_STARTED_AT


def _set_training_state(state):
    global CURRENT_TRAINING_STATE, PROGRESSION_STATE_STARTED_AT, PROGRESSION_GENERATION
    global PROGRESSION_COMPLETION, PROGRESSION_COMPLETE, PROGRESSION_TRACKED_STATE
    global PROGRESSION_CAPPED_STATE
    with _stop_input_gate:
        _reset_ki_v9()
        PROGRESSION_GENERATION += 1
        CURRENT_TRAINING_STATE = state
        PROGRESSION_STATE_STARTED_AT = time.monotonic()
        PROGRESSION_COMPLETION = None
        PROGRESSION_COMPLETE_REQUESTED.clear()
        PROGRESSION_COMPLETE = PROGRESSION_TRACKED_STATE = PROGRESSION_CAPPED_STATE = None


def _publish_progression_completion(token, reason):
    global PROGRESSION_COMPLETION, PROGRESSION_COMPLETE, PROGRESSION_CAPPED_STATE
    with _stop_input_gate:
        if (token != _progression_token() or token[1] is None
                or CONTROLLER_PAUSED or PAUSE_TOGGLE_REQUESTED
                or SENZU_CONTROLLER_ACTIVE.is_set() or SENZU_CONTROLLER_RESUME_REQUIRED.is_set()
                or PROGRESSION_STALL_RESET_REQUESTED.is_set()
                or GC_RECOVERY_VERIFY_PENDING or UI_STOP_REQUESTED or _USER_STOP_LATCHED):
            return False
        PROGRESSION_COMPLETION = (token, reason)
        PROGRESSION_COMPLETE = reason == "displayed_target"
        PROGRESSION_CAPPED_STATE = token[1] if reason != "displayed_target" else None
        PROGRESSION_COMPLETE_REQUESTED.set()
        return True


def _pending_progression_completion(*, consume=False):
    global PROGRESSION_COMPLETION
    with _stop_input_gate:
        if not PROGRESSION_COMPLETE_REQUESTED.is_set():
            return None
        pending = PROGRESSION_COMPLETION
        if pending is None or pending[0] != _progression_token() or pending[0][1] is None:
            PROGRESSION_COMPLETE_REQUESTED.clear()
            PROGRESSION_COMPLETION = None
            return None
        if consume:
            PROGRESSION_COMPLETE_REQUESTED.clear()
            PROGRESSION_COMPLETION = None
        return pending[1]


def check_exit():
    if _CHARGE_HELD:
        _set_charge_hold(True)
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
    if (MANUAL_NEXT_REQUESTED or PAUSE_TOGGLE_REQUESTED) and CURRENT_TRAINING_STATE is not None:
        # Startup and category switches have no skip consumer yet. Keep requests
        # queued until their menu handshake finishes, then unwind minigame waits.
        # The Senzu thread must finish its menu transaction before we act on them.
        if threading.current_thread() is not _background_monitor_thread:
            raise SkipMinigameException()
    if _pending_progression_completion() is not None:
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
    end = time.monotonic() + max(0.0, float(duration))
    # Short sleeps need fine granularity; long sleeps can use coarser steps.
    step = 0.005 if duration <= 0.1 else 0.05
    while True:
        check_exit()
        if SHOW_DEBUG_HUD:
            cv2.waitKey(1)
        remaining = end - time.monotonic()
        if remaining <= 0:
            break
        time.sleep(min(step, remaining))


def detect_yellow_progress(sct, state, geometry=None):
    """
    Returns: (yellow_pixels, raw_img_BGRA, mask_GRAY)
    """
    if _new_game_ui():
        # No yellow meter any more; the panel's "Progression X/Y" label is the
        # sign that a minigame is on screen.
        raw = _grab_reference_box(sct, training_ui.progression_label_box(state), geometry=geometry)
        present = training_ui.progression_spans(raw) is not None
        if not present and state in ("Ki Control", "Ki Damage"):
            present = training_ui.ki_label_visible(raw)
        if not present and state == "Health":
            # Scenery can hide the floating label; a readable Health bar still
            # proves the minigame is running, so it must not time out.
            present = training_ui.health_bar(
                _grab_reference_box(sct, training_ui.HEALTH_BAR_BOX, geometry=geometry)) is not None
        return int(present), raw, np.full(raw.shape[:2], 255 if present else 0, np.uint8)
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
GC_RECOVERY_VERIFY_PENDING = False
GC_RECOVERY_VERIFY_TIMEOUT_SEC = 40.0
GC_RECOVERY_VERIFY_TIMEOUT_NEW_UI_SEC = 90.0  # one level takes ~30 s at 20G
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


def _training_order_result(skipped_stats, capped_stats=()):
    skipped = [str(stat) for stat in skipped_stats if str(stat)]
    capped = [str(stat) for stat in capped_stats if str(stat)]
    confirmed = ([stat for stat in capped if capped_stats[stat] == "points_left"]
                 if isinstance(capped_stats, dict) else [])
    inferred = [stat for stat in capped if stat not in confirmed]
    reasons = []
    if skipped:
        reasons.append(f"skipped stats: {', '.join(skipped)}")
    if confirmed:
        reasons.append(f"capped stats (confirmed Points Left limit): {', '.join(confirmed)}")
    if inferred:
        reasons.append(f"capped stats (likely out of Points Left): {', '.join(inferred)}")
    if reasons:
        return "incomplete", f"Training order ended with {'; '.join(reasons)}"
    return "completed", "Training order completed"


def _progression_label_glyphs(frame):
    """Find the two numbers beside Progression without guessing digit identities."""
    bgr = frame[:, :, :3]
    channel_min = bgr.min(axis=2)
    channel_spread = bgr.max(axis=2) - channel_min
    # The label text is pure white. The GC's lavender wall measures ~(225,200,201)
    # and passed the old >190 cut, so with the camera facing it the right-hand
    # digits merged into the wall and 20/20 read as incomplete forever.
    mask = ((channel_min > 225) & (channel_spread < 45)).astype(np.uint8) * 255
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
            # "0/5" (low-level targets) measures ~50 px; "20/20" ~110 px.
            if not (35 <= inner_width <= 130):
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
                return mask, left, right
    return None


def _progression_signature_from_frame(frame):
    """Keep number shapes so recovery can confirm the displayed count changes."""
    label = _progression_label_glyphs(frame)
    if label is None:
        return None
    mask, left, right = label
    return tuple(
        tuple(cv2.resize(mask[y:y + h, x:x + w], (32, 32),
                         interpolation=cv2.INTER_NEAREST) > 0
              for x, y, w, h, _ in side)
        for side in (left, right)
    )


def _progression_shapes_match(first, second):
    if first is None or second is None or len(first) != len(second):
        return False
    for a, b in zip(first, second):
        union = np.count_nonzero(a | b)
        if not union or np.count_nonzero(a & b) / union < 0.88:
            return False
    return True


def _progression_count_changed(first, second):
    # A changed target or missing label is not evidence of counting. Only the
    # numerator may change, during the same selected minigame.
    return bool(first is not None and second is not None
                and _progression_shapes_match(first[1], second[1])
                and not _progression_shapes_match(first[0], second[0]))


def _new_ui_progression(sct, geometry, stat):
    """(completion, signature) from the October 2026 "Progression X/Y" label."""
    label = _grab_reference_box(sct, training_ui.progression_label_box(stat), geometry=geometry)
    numbers = training_ui.progression_numbers(label)
    return training_ui.progression_complete(numbers), training_ui.progression_signature(numbers)


def read_progression_completion(sct, geometry=None):
    if _new_game_ui():
        return read_progression_state(sct, geometry, CURRENT_TRAINING_STATE)[0]
    frame = _grab_reference_box(sct, PROG_SEARCH_BOX, geometry=geometry)
    return _apply_points_cap(frame, _progression_completion_from_frame(frame))


def read_progression_state(sct, geometry=None, stat=None):
    stat = CURRENT_TRAINING_STATE if stat is None else stat
    if _new_game_ui():
        frame = _grab_reference_box(sct, training_ui.progression_label_box(stat), geometry=geometry)
        numbers = training_ui.progression_numbers(frame)
        completion = training_ui.progression_complete(numbers)
        reason = _progression_completion_reason(frame, completion, stat)
        return (True if reason else completion, training_ui.progression_signature(numbers), reason)
    frame = _grab_reference_box(sct, PROG_SEARCH_BOX, geometry=geometry)
    completion = _progression_completion_from_frame(frame)
    reason = _progression_completion_reason(frame, completion, stat)
    return (True if reason else completion, _progression_signature_from_frame(frame), reason)


# Training Progression panel shown with the Tab menu (1920x1080 reference):
# each stat's column centre, then its "X/20" row and its "Points Left" row.
TRAINING_POINTS_BOX = (1230, 820, 690, 120)
TRAINING_POINTS_COLUMNS = {
    "Health": 1306, "Agility": 1443, "Ki Control": 1579,
    "Physical Damage": 1715, "Ki Damage": 1852,
}
TRAINING_POINTS_COUNT_ROWS = (828, 868)
TRAINING_POINTS_LEFT_ROWS = (890, 930)
# stat -> (count at which Points Left run out, displayed target), read at selection.
PROGRESSION_TARGETS = {}
_PROGRESSION_CAP_LOGGED = set()
# Stats whose Training Progression panel already shows them at their Points Left
# cap. Ends them even when the minigame label can't be read (pale HTC sky).
_POINTS_CAP_REACHED = set()
# Last Training Progression readings and panel picture, for Discord run updates.
_LATEST_POINTS = {}
_LATEST_POINTS_PANEL = None


_MENU_DIGIT_GLYPHS = None


def _digit_glyphs():
    """Glyphs for GC's white menu/label numbers (Arial Bold; see tpl_menu_digits.png)."""
    global _MENU_DIGIT_GLYPHS
    if _MENU_DIGIT_GLYPHS is None:
        sheet = cv2.imread(os.path.join(BASE_DIR, "tpl_menu_digits.png"), cv2.IMREAD_GRAYSCALE)
        width, height = _INVENTORY_GLYPH_SIZE
        if sheet is None or sheet.shape != (height, width * 10):
            print("[PROG] Menu digits unavailable; using displayed progression without Points Left caps")
            _MENU_DIGIT_GLYPHS = []
        else:
            _MENU_DIGIT_GLYPHS = [sheet[:, d * width:(d + 1) * width] > 127 for d in range(10)]
    return _MENU_DIGIT_GLYPHS


def _classify_number(labels, components):
    """Read left-to-right digit components; None when any digit is unsure."""
    glyphs = _digit_glyphs()
    if not glyphs:
        return None
    digits = []
    for index in components:
        shape = _normalized_glyph(labels == index)
        scores = sorted(
            ((_mask_iou(shape, glyph), digit) for digit, glyph in enumerate(glyphs)),
            reverse=True,
        )
        best, second = scores[0], scores[1]
        if best[0] < INVENTORY_DIGIT_MIN_IOU or best[0] - second[0] < INVENTORY_DIGIT_MIN_MARGIN:
            return None
        digits.append(str(best[1]))
    return int("".join(digits)) if 1 <= len(digits) <= 3 else None


def _white_text_components(crop):
    hsv = cv2.cvtColor(crop[:, :, :3], cv2.COLOR_BGR2HSV)
    mask = ((hsv[:, :, 1] < 50) & (hsv[:, :, 2] > 200)).astype(np.uint8)
    count, labels, stats, _centroids = cv2.connectedComponentsWithStats(mask, 8)
    # Smaller overlapping text (the moves panel, stat names) stays under 18 px.
    components = [i for i in range(1, count) if 18 <= stats[i][3] <= 34 and stats[i][4] >= 40]
    return labels, stats, sorted(components, key=lambda i: stats[i][0])


def _read_training_points(panel, stat):
    """Return (count, target, points_left) for one stat column, or None.

    `panel` is the TRAINING_POINTS_BOX crop of the Tab menu.
    """
    if _new_game_ui():
        return training_ui.read_training_points(panel, stat)
    centre = TRAINING_POINTS_COLUMNS.get(stat)
    if centre is None or panel is None:
        return None
    ox, oy = TRAINING_POINTS_BOX[:2]
    x0, x1 = centre - 68 - ox, centre + 68 - ox

    top, bottom = TRAINING_POINTS_COUNT_ROWS
    labels, stats, components = _white_text_components(panel[top - oy:bottom - oy, x0:x1])
    if len(components) < 3:
        return None
    digit_height = int(np.median([stats[i][3] for i in components]))
    # "/" is narrower than any digit and a couple of pixels taller.
    slashes = [i for i in components if stats[i][2] <= 11 and stats[i][3] > digit_height]
    if len(slashes) != 1:
        return None
    slash_x = stats[slashes[0]][0]
    count = _classify_number(labels, [i for i in components if stats[i][0] < slash_x])
    target = _classify_number(labels, [i for i in components if stats[i][0] > slash_x])

    top, bottom = TRAINING_POINTS_LEFT_ROWS
    labels, stats, components = _white_text_components(panel[top - oy:bottom - oy, x0:x1])
    points_left = _classify_number(labels, components)
    if count is None or target is None or points_left is None or count > target:
        return None
    return count, target, points_left


# About 0.9 s of extra grabs while the panel animates in. Counted in grabs, not
# seconds, so a frozen clock can never keep the menu open.
POINTS_PANEL_SETTLE_GRABS = 11
POINTS_PANEL_SETTLE_STEP_SEC = 0.08


def _capture_points_panels(sct, stat=None):
    """Two captures of the Training Progression panel, or none if capture fails.

    The panel slides and scales in for about half a second after the menu opens,
    so with a stat given this keeps grabbing until two grabs in a row read the
    same numbers, giving up after POINTS_PANEL_SETTLE_GRABS more grabs.

    Points Left only refines completion, so a capture problem here must never
    stop the stat from being selected.
    """
    box = training_ui.TRAINING_POINTS_BOX if _new_game_ui() else TRAINING_POINTS_BOX
    try:
        first = _grab_reference_box(sct, box)
        time.sleep(0.05)
        second = _grab_reference_box(sct, box)
        if stat is None:
            return [first, second]
        previous = _read_training_points(first, stat)
        for _ in range(POINTS_PANEL_SETTLE_GRABS):
            reading = _read_training_points(second, stat)
            if reading is not None and reading == previous:
                break
            time.sleep(POINTS_PANEL_SETTLE_STEP_SEC)
            first, previous = second, reading
            second = _grab_reference_box(sct, box)
        global _LATEST_POINTS_PANEL
        _LATEST_POINTS_PANEL = second
        return [first, second]
    except Exception as error:
        print(f"[PROG] Training Progression panel capture failed: {error}")
        return []


def _notification_extras(telemetry=None):
    """(summary, PNG bytes or None) for a Discord run update; structured values only."""
    summary = {"stats": dict(_LATEST_POINTS), "telemetry": dict(telemetry or TELEMETRY)}
    image = None
    if _LATEST_POINTS_PANEL is not None:
        ok, encoded = cv2.imencode(".png", _LATEST_POINTS_PANEL)
        image = encoded.tobytes() if ok else None
    return summary, image


def _record_points_target(stat, panels):
    """Remember where this stat stops counting so a Points Left cap ends it.

    A misread would end training early, so two captures must agree.
    """
    _PROGRESSION_CAP_LOGGED.discard(stat)
    _POINTS_CAP_REACHED.discard(stat)
    readings = {_read_training_points(panel, stat) for panel in panels}
    points = readings.pop() if len(panels) == 2 and len(readings) == 1 else None
    if points is None:
        PROGRESSION_TARGETS.pop(stat, None)
        print(f"[PROG] {stat}: Points Left not read; using the X/20 and stuck-count checks")
        return
    count, target, points_left = points
    _LATEST_POINTS[stat] = points
    finish = min(target, count + points_left)
    PROGRESSION_TARGETS[stat] = (finish, target)
    print(f"[PROG] {stat}: {count}/{target} with {points_left} Points Left; done at {finish}/{target}")
    if count >= finish and finish < target:
        _POINTS_CAP_REACHED.add(stat)
        print(f"[PROG] {stat}: already at its Points Left cap")


def _progression_numbers_from_frame(frame):
    """Read the in-training "(X/Y)" numbers, or None when any digit is unsure."""
    if _new_game_ui():
        return training_ui.read_progression_level(frame)
    label = _progression_label_glyphs(frame)
    if label is None:
        return None
    mask, left, right = label
    count, labels, stats, _centroids = cv2.connectedComponentsWithStats(mask, 8)
    by_box = {tuple(int(v) for v in stats[i][:4]): i for i in range(1, count)}
    numbers = []
    for side in (left, right):
        indices = [by_box.get((x, y, w, h)) for x, y, w, h, _area in side]
        if None in indices:
            return None
        numbers.append(_classify_number(labels, indices))
    return None if None in numbers else tuple(numbers)


def _progression_completion_reason(frame, completion, stat):
    if completion is True:
        return "displayed_target"
    if stat in _POINTS_CAP_REACHED:
        return "points_left"
    cap = PROGRESSION_TARGETS.get(stat)
    if completion is not False or cap is None or cap[0] >= cap[1]:
        return None
    numbers = _progression_numbers_from_frame(frame)
    if numbers is not None and numbers[1] == cap[1] and numbers[0] >= cap[0]:
        return "points_left"
    return None


def _apply_points_cap(frame, completion):
    reason = _progression_completion_reason(frame, completion, CURRENT_TRAINING_STATE)
    return True if reason else completion


def _progression_stall_state(
    current_state,
    tracked_state,
    completion,
    signature,
    now,
    stall_state,
    suspended=False,
):
    """Time only a continuously readable, locked, incomplete count."""
    if not COMPATIBILITY_SETTINGS["progression_stall_enabled"]:
        return None, False
    if (not current_state or tracked_state != current_state
            or completion is not False or signature is None or suspended):
        return None, False
    if (stall_state is None or stall_state[0] != current_state
            or not all(_progression_shapes_match(previous, current)
                       for previous, current in zip(stall_state[1], signature))):
        return (current_state, signature, now), False
    return stall_state, now - stall_state[2] >= PROGRESSION_STALL_TIMEOUT_SEC


def _progression_meter_growth(fill, previous):
    if fill is None:
        return None, False
    if previous is None or fill < previous[0]:
        return (fill, 0), False
    baseline, streak = previous
    streak = streak + 1 if fill >= baseline + 3 else 0
    if streak >= 3:
        return (fill, 0), True
    return (baseline, streak), False


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


def _recover_from_training_ui_loss(current_state):
    """Drop a stale progression lock instead of killing an active run.

    HTC can hide or redraw the label for longer than the old eight-second
    watchdog even while the minigame is still accepting input. Death has its
    own confirmed detector, so a missing label is not authoritative evidence
    that the session ended. A later readable frame will lock tracking again.
    """
    global PROGRESSION_TRACKED_STATE, PROGRESSION_COMPLETE
    PROGRESSION_TRACKED_STATE = None
    PROGRESSION_COMPLETE = None
    print(
        f"[MONITOR] Progression label missing for {current_state}; "
        "continuing and waiting for it to reappear"
    )


def _background_game_monitor(stop_event, ready_event=None):
    """Continuously watch progression and critical HP during every minigame."""
    global PROGRESSION_TRACKED_STATE, PROGRESSION_COMPLETE, PROGRESSION_CAPPED_STATE
    global PROGRESSION_STATE_STARTED_AT
    global GC_RECOVERY_VERIFY_PENDING, UI_STOP_REQUESTED, HEALING_ACTION

    tracked_token = None
    progression_stall = None
    stall_meter = None
    complete_streak = 0
    completion_sent = False
    red_streak = 0
    red_since = None
    red_handled = False
    last_heal_method = _effective_heal_method()
    ki_hp_state, ki_hp_checked_at = None, 0.0
    charge_checked_at = 0.0
    hp_sampled_at = 0.0
    death_streak = 0
    last_error_log = 0.0
    progression_missing_since = None
    recovery_state = None
    recovery_baseline = None
    recovery_signature = None
    recovery_growth_streak = 0
    recovery_active_sec = 0.0
    recovery_last_tick = time.monotonic()
    capture_failure_since = None
    covering_check_at = 0.0

    with mss.MSS() as sct:
        if ready_event is not None:
            ready_event.set()
        while not stop_event.is_set():
            now = time.monotonic()
            try:
                if _CHARGE_HELD:
                    _set_charge_hold(True)
                if not update_game_window():
                    _set_charge_hold(False)
                    progression_stall = None
                    stop_event.wait(PROG_POLL_INTERVAL_SEC)
                    continue
                if now >= covering_check_at and (
                        _GAME_COVERED_SINCE is not None
                        or (CURRENT_TRAINING_STATE and _game_has_focus())):
                    covering_check_at = now + 0.5
                    if _watch_game_covered():
                        break
                game_geometry = _game_geometry()
                token = _progression_token()
                _, current_state, state_started_at = token
                tick = now
                recovery_suspended = (not current_state or TRAINING_MENU_VISIBLE
                    or CONTROLLER_PAUSED or SENZU_CONTROLLER_ACTIVE.is_set()
                    or SENZU_CONTROLLER_RESUME_REQUIRED.is_set() or not _game_has_focus())
                if GC_RECOVERY_VERIFY_PENDING and not recovery_suspended:
                    recovery_active_sec += min(1.0, tick - recovery_last_tick)
                    if current_state != recovery_state:
                        recovery_state = current_state
                        recovery_baseline = recovery_signature = None
                        recovery_growth_streak = 0
                    if _new_game_ui():
                        # No partial-progress meter any more: only the Level
                        # count moving (~30 s per level live) proves the spot
                        # is out of the safe zone and counting.
                        signature = _new_ui_progression(sct, game_geometry, current_state)[1]
                        if signature is not None and recovery_signature is None:
                            recovery_signature = signature
                        if _progression_count_changed(recovery_signature, signature):
                            recovery_growth_streak = 3
                    else:
                        recovery_frame = _grab_reference_box(sct, (0, 0, 1920, 1080), game_geometry)
                        signature = _progression_signature_from_frame(recovery_frame[300:800, 700:1400])
                        fill = gc_recovery.progress_fill(recovery_frame, current_state)
                        if signature is not None and fill is not None:
                            if recovery_baseline is None:
                                recovery_baseline, recovery_signature = fill, signature
                            growth = (fill >= recovery_baseline + 3
                                      or _progression_count_changed(recovery_signature, signature))
                            recovery_growth_streak = recovery_growth_streak + 1 if growth else 0
                        else:
                            recovery_growth_streak = 0
                    if recovery_growth_streak >= 3:
                        GC_RECOVERY_VERIFY_PENDING = False
                        print('[RECOVERY] Training spot confirmed: progression is counting')
                    elif recovery_active_sec >= (GC_RECOVERY_VERIFY_TIMEOUT_SEC if not _new_game_ui()
                                                 else GC_RECOVERY_VERIFY_TIMEOUT_NEW_UI_SEC):
                        _record_run_outcome('error', 'GC return could not confirm counting progress')
                        UI_STOP_REQUESTED = True
                        _release_macro_input()
                        break
                recovery_last_tick = tick
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
                    if current_state in ("Ki Control", "Ki Damage"):
                        _note_ki_death()
                    _stop_for_game_death()
                    break
                if token != tracked_token:
                    tracked_token = token
                    complete_streak = 0
                    completion_sent = False
                    progression_missing_since = None
                    progression_stall = None
                    PROGRESSION_TRACKED_STATE = None
                    PROGRESSION_COMPLETE = None
                    PROGRESSION_CAPPED_STATE = None

                if PROGRESSION_STALL_RESET_REQUESTED.is_set():
                    PROGRESSION_STALL_RESET_REQUESTED.clear()
                    progression_stall = None
                stall_suspended = (
                    recovery_suspended or GC_RECOVERY_VERIFY_PENDING or death_visible
                    or PAUSE_TOGGLE_REQUESTED or UI_STOP_REQUESTED or _USER_STOP_LATCHED
                )
                if stall_suspended:
                    progression_stall = None
                stalled = False
                completion_reason = None
                completion_suspended = (CONTROLLER_PAUSED or PAUSE_TOGGLE_REQUESTED
                    or SENZU_CONTROLLER_ACTIVE.is_set() or SENZU_CONTROLLER_RESUME_REQUIRED.is_set()
                    or GC_RECOVERY_VERIFY_PENDING or death_visible or not _game_has_focus()
                    or UI_STOP_REQUESTED or _USER_STOP_LATCHED)
                if completion_suspended:
                    complete_streak = 0

                if (current_state and not completion_suspended
                        and not completion_sent
                        and now - state_started_at >= PROG_AFTER_SWITCH_GRACE_SEC):
                    completion, signature, completion_reason = read_progression_state(sct, game_geometry, current_state)
                    if token != _progression_token():
                        complete_streak = 0
                        progression_stall = None
                        continue
                    if completion is not None:
                        progression_missing_since = None
                        if PROGRESSION_TRACKED_STATE != current_state:
                            print(_progression_tracking_message(current_state))
                        PROGRESSION_TRACKED_STATE = current_state
                        PROGRESSION_COMPLETE = False
                        complete_streak = complete_streak + 1 if completion else 0
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
                            _recover_from_training_ui_loss(current_state)
                            progression_missing_since = None

                    progression_stall, stalled = _progression_stall_state(
                        current_state, PROGRESSION_TRACKED_STATE, completion,
                        signature, now, progression_stall, suspended=stall_suspended,
                    )
                    if progression_stall is None or progression_stall[2] == now:
                        stall_meter = None
                    elif now - progression_stall[2] >= max(0.0, PROGRESSION_STALL_TIMEOUT_SEC - 10.0):
                        meter_fill = None
                        if not _new_game_ui():  # the new panel has no partial meter
                            meter_frame = _grab_reference_box(sct, (0, 0, 1920, 1080), game_geometry)
                            meter_fill = gc_recovery.progress_fill(meter_frame, current_state)
                        stall_meter, growing = _progression_meter_growth(meter_fill, stall_meter)
                        if growing:
                            progression_stall = (current_state, signature, now)
                            stalled = False

                # Post-switch settle window (like the progression check above): right
                # after a category starts the HP-fill box can misread as red while the
                # UI is mid-transition, which spuriously fired Auto-Senzu on startup
                # (the visible "it just presses Tab" symptom). Suppress the check for
                # SENZU_AFTER_SWITCH_GRACE_SEC; a real low-HP emergency still needs
                # 2 red frames + SENZU_DELAY_SEC, so it lands just after the window.
                ki_stat = _new_game_ui() and current_state in ("Ki Control", "Ki Damage")
                if current_state != ki_hp_state:
                    ki_hp_state, ki_hp_checked_at = current_state, now
                    _KI_HP_READINGS.clear()
                    if ki_stat and _healing_monitor_enabled() and _in_htc(current_state):
                        print(f"[HP] {current_state} is in HTC; skipping Ki HP checks")
                if (ki_stat and _healing_monitor_enabled()
                        and not _in_htc(current_state)
                        and now - ki_hp_checked_at >= _ki_hp_interval()):
                    ki_hp_checked_at = now
                    stall_before_check = progression_stall
                    if _claim_healing(token):
                        healed = True
                        try:
                            healed = _ki_hp_check(sct)
                        except QuitException:
                            raise
                        except Exception as e:
                            print(f"[HP] Ki HP check failed: {e}")
                            _stop_for_senzu_failure(f"Ki HP check: {e.__class__.__name__}: {e}")
                            break
                        finally:
                            with _stop_input_gate:
                                _set_training_state(CURRENT_TRAINING_STATE)
                                HEALING_ACTION = None
                                SENZU_CONTROLLER_ACTIVE.clear()
                        ki_hp_checked_at = time.monotonic()
                        if healed:
                            progression_stall = None
                        else:
                            # Nothing changed in the game; claiming bumped the
                            # generation, so carry the stall clock across it or
                            # a 30 s check cadence would never let it expire.
                            tracked_token = _progression_token()
                            progression_stall = stall_before_check
                            PROGRESSION_TRACKED_STATE = current_state
                        complete_streak = 0
                        completion_sent = False
                        continue
                if (_new_game_ui() and current_state in ("Health", "Agility", "Physical Damage")
                        and now - hp_sampled_at >= HP_DRAIN_SAMPLE_SEC):
                    hp_sampled_at = now
                    fraction = _new_ui_hp_fraction(sct)
                    if fraction is not None:
                        _note_hp_reading(fraction, now)
                charge_mode = COMPATIBILITY_SETTINGS["charge_hold_mode"]
                if (_new_game_ui() and charge_mode != "off"
                        and not (STOP_CHARGING_AFTER_KI_CONTROL and _KI_CONTROL_DONE)
                        and not GC_RECOVERY_VERIFY_PENDING
                        and current_state in ("Health", "Agility", "Physical Damage")
                        and now - charge_checked_at >= MID_STAT_CHARGE_POLL_SEC
                        and now - PROGRESSION_STATE_STARTED_AT >= SENZU_AFTER_SWITCH_GRACE_SEC):
                    charge_checked_at = now
                    level = _ki_charge_level(sct)
                    if (level is not None
                            and _ki_charge_drained(level, KI_CHARGE_TARGETS[charge_mode])
                            and _claim_healing(token, for_charge=True)):
                        print(f"[CHARGE] {current_state}: Ki down to {level:.2f}; stepping out to charge")
                        stall_before_charge = progression_stall
                        try:
                            charged = _mid_stat_recharge(sct)
                        except QuitException:
                            raise
                        except Exception as e:
                            print(f"[CHARGE] Mid-stat charge failed: {e}")
                            _stop_for_senzu_failure(f"Mid-stat charge: {e.__class__.__name__}: {e}")
                            break
                        finally:
                            with _stop_input_gate:
                                _set_training_state(CURRENT_TRAINING_STATE)
                                HEALING_ACTION = None
                                SENZU_CONTROLLER_ACTIVE.clear()
                        charge_checked_at = time.monotonic()
                        if not charged:
                            charge_checked_at += MID_STAT_CHARGE_BACKOFF_SEC
                            print(f"[CHARGE] Couldn't charge; next mid-stat try in {MID_STAT_CHARGE_BACKOFF_SEC:.0f}s")
                        # Same as a Ki check that didn't heal: the count didn't move.
                        tracked_token = _progression_token()
                        progression_stall = stall_before_charge
                        PROGRESSION_TRACKED_STATE = current_state
                        complete_streak = 0
                        completion_sent = False
                        continue
                if _effective_heal_method() != last_heal_method:
                    last_heal_method = _effective_heal_method()
                    red_handled = False
                    red_streak = 0
                    red_since = None
                if (_effective_heal_method() == "majin_regen" and red_handled
                        and now >= MAJIN_REGEN_READY_AT):
                    red_handled = False
                if (_healing_monitor_enabled() and current_state
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
                        if not _claim_healing(token):
                            red_streak = 0
                            red_since = None
                            continue
                        red_handled = True
                        red_streak = 0
                        red_since = None
                        progression_stall = None
                        PROGRESSION_CAPPED_STATE = None
                        PROGRESSION_COMPLETE_REQUESTED.clear()
                        heal_method = HEALING_ACTION
                        try:
                            if heal_method == "majin_regen":
                                use_majin_regen(sct)
                            else:
                                eat_senzu(sct)
                        except QuitException:
                            # User Stop is normal control flow. Let the outer
                            # handler end this monitor without turning the run
                            # into an Auto-Senzu error.
                            raise
                        except Exception as e:
                            import traceback
                            label = "MAJIN" if heal_method == "majin_regen" else "SENZU"
                            print(f"[{label}] {traceback.format_exc()}")
                            stop_healing = (_stop_for_majin_failure if heal_method == "majin_regen"
                                            else _stop_for_senzu_failure)
                            stop_healing(f"Unexpected {e.__class__.__name__}: {e}")
                            break
                        finally:
                            with _stop_input_gate:
                                # Publish the new attempt before the controller can resume.
                                _set_training_state(CURRENT_TRAINING_STATE)
                                HEALING_ACTION = None
                                SENZU_CONTROLLER_ACTIVE.clear()
                        complete_streak = 0
                        completion_sent = False
                else:
                    red_streak = 0
                    red_since = None
                    # Majin's cooldown latch must survive the post-heal grace
                    # window and Pause, otherwise it reopens menus every second.
                    if _effective_heal_method() != "majin_regen" or not current_state:
                        red_handled = False
                # Healing or a stat switch can invalidate a frame while it is being read.
                if complete_streak >= PROG_STABLE_COMPLETE_FRAMES and completion_reason:
                    completion_sent = _publish_progression_completion(token, completion_reason)
                    if completion_sent:
                        label = ("reached its displayed target" if completion_reason == "displayed_target"
                                 else "reached its confirmed Points Left cap")
                        print(f"[PROG] {current_state} {label} ({PROG_STABLE_COMPLETE_FRAMES} stable reads)")
                elif stalled and progression_stall is not None and not TRAINING_MENU_VISIBLE:
                    completion_sent = _publish_progression_completion(token, "stall")
                    if completion_sent:
                        stuck_for = now - progression_stall[2]
                        print(
                            f"[PROG] {current_state} stuck at the same count for "
                            f"{_format_seconds(stuck_for)} (likely out of Points Left); moving on"
                        )
                    else:
                        progression_stall = None
                capture_failure_since = None
            except QuitException:
                break
            except Exception as e:
                progression_stall = None
                # Capture can fail briefly while Roblox changes display mode. Keep
                # the monitor alive, but rate-limit the diagnostic.
                if capture_failure_since is None:
                    capture_failure_since = now
                elif now - capture_failure_since >= 5.0:
                    raise RuntimeError("Game-state capture failed continuously for 5 seconds") from e
                if now - last_error_log >= 5.0:
                    last_error_log = now
                    print(f"[MONITOR] {e.__class__.__name__}: {e}")

            stop_event.wait(PROG_POLL_INTERVAL_SEC)


def _background_game_monitor_worker(stop_event, ready_event):
    global UI_STOP_REQUESTED, _AFTER_ACTIONS_BLOCKED
    try:
        _background_game_monitor(stop_event, ready_event)
    except Exception as error:
        if not stop_event.is_set():
            _AFTER_ACTIONS_BLOCKED = True
            UI_STOP_REQUESTED = True
            _record_run_outcome("error", f"Game-state monitor failed: {error}")
            print(f"[SAFETY] Game-state monitor failed: {error}")
            stop_event.set()
    finally:
        # Wake startup even on constructor failure; it also checks the stop
        # event and worker lifetime before permitting controller input.
        ready_event.set()


def _start_background_game_monitor():
    global _background_monitor_stop, _background_monitor_thread
    global PROGRESSION_TRACKED_STATE, PROGRESSION_COMPLETE, PROGRESSION_CAPPED_STATE
    global _AFTER_ACTIONS_BLOCKED
    PROGRESSION_COMPLETE_REQUESTED.clear()
    PROGRESSION_STALL_RESET_REQUESTED.clear()
    PROGRESSION_TRACKED_STATE = None
    PROGRESSION_COMPLETE = None
    PROGRESSION_CAPPED_STATE = None
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
    ready_event = threading.Event()
    _background_monitor_thread = threading.Thread(
        target=_background_game_monitor_worker,
        args=(_background_monitor_stop, ready_event),
        daemon=True,
        name="game-state-monitor",
    )
    _background_monitor_thread.start()
    if (not ready_event.wait(timeout=2.0)
            or _background_monitor_stop.is_set()
            or not _background_monitor_thread.is_alive()):
        _background_monitor_stop.set()
        _AFTER_ACTIONS_BLOCKED = True
        _record_run_outcome("error", "Game-state monitor could not initialize")
        print("[SAFETY] Game-state monitor could not initialize; Start cancelled")
        return False
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
    if _new_game_ui() and score < TRAINING_MENU_MATCH_THRESHOLD:
        if "gold" not in _TRAINING_MENU_TEMPLATES:
            _TRAINING_MENU_TEMPLATES["gold"] = training_ui.load_menu_heading_gold()
        gold = _TRAINING_MENU_TEMPLATES["gold"]
        if gold is not None:
            score = max(score, cv2.minMaxLoc(cv2.matchTemplate(gray, gold, cv2.TM_CCOEFF_NORMED))[1])
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


def _load_gravity_templates():
    if _new_game_ui():
        return training_ui.load_gravity_templates()
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


def _close_roblox_chat(sct, monitor):
    """Close Roblox chat with a click on its top-bar button if it is open.

    Needs two "open" reads before clicking, then waits up to a second for the
    button to show closed. One more click only if it still reads open: live, a
    click sent right after a respawn didn't register and the open chat box then
    covered the gravity button.
    """
    x, y, width, height = training_ui.CHAT_BUTTON_BOX
    box = {"left": monitor["left"] + x, "top": monitor["top"] + y, "width": width, "height": height}

    def read():
        return training_ui.roblox_chat_state(np.array(sct.grab(box)))

    state, centre = read()
    if state != "open":
        return
    safe_sleep(0.10)
    state, centre = read()
    if state != "open":
        return
    for attempt in (1, 2):
        click_at(box["left"] + centre[0], box["top"] + centre[1])
        robust_move(*_reference_point(*GRAVITY_POINTER_PARK_POINT, monitor))
        for _ in range(10):
            safe_sleep(0.10)
            state, seen_centre = read()
            if state == "closed":
                print("[CHAT] Closed Roblox chat")
                return
        if state != "open":
            break
        centre = seen_centre
        if attempt == 1:
            print("[CHAT] Chat still open after the click; clicking once more")
    print("[CHAT] Clicked the chat button but couldn't confirm chat closed"
          + ("" if state == "open" else " (button unreadable)"))


def _wait_for_gravity_click_gap(last_click_at, after_run=False):
    wait = GRAVITY_CLICK_GAP_SEC - (time.monotonic() - last_click_at)
    if wait > 0:
        (time.sleep if after_run else safe_sleep)(wait)


def _gravity_advance_is_safe(current, observed, target):
    return bool(
        observed in range(0, 101, 10)
        and current < observed <= target
    )


def _read_gc_gravity(sct, geometry, templates):
    global GC_RUN_CONFIRMED
    frame = _grab_reference_box(sct, GRAVITY_LABEL_BOX, geometry=geometry)
    if _new_game_ui():
        value, score, second, control_present = training_ui.read_gravity(frame, templates)
        # Live 2026-10-13: the first read after Training Mode opens catches the
        # menu mid-animation (0.29); 0.18 s later it is exact. Retry briefly.
        deadline = time.monotonic() + 0.5
        while value is None and control_present and time.monotonic() < deadline:
            time.sleep(0.06)
            frame = _grab_reference_box(sct, GRAVITY_LABEL_BOX, geometry=geometry)
            value, score, second, control_present = training_ui.read_gravity(frame, templates)
        if control_present:
            GC_RUN_CONFIRMED = True
        return value, score, second, control_present
    mask = _gravity_mask_from_frame(frame)
    value, score, second = _gravity_value_from_mask(mask, templates)
    control_present = _gravity_control_glyph_count(mask) > 0
    if control_present:
        GC_RUN_CONFIRMED = True
    return value, score, second, control_present


def _gc_room_confirmed(sct, geometry):
    global GC_RUN_CONFIRMED
    if not GC_RUN_CONFIRMED:
        GC_RUN_CONFIRMED = gc_recovery.room_visible(
            _grab_reference_box(sct, (0, 0, 1920, 1080), geometry))
    return GC_RUN_CONFIRMED


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

    # Nudge so Roblox redraws its own cursor at the park point; otherwise it can
    # stay drawn over the label (seen on a live client after a manual gravity click) and
    # the read fails as "no control".
    robust_move(*_reference_point(*GRAVITY_POINTER_PARK_POINT, geometry), nudge=True)
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
    started = time.monotonic()
    click_point = _reference_point(*GRAVITY_CLICK_POINT, geometry)
    park_point = _reference_point(*GRAVITY_POINTER_PARK_POINT, geometry)
    last_click_at = 0.0
    while current < target:
        expected = current + 10
        accepted = False
        for attempt in range(1, 4):
            check_exit()
            _wait_for_gravity_click_gap(last_click_at)
            last_click_at = time.monotonic()
            click_at(*click_point)
            robust_move(*park_point, nudge=True)
            deadline = time.monotonic() + 0.8
            missing_since = None
            unchanged_seen = False
            while time.monotonic() < deadline:
                safe_sleep(GRAVITY_POLL_SEC)
                observed, score, second, control_present = _read_gc_gravity(
                    sct, geometry, templates
                )
                if not control_present:
                    missing_since = missing_since or time.monotonic()
                    if time.monotonic() - missing_since >= GRAVITY_MISSING_SEC:
                        print("[GRAVITY] GC control disappeared; refusing further clicks")
                        return False
                    continue
                missing_since = None
                unchanged_seen = unchanged_seen or observed == current
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
            if not unchanged_seen:
                # Live 2026-10-14: an unreadable label after a click that did
                # land led to retry clicks that overshot 0G and stopped at 100G.
                print(
                    f"[GRAVITY] Couldn't read the value after clicking {current}G -> "
                    f"{expected}G; refusing another click"
                )
                return False
            print(
                f"[GRAVITY] Click {current}G -> {expected}G not confirmed; "
                f"retrying ({attempt}/3)"
            )
        if not accepted:
            print(f"[GRAVITY] Could not confirm {expected}G; leaving it at {current}G")
            return False

    print(
        f"[GRAVITY] Raised {start_value}G -> {current}G in "
        f"{time.monotonic() - started:.1f}s; target confirmed"
    )
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
            _nudge_pointer()
        time.sleep(0.10)
        _ensure_after_run_active()
    else:
        robust_move(*park_at, nudge=True)
        safe_sleep(0.10)
    current, score, second, control_present = _read_gc_gravity(
        sct, geometry, templates
    )
    if not control_present:
        if _gc_room_confirmed(sct, geometry):
            print(
                "[GRAVITY] GC gravity control not found; it may be covered by an "
                "overlay, so 0G could not be confirmed"
            )
            return False
        print("[GRAVITY] No GC control or GC room detected; 0G does not apply")
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
    last_click_at = 0.0
    while current != 0:
        expected = 0 if current == 100 else current + 10
        accepted = False
        for attempt in range(1, 4):
            _wait_for_gravity_click_gap(last_click_at, after_run)
            last_click_at = time.monotonic()
            if after_run:
                _ensure_after_run_active()
                with _input_lock:
                    _ensure_after_run_active()
                    _click_sendinput_abs(*click_point)
                    _ensure_after_run_active()
                    _user32.SetCursorPos(*park_point)
                    _nudge_pointer()
            else:
                check_exit()
                click_at(*click_point)
                robust_move(*park_point, nudge=True)
            deadline = time.monotonic() + 0.8
            missing_since = None
            unchanged_seen = False
            while time.monotonic() < deadline:
                if after_run:
                    time.sleep(GRAVITY_POLL_SEC)
                    _ensure_after_run_active()
                else:
                    safe_sleep(GRAVITY_POLL_SEC)
                observed, score, second, control_present = _read_gc_gravity(
                    sct, geometry, templates
                )
                if not control_present:
                    missing_since = missing_since or time.monotonic()
                    if time.monotonic() - missing_since >= GRAVITY_MISSING_SEC:
                        print("[GRAVITY] GC control disappeared; refusing further clicks")
                        return False
                    continue
                missing_since = None
                unchanged_seen = unchanged_seen or observed == current
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
            if not unchanged_seen:
                # Live 2026-10-14: an unreadable label after a click that did
                # land led to retry clicks that overshot 0G and stopped at 100G.
                print(
                    f"[GRAVITY] Couldn't read the value after clicking {current}G -> "
                    f"{expected}G; refusing another click"
                )
                return False
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


def _backup_invalid_master_config():
    global _config_backup_required
    with _config_lock:
        if not _config_backup_required:
            return
        backup_path = (
            f"{os.path.splitext(MACRO_CONFIG_FILE)[0]}.invalid-"
            f"{time.strftime('%Y%m%d-%H%M%S')}-{time.time_ns()}.json"
        )
        os.rename(MACRO_CONFIG_FILE, backup_path)
        _config_backup_required = False
        print(f"[WARN] Preserved original settings at {backup_path}")


def load_master_config():
    """
    Loads user settings from `config/macro_config.json` (safe/optional).
    If missing, creates a default config file using the values in this script.
    """
    global _config_backup_required
    global START_DELAY, GC_GRAVITY_TARGET_G, PREVENT_SLEEP_WHILE_RUNNING, SWITCH_TO_GAME_ON_START
    global RESTORE_FULLSCREEN_ON_START, WINDOWED_MODE_ON_START, DISPLAY_CONFIRM_CHANGES
    global HIDE_ROBLOX_CHAT_ON_START, STOP_CHARGING_AFTER_KI_CONTROL, KI_HP_CHECK_MODE
    global SHUTDOWN_PC_WHEN_FINISHED, AFTER_RUN_GAME_ACTION, AFTER_RUN_ON_FAILURE
    global AUTO_RETRY_ON_FAILURE, AUTO_RETRY_MAX_ATTEMPTS
    global AUTO_RETRY_RECOVERY_MODE, AUTO_RETRY_WALK_OUT, AUTO_RETRY_WALK_SECONDS
    global DIAGNOSTIC_MODE
    global NEW_GAME_WAIT, NO_YELLOW_TIMEOUT_SEC
    global PROGRESSION_STALL_TIMEOUT_SEC
    global MANUAL_NEXT_KEY, START_STOP_HOTKEY
    global HEALTH_HIT_COOLDOWN_SEC, HEALTH_MODE
    global KEY_PRESS_DELAY, STABILIZE_DELAY, POST_COMBO_DELAY
    global TRAINING_ORDER_CUSTOM, AGILITY_MODE
    global AGILITY_GREEN_OBSERVE_SEC, AGILITY_INTER_STRING_WAIT_SEC, AGILITY_AFTER_GREEN_SETTLE_SEC

    def _apply(data):
        # IMPORTANT: this is a nested function; we must declare globals here
        # or assignments will create locals and the config won't actually apply.
        global START_DELAY, GC_GRAVITY_TARGET_G, PREVENT_SLEEP_WHILE_RUNNING, SWITCH_TO_GAME_ON_START
        global RESTORE_FULLSCREEN_ON_START, WINDOWED_MODE_ON_START, DISPLAY_CONFIRM_CHANGES
        global HIDE_ROBLOX_CHAT_ON_START, STOP_CHARGING_AFTER_KI_CONTROL, KI_HP_CHECK_MODE
        global SHUTDOWN_PC_WHEN_FINISHED, AFTER_RUN_GAME_ACTION, AFTER_RUN_ON_FAILURE
        global AUTO_RETRY_ON_FAILURE, AUTO_RETRY_MAX_ATTEMPTS
        global AUTO_RETRY_RECOVERY_MODE, AUTO_RETRY_WALK_OUT, AUTO_RETRY_WALK_SECONDS
        global DIAGNOSTIC_MODE
        global NEW_GAME_WAIT, NO_YELLOW_TIMEOUT_SEC
        global PROGRESSION_STALL_TIMEOUT_SEC
        global MANUAL_NEXT_KEY, START_STOP_HOTKEY, PAUSE_HOTKEY
        global HEALTH_HIT_COOLDOWN_SEC, HEALTH_MODE
        global KEY_PRESS_DELAY, STABILIZE_DELAY, POST_COMBO_DELAY
        global TRAINING_ORDER_CUSTOM, AGILITY_MODE
        global AGILITY_GREEN_OBSERVE_SEC, AGILITY_INTER_STRING_WAIT_SEC, AGILITY_AFTER_GREEN_SETTLE_SEC

        if not isinstance(data, dict):
            raise ValueError("Settings must be a JSON object")
        for key in COMPATIBILITY_DEFAULTS:
            if key in data:
                _set_compatibility_setting(key, data[key])
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
        if "switch_to_game_on_start" in data:
            SWITCH_TO_GAME_ON_START = _ui_bool(data["switch_to_game_on_start"])
        if "hide_roblox_chat_on_start" in data:
            HIDE_ROBLOX_CHAT_ON_START = _ui_bool(data["hide_roblox_chat_on_start"])
        if "stop_charging_after_ki_control" in data:
            STOP_CHARGING_AFTER_KI_CONTROL = _ui_bool(data["stop_charging_after_ki_control"])
        if data.get("ki_hp_check_mode") in ("fixed", "adaptive"):
            KI_HP_CHECK_MODE = data["ki_hp_check_mode"]
        if "restore_fullscreen_on_start" in data:
            RESTORE_FULLSCREEN_ON_START = _ui_bool(
                data["restore_fullscreen_on_start"]
            )
        if "windowed_mode_on_start" in data:
            WINDOWED_MODE_ON_START = _ui_bool(data["windowed_mode_on_start"])
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
        if "progression_stall_timeout_sec" in data:
            PROGRESSION_STALL_TIMEOUT_SEC = _bounded_float(
                data["progression_stall_timeout_sec"], 30.0, 1800.0
            )

        if "manual_next_key" in data:
            MANUAL_NEXT_KEY = _loaded_hotkey(data["manual_next_key"], MANUAL_NEXT_KEY)
        if "start_stop_hotkey" in data:
            START_STOP_HOTKEY = _loaded_hotkey(data["start_stop_hotkey"], START_STOP_HOTKEY)
        if "pause_hotkey" in data:
            PAUSE_HOTKEY = _loaded_hotkey(data["pause_hotkey"], PAUSE_HOTKEY)

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
        if "ki_adaptive_brightness" in data:
            global KI_V8_ADAPTIVE_BRIGHTNESS
            KI_V8_ADAPTIVE_BRIGHTNESS = _ui_bool(data["ki_adaptive_brightness"])
        if "trait_click_approach" in data:
            global TRAIT_CLICK_APPROACH_ENABLED
            TRAIT_CLICK_APPROACH_ENABLED = _ui_bool(data["trait_click_approach"])
        if "senzu_enabled" in data:
            global SENZU_ENABLED
            SENZU_ENABLED = _ui_bool(data["senzu_enabled"])
        if "heal_method" in data:
            global HEAL_METHOD
            HEAL_METHOD = _normalize_heal_method(data["heal_method"])
        if "majin_regen_slot" in data:
            global MAJIN_REGEN_SLOT
            MAJIN_REGEN_SLOT = min(8, max(1, _finite_int(data["majin_regen_slot"])))
        if "majin_regen_senzu_reserve" in data:
            global MAJIN_REGEN_SENZU_RESERVE
            MAJIN_REGEN_SENZU_RESERVE = _ui_bool(data["majin_regen_senzu_reserve"])
        if "majin_regen_auto" in data:
            global MAJIN_REGEN_AUTO
            MAJIN_REGEN_AUTO = _ui_bool(data["majin_regen_auto"])
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
        if "START_DELAY" in data and "start_delay_sec" not in data:
            START_DELAY = _bounded_float(data["START_DELAY"], 0.0, 30.0)
        if "NEW_GAME_WAIT" in data and "after_switch_wait_sec" not in data:
            NEW_GAME_WAIT = _bounded_float(data["NEW_GAME_WAIT"], 0.0, 10.0)

        # Back-compat: older yellow keys
        if "YELLOW_TIMEOUT_SEC" in data and "no_yellow_timeout_sec" not in data:
            NO_YELLOW_TIMEOUT_SEC = _bounded_float(data["YELLOW_TIMEOUT_SEC"], 1.0, 300.0)
        if "YELLOW_MISSING_TIMEOUT_SEC" in data and "no_yellow_timeout_sec" not in data:
            NO_YELLOW_TIMEOUT_SEC = _bounded_float(data["YELLOW_MISSING_TIMEOUT_SEC"], 1.0, 300.0)

        # Back-compat: older timing names
        if "MANUAL_NEXT_KEY" in data and "manual_next_key" not in data:
            MANUAL_NEXT_KEY = _loaded_hotkey(str(data["MANUAL_NEXT_KEY"])[:1], MANUAL_NEXT_KEY)
        if ("AGILITY_KEY_HOLD" in data and "wasd_key_press_delay_sec" not in data
                and "wsad_key_press_delay_sec" not in data
                and "agility_key_press_delay_sec" not in data):
            KEY_PRESS_DELAY = _bounded_float(data["AGILITY_KEY_HOLD"], 0.0, 1.0)
        if ("AGILITY_STABILIZE" in data and "wasd_stabilize_delay_sec" not in data
                and "wsad_stabilize_delay_sec" not in data
                and "agility_stabilize_delay_sec" not in data):
            STABILIZE_DELAY = _bounded_float(data["AGILITY_STABILIZE"], 0.0, 5.0)
        if ("AGILITY_POST_BURST" in data and "wasd_post_burst_delay_sec" not in data
                and "wsad_post_burst_delay_sec" not in data
                and "agility_post_burst_delay_sec" not in data):
            POST_COMBO_DELAY = _bounded_float(data["AGILITY_POST_BURST"], 0.0, 5.0)

        # Back-compat: prior "agility_*" keys
        if ("wasd_key_press_delay_sec" not in data) and ("wsad_key_press_delay_sec" not in data) and ("agility_key_press_delay_sec" in data):
            KEY_PRESS_DELAY = _bounded_float(data["agility_key_press_delay_sec"], 0.0, 1.0)
        if ("wasd_stabilize_delay_sec" not in data) and ("wsad_stabilize_delay_sec" not in data) and ("agility_stabilize_delay_sec" in data):
            STABILIZE_DELAY = _bounded_float(data["agility_stabilize_delay_sec"], 0.0, 5.0)
        if ("wasd_post_burst_delay_sec" not in data) and ("wsad_post_burst_delay_sec" not in data) and ("agility_post_burst_delay_sec" in data):
            POST_COMBO_DELAY = _bounded_float(data["agility_post_burst_delay_sec"], 0.0, 5.0)

        hotkeys = (MANUAL_NEXT_KEY, START_STOP_HOTKEY, PAUSE_HOTKEY)
        if any(_hotkeys_overlap(first, second)
               for index, first in enumerate(hotkeys) for second in hotkeys[index + 1:]):
            raise ValueError("Hotkeys must use different keys or chords")

    def _apply_atomically(data):
        previous = _master_config_snapshot()
        try:
            _apply(data)
        except Exception:
            _apply(previous)
            raise

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
                _apply_atomically(legacy)
                save_master_config()
                print(f"[OK] Migrated legacy settings.json -> {MACRO_CONFIG_FILE}")
                return

            save_master_config()
            print(f"[OK] Created default config: {MACRO_CONFIG_FILE}")
            return

        with open(MACRO_CONFIG_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)

        _apply_atomically(data)
        # Rewrite once with the supported schema. This drops old internal/debug
        # fields that are intentionally no longer user-configurable.
        save_master_config()
        print(f"[OK] Loaded settings from {MACRO_CONFIG_FILE}")
    except Exception as e:
        print(f"[WARN] Could not load {MACRO_CONFIG_FILE}: {e}")
        if os.path.exists(MACRO_CONFIG_FILE):
            _config_backup_required = True
            try:
                _backup_invalid_master_config()
            except OSError as backup_error:
                print(f"[WARN] Could not preserve settings; saving is blocked: {backup_error}")


def _master_config_snapshot():
    return {
        **COMPATIBILITY_SETTINGS,
        **settings_schema.snapshot_settings(
            globals(), serialize=True, sanitize_training_order=_sanitize_training_order,
        ),
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
        _backup_invalid_master_config()
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

_CHARGE_HELD = False
_CHARGE_SCAN_CODE = pydirectinput.KEYBOARD_MAPPING[CHARGE_KEY]
# Shift is only ever held inside charge_ki_bar(): None, "controller" or "healing".
_CHARGING_NOW = None
KI_CHARGE_TARGETS = {"second": 2.0, "third": 3.0}
KI_CHARGE_FULL_MARGIN = 0.03
# Recharge once the bar has dropped a whole layer below the target. LSSJ never
# drains below the second layer, so "all the way down" alone would never fire.
KI_CHARGE_RECHARGE_MARGIN = 0.95
KI_CHARGE_STALL_SEC = 3.0       # some forms may never reach the third layer
# Highest level a charge reached this run when it stalled below the target. The
# drained check measures from here, or a form capped at the second layer would
# look a layer short forever and step out of every minigame to charge.
_KI_CHARGE_CEILING = None
# Set once Ki Control is finished this run (see STOP_CHARGING_AFTER_KI_CONTROL).
_KI_CONTROL_DONE = False
# Health, Agility and Physical Damage keep the HUD up, so the bar is read live.
MID_STAT_CHARGE_POLL_SEC = 2.0
# After a step-out that couldn't charge, wait this long before trying again, so a
# blocked charge can't pull the player out of the minigame every few seconds.
MID_STAT_CHARGE_BACKOFF_SEC = 60.0
KI_CHARGE_MAX_SEC = 45.0
# Leaving training dims the screen for a moment, which hides the HUD.
KI_CHARGE_UNSEEN_SEC = 2.0
CHARGE_TO_TAB_SETTLE_SEC = 0.4


def _set_charge_hold(want):
    """Press or release the charge key (Shift). Idempotent."""
    global _CHARGE_HELD
    with _stop_input_gate:
        want = bool(want and not (_USER_STOP_LATCHED or UI_STOP_REQUESTED)
                    and _charge_wanted(CURRENT_TRAINING_STATE))
        if want == _CHARGE_HELD:
            return
        if want:
            _CHARGE_HELD = True
            try:
                pydirectinput.keyDown(CHARGE_KEY)
            except Exception:
                pydirectinput.keyUp(CHARGE_KEY)
                _CHARGE_HELD = False
                raise
        else:
            pydirectinput.keyUp(CHARGE_KEY)
            _CHARGE_HELD = False


def _charge_wanted(state):
    """Shift is held only while charge_ki_bar() is charging, never in a minigame."""
    if COMPATIBILITY_SETTINGS["charge_hold_mode"] == "off" or not _CHARGING_NOW:
        return False
    # Healing owns input once it claims it; only its own Ki-check charge may hold.
    if SENZU_CONTROLLER_ACTIVE.is_set() and _CHARGING_NOW != "healing":
        return False
    return (_game_has_focus() and not CONTROLLER_PAUSED and not PAUSE_TOGGLE_REQUESTED
            and not TRAINING_MENU_VISIBLE and not GC_RECOVERY_VERIFY_PENDING
            and _GAME_COVERED_SINCE is None)


def _ki_charge_level(sct):
    """Ki charge in layers (0.0-3.0) from the HUD, or None while the HUD is hidden."""
    if not training_ui.hud_visible(_grab_reference_box(sct, training_ui.HUD_LABELS_BOX)):
        return None
    return training_ui.read_ki_charge(_grab_reference_box(sct, training_ui.KI_BAR_BOX))


def _charge_interrupted():
    return (_USER_STOP_LATCHED or UI_STOP_REQUESTED or CONTROLLER_PAUSED
            or PAUSE_TOGGLE_REQUESTED or not _game_has_focus()
            or _GAME_COVERED_SINCE is not None)


def _mark_ki_control_done():
    global _KI_CONTROL_DONE
    _KI_CONTROL_DONE = True
    if STOP_CHARGING_AFTER_KI_CONTROL and COMPATIBILITY_SETTINGS["charge_hold_mode"] != "off":
        print("[CHARGE] Ki Control is done; no more charging this run")


def _ki_charge_drained(level, target):
    ceiling = min(target, _KI_CHARGE_CEILING) if _KI_CHARGE_CEILING is not None else target
    return level <= ceiling - KI_CHARGE_RECHARGE_MARGIN


def charge_ki_bar(sct, reason, only_if_drained=False, owner="controller"):
    """Hold Shift out of training until the Ki bar reaches the chosen layer.

    Only runs where the HUD shows the bar (out in the room, not in a minigame).
    only_if_drained: charge only once the bar is a whole layer below the target.
    Returns True when Shift was held.
    """
    global _CHARGING_NOW, _KI_CHARGE_CEILING
    mode = COMPATIBILITY_SETTINGS["charge_hold_mode"]
    if mode == "off" or not _new_game_ui() or _charge_interrupted():
        return False
    if STOP_CHARGING_AFTER_KI_CONTROL and _KI_CONTROL_DONE:
        return False
    target = KI_CHARGE_TARGETS[mode]
    level = _ki_charge_level(sct)
    if level is None:
        print(f"[CHARGE] {reason}: Ki bar not visible; skipped")
        return False
    if (level >= target - KI_CHARGE_FULL_MARGIN
            or (only_if_drained and not _ki_charge_drained(level, target))):
        return False
    print(f"[CHARGE] {reason}: Ki {level:.2f} of {target:.0f} layers; charging")
    started = best_at = seen_at = time.monotonic()
    best = level
    _CHARGING_NOW = owner
    try:
        _set_charge_hold(True)
        if not _CHARGE_HELD:
            # Held off on purpose (e.g. checking progress right after a respawn).
            print(f"[CHARGE] {reason}: charging isn't allowed right now; skipped")
            return False
        while _CHARGE_HELD and not _charge_interrupted():
            time.sleep(0.2)
            now = time.monotonic()
            reading = _ki_charge_level(sct)
            if reading is None:
                if now - seen_at >= KI_CHARGE_UNSEEN_SEC:
                    print("[CHARGE] Ki bar went out of view; stopped charging")
                    break
                continue
            if now - seen_at > 0.5:
                best_at = now   # the stall clock only counts time the bar was visible
            seen_at = now
            level = reading
            if level >= target - KI_CHARGE_FULL_MARGIN:
                break
            if level > best + 0.02:
                best, best_at = level, now
            elif now - best_at >= KI_CHARGE_STALL_SEC:
                print(f"[CHARGE] Ki stopped rising at {level:.2f}; this form may not go higher")
                _KI_CHARGE_CEILING = level
                break
            if now - started >= KI_CHARGE_MAX_SEC:
                print("[CHARGE] Charging took too long; carrying on")
                break
    finally:
        _CHARGING_NOW = None
        _set_charge_hold(False)
    print(f"[CHARGE] Ki {level:.2f} after {time.monotonic() - started:.1f}s")
    return True


def _tap_key_unchecked(key, *, minigame=False, wait_for_cover=True):
    # Menu actions must survive a slow game frame; minigame timing stays at 30ms.
    # Callers already holding _stop_input_gate pass wait_for_cover=False and wait
    # before taking it: waiting with the gate held would block Stop.
    if wait_for_cover:
        _wait_while_game_covered()
    with _stop_input_gate:
        if _controller_decisions_suspended():
            raise SenzuControllerPause()
        if MAJIN_CONTROLLER_ACTIVE:
            _majin_check()
        if _USER_STOP_LATCHED:
            return False
        if not minigame:
            # Tab/H/M/G with Shift held become different shortcuts; the
            # controller loop re-holds the charge once training is back.
            _set_charge_hold(False)
        try:
            pydirectinput.keyDown(key)
            time.sleep(0.030 if minigame else UTILITY_KEY_HOLD_SEC)
        finally:
            pydirectinput.keyUp(key)
    time.sleep(KEY_PRESS_DELAY if minigame else UTILITY_KEY_GAP_SEC)
    return True


def hardware_tap(key):
    check_exit()
    with _input_lock:
        # Auto-Senzu may have started while this thread waited for the lock.
        check_exit()
        if not _ensure_game_focus_for_input():
            return False
        _tap_key_unchecked(key, minigame=key in ("w", "a", "s", "d", "f"))
    check_exit()
    return True

# ─── Mouse input — absolute-coordinate SendInput ──────────────────────────────
# Roblox anti-cheat silently drops mouse_event() clicks unless they're preceded
# by an absolute-coord SendInput MOVE:
#   SetCursorPos → 20ms wait → SendInput MOVE (ABSOLUTE) → 10ms → SendInput MOVE
#   again → 10ms → SendInput LEFTDOWN+LEFTUP (ABSOLUTE).
# The double MOVE is non-obvious but Roblox-specific: one MOVE isn't always
# registered by the in-game cursor layer.
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


def _effective_click_button():
    button = COMPATIBILITY_SETTINGS["mouse_click_button"]
    if button == "windows":
        return "right" if _user32.GetSystemMetrics(23) else "left"
    return button


def _mouse_button_flags():
    # Resolve once for each down/up pair, even if settings change mid-click.
    return (0x0008, 0x0010) if _effective_click_button() == "right" else (0x0002, 0x0004)


def _send_click_buttons():
    down, up = _mouse_button_flags()
    packets = (_INPUT * 2)(_make_mouse_input(down), _make_mouse_input(up))
    sent = _user32.SendInput(2, packets, _ctypes.sizeof(_INPUT))
    if sent != 2:
        # Partial injection may have delivered only DOWN. Release the same button.
        release = (_INPUT * 1)(_make_mouse_input(up))
        _user32.SendInput(1, release, _ctypes.sizeof(_INPUT))
        raise RuntimeError("Windows did not accept the complete mouse click")


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
    _send_click_buttons()


def _click_sendinput_abs(x, y):
    with _stop_input_gate:
        if _controller_decisions_suspended():
            raise SenzuControllerPause()
        if MAJIN_CONTROLLER_ACTIVE:
            _majin_check()
        if _USER_STOP_LATCHED:
            return False
        _click_sendinput_abs_unchecked(x, y)
    return True


def _click_setcursor_mouseevent(x, y):
    """Legacy: SetCursorPos + mouse_event with relative (0,0). What this build used before — Roblox often drops these silently."""
    win32api.SetCursorPos((int(x), int(y)))
    time.sleep(0.01)
    down, up = _mouse_button_flags()
    try:
        win32api.mouse_event(down, 0, 0)
        time.sleep(0.005)
    finally:
        win32api.mouse_event(up, 0, 0)


def _click_setcursor_only(x, y):
    """Debug: move only, no click. Use to see if the cursor actually goes where expected."""
    win32api.SetCursorPos((int(x), int(y)))


def click_at(x, y, method=None):
    """Dispatch a click at screen-space (x, y) via the configured method."""
    method = method or MOUSE_METHOD
    with _input_lock:
        if MAJIN_CONTROLLER_ACTIVE:
            _majin_check()
        if _controller_decisions_suspended():
            raise SenzuControllerPause()
        if not _ensure_game_focus_for_input():
            raise RuntimeError("Roblox is not foreground for click")
        with _stop_input_gate:
            if _controller_decisions_suspended():
                raise SenzuControllerPause()
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


def click_current_position(expected_x=None, expected_y=None, tolerance=3):
    """Click in place, refusing if the cursor moved away from an expected target."""
    with _input_lock:
        if _controller_decisions_suspended():
            raise SenzuControllerPause()
        if not _ensure_game_focus_for_input():
            raise RuntimeError("Roblox is not foreground for click")
        with _stop_input_gate:
            if _USER_STOP_LATCHED:
                raise QuitException()
            if expected_x is not None and expected_y is not None:
                cursor_x, cursor_y = win32api.GetCursorPos()
                if (
                    abs(int(cursor_x) - int(expected_x)) > int(tolerance)
                    or abs(int(cursor_y) - int(expected_y)) > int(tolerance)
                ):
                    return False
            _send_click_buttons()
            return True


def _nudge_pointer():
    """Relative +1/-1 px wiggle so a raw-input reader (Roblox's own cursor)
    catches up with an absolute move it would otherwise ignore."""
    nudge_out = (_INPUT * 1)(_make_mouse_input(_MOUSEEVENTF_MOVE, 1, 0))
    nudge_back = (_INPUT * 1)(_make_mouse_input(_MOUSEEVENTF_MOVE, -1, 0))
    _user32.SendInput(1, nudge_out, _ctypes.sizeof(_INPUT))
    time.sleep(0.005)
    _user32.SendInput(1, nudge_back, _ctypes.sizeof(_INPUT))


def robust_move(x, y, nudge=False):
    """Move the cursor without clicking. Uses SendInput-absolute so Roblox notices the move.

    nudge adds a relative wiggle for callers that need the game to re-read the
    cursor, such as the gravity label reads (Roblox's cursor drawn over the label
    hides the digits)."""
    check_exit()
    with _stop_input_gate:
        if _controller_decisions_suspended():
            raise SenzuControllerPause()
        if _USER_STOP_LATCHED:
            raise QuitException()
        nx, ny = _absolute_virtual_coords(x, y)
        _user32.SetCursorPos(int(x), int(y))
        time.sleep(0.01)
        if nudge:
            _nudge_pointer()
        move = (_INPUT * 1)(_make_mouse_input(
            _MOUSEEVENTF_MOVE | _MOUSEEVENTF_ABSOLUTE | _MOUSEEVENTF_VIRTUALDESK,
            nx,
            ny,
        ))
        _user32.SendInput(1, move, _ctypes.sizeof(_INPUT))
    time.sleep(0.005)
    check_exit()


def approach_cursor(x, y, monitor):
    """Walk the cursor to (x, y) instead of teleporting onto it.

    Reported from the field: the cursor lands on the trait, the trait highlights,
    and the click does nothing — every trait, three retries, and an external auto
    clicker at the same spot works. That is not a coordinate problem. DBOG reads
    the cursor through raw input, and a single absolute jump gives it no motion to
    follow, so its pointer can still be wherever the cursor was before.

    Passing through the window centre first produces two real moves with a nudge
    on each, which is what the game needs to see to agree where the cursor is.
    """
    centre_x = monitor["left"] + monitor["width"] // 2
    centre_y = monitor["top"] + monitor["height"] // 2
    for point_x, point_y in ((centre_x, centre_y), (x, y)):
        robust_move(int(point_x), int(point_y), nudge=True)
        safe_sleep(TRAIT_CLICK_APPROACH_PAUSE_SEC)


def _trait_click_uses_approach(attempt):
    """Retries always approach; the saved setting only changes the first click."""
    return int(attempt) > 1 or TRAIT_CLICK_APPROACH_ENABLED


def _remember_trait_click_approach():
    """Make a confirmed approach-based selection the default for later runs."""
    global TRAIT_CLICK_APPROACH_ENABLED
    if TRAIT_CLICK_APPROACH_ENABLED:
        return
    with _config_lock:
        if TRAIT_CLICK_APPROACH_ENABLED:
            return
        TRAIT_CLICK_APPROACH_ENABLED = True
        save_master_config()
    print("[TRAIT CLICK] Saved Approach first after confirmed retry")


def _confirm_trait_click(category, log_prefix, used_approach):
    """Record only a stable menu disappearance, never a hover/highlight."""
    print(f"{log_prefix} {category} selection confirmed")
    if used_approach:
        _remember_trait_click_approach()


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
        if not hardware_tap('f'):
            return
        TELEMETRY["health_hits"] += 1
        if not COMPATIBILITY_SETTINGS["restart_health_after_hit"]:
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
    scale_x = monitor["width"] / GAME_REFERENCE_WIDTH
    scale_y = monitor["height"] / GAME_REFERENCE_HEIGHT
    scale_area = scale_x * scale_y
    green = cv2.inRange(hsv, np.array([40, 120, 120]), np.array([80, 255, 255]))
    contours, _ = cv2.findContours(green, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    candidates = []
    for contour in contours:
        x, y, w, h = cv2.boundingRect(contour)
        if w * h >= max(80, int(round(300 * scale_area))) \
                and h >= max(8, int(round(20 * scale_y))):
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
        and max(6, int(round(12 * scale_x))) <= len(run)
        <= int(roi["width"] * 0.20)
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
    now = time.monotonic()
    if (_HEALTH_V2_STATE["armed"] and hit_left <= marker_center <= hit_right
            and now - _HEALTH_V2_STATE["last_hit"] >= HEALTH_HIT_COOLDOWN_SEC):
        print(">>> [HEALTH v2] Hit")
        if not hardware_tap('f'):
            return
        TELEMETRY["health_hits"] += 1
        _HEALTH_V2_STATE["armed"] = False
        _HEALTH_V2_STATE["last_hit"] = now


_health_last_error_log_ts = None


def logic_health(sct, monitor):
    global _health_last_error_log_ts
    try:
        if _new_game_ui():
            _logic_health_v3(sct, monitor)
        elif HEALTH_MODE == "v2_track":
            _logic_health_v2(sct, monitor)
        else:
            _logic_health_v1(sct, monitor)
    except (QuitException, SkipMinigameException):
        raise
    except Exception as e:
        now = time.monotonic()
        if _health_last_error_log_ts is None or now - _health_last_error_log_ts >= 5.0:
            _health_last_error_log_ts = now
            print(f"[HEALTH] {type(e).__name__}: {e}")

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
        _agility_chain_yellow_ts = time.monotonic()
        return False, None
    now = time.monotonic()
    if absent_since is None:
        return False, now
    if now - absent_since >= NO_YELLOW_TIMEOUT_SEC:
        print(f"[WASD] No yellow for {now - absent_since:.1f}s between sequences — set complete, yielding for switch")
        return True, absent_since
    return False, absent_since


AGILITY_NO_LETTERS_LOG_SEC = 5.0
_agility_no_letters_since = None
_agility_no_letters_logged_at = 0.0
_agility_last_scan_at = 0.0


def _agility_best_letter_score(sct, templates):
    """Best raw template score in the Agility box, for the no-letters diagnostic."""
    try:
        img_gray = cv2.cvtColor(_grab_reference_box(sct, AGILITY_BOX), cv2.COLOR_BGR2GRAY)
    except Exception:
        return None
    best = 0.0
    for tpl, _char in templates:
        if tpl.shape[0] > img_gray.shape[0] or tpl.shape[1] > img_gray.shape[1]:
            continue
        _, score, _, _ = cv2.minMaxLoc(cv2.matchTemplate(img_gray, tpl, cv2.TM_CCOEFF_NORMED))
        best = max(best, float(score))
    return best


def _note_agility_letter_scan(sct, templates, state, found):
    """Log once per AGILITY_NO_LETTERS_LOG_SEC while Agility sees no letters.

    An empty scan returns silently, so a misplaced Agility region or a letter
    style the templates don't match used to look like the macro doing nothing.
    """
    global _agility_no_letters_since, _agility_no_letters_logged_at, _agility_last_scan_at
    now = time.monotonic()
    # A long gap means another stat, menu or recovery ran in between; start over.
    stale = now - _agility_last_scan_at > 2.0
    _agility_last_scan_at = now
    if found:
        _agility_no_letters_since = None
        return
    if _agility_no_letters_since is None or stale:
        _agility_no_letters_since = now
        return
    missing_for = now - _agility_no_letters_since
    if (missing_for >= AGILITY_NO_LETTERS_LOG_SEC
            and now - _agility_no_letters_logged_at >= AGILITY_NO_LETTERS_LOG_SEC):
        _agility_no_letters_logged_at = now
        best = _agility_best_letter_score(sct, templates)
        best_text = "unreadable" if best is None else f"{best:.2f}"
        print(
            f"[WASD] {state}: no letters found for {missing_for:.0f}s "
            f"(best match {best_text}, needs {AGILITY_CONFIDENCE:.2f}; "
            f"region {AGILITY_BOX['left']},{AGILITY_BOX['top']} "
            f"{AGILITY_BOX['width']}x{AGILITY_BOX['height']})"
        )


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
    _note_agility_letter_scan(sct, templates, state, bool(letters))
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
            t_press = time.monotonic()
            t_window_start = t_press
            _ = False

            while True:
                check_exit()
                st = _letter_state_at(sct, AGILITY_BOX, cx, cy)
                if st == 'green':
                    _ = True
                    TELEMETRY["wasd_greens"] += 1
                    total_ms = (time.monotonic() - t_press) * 1000.0
                    extra = f" (attempt {attempt})" if attempt > 1 else ""
                    print(f"    [WASD v2] {char.upper()} green in {total_ms:.0f}ms{extra}")
                    break
                if st == 'red':
                    TELEMETRY["wasd_reds"] += 1
                    print(f"    [WASD v2] FAIL — {char.upper()} red, abandoning sequence")
                    sequence_failed = True
                    break

                elapsed = time.monotonic() - t_window_start
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
                    t_window_start = time.monotonic()
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
    _note_agility_letter_scan(sct, templates, state, bool(letters))
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
            t0 = time.monotonic()
            confirmed = False
            repressed = False
            while time.monotonic() - t0 < AGILITY_PER_LETTER_TIMEOUT_SEC * 2:
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
                if not repressed and (time.monotonic() - t0) > AGILITY_PER_LETTER_TIMEOUT_SEC:
                    hardware_tap(char)
                    repressed = True
                time.sleep(0.005)

            if not confirmed:
                print(f"    [WASD v3] {char.upper()} never confirmed green, continuing")
            else:
                ms = (time.monotonic() - t0) * 1000.0
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

    With KI_V8_ADAPTIVE_BRIGHTNESS on, "dark" is measured against the crop's own
    median brightness rather than a fixed value, so a display pipeline that lifts
    brightness doesn't push the stroke out of range.
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
    max_channel = patch.max(axis=2)
    dark_thresh = KI_V8_DARK_THRESH
    if KI_V8_ADAPTIVE_BRIGHTNESS:
        dark_thresh = max(
            dark_thresh, int(np.median(max_channel) * KI_V8_ADAPTIVE_DARK_FRAC)
        )
    dark_mask = (max_channel < dark_thresh).astype(np.uint8)
    col_sums = dark_mask.sum(axis=0)
    min_h = int(patch.shape[0] * KI_V8_DIGIT_DARK_FRAC_MIN)
    n_dark_cols = int((col_sums >= min_h).sum())
    passed = KI_V8_DIGIT_WIDTH_MIN <= n_dark_cols <= KI_V8_DIGIT_WIDTH_MAX
    if not passed:
        _record_ki_digit_rejection(max_channel, min_h, dark_thresh, n_dark_cols, dot_r)
    return passed


# Set every time the digit check rejects a dot; read by the bug report. This is
# what identifies a capture that arrives brighter than the detector expects —
# "needed 129, using 90" says it in one line, where a screenshot only shows a
# picture that looks fine.
_ki_v8_last_digit_rejection = None


def _record_ki_digit_rejection(max_channel, min_h, dark_thresh, n_dark_cols, dot_r):
    global _ki_v8_last_digit_rejection
    needed = None
    if min_h >= 1:
        # Per column, the value the (min_h)-th darkest pixel sits at: the
        # threshold at which that column would start counting as the stroke.
        # The smallest across columns is what the check as a whole needs.
        ranked = np.sort(max_channel, axis=0)
        needed = int(ranked[min_h - 1, :].min()) + 1
    _ki_v8_last_digit_rejection = {
        "at": time.time(),
        "dot_r": int(dot_r),
        "columns": n_dark_cols,
        "threshold": int(dark_thresh),
        "needed": needed,
        "dot_brightness": int(np.median(max_channel)),
        "adaptive": bool(KI_V8_ADAPTIVE_BRIGHTNESS),
    }


_ki_v8_no_dot_last_log_ts = 0.0
_ki_v8_no_dot_since = None


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


_ki_last_error_log_ts = None


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
    global _ki_v8_no_dot_last_log_ts, _ki_v8_no_dot_since, _ki_last_error_log_ts
    try:
        check_exit()
        now = time.monotonic()
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
            now2 = time.monotonic()
            if _ki_v8_no_dot_since is None:
                _ki_v8_no_dot_since = now2
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
                if VERBOSE_DETECTOR_LOGS or now2 - _ki_v8_no_dot_since > 5.0:
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

        _ki_v8_no_dot_since = None
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

        screen_cx = cx + monitor["left"]
        screen_cy = cy + monitor["top"]
        if _ki_click_blocked(screen_cx, screen_cy):
            _ki_v8_state["consecutive_seen"] = 0
            _ki_v8_state["last_dot"] = None
            return
        TELEMETRY["ki_dots_found"] += 1

        def _commit_click(label):
            print(f">>> [KI v8] Click ({label}): ({screen_cx}, {screen_cy}) r={dot_r}")
            _ki_click(screen_cx, screen_cy)
            _ki_v8_state["last_click_at"] = time.monotonic()
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
            t_start = time.monotonic()
            while time.monotonic() - t_start < KI_V8_V2_TIMEOUT_SEC:
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
                t_ms = int((time.monotonic() - t_start) * 1000)
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
        now = time.monotonic()
        if _ki_last_error_log_ts is None or now - _ki_last_error_log_ts >= 5.0:
            _ki_last_error_log_ts = now
            import traceback
            print(f"[KI v8] Error: {e}")
            for ln in traceback.format_exc().splitlines()[-4:]:
                if ln.strip(): print(f"[KI v8] {ln}")


class GcRoomWatch:
    """Notice the GC session ending under a running plan.

    Live 2026-10-04: the GC timer ran out mid-Ki Control and the game teleported
    the player; the stale no-yellow fallback then reported the plan as done.
    The room detector stayed true through every heal and menu in that session.
    """
    CHECK_EVERY_SEC = 1.0
    MISSING_FOR_SEC = 8.0

    def __init__(self):
        self.last_check = 0.0
        self.missing_since = None

    def left_room(self, sct, monitor, now):
        if not GC_RUN_CONFIRMED or now - self.last_check < self.CHECK_EVERY_SEC:
            return False
        self.last_check = now
        frame = _grab_reference_box(sct, (0, 0, 1920, 1080), monitor)
        # Death has its own recovery; it is not the session ending.
        if (gc_recovery.room_visible(frame) or _gc_banner_or_menu_visible(frame)
                or _gc_death_dialog_visible(sct, monitor)):
            self.missing_since = None
            return False
        if self.missing_since is None:
            self.missing_since = now
        return now - self.missing_since >= self.MISSING_FOR_SEC


_GC_BANNER_TEMPLATE = []


def _gc_banner_or_menu_visible(frame):
    """October 2026 UI: the GC banner, or the Training menu that hides it.

    The room's blue windows that room_visible() needs are out of view when
    the camera looks down at the floor (live 2026-10-13).
    """
    if not _new_game_ui():
        return False
    if not _GC_BANNER_TEMPLATE:
        _GC_BANNER_TEMPLATE.append(training_ui.load_gc_banner())
    if training_ui.gc_banner_visible(training_ui.crop(frame, training_ui.GC_BANNER_BOX),
                                     _GC_BANNER_TEMPLATE[0]):
        return True
    heading = _training_menu_template()
    if heading is None:
        return False
    menu = cv2.cvtColor(training_ui.crop(frame, training_ui.MENU_HEADING_BOX), cv2.COLOR_BGR2GRAY)
    return cv2.minMaxLoc(cv2.matchTemplate(menu, heading, cv2.TM_CCOEFF_NORMED))[1] >= training_ui.MENU_HEADING_MIN


def _stop_for_leaving_gc_room():
    global UI_STOP_REQUESTED, _AFTER_ACTIONS_BLOCKED
    # Out of the GC there is no gravity to lower, and menu keys would land in
    # the overworld.
    _AFTER_ACTIONS_BLOCKED = True
    UI_STOP_REQUESTED = True
    _record_run_outcome(
        "error", "Left the GC training room (the GC time limit ran out or the game moved you)")
    _release_macro_input()
    print("[SAFETY] GC training room gone for 8s (time limit or teleport); stopping")


# Reference-space boxes a Ki click must never land on (x, y, width, height).
# Live 2026-10-04: dots over the open Training Mode menu got clicked, raising
# GC gravity 10G -> 30G through its button and reselecting stats; the top-left
# Roblox buttons open chat or the Roblox menu.
KI_CLICK_BLOCKED_BOXES = (
    (0, 0, 230, 70),       # Roblox menu, chat and voice buttons
    (0, 255, 665, 580),    # Training Mode prompt, gravity button and stat list
)


def _ki_click_blocked(screen_x, screen_y):
    reference_x = (screen_x - GAME_OFFSET_X) * 1920 / max(1, GAME_WIDTH)
    reference_y = (screen_y - GAME_OFFSET_Y) * 1080 / max(1, GAME_HEIGHT)
    return any(x <= reference_x < x + width and y <= reference_y < y + height
               for x, y, width, height in KI_CLICK_BLOCKED_BOXES)


def _ki_click(screen_cx, screen_cy):
    # One-call atomic click: SendInput pipeline (move + click) at the target coords.
    # Previously was two phases (SetCursorPos then mouse_event(0,0)); Roblox often
    # accepted the cursor move but silently dropped the click.
    click_at(int(screen_cx), int(screen_cy))


# --- October 2026 minigames (see training_ui for what each reader measures) ---

HEALTH_V3_INPUT_LEAD_SEC = 0.02   # capture age + key-down, at ~650 px/s marker speed
_HEALTH_V3_STATE = {"marker": None, "seen_at": 0.0, "velocity": 0.0, "pressed_at": 0.0,
                     "frame_interval": 0.02}
HEALTH_V3_MAX_WAIT_SEC = 0.05   # never hold the loop longer than this waiting for the centre


def _logic_health_v3(sct, monitor):
    """Tap F while the sweeping white marker is inside the gold zone."""
    reading = training_ui.health_bar(
        _grab_reference_box(sct, training_ui.HEALTH_BAR_BOX, geometry=monitor))
    memo = _HEALTH_V3_STATE
    now = time.monotonic()
    if reading is None:
        memo.update(marker=None, velocity=0.0)
        return
    marker, zone = reading
    elapsed = now - memo["seen_at"]
    if memo["marker"] is not None and 0 < elapsed < 0.2:
        memo["velocity"] = (marker - memo["marker"]) / elapsed
        memo["frame_interval"] += (elapsed - memo["frame_interval"]) * 0.2
    else:
        # A stale speed would turn a hit's frozen marker into a second press.
        memo["velocity"] = 0.0
    memo.update(marker=marker, seen_at=now)
    # A hit freezes the marker inside the zone until the next round; a second
    # F there would count as a miss.
    if abs(memo["velocity"]) < 100 or now - memo["pressed_at"] < HEALTH_HIT_COOLDOWN_SEC:
        return
    delay = training_ui.health_press_delay(
        marker, zone, memo["velocity"], HEALTH_V3_INPUT_LEAD_SEC, memo["frame_interval"])
    if delay is None:
        return
    if delay > 0:
        time.sleep(min(delay, HEALTH_V3_MAX_WAIT_SEC))
    print(">>> [HEALTH] Hit")
    if hardware_tap("f"):
        memo["pressed_at"] = now + delay
        TELEMETRY["health_hits"] += 1


_AGILITY_V4_STATE = {"key": None, "cleared": True, "pressed_at": 0.0, "x": None}
_AGILITY_V4_TEMPLATES = None
AGILITY_SLOT_SHIFT_PX = 25   # letters sit ~70 px apart; a move this big is the next letter


def logic_agility_v4(sct):
    """Press the current letter of the round; it turns green and the next one lights up."""
    global _AGILITY_V4_TEMPLATES
    if _AGILITY_V4_TEMPLATES is None:
        _AGILITY_V4_TEMPLATES = training_ui.load_agility_templates()
    key, _score, centre_x = training_ui.agility_letter_at(
        _grab_reference_box(sct, training_ui.AGILITY_LETTER_BOX), _AGILITY_V4_TEMPLATES)
    memo = _AGILITY_V4_STATE
    if key is None:
        memo["cleared"] = True
        return
    now = time.monotonic()
    # The same letter again is a new one only once the highlight has moved to the
    # next slot (or the letters blanked between rounds). Without the position, the
    # second D of "W D D" waited for the 0.6 s fallback: a quarter of live run time.
    moved = (centre_x is not None and memo.get("x") is not None
             and abs(centre_x - memo["x"]) > AGILITY_SLOT_SHIFT_PX)
    if key == memo["key"] and not memo["cleared"] and not moved and now - memo["pressed_at"] < 0.6:
        return
    print(f">>> [AGILITY] {key.upper()} ({_score:.2f})")
    if hardware_tap(key):
        memo.update(key=key, cleared=False, pressed_at=now, x=centre_x)
        TELEMETRY["wasd_greens"] += 1


KI_V9_RING_SHRINK_PER_SEC = 2.2
# When the button press should land after the ring meets the circle (fitted close).
# 120 fps capture of a live run: PERFECT presses landed +23..+143 ms,
# median 114, all 47 of 48 presses at +70..+143 PERFECT; GOOD/BAD were all early.
# The late edge of PERFECT is unmeasured beyond +143, so aim inside the seen range.
KI_V9_LAND_AFTER_CLOSE = 0.11
KI_V9_LAND_LATEST = KI_V9_LAND_AFTER_CLOSE + 0.06


class _KiDeadlineScheduler:
    """Clock-free tracker/queue; the single worker owns deadline dispatch."""

    def __init__(self):
        self.clear()

    def clear(self):
        self.tracks = []
        self.clicked = []
        self.captured_at = None

    def observe(self, captured_at, circles, now, input_latency):
        if self.captured_at is not None and captured_at <= self.captured_at:
            return
        self.captured_at = captured_at
        available = list(self.tracks)
        self.tracks = []
        self.clicked = [c for c in self.clicked if now-c[2] < .5]
        for x, y, radius, ring in circles:
            if not all(math.isfinite(v) for v in (x, y, radius)) or radius <= 0:
                continue
            matches = [tr for tr in available
                       if math.hypot(x-tr['x'], y-tr['y']) <= max(12., radius*.35)
                       and abs(radius/tr['radius']-1) <= .18]
            if matches:
                track = min(matches, key=lambda tr: math.hypot(x-tr['x'], y-tr['y']))
                available.remove(track)
            else:
                if any(math.hypot(x-cx, y-cy) < radius for cx, cy, _ in self.clicked):
                    continue
                track = dict(x=x, y=y, radius=radius, radii=[], samples=[], close=None,
                             deadline=None, fired=False)
            # Re-normalize every sample against one stable fill radius, so
            # contour jitter cannot masquerade as ring acceleration.
            track['radii'].append(radius)
            del track['radii'][:-8]
            radii = sorted(track['radii'])
            middle = len(radii)//2
            track['radius'] = (radii[middle]+radii[(len(radii)-1)//2])/2
            track['seen_at'] = captured_at
            self.tracks.append(track)
            if track['fired'] or ring is None or not math.isfinite(ring):
                continue
            ratio = ring/track['radius']
            if not 1 <= ratio <= 3.2:
                continue
            samples = track['samples']
            # Near closure the reader sees the stationary disc border. Keep the
            # earlier shrink fit instead of fitting that border's zero velocity.
            if track['close'] is not None and ratio < 1.30:
                continue
            if samples and ring > samples[-1][1]+.12*track['radius']:
                # A new ring at the same centre must not inherit the old click.
                track.update(close=None, deadline=None)
                samples.clear()
                continue
            samples.append((captured_at, ring))
            del samples[:-12]
            speed = KI_V9_RING_SHRINK_PER_SEC
            close = captured_at+(ratio-1)/speed
            if len(samples) >= 2 and samples[-1][0]-samples[0][0] >= .035:
                origin = samples[0][0]
                times = [s[0]-origin for s in samples]
                mean_t = sum(times)/len(times)
                mean_r = sum(s[1]/track['radius'] for s in samples)/len(samples)
                variance = sum((t-mean_t)**2 for t in times)
                slope = sum((t-mean_t)*(s[1]/track['radius']-mean_r) for t, s in zip(times, samples))/variance
                if .8 <= -slope <= 4.0:
                    speed = -slope
                    close = origin+mean_t+(mean_r-1)/speed
                elif track['close'] is not None:
                    continue
            track['close'] = close
            track['deadline'] = max(now, close+KI_V9_LAND_AFTER_CLOSE-input_latency)

    def next_deadline(self):
        deadlines = [tr['deadline'] for tr in self.tracks
                     if not tr['fired'] and tr['deadline'] is not None]
        return min(deadlines) if deadlines else None

    def pop_due(self, now, input_latency):
        due = [tr for tr in self.tracks if not tr['fired']
               and tr['deadline'] is not None and tr['deadline'] <= now]
        if not due:
            return None
        track = min(due, key=lambda tr: tr['deadline'])
        track['fired'] = True
        # A delayed worker/input lock must never turn a stale target into input.
        if now-track['seen_at'] > .4 or now+input_latency > track['close']+KI_V9_LAND_LATEST:
            return None
        self.clicked.append((track['x'], track['y'], now))
        return track


_KI_V9_STATE = {'label_seen_at': None, 'context': None, 'input_latency': .055,
                'input_method': None, 'restart_pending': False, 'error': None}
_KI_V9_SCHEDULER = _KiDeadlineScheduler()
_KI_V9_CONDITION = threading.Condition(threading.RLock())
_KI_V9_WORKER = None


def _reset_ki_v9():
    with _KI_V9_CONDITION:
        _KI_V9_SCHEDULER.clear()
        _KI_V9_STATE.update(label_seen_at=None, context=None, restart_pending=False, error=None)
        training_ui._TOUCHING_CACHE.update(at=-1., circles=[])
        _KI_V9_CONDITION.notify_all()


def _ki_v9_context_valid(context):
    return bool(context is not None
                and context == (PROGRESSION_GENERATION, CURRENT_TRAINING_STATE,
                                (GAME_OFFSET_X, GAME_OFFSET_Y, GAME_WIDTH, GAME_HEIGHT), MOUSE_METHOD)
                and CURRENT_TRAINING_STATE in ('Ki Control', 'Ki Damage')
                and ENABLE_KI_MINIGAME and _new_game_ui()
                and not (_USER_STOP_LATCHED or UI_STOP_REQUESTED or CONTROLLER_PAUSED
                         or PAUSE_TOGGLE_REQUESTED or MANUAL_NEXT_REQUESTED
                         or MAJIN_CONTROLLER_ACTIVE or _sidecar_closing
                         or _GAME_COVERED_SINCE is not None)
                and not _controller_decisions_suspended()
                and not PROGRESSION_COMPLETE_REQUESTED.is_set())


def _ki_v9_dispatch_due():
    # Same lock order as click_at. Reset/state changes and Stop are serialized
    # with the final validation and click, including time spent waiting for input.
    with _input_lock, _stop_input_gate, _KI_V9_CONDITION:
        memo = _KI_V9_STATE
        if not _ki_v9_context_valid(memo['context']):
            _KI_V9_SCHEDULER.clear()
            return
        now = time.perf_counter()
        if memo['label_seen_at'] is None or now-memo['label_seen_at'] > 1.5:
            _KI_V9_SCHEDULER.clear()
            return
        track = _KI_V9_SCHEDULER.pop_due(now, memo['input_latency'])
        if track is None:
            return
        screen_x, screen_y = _reference_point(track['x'], track['y'], memo['context'][2])
        if _ki_click_blocked(screen_x, screen_y) or not _game_has_focus():
            _KI_V9_SCHEDULER.clear()
            return
        try:
            check_exit()
            started = time.perf_counter()
            _ki_click(screen_x, screen_y)
        except (QuitException, SkipMinigameException, SenzuControllerPause):
            _KI_V9_SCHEDULER.clear()
            return
        except Exception as error:
            # Let the controller handle/report input failures, not a silent dead thread.
            memo['error'] = error
            _KI_V9_SCHEDULER.clear()
            return
        elapsed = time.perf_counter()-started
        if MOUSE_METHOD == 'setcursor_mouseevent':
            elapsed = max(0., elapsed-.005)  # mouse-up dwell is after the accepted DOWN
        memo['input_latency'] += .25*(elapsed-memo['input_latency'])
        for pending in _KI_V9_SCHEDULER.tracks:
            if not pending['fired'] and pending['close'] is not None:
                pending['deadline'] = pending['close']+KI_V9_LAND_AFTER_CLOSE-memo['input_latency']
        memo['restart_pending'] = True
        TELEMETRY['ki_dots_found'] += 1
        TELEMETRY['ki_clicks'] += 1
        print(f'>>> [KI] Click ({screen_x}, {screen_y})')


def _ki_v9_worker():
    while True:
        with _KI_V9_CONDITION:
            if _sidecar_closing:
                return
            if not _ki_v9_context_valid(_KI_V9_STATE['context']):
                _KI_V9_SCHEDULER.clear()
            deadline = _KI_V9_SCHEDULER.next_deadline()
            if deadline is None:
                _KI_V9_CONDITION.wait(.1)
                continue
            remaining = deadline-time.perf_counter()
            if remaining > 0:
                _KI_V9_CONDITION.wait(min(remaining, .01))
                continue
        _ki_v9_dispatch_due()


def _start_ki_v9_worker():
    global _KI_V9_WORKER
    # Called with the condition held; resets reuse this one idle worker.
    if _KI_V9_WORKER is None or not _KI_V9_WORKER.is_alive():
        _KI_V9_WORKER = threading.Thread(target=_ki_v9_worker, name='ki-deadline', daemon=True)
        _KI_V9_WORKER.start()


def _grab_ki_frame(sct, geometry):
    """Full reference-size frame with only training_ui.KI_CAPTURE_BOX grabbed, the rest
    black (a ring sample there can't pass the bright-ring test)."""
    x, y, width, height = training_ui.KI_CAPTURE_BOX
    box = _grab_reference_box(sct, training_ui.KI_CAPTURE_BOX, geometry=geometry)
    frame = np.zeros((GAME_REFERENCE_HEIGHT, GAME_REFERENCE_WIDTH, 3), np.uint8)
    frame[y:y + height, x:x + width] = box[:height, :width, :3]
    return frame


def logic_ki_v9(sct, monitor):
    """Observe numbered circles; a separate worker dispatches their deadlines."""
    geometry = _game_geometry(monitor)
    context = (PROGRESSION_GENERATION, CURRENT_TRAINING_STATE, geometry, MOUSE_METHOD)
    captured_at = time.perf_counter()
    frame = _grab_ki_frame(sct, geometry)
    label = training_ui.crop(frame, training_ui.progression_label_box('Ki Control'))
    label_visible = (training_ui.progression_spans(label) is not None
                     or training_ui.ki_label_visible(label))
    with _KI_V9_CONDITION:
        memo = _KI_V9_STATE
        if memo.get('error') is not None:
            error, memo['error'] = memo['error'], None
            raise error
        if context != memo.get('context'):
            _KI_V9_SCHEDULER.clear()
            memo.update(context=context, label_seen_at=None, restart_pending=False)
        now = time.perf_counter()
        if label_visible:
            memo['label_seen_at'] = captured_at
        seen_at = memo.get('label_seen_at')
        if not _ki_v9_context_valid(context) or seen_at is None or now-seen_at > 1.5:
            _KI_V9_SCHEDULER.clear()
            _KI_V9_CONDITION.notify_all()
            return
        if memo.get('input_method') != MOUSE_METHOD:
            memo.update(input_method=MOUSE_METHOD,
                        input_latency=.01 if MOUSE_METHOD == 'setcursor_mouseevent' else .055)
    circles = [(x, y, radius, training_ui.ki_ring_radius(frame, x, y, radius))
               for x, y, radius in training_ui.find_ki_circles(frame)]
    with _KI_V9_CONDITION:
        if memo.get('context') != context or not _ki_v9_context_valid(context):
            return
        _KI_V9_SCHEDULER.observe(captured_at, circles, time.perf_counter(), memo['input_latency'])
        _start_ki_v9_worker()
        _KI_V9_CONDITION.notify_all()


## Progress template/OCR progression was removed (yellow pixel progression only).


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


def _stable_scan_letters(sct, templates, max_wait_sec, settle_ms=25):
    """Wait until two consecutive scans of AGILITY_BOX return matching letters.
    Filters out transient white-flash states between letters/strings.
    Returns the matched scan, or [] if max_wait_sec elapses."""
    t0 = time.monotonic()
    prev = []
    gap = settle_ms / 1000.0
    while time.monotonic() - t0 < max_wait_sec:
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
    global TRAINING_MENU_VISIBLE, SENZU_TRAIT_RESELECT_DEFERRED
    check_exit()
    if not GC_RECOVERY_VERIFY_PENDING:
        CONTROLLER_PAUSED = False
    TRAINING_MENU_VISIBLE = False
    if START_DELAY > 0:
        if SWITCH_TO_GAME_ON_START:
            # Show the game for the whole countdown, not only once it ends.
            focus_game_window()
        print(f"Starting in {_format_seconds(START_DELAY)}... (focus the game window)")
        safe_sleep(START_DELAY)
    # After the countdown: a death-screen Respawn is a click, and the player may
    # still be switching to Roblox.
    if _stop_if_starting_on_death_screen():
        return
    print("[XynMacro] macro loop started")

    # Settings are loaded before the UI server starts and live changes already
    # update these globals. Reloading here can overwrite a just-saved UI change.
    if _start_background_game_monitor() is False:
        raise RuntimeError(
            "Game-state monitor is unavailable. Wait a moment and try Start again."
        )

    agility_templates = []
    for char in ['w', 'a', 's', 'd']:
        tpl_path = os.path.join(BASE_DIR, f"tpl_{char}.png")
        if os.path.exists(tpl_path):
            base = cv2.imread(tpl_path, cv2.IMREAD_GRAYSCALE)
            for s in SCALES:
                agility_templates.append((cv2.resize(base, (int(base.shape[1]*s), int(base.shape[0]*s))), char))

    training_menu_template = _training_menu_template()
    if training_menu_template is None:
        print("[WARN] Training menu template missing; menu shadow detector disabled.")

    runtime_order = _sanitize_training_order(TRAINING_ORDER_CUSTOM)
    if not runtime_order:
        raise RuntimeError("Training Order is empty; add at least one stat before Start")

    current_idx = 0
    state = runtime_order[current_idx]
    skipped_stats = []
    capped_stats = {}

    if SHOW_DEBUG_HUD:
        cv2.namedWindow("BOT VISION HUD", cv2.WINDOW_NORMAL)
        cv2.resizeWindow("BOT VISION HUD", 400, 200)

    print(f"STARTING: {state}")
    print(f"[ORDER] {' -> '.join(runtime_order)}")

    def _build_monitor_from_game():
        """Build a capture rect only from a confirmed live Roblox client."""
        return _confirmed_game_capture_rect(), "game-window"

    with mss.MSS() as sct:
        monitor, monitor_source = _build_monitor_from_game()
        _gc_room_confirmed(sct, monitor)
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
        window_last_refresh = time.monotonic()

        gc_room_watch = GcRoomWatch()
        # Yellow tracking state
        yellow_last_poll = 0.0
        yellow_last_seen_ts = time.monotonic()
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

        def _click_trait_and_confirm(category, log_prefix, interrupt_check=None, panels=None):
            """Click a trait and require the menu to disappear stably."""
            nonlocal monitor, monitor_source
            for attempt in range(1, 4):
                if interrupt_check:
                    interrupt_check()
                monitor, monitor_source = _build_monitor_from_game()
                if attempt == 1:
                    if panels is None:
                        panels = _capture_points_panels(sct, category)
                    _record_points_target(category, panels)
                bx, by = _button_screen_point(category, monitor)
                focus_game_window()
                print(
                    f"{log_prefix} Clicking trait: {category} @ "
                    f"({int(bx)}, {int(by)}) attempt {attempt}/3"
                )
                used_approach = _trait_click_uses_approach(attempt)
                if used_approach:
                    print(f"{log_prefix} Approaching via centre before trait click")
                    approach_cursor(int(bx), int(by), monitor)
                    if interrupt_check:
                        interrupt_check()
                    if not click_current_position(int(bx), int(by)):
                        print(f"{log_prefix} Cursor moved before the trait click; retrying")
                        continue
                else:
                    click_at(int(bx), int(by))
                hidden_streak = 0
                deadline = time.monotonic() + 1.25
                while time.monotonic() < deadline:
                    if interrupt_check:
                        interrupt_check()
                    visible, _score = detect_training_menu(
                        sct, monitor, training_menu_template
                    )
                    hidden_streak = 0 if visible else hidden_streak + 1
                    if hidden_streak >= TRAINING_MENU_STABLE_FRAMES:
                        _confirm_trait_click(category, log_prefix, used_approach)
                        return True
                    safe_sleep(0.05)
                print(f"{log_prefix} Menu stayed open; retrying trait click")
            return False

        def _first_unfinished_stat(start_idx, log_prefix):
            """Index of the first stat from start_idx with training left, or None.

            Reads the open menu's Training Progression panel so a stat already at
            its target or out of Points Left is passed over without being clicked.
            An unreadable panel counts as unfinished; the in-game checks still apply.
            """
            if start_idx >= len(runtime_order):
                return None, None
            panels = _capture_points_panels(sct, runtime_order[start_idx])
            for idx in range(start_idx, len(runtime_order)):
                stat = runtime_order[idx]
                readings = {_read_training_points(panel, stat) for panel in panels}
                if len(panels) != 2 or len(readings) != 1 or None in readings:
                    return idx, panels
                count, target, points_left = next(iter(readings))
                _LATEST_POINTS[stat] = (count, target, points_left)
                if count < min(target, count + points_left):
                    return idx, panels
                print(f"{log_prefix} {stat}: {count}/{target} with {points_left} Points Left; "
                      "already done, skipping it")
                if stat == "Ki Control":
                    _mark_ki_control_done()
                if count < target:
                    capped_stats[stat] = "points_left"
            return None, None

        def _settle_after_select(stat):
            """Wait for the new minigame, up to the full after-switch wait.

            Ends early once the minigame shows on screen two reads in a row, so a
            quick game doesn't sit out the whole wait.
            """
            started = time.monotonic()
            deadline = started + 0.5 + NEW_GAME_WAIT
            safe_sleep(0.2)   # let the click land and the menu fade
            seen = 0
            while time.monotonic() < deadline:
                seen = seen + 1 if detect_yellow_progress(sct, stat, monitor)[0] else 0
                if seen >= 2:
                    print(f"[SWITCH] {stat} on screen after {int((time.monotonic() - started) * 1000)}ms")
                    return
                safe_sleep(0.05)

        # Initial Start is allowed from the open trait menu, Game Menu, or normal
        # gameplay. Confirm the menu before selecting the first trait so a blind
        # Health click can never land inside the game area.
        if state in BUTTONS:
            if SWITCH_TO_GAME_ON_START:
                focus_game_window()
            if _game_window_covering_title():
                print("[ERROR] Another application window is covering Roblox; move it off the game and Start again.")
                _record_run_outcome("error", "Another window is covering Roblox", state)
                return
            if HIDE_ROBLOX_CHAT_ON_START:
                _close_roblox_chat(sct, monitor)
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
                    if charge_ki_bar(sct, "Before training"):
                        # The game can drop a Tab sent the instant Shift comes up.
                        safe_sleep(CHARGE_TO_TAB_SETTLE_SEC)
                    menu_opened = False
                    for tab_attempt in (1, 2):
                        hardware_tap('tab')
                        t_tab = time.monotonic()
                        while time.monotonic() - t_tab < 2.0:
                            match, _score = detect_training_menu(sct, monitor, training_menu_template)
                            if match and _menu_stably_visible():
                                print(f"[INIT MENU] Open after {int((time.monotonic()-t_tab)*1000)}ms")
                                menu_opened = True
                                break
                            safe_sleep(0.1)
                        # A second Tab is only safe while the menu has shown no sign of
                        # opening, since Tab also closes it.
                        if menu_opened or _menu_stably_visible() or _USER_STOP_LATCHED or UI_STOP_REQUESTED:
                            break
                        if tab_attempt == 1:
                            print(f"[INIT MENU] No menu after Tab (score {_score:.2f}); pressing Tab once more")
                    if not menu_opened:
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

            first_idx, first_panels = _first_unfinished_stat(current_idx, "[INIT]")
            if first_idx is None:
                outcome, outcome_reason = _training_order_result(skipped_stats, capped_stats)
                print("[INIT] Every stat in the Training Order is already done")
                _record_run_outcome(outcome, outcome_reason, state)
                return
            if first_idx != current_idx:
                current_idx, state = first_idx, runtime_order[first_idx]
                print(f"[INIT] Starting with {state}")
            if not _click_trait_and_confirm(state, "[INIT]", panels=first_panels):
                raise RuntimeError(f"Could not confirm {state} trait selection after 3 clicks")
            _set_training_state(CURRENT_TRAINING_STATE)
            if "Ki" in state:
                safe_sleep(0.1)
            else:
                _settle_after_select(state)
            # Do not let the background progression watcher interrupt startup's
            # post-selection settle wait. It begins tracking once startup is done.
            PROGRESSION_COMPLETE_REQUESTED.clear()
            _set_training_state(state)

        # Restamp after startup menu handling so startup time cannot count as
        # "no yellow" and skip the first category.
        yellow_last_seen_ts = time.monotonic()
        awaiting_first_yellow = True

        if NO_YELLOW_FALLBACK_ENABLED:
            print(
                "[OK] Progression: label completion detector; "
                f"yellow timeout is a {NO_YELLOW_TIMEOUT_SEC:.1f}s fallback until tracking locks"
            )
        else:
            print(
                "[OK] Progression: label completion detector; "
                f"no-yellow fallback switch is disabled ({MANUAL_NEXT_KEY.upper()} skips manually)"
            )

        def do_switch(reason, *, current_completed=True, current_capped=False):
            nonlocal state, current_idx, yellow_last_poll, yellow_last_seen_ts
            nonlocal menu_match_streak, menu_shadow_visible, awaiting_first_yellow
            global CURRENT_TRAINING_STATE, PROGRESSION_STATE_STARTED_AT
            global SENZU_TRAIT_RESELECT_DEFERRED
            print(reason)
            SENZU_TRAIT_RESELECT_DEFERRED = False
            # Claim the menu transition before healing can claim the old stat.
            with _stop_input_gate:
                if _controller_decisions_suspended():
                    raise SenzuControllerPause()
                _set_training_state(None)
                _set_charge_hold(False)
            TELEMETRY["switches"] += 1
            if state == "Ki Control" and (current_completed or current_capped):
                _mark_ki_control_done()
            if current_capped:
                capped_stats[state] = current_capped
            elif not current_completed and state not in skipped_stats:
                skipped_stats.append(state)
            next_idx = current_idx + 1
            if next_idx >= len(runtime_order):
                outcome, outcome_reason = _training_order_result(skipped_stats, capped_stats)
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
                charge_ki_bar(sct, "Before the next stat")
                hardware_tap('tab')
                if training_menu_template is not None:
                    t_tab = time.monotonic()
                    while time.monotonic() - t_tab < 2.0:
                        match, _s = detect_training_menu(sct, monitor, training_menu_template)
                        if match and _menu_stably_visible():
                            print(f"[MENU] Open after {int((time.monotonic()-t_tab)*1000)}ms")
                            break
                        safe_sleep(0.1)
                    else:
                        raise RuntimeError(
                            "Training Mode menu was not confirmed after Tab; switch cancelled"
                        )
                else:
                    safe_sleep(0.5)
            next_idx, next_panels = _first_unfinished_stat(next_idx, "[SWITCH]")
            if next_idx is None:
                outcome, outcome_reason = _training_order_result(skipped_stats, capped_stats)
                print("ALL DONE!" if outcome == "completed" else outcome_reason.upper())
                _record_run_outcome(outcome, outcome_reason, state)
                return True
            next_cat = runtime_order[next_idx]
            if not _click_trait_and_confirm(next_cat, "[SWITCH]", panels=next_panels):
                raise RuntimeError(
                    f"Could not confirm {next_cat} trait selection after 3 clicks"
                )
            state = next_cat; current_idx = next_idx
            _set_training_state(CURRENT_TRAINING_STATE)
            PROGRESSION_COMPLETE_REQUESTED.clear()
            print(f"NEW STATE: {state}")
            if "Ki" in state:
                safe_sleep(0.1)
            else:
                _settle_after_select(state)
            PROGRESSION_COMPLETE_REQUESTED.clear()
            _set_training_state(state)

            # reset yellow tracking
            yellow_last_poll = 0.0
            yellow_last_seen_ts = time.monotonic()
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
                yellow_last_seen_ts = time.monotonic()
                awaiting_first_yellow = True
                menu_match_streak = 0
                menu_shadow_visible = None
                SENZU_CONTROLLER_RESUME_REQUIRED.clear()
                continue
            # Pause + manual-skip flag handling happens BEFORE check_exit, because
            # check_exit raises SkipMinigameException when MANUAL_NEXT_REQUESTED is set
            # (so it can unwind out of deep minigame loops). If we let that raise here,
            # the consumer block below would never run.
            if _consume_pause_toggle():
                if CONTROLLER_PAUSED:
                    _set_charge_hold(False)
                    print(f"\n[PAUSED] Press {PAUSE_HOTKEY.upper()} to resume")
                else:
                    yellow_last_seen_ts = time.monotonic()
                    yellow_last_poll = 0.0
                    print("\n[RESUMED]")
            if CONTROLLER_PAUSED:
                # Completion/manual-next events intentionally unwind blocking
                # minigame loops. While paused, preserve them for resume instead
                # of letting that unwind terminate the whole macro.
                if _USER_STOP_LATCHED or UI_STOP_REQUESTED:
                    check_exit()
                time.sleep(0.05)
                continue

            _set_charge_hold(_charge_wanted(state))

            if MANUAL_NEXT_REQUESTED:
                MANUAL_NEXT_REQUESTED = False
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

            completion_reason = _pending_progression_completion(consume=True)
            if completion_reason is not None:
                capped = completion_reason if completion_reason != "displayed_target" else False
                descriptions = {
                    "displayed_target": "reached its displayed target",
                    "points_left": "reached its confirmed Points Left cap",
                    "stall": "capped (likely out of Points Left)",
                }
                reason = f"[PROG] {state} {descriptions[completion_reason]} -> Switching..."
                try:
                    if do_switch(reason, current_capped=capped):
                        break
                except SenzuControllerPause:
                    pass
                continue

            try:
                check_exit()

                if SENZU_TRAIT_RESELECT_DEFERRED:
                    _set_training_state(None)
                    if not _click_trait_and_confirm(state, "[SENZU RESUME]"):
                        raise RuntimeError(
                            f"Training Mode is open and {state} could not be resumed"
                        )
                    SENZU_TRAIT_RESELECT_DEFERRED = False
                    TRAINING_MENU_VISIBLE = False
                    yellow_last_seen_ts = time.monotonic()
                    awaiting_first_yellow = True
                    _set_training_state(state)

                # --- DYNAMIC CAPTURE REFRESH ---
                # The Roblox window can move between monitors (user dragging, alt-tab,
                # etc.). Refresh the capture rect every ~2s so a stale region doesn't
                # trap us looking at a static piece of wallpaper.
                if time.monotonic() - window_last_refresh > 2.0:
                    window_last_refresh = time.monotonic()
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
                now = time.monotonic()
                if gc_room_watch.left_room(sct, monitor, now):
                    _stop_for_leaving_gc_room()
                    check_exit()
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
                    if _pending_progression_completion() is not None:
                        continue
                    _set_training_state(None)
                    if not _click_trait_and_confirm(state, "[MENU RECOVERY]"):
                        raise RuntimeError(
                            f"Training Mode is open and {state} could not be resumed"
                        )
                    TRAINING_MENU_VISIBLE = False
                    menu_match_streak = 0
                    menu_shadow_visible = False
                    yellow_last_seen_ts = time.monotonic()
                    awaiting_first_yellow = True
                    _set_training_state(state)
                    PROGRESSION_COMPLETE_REQUESTED.clear()
                    print(f"[MENU RECOVERY] Resumed incomplete stat: {state}")
                    continue

                cycle_started = time.perf_counter()
                hit_count_before = TELEMETRY["health_hits"] + TELEMETRY["ki_clicks"]
                # --- MINIGAME LOGIC --- (wrapped so L key during a green-observe wait
                # unwinds back to the loop top, where MANUAL_NEXT_REQUESTED gets consumed
                # on the next iteration).
                try:
                    if state == "Health" and ENABLE_HEALTH_MINIGAME:
                        logic_health(sct, monitor)
                    elif state in ["Agility", "Physical Damage"] and ENABLE_PHYSICAL_MINIGAME:
                        if _new_game_ui():
                            logic_agility_v4(sct)
                        elif AGILITY_MODE == "v3":
                            logic_agility_v3(sct, agility_templates, state)
                        elif AGILITY_MODE == "v2":
                            logic_agility_v2(sct, agility_templates, state)
                        else:
                            logic_agility_v1(sct, agility_templates)
                    elif state in ["Ki Control", "Ki Damage"] and ENABLE_KI_MINIGAME:
                        if _new_game_ui():
                            logic_ki_v9(sct, monitor)
                        else:
                            logic_ki_v8(sct, monitor)
                except SkipMinigameException:
                    pass

                restart_enabled = (
                    state == "Health" and COMPATIBILITY_SETTINGS["restart_health_after_hit"]
                    or state in ("Ki Control", "Ki Damage") and COMPATIBILITY_SETTINGS["restart_ki_after_hit"]
                )
                with _KI_V9_CONDITION:
                    ki_hit = _KI_V9_STATE.get("restart_pending", False)
                    _KI_V9_STATE["restart_pending"] = False
                if restart_enabled and (ki_hit or TELEMETRY["health_hits"] + TELEMETRY["ki_clicks"] > hit_count_before):
                    _restart_minigame_after_hit(state, _menu_stably_visible, _click_trait_and_confirm)
                    TRAINING_MENU_VISIBLE = False
                    menu_match_streak = 0
                    yellow_last_seen_ts = time.monotonic()
                    awaiting_first_yellow = True
                    _set_training_state(CURRENT_TRAINING_STATE)
                _finish_minigame_cycle(cycle_started)

            except QuitException:
                # Propagate configured Stop immediately (no return-to-menu prompts).
                raise
            except SkipMinigameException:
                # Race: flag re-set between consumer and check_exit. Loop will catch it next iter.
                pass
            except Exception as e:
                print(f"Error: {e}")
                _record_run_outcome(
                    "error", f"Controller error: {e.__class__.__name__}: {e}", state,
                    retryable=isinstance(e, RuntimeError) and _runtime_error_is_retryable(e),
                )
                break

    _destroy_cv_windows()


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
        if not _ensure_game_focus_for_input():
            raise RuntimeError("Roblox is not foreground for click")
        _click_sendinput_abs(screen_x, screen_y)
        _park_menu_cursor(geometry)


def _after_run_wait_training_menu(sct, geometry, template, visible, timeout=2.0):
    deadline = time.monotonic() + timeout
    stable = 0
    while time.monotonic() < deadline:
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
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
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

    with mss.MSS() as sct, _menu_cursor_guard(geometry):
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
        deadline = time.monotonic() + 5.0
        travel_open = False
        while time.monotonic() < deadline:
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
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
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
    with mss.MSS() as sct, _menu_cursor_guard(geometry):
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
        _safe_get_cursor_pos()
        if action in {"main_menu", "zero_gravity"}
        else None
    )
    try:
        return bool(handler())
    except Exception as error:
        print(f"[AFTER RUN] {action} failed: {type(error).__name__}: {error}")
        return False
    finally:
        if original_cursor is not None:
            _restore_menu_cursor(original_cursor)


# --- Bug report ---------------------------------------------------------------------
# Everything a bug report needs, assembled here rather than in the WebView so the
# exact text the user approves is the exact text that gets sent. Unlike
# _diagnostic_report() this never hard-fails: people file bugs precisely when the
# game is closed or something is broken.

ISSUE_URL = "https://github.com/lowkxyn/xynmacro/issues/new"
# GitHub truncates very long query strings; past this we tell the user to paste instead.
ISSUE_URL_LIMIT = 6000

BUG_REPORT_SECTIONS = ("system", "display", "game", "settings", "capture", "logs", "crash")


def _failed_run_needs_zero_gravity(outcome):
    """A failed run must not leave the player draining in raised GC gravity.

    Manual Stop and blocked runs (covered window, broken monitor) stay hands-off.
    """
    return outcome == "error" and not (_USER_STOP_LATCHED or _AFTER_ACTIONS_BLOCKED)


def _run_after_actions(outcome, reason):
    if not _should_run_after_actions(outcome):
        if _failed_run_needs_zero_gravity(outcome):
            _perform_after_run_game_action("zero_gravity")
            return True
        return False
    action = _normalize_after_run_game_action(AFTER_RUN_GAME_ACTION)
    if action == "none" and _failed_run_needs_zero_gravity(outcome):
        action = "zero_gravity"
    if action != "none":
        _perform_after_run_game_action(action)
    # PC shutdown is independent: a failed game action must not suppress an
    # explicitly enabled shutdown.
    _schedule_pc_shutdown(outcome, reason)
    return action != "none" or bool(SHUTDOWN_PC_WHEN_FINISHED)


_shutdown_lock = threading.RLock()
_shutdown_timer = None
_shutdown_generation = 0
_shutdown_status = "idle"
_shutdown_deadline = 0.0


def _shutdown_snapshot():
    with _shutdown_lock:
        return {
            "status": _shutdown_status,
            "pending": _shutdown_status == "pending",
            "seconds_remaining": max(0, math.ceil(_shutdown_deadline - time.monotonic()))
            if _shutdown_status == "pending" else 0,
        }


def _cancel_pc_shutdown():
    global _shutdown_status, _shutdown_generation, _shutdown_timer
    with _shutdown_lock:
        if _shutdown_status != "pending":
            return False, "No cancellable XynMacro countdown is pending."
        _shutdown_generation += 1
        if _shutdown_timer is not None:
            _shutdown_timer.cancel()
            _shutdown_timer = None
        _shutdown_status = "cancelled"
    print("[POWER] XynMacro shutdown countdown cancelled")
    return True, "Shutdown countdown cancelled."


def _dispatch_pc_shutdown(generation):
    global _shutdown_status, _shutdown_timer
    with _shutdown_lock:
        if generation != _shutdown_generation or _shutdown_status != "pending":
            return
        if _USER_STOP_LATCHED:
            _cancel_pc_shutdown()
            return
        # Commit only when our cancellable countdown expires. Never abort a
        # Windows countdown belonging to another application via shutdown /a.
        _shutdown_status = "requesting"
        _shutdown_timer = None
    executable = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "shutdown.exe")
    try:
        result = subprocess.run(
            [executable, "/s", "/t", "0", "/c", "XynMacro training run ended"],
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            capture_output=True, timeout=5, check=False,
        )
        status = "requested" if result.returncode == 0 else "failed"
    except subprocess.TimeoutExpired:
        status = "unknown"
    except OSError:
        status = "failed"
    with _shutdown_lock:
        _shutdown_status = status
    print(f"[POWER] Windows shutdown request: {status}")


def _schedule_pc_shutdown(outcome, reason):
    global _shutdown_status, _shutdown_timer, _shutdown_generation, _shutdown_deadline
    with _shutdown_lock:
        if not _should_shutdown_pc(outcome) or os.name != "nt":
            return False
        if _shutdown_status in ("pending", "requesting", "requested", "unknown"):
            return False
        _shutdown_generation += 1
        _shutdown_status = "pending"
        _shutdown_deadline = time.monotonic() + PC_SHUTDOWN_DELAY_SEC
        _shutdown_timer = threading.Timer(
            PC_SHUTDOWN_DELAY_SEC, _dispatch_pc_shutdown, args=(_shutdown_generation,)
        )
        _shutdown_timer.daemon = True
        _shutdown_timer.start()
    print(f"[POWER] Shutdown in {PC_SHUTDOWN_DELAY_SEC}s; cancel in XynMacro or close the app")
    return True


def _finalize_run_result():
    global LAST_RUN_RESULT, MACRO_ERROR_COUNT, MACRO_LAST_ERROR
    ended_at = time.time()
    started_at = MACRO_STARTED_AT or ended_at
    elapsed = _run_elapsed_seconds(ended_at)
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
    notifications.send(outcome, elapsed, category, *_notification_extras(final_telemetry))


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
    deadline = time.monotonic() + max(0.0, float(duration))
    while time.monotonic() < deadline:
        if _auto_retry_cancelled():
            return False
        time.sleep(min(step, max(0.0, deadline - time.monotonic())))
    return not _auto_retry_cancelled()


def _gc_death_dialog_visible(sct, geometry):
    """Match DBOG's centered You Died/Respawn dialog by its three blue bands."""
    if _new_game_ui():
        return training_ui.death_dialog_visible(
            _grab_reference_box(sct, (0, 0, GAME_REFERENCE_WIDTH, GAME_REFERENCE_HEIGHT), geometry))
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
    deadline = time.monotonic() + timeout
    with mss.MSS() as sct:
        while time.monotonic() < deadline:
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
            if not _ensure_game_focus_for_input():
                return False
            if not _click_sendinput_abs(*click_point):
                return False
    print("[RECOVERY] Confirmed GC death dialog; clicked Respawn")

    stable_hidden = 0
    deadline = time.monotonic() + timeout
    with mss.MSS() as sct:
        while time.monotonic() < deadline:
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
    # Already on the death screen: Respawn is right there, no reset needed.
    if not _auto_retry_cancelled() and _auto_retry_wait_for_death_dialog(0.5):
        print("[RECOVERY] Death screen already up; clicking Respawn")
        return _auto_retry_click_respawn()
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
            # Esc/R/Enter in another app could confirm its dialog (audit
            # 2026-10-14): re-check Roblox owns the keyboard before each key.
            if not _game_has_focus():
                print("[RECOVERY] Roblox lost focus during the reset; stopping it")
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


def _gc_recovery_ready():
    """Honor pause during recovery without clearing the failure's stop request."""
    global PAUSE_TOGGLE_REQUESTED, CONTROLLER_PAUSED
    while True:
        if _auto_retry_cancelled() or not _game_has_focus():
            return False
        if _consume_pause_toggle():
            print('[RECOVERY] Paused' if CONTROLLER_PAUSED else '[RECOVERY] Resumed')
        if not CONTROLLER_PAUSED:
            return True
        if not _auto_retry_wait(.05):
            return False


def _gc_recovery_mouse(dx):
    while _gc_recovery_ready():
        with _input_lock, _stop_input_gate:
            if _auto_retry_cancelled() or not _game_has_focus():
                return False
            if PAUSE_TOGGLE_REQUESTED:
                continue  # Consume the pause outside the input gate.
            packet = (_INPUT * 1)(_make_mouse_input(_MOUSEEVENTF_MOVE, int(dx), 0))
            if _user32.SendInput(1, packet, _ctypes.sizeof(_INPUT)) != 1:
                raise RuntimeError('GC camera input failed')
        return _auto_retry_wait(.15)
    return False


def _gc_recovery_hold(duration, key='w'):
    """Short movement pulse, always released before reading the next frame."""
    with _input_lock:
        was_paused = CONTROLLER_PAUSED or PAUSE_TOGGLE_REQUESTED
        if not _gc_recovery_ready():
            return None
        if was_paused:
            return 0.0  # Require a fresh scene after waiting for Resume.
        key_is_down = False
        started = time.monotonic()
        try:
            with _stop_input_gate:
                if _auto_retry_cancelled() or not _game_has_focus():
                    return None
                if CONTROLLER_PAUSED or PAUSE_TOGGLE_REQUESTED:
                    return 0.0
                key_is_down = True
                pydirectinput.keyDown(key)
            while time.monotonic() - started < duration:
                if _auto_retry_cancelled() or not _game_has_focus():
                    return None
                if PAUSE_TOGGLE_REQUESTED:
                    break
                time.sleep(.025)
            return time.monotonic() - started
        finally:
            if key_is_down:
                pydirectinput.keyUp(key)


GC_RESPAWN_STABLE_FRAMES = 10     # 2.5s of continuous prompt at 0.25s per frame
GC_RESPAWN_LOADING_FRAMES = 60    # 15s for the respawn and loading screen


def _auto_retry_walk_forward():
    """Legacy setting name; return across the GC safety boundary using vision."""
    global GC_RECOVERY_VERIFY_PENDING
    if not AUTO_RETRY_WALK_OUT:
        return True
    settle = COMPATIBILITY_SETTINGS["respawn_settle_sec"]
    print(f"[RECOVERY] Waiting {settle:g}s before finding the GC training area")
    if not _auto_retry_wait(settle):
        return False
    if _auto_retry_cancelled() or not focus_game_window():
        print("[RECOVERY] Roblox could not be focused for the GC return")
        return False
    if _auto_retry_cancelled():
        return False
    if _new_game_ui():
        return _walk_off_respawn_pad()
    template = cv2.imread(os.path.join(BASE_DIR, 'tpl_gc_enter.png'), cv2.IMREAD_GRAYSCALE)
    budget = _bounded_float(AUTO_RETRY_WALK_SECONDS, .5, 10.0)
    moved = 0.0
    edge_streak = 0
    with mss.MSS() as sct:
        room_confirmed = False
        # Respawn can close its dialog several seconds before the room loads,
        # and the old scene (prompt included) can linger until the loading
        # screen replaces it. Live 2026-10-04: walking on that stale frame ran
        # into the loading screen. Trust only a prompt that stays visible.
        prompt_streak = 0
        for _loading_frame in range(GC_RESPAWN_LOADING_FRAMES):
            if not _gc_recovery_ready():
                return False
            geometry = _confirmed_game_capture_rect()
            frame = _grab_reference_box(sct, (0, 0, 1920, 1080), geometry)
            if not _gc_death_dialog_visible(sct, geometry) and gc_recovery.enter_prompt_visible(frame, template):
                prompt_streak += 1
                if (prompt_streak >= GC_RESPAWN_STABLE_FRAMES
                        and gc_recovery.room_landmarks(frame, template) is not None):
                    room_confirmed = True
                    break
            else:
                prompt_streak = 0
            if not _auto_retry_wait(.25):
                return False
        if prompt_streak < GC_RESPAWN_STABLE_FRAMES:
            print('[RECOVERY] GC entrance is not confirmed; no movement sent')
            return False
        if not room_confirmed:
            return _gc_fallback_walk('GC floor and windows not in view', budget, sct, template)
        # Like Grand Kai, a relative nudge distinguishes a free cursor from
        # Roblox's camera lock. Absolute mouse movement can spin a locked view.
        before = win32api.GetCursorPos()
        if not _gc_recovery_mouse(12):
            return False
        free = abs(win32api.GetCursorPos()[0] - before[0]) >= 8
        if not _gc_recovery_mouse(-12):
            return False
        if free:
            with _input_lock:
                if not _gc_recovery_ready() or not _tap_key_unchecked('alt'):
                    return False
            before = win32api.GetCursorPos()
            if not _gc_recovery_mouse(12):
                return False
            locked = abs(win32api.GetCursorPos()[0] - before[0]) < 8
            if not _gc_recovery_mouse(-12):
                return False
            if not locked:
                return _gc_fallback_walk('Camera lock could not be confirmed', budget, sct, template)
        # Rotation is bounded; movement only follows a positively found column.
        for _step in range(60):
            if not _gc_recovery_ready():
                return False
            geometry = _confirmed_game_capture_rect()
            frame = _grab_reference_box(sct, (0, 0, 1920, 1080), geometry)
            if _gc_death_dialog_visible(sct, geometry) or not gc_recovery.enter_prompt_visible(frame, template):
                print('[RECOVERY] Lost the GC room cues; stopping the return')
                return False
            landmarks = gc_recovery.room_landmarks(frame, template)
            if landmarks is None:
                return _gc_fallback_walk('GC floor and windows left the view', budget - moved, sct, template)
            pillar, edge = landmarks
            if pillar is None:
                if moved or _step >= 16:
                    return _gc_fallback_walk('Central column not found', budget - moved, sct, template)
                if not _gc_recovery_mouse(-75):
                    return False
                continue
            if not 720 <= pillar <= 1100:
                if not _gc_recovery_mouse(max(-60, min(60, (pillar - 900) / 5))):
                    return False
                continue
            # One step can carry a line seen at >= 910 past the bottom of the
            # frame; that disappearance confirms the crossing rather than losing it.
            past_edge = (edge is not None and edge >= 910) or (edge is None and edge_streak > 0)
            edge_streak = edge_streak + 1 if past_edge else 0
            if edge_streak >= 2:
                if not _gc_recovery_ready():
                    return False
                GC_RECOVERY_VERIFY_PENDING = True
                print('[RECOVERY] Crossed the safety boundary; restarting to verify progress')
                return True
            if edge_streak:
                if not _auto_retry_wait(.2):
                    return False
                continue
            if edge is None:
                return _gc_fallback_walk(
                    f'Safety boundary line not visible after {moved:.1f}s of walking', budget - moved, sct, template)
            if moved >= budget:
                return _gc_fallback_walk('Safety boundary crossing not seen within the movement budget', 0, sct, template)
            # Perspective speeds the line up near the bottom: a 0.2 s step from
            # ~730 carried it straight off the frame in a live capture.
            step = .08 if edge >= 650 else .20
            elapsed = _gc_recovery_hold(min(step, budget - moved))
            if elapsed is None:
                return False
            moved += elapsed
            if not _auto_retry_wait(.12):
                return False
    return False


def _walk_off_respawn_pad():
    """October 2026 GC: respawn lands on the LEAVE pad (a safe zone where
    training earns nothing) facing the wall it sits against; backing away
    with S leaves it (live 2026-10-13: 1.5 s was enough). The restarted plan's
    level-count check then decides whether the spot really counts."""
    global GC_RECOVERY_VERIFY_PENDING
    seconds = _bounded_float(AUTO_RETRY_WALK_SECONDS, .5, 10.0)
    with mss.MSS() as sct:
        # The old scene can linger until the loading screen replaces it; trust
        # only a banner that stays up (same rule as the legacy prompt).
        streak = 0
        for _loading_frame in range(GC_RESPAWN_LOADING_FRAMES):
            if not _gc_recovery_ready():
                return False
            geometry = _confirmed_game_capture_rect()
            line = _grab_reference_box(sct, training_ui.OPEN_TRAINING_BOX, geometry)
            ready = (training_ui.open_training_prompt(line)
                     and not _gc_death_dialog_visible(sct, geometry))
            streak = streak + 1 if ready else 0
            if streak >= GC_RESPAWN_STABLE_FRAMES:
                break
            if not _auto_retry_wait(.25):
                return False
        else:
            print('[RECOVERY] GC banner not confirmed after respawn; no movement sent')
            return False
    # Measured live: 1.5 s leaves the pad. Longer only walks further from a
    # facing we've seen on a few accounts, so cap it.
    seconds = min(seconds, 3.0)
    print(f'[RECOVERY] Backing off the respawn pad with S for {seconds:.1f}s')
    walked = 0.0
    with mss.MSS() as sct:
        while walked < seconds:
            # Re-check the scene before every pulse (a death or teleport mid-walk).
            geometry = _confirmed_game_capture_rect()
            line = _grab_reference_box(sct, training_ui.OPEN_TRAINING_BOX, geometry)
            if (not training_ui.open_training_prompt(line)
                    or _gc_death_dialog_visible(sct, geometry)):
                print('[RECOVERY] GC banner lost or death visible while backing off; stopping')
                return False
            elapsed = _gc_recovery_hold(min(.25, seconds - walked), key='s')
            if elapsed is None:
                return False
            walked += elapsed
    if not _gc_recovery_ready():
        return False
    GC_RECOVERY_VERIFY_PENDING = True
    print('[RECOVERY] Off the pad; restarting to verify the level count moves')
    return True


def _gc_fallback_walk(reason, seconds, sct=None, template=None):
    """Hold W for what is left of the movement budget, then let the restarted
    training's progress check decide whether the spot is right.

    Walks in short steps and keeps looking: if the boundary line comes into
    view and passes below the character, it stops there instead of overshooting.
    """
    global GC_RECOVERY_VERIFY_PENDING
    if not COMPATIBILITY_SETTINGS["auto_retry_walk_fallback"]:
        print(f'[RECOVERY] {reason}; the hold-W fallback is off, stopping the return')
        return False
    if sct is None:
        return False
    seconds = max(0.0, seconds)
    print(f'[RECOVERY] {reason}; falling back to holding W for {seconds:.1f}s')
    walked = 0.0
    edge_streak = 0
    while True:
        if not _gc_recovery_ready():
            return False
        geometry = _confirmed_game_capture_rect()
        frame = _grab_reference_box(sct, (0, 0, 1920, 1080), geometry)
        if (_gc_death_dialog_visible(sct, geometry)
                or not gc_recovery.enter_prompt_visible(frame, template)):
            print('[RECOVERY] GC enter prompt lost or death visible; stopping the fallback')
            return False
        landmarks = gc_recovery.room_landmarks(frame, template)
        if landmarks is None:
            print('[RECOVERY] GC scene is no longer recognizable; stopping the fallback')
            return False
        edge = landmarks[1]
        past_edge = (edge is not None and edge >= 910) or (edge is None and edge_streak > 0)
        edge_streak = edge_streak + 1 if past_edge else 0
        if edge_streak >= 2:
            print(f'[RECOVERY] Boundary crossing seen after {walked:.1f}s of the fallback walk')
            break
        if walked >= seconds:
            break
        if edge_streak:
            if not _auto_retry_wait(.12):
                return False
            continue
        elapsed = _gc_recovery_hold(min(.20, seconds - walked))
        if elapsed is None:
            return False
        walked += elapsed
    if not _gc_recovery_ready():
        return False
    GC_RECOVERY_VERIFY_PENDING = True
    print('[RECOVERY] Fallback walk done; restarting to verify progress')
    return True


def _auto_retry_recover(*, stuck_senzu=False):
    if _auto_retry_cancelled():
        return False
    if not stuck_senzu and AUTO_RETRY_RECOVERY_MODE == "wait_for_death":
        ready = _auto_retry_wait_for_death()
    else:
        ready = _auto_retry_reset_character()
    if not ready or _auto_retry_cancelled():
        return False
    if not stuck_senzu and not AUTO_RETRY_WALK_OUT:
        settle = COMPATIBILITY_SETTINGS["respawn_settle_sec"]
        print(f"[RECOVERY] Waiting {settle:g}s after respawn")
        return _auto_retry_wait(settle)
    return _auto_retry_walk_forward()


def _prepare_controller_attempt():
    global UI_STOP_REQUESTED, CURRENT_TRAINING_STATE
    global MANUAL_NEXT_REQUESTED, PAUSE_TOGGLE_REQUESTED, TRAINING_MENU_VISIBLE
    global CONTROLLER_PAUSED, SENZU_TRAIT_RESELECT_DEFERRED, SENZU_DISABLED_FOR_RUN, SENZU_STATUS
    global SENZU_ACTIVE_TYPE, SENZU_REMAINING
    if _auto_retry_cancelled():
        UI_STOP_REQUESTED = True
        return False
    UI_STOP_REQUESTED = False
    MANUAL_NEXT_REQUESTED = False
    if not GC_RECOVERY_VERIFY_PENDING:
        PAUSE_TOGGLE_REQUESTED = False
        CONTROLLER_PAUSED = False
    SENZU_TRAIT_RESELECT_DEFERRED = False
    _set_training_state(None)
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
    global GC_RECOVERY_VERIFY_PENDING
    global SENZU_STUCK_RESETS_USED, SENZU_STUCK_RESET_REQUESTED
    global MAJIN_REGEN_UNAVAILABLE_FOR_RUN
    SENZU_STUCK_RESETS_USED = 0
    SENZU_STUCK_RESET_REQUESTED = False
    MAJIN_REGEN_UNAVAILABLE_FOR_RUN = False
    GC_RECOVERY_VERIFY_PENDING = False
    PROGRESSION_TARGETS.clear()
    _POINTS_CAP_REACHED.clear()
    _LATEST_POINTS.clear()
    global _KI_CHARGE_CEILING, _KI_CONTROL_DONE, _LATEST_POINTS_PANEL
    _LATEST_POINTS_PANEL = None
    _KI_CHARGE_CEILING = None
    _KI_CONTROL_DONE = False
    notifications.begin_run()
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

            _reset_ki_v9()
            _set_charge_hold(False)
            monitor_stopped = _stop_background_game_monitor()
            stuck_senzu = (SENZU_STUCK_RESET_REQUESTED
                           and not _auto_retry_cancelled() and not _AFTER_ACTIONS_BLOCKED)
            if not monitor_stopped or (not stuck_senzu and not _auto_retry_can_run(retries_used)):
                break

            TELEMETRY["recovery_attempts"] += 1
            with _run_result_lock:
                failed_reason = _CURRENT_RUN_REASON or "Unknown controller error"
            if stuck_senzu:
                SENZU_STUCK_RESET_REQUESTED = False
                print("[RECOVERY] Clearing the stuck Senzu item state and returning to GC training")
                recovered = _auto_retry_recover(stuck_senzu=True)
            else:
                retries_used += 1
                print(
                    f"[RECOVERY] Attempt {retries_used}/{AUTO_RETRY_MAX_ATTEMPTS} "
                    f"after: {failed_reason}"
                )
                recovered = _auto_retry_recover()
            if not recovered:
                if _auto_retry_cancelled():
                    _record_run_outcome("stopped", "User requested stop")
                    break
                _record_run_outcome(
                    "error", f"Recovery attempt {retries_used} could not be confirmed"
                )
                if stuck_senzu:
                    print("[SENZU] Character reset or GC return could not be confirmed; stopping safely")
                break
            if _auto_retry_cancelled():
                _record_run_outcome("stopped", "User requested stop")
                break
            _clear_attempt_outcome()
            print("[RECOVERY] Restarting selected training plan")
    finally:
        _reset_ki_v9()
        _set_charge_hold(False)
        _stop_background_game_monitor()
        GC_RECOVERY_VERIFY_PENDING = False
        if sleep_hold_active:
            _set_thread_sleep_hold(False)
        _finalize_run_result()
        _set_training_state(None)
        TRAINING_MENU_VISIBLE = False
        CONTROLLER_PAUSED = False
        PROGRESSION_COMPLETE_REQUESTED.clear()
        SENZU_STATUS = "idle"
        SENZU_ACTIVE_TYPE = None


# Serialises live setting changes + the config write so concurrent /command
# requests (Flask is threaded) can't interleave and lose updates or corrupt the
# file. Re-entrant because _ui_apply_setting holds it across save_master_config.
_config_lock = threading.RLock()
_save_dir_picker_lock = threading.Lock()


_macro_start_lock = threading.Lock()
_macro_start_generation = 0
_macro_pending_start_generation = None
_macro_pending_start_requests = 0
_sidecar_closing = False


def _release_macro_input():
    """Release the held retry-walk/charge keys and either configured click button on close."""
    _reset_ki_v9()
    pydirectinput.keyUp("w")
    pydirectinput.keyUp("s")  # the October 2026 walk off the respawn pad
    _set_charge_hold(False)
    if MAJIN_CONTROLLER_ACTIVE:
        pydirectinput.keyUp(MAJIN_PANEL_KEY)
    releases = (_INPUT * 2)(_make_mouse_input(0x0004), _make_mouse_input(0x0010))
    if _user32.SendInput(2, releases, _ctypes.sizeof(_INPUT)) != 2:
        raise RuntimeError("Windows did not release both mouse buttons")


def _ui_shutdown_clean():
    global _sidecar_closing
    with _stop_input_gate:
        # Closing must also invalidate a Start still preparing its window and
        # refuse queued HTTP requests or hotkeys for the rest of this process.
        _sidecar_closing = True
    _ui_stop_macro()
    # An idle close must not release keys the player is physically holding.
    if _ui_is_running():
        try:
            _release_macro_input()
        except Exception as e:
            print(f"[sidecar] Input release on close failed: {e}")
    _release_session_marker()


def _hotkey_manual_next():
    global MANUAL_NEXT_REQUESTED
    if _ui_is_running() and not _USER_STOP_LATCHED and _game_has_focus():
        MANUAL_NEXT_REQUESTED = True


def _hotkey_pause_toggle():
    global PAUSE_TOGGLE_REQUESTED
    with _stop_input_gate:
        if _ui_is_running() and not _USER_STOP_LATCHED and _game_has_focus():
            PAUSE_TOGGLE_REQUESTED = True


def _register_edge_hotkey(key, callback):
    # add_hotkey(suppress=False) matches queued events against the listener's
    # *current* pressed-key snapshot. A down/up pair can already be gone by then.
    # Reconstruct chords from ordered events instead, and ignore key auto-repeat.
    steps = keyboard.parse_hotkey_combinations(key)
    pressed = set()
    last_down_event = {}
    step_index = 0
    matched = False
    last_match_time = 0.0

    def on_event(event):
        nonlocal step_index, matched, last_match_time
        if event.event_type == keyboard.KEY_UP:
            pressed.discard(event.scan_code)
            last_down_event.pop(event.scan_code, None)
            if matched and not any(set(chord) <= pressed for chord in steps[step_index]):
                matched = False
                step_index = (step_index + 1) % len(steps)
            return
        if event.event_type != keyboard.KEY_DOWN:
            return
        if event.scan_code in pressed:
            stale = event.time - last_down_event[event.scan_code] >= 1.5
            last_down_event[event.scan_code] = event.time
            if not stale:
                return
            # A secure desktop can swallow KEY_UP. Treat this late down as a
            # new press while ordinary OS repeats keep the held state fresh.
            pressed.discard(event.scan_code)
            if matched and not any(set(chord) <= pressed for chord in steps[step_index]):
                matched = False
                step_index = (step_index + 1) % len(steps)
        pressed.add(event.scan_code)
        last_down_event[event.scan_code] = event.time
        if step_index and event.time - last_match_time >= 1.0:
            step_index = 0
            matched = False
        if not matched and any(
            event.scan_code in chord and set(chord) <= pressed
            and not any(keyboard.is_modifier(code)
                        and not (_CHARGE_HELD and code == _CHARGE_SCAN_CODE)
                        for code in pressed.difference(chord))
            for chord in steps[step_index]
        ):
            matched = True
            last_match_time = event.time
            if step_index == len(steps) - 1:
                callback()

    return keyboard.hook(on_event, suppress=False)


def register_manual_next_hotkey():
    """(Re)register the manual-next global hotkey to match MANUAL_NEXT_KEY.
    Called at startup and whenever the user changes the keybind in the UI."""
    global _manual_next_hotkey_handle
    try:
        new_handle = _register_edge_hotkey(MANUAL_NEXT_KEY, _hotkey_manual_next)
        if _manual_next_hotkey_handle is not None:
            try:
                _manual_next_hotkey_handle()
            except Exception:
                new_handle()
                raise
        _manual_next_hotkey_handle = new_handle
        print(f"[sidecar] Registered global hotkey: {MANUAL_NEXT_KEY.upper()} (manual next)")
        return True
    except Exception as e:
        print(f"[sidecar] Manual-next hotkey registration failed: {e}")
        return False


def register_pause_hotkey():
    global _pause_hotkey_handle
    try:
        new_handle = _register_edge_hotkey(PAUSE_HOTKEY, _hotkey_pause_toggle)
        if _pause_hotkey_handle is not None:
            try:
                _pause_hotkey_handle()
            except Exception:
                new_handle()
                raise
        _pause_hotkey_handle = new_handle
        print(f"[sidecar] Registered global hotkey: {PAUSE_HOTKEY.upper()} (pause toggle)")
        return True
    except Exception as e:
        print(f"[sidecar] Pause hotkey registration failed: {e}")
        return False


_start_stop_hotkey_handle = None
_hotkey_toggle_queue = queue.SimpleQueue()
_hotkey_toggle_worker = None
_hotkey_toggle_lock = threading.Lock()
_hotkey_toggle_busy = False
_hotkey_toggle_last_accepted = 0.0


def _process_hotkey_toggles():
    global _hotkey_toggle_busy
    while True:
        generation, request_stop = _hotkey_toggle_queue.get()
        try:
            if _sidecar_closing:
                continue
            if request_stop:
                _ui_stop_macro(expected_generation=generation)
            else:
                _ui_start_macro(expected_generation=generation)
        except Exception as e:
            print(f"[sidecar] Start/stop hotkey failed: {e}")
        finally:
            with _hotkey_toggle_lock:
                _hotkey_toggle_busy = False


def _hotkey_toggle_macro():
    global _hotkey_toggle_busy, _hotkey_toggle_last_accepted
    # Window restore/fullscreen setup may wait seconds. Never block key delivery.
    with _hotkey_toggle_lock:
        if _sidecar_closing:
            return
        now = time.monotonic()
        if _hotkey_toggle_busy or now - _hotkey_toggle_last_accepted < 0.4:
            return
        with _stop_input_gate:
            if _sidecar_closing:
                return
            # Carry both the accepted action and generation through the queue.
            # A later Stop must not turn an old F6 into a fresh Start.
            request_stop = (_ui_is_running()
                            or _macro_pending_start_generation == _macro_start_generation)
            _hotkey_toggle_busy = True
            _hotkey_toggle_last_accepted = now
            _hotkey_toggle_queue.put((_macro_start_generation, request_stop))


def register_start_stop_hotkey():
    global _start_stop_hotkey_handle, _hotkey_toggle_worker
    if _hotkey_toggle_worker is None or not _hotkey_toggle_worker.is_alive():
        _hotkey_toggle_worker = threading.Thread(
            target=_process_hotkey_toggles, daemon=True, name="hotkey-actions"
        )
        _hotkey_toggle_worker.start()
    try:
        new_handle = _register_edge_hotkey(START_STOP_HOTKEY, _hotkey_toggle_macro)
        if _start_stop_hotkey_handle is not None:
            try:
                _start_stop_hotkey_handle()
            except Exception:
                new_handle()
                raise
        _start_stop_hotkey_handle = new_handle
        print(f"[sidecar] Registered global hotkey: {START_STOP_HOTKEY.upper()} (start/stop)")
        return True
    except Exception as e:
        print(f"[sidecar] Start/stop hotkey registration failed: {e}")
        return False


def _read_auth_token_from_stdin(stream=None):
    """Read the launcher-supplied token from the first line of stdin.

    The launcher writes one line and closes the pipe, so a missing token returns ""
    and the caller fails closed instead of hanging. This is a console=False build,
    where sys.stdin can be None even though the launcher handed us a real pipe, so
    fall back to file descriptor 0 before giving up.
    """
    source = sys.stdin if stream is None else stream
    if source is not None:
        try:
            token = source.readline().strip()
            if token:
                return token
        except (OSError, ValueError):
            pass
    if stream is not None:
        return ""
    try:
        with os.fdopen(os.dup(0), "r", encoding="utf-8", errors="replace") as pipe:
            return pipe.readline().strip()
    except (OSError, ValueError):
        return ""


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


_LAYOUT_CONSTANT_NAMES = (
    "BUTTONS", "DEFAULT_BUTTONS", "TRAINING_MENU_BOX", "TRAINING_MENU_MATCH_THRESHOLD",
    "GRAVITY_LABEL_BOX", "GRAVITY_CLICK_POINT", "SENZU_HP_FILL_BOX", "KI_CLICK_BLOCKED_BOXES",
    "GC_RESPAWN_POINT",
)
_LEGACY_LAYOUT_CONSTANTS = {name: globals()[name] for name in _LAYOUT_CONSTANT_NAMES}
_NEW_LAYOUT_CONSTANTS = {
    "BUTTONS": dict(training_ui.STAT_BUTTONS),
    "DEFAULT_BUTTONS": dict(training_ui.STAT_BUTTONS),
    "TRAINING_MENU_BOX": training_ui.MENU_HEADING_BOX,
    "TRAINING_MENU_MATCH_THRESHOLD": training_ui.MENU_HEADING_MIN,
    "GRAVITY_LABEL_BOX": training_ui.GRAVITY_BOX,
    "GRAVITY_CLICK_POINT": training_ui.GRAVITY_CLICK_POINT,
    "SENZU_HP_FILL_BOX": training_ui.HP_FILL_BOX,
    "KI_CLICK_BLOCKED_BOXES": training_ui.KI_CLICK_BLOCKED_BOXES,
    "GC_RESPAWN_POINT": training_ui.RESPAWN_POINT,
}


def _layout_constants(layout):
    source = _LEGACY_LAYOUT_CONSTANTS if layout == LEGACY_UI_LAYOUT else _NEW_LAYOUT_CONSTANTS
    return {name: (dict(value) if isinstance(value, dict) else value)
            for name, value in source.items()}


def _apply_game_ui_layout(layout):
    global GAME_UI_LAYOUT
    GAME_UI_LAYOUT = layout
    globals().update(_layout_constants(layout))


_apply_game_ui_layout(GAME_UI_LAYOUT)


# Late imports keep core state initialized before exposing the extracted helpers.
# The script/PyInstaller entry must share that state with imports by module name.
if __name__ == "__main__":
    sys.modules["xynmacro_core"] = sys.modules[__name__]

import bug_report as _bug_report
_capture_brightness_profile = _bug_report._capture_brightness_profile
_bug_report_sections = _bug_report._bug_report_sections
build_bug_report = _bug_report.build_bug_report
bug_report_payload = _bug_report.bug_report_payload
reviewed_bug_report_url = _bug_report.reviewed_bug_report_url
_diagnostic_report = _bug_report._diagnostic_report
_overlay_colour = _bug_report._overlay_colour
_scan_region_payload = _bug_report._scan_region_payload
_diagnostic_regions = _bug_report._diagnostic_regions
_diagnostic_overlay_frame = _bug_report._diagnostic_overlay_frame
import ui_server as _ui_server
_log_entries_since = _ui_server._log_entries_since
_ui_calibrate_button_begin = _ui_server._ui_calibrate_button_begin
_ui_calibrate_button_cancel = _ui_server._ui_calibrate_button_cancel
_ui_calibrate_region_begin = _ui_server._ui_calibrate_region_begin
_ui_calibrate_region_cancel = _ui_server._ui_calibrate_region_cancel
_ui_bool = _ui_server._ui_bool
_ui_is_running = _ui_server._ui_is_running
_get_screen_info = _ui_server._get_screen_info
_ui_config_snapshot = _ui_server._ui_config_snapshot
_ui_state_snapshot = _ui_server._ui_state_snapshot
_health_snapshot = _ui_server._health_snapshot
_ui_apply_setting_unlocked = _ui_server._ui_apply_setting_unlocked
_ui_apply_setting = _ui_server._ui_apply_setting
_cleanup_stale_port_files = _ui_server._cleanup_stale_port_files
_start_parent_watchdog = _ui_server._start_parent_watchdog
_ui_start_macro = _ui_server._ui_start_macro
_ui_stop_macro = _ui_server._ui_stop_macro
_validated_auth_token = _ui_server._validated_auth_token
_loopback_request_denial = _ui_server._loopback_request_denial
_make_exclusive_server = _ui_server._make_exclusive_server
run_ui_server = _ui_server.run_ui_server


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
                    help="Per-launch token. Development use only — a command line is "
                         "readable by any same-user process. The packaged launcher uses "
                         "--auth-token-stdin.")
    ap.add_argument("--auth-token-stdin", action="store_true",
                    help="Read the per-launch token from the first line of stdin. This is "
                         "how the Tauri launcher supplies it, so the credential never "
                         "appears in the process list.")
    args = ap.parse_args()
    if not args.sidecar:
        ap.error("--sidecar is required. This is XynMacro's backend; the desktop app "
                 "launches it, so it isn't meant to be run directly.")
    if args.pid is None:
        args.pid = os.getpid()
    if not args.app_version:
        ap.error("--app-version is required when the Tauri shell launches the sidecar")
    try:
        auth_token = _validated_auth_token(
            _read_auth_token_from_stdin() if args.auth_token_stdin else args.auth_token
        )
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
    _start_session_logging()
    print(f"[sidecar] Data dir: {DATA_DIR}")
    SAVE_DIR = os.path.join(DATA_DIR, "saves")
    _load_save_dir()
    seed_defaults()
    notifications.load(os.path.join(JSON_DIR, "notifications.dpapi"))
    load_master_config()
    load_button_overrides()
    load_region_overrides()
    # One-time detection at startup so the UI can show whether DBOG is found.
    # The macro re-runs this on Start in case the window has moved.
    update_game_window()
    run_ui_server(sidecar_pid=args.pid, auth_token=auth_token)
