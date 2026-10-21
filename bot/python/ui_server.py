"""Local HTTP API and UI command helpers for the Tauri shell."""

DISCORD_INVITE_URL = "https://discord.gg/dbogen"


class _HotkeyRegistrationError(ValueError):
    pass


def _hotkey_registration_error(label, key):
    return f"Could not register the {label} hotkey ({str(key).upper()}). Another app may be using it."


def _log_entries_since(since):
    import xynmacro_core as core

    with core._ui_log_lock:
        return [entry.copy() for entry in core._ui_log_ring if entry["seq"] > since]


def _ui_calibrate_button_begin(stat_name):
    import xynmacro_core as core

    with core._macro_start_lock:
        if core._ui_is_running():
            return False, "Stop the macro before calibrating."
        if stat_name not in core.BUTTONS:
            return False, f"Unknown stat: {stat_name}"
        if core._calibration_is_active():
            return False, "Finish or cancel calibration first."
        core.BUTTON_CALIBRATION_GENERATION += 1
        core.BUTTON_CALIBRATION_ERROR = None
        generation = core.BUTTON_CALIBRATION_GENERATION
        core.BUTTON_CALIBRATION_WAITING = stat_name
        core.BUTTON_CALIBRATION_THREAD = core.threading.Thread(
            target=core._run_button_calibration,
            args=(stat_name, generation),
            daemon=True,
        )
        core.BUTTON_CALIBRATION_THREAD.start()
    return True, f"Waiting for click on {stat_name}"


def _ui_calibrate_button_cancel():
    # The overlay polls this generation on its own Tk thread before closing.
    import xynmacro_core as core

    core.BUTTON_CALIBRATION_GENERATION += 1
    core.BUTTON_CALIBRATION_WAITING = None
    return True, "Calibration cancelled"


def _ui_calibrate_region_begin(region_key):
    import xynmacro_core as core

    with core._macro_start_lock:
        if core._ui_is_running():
            return False, "Stop the macro before calibrating."
        if region_key not in core.REGION_NAMES:
            return False, f"Unknown region: {region_key}"
        if core._calibration_is_active():
            return False, "Finish or cancel calibration first."
        core.REGION_CALIBRATION_GENERATION += 1
        core.REGION_CALIBRATION_ERROR = None
        generation = core.REGION_CALIBRATION_GENERATION
        core.REGION_CALIBRATION_WAITING = region_key
        core.REGION_CALIBRATION_THREAD = core.threading.Thread(
            target=core._run_region_calibration,
            args=(region_key, generation),
            daemon=True,
        )
        core.REGION_CALIBRATION_THREAD.start()
    return True, f"Drag a rectangle for {region_key}"


def _ui_calibrate_region_cancel():
    import xynmacro_core as core

    core.REGION_CALIBRATION_GENERATION += 1
    core.REGION_CALIBRATION_WAITING = None
    return True, "Region calibration cancelled"


def _ui_bool(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on", "y")
    return bool(value)


def _ui_is_running():
    import xynmacro_core as core

    return core.MACRO_THREAD is not None and core.MACRO_THREAD.is_alive()


def _get_screen_info():
    """Return bounds and active mode for the display containing Roblox."""
    import xynmacro_core as core

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
        "scale": 1.0,
        "source": "unavailable",
    }
    monitor = core._current_game_monitor_info()
    if core.GAME_HWND is None or monitor is None:
        return unavailable
    try:
        mode = core.win32api.EnumDisplaySettings(
            monitor["device"], core.win32con.ENUM_CURRENT_SETTINGS
        )
        width = int(getattr(mode, "PelsWidth", 0)) or monitor["width"]
        height = int(getattr(mode, "PelsHeight", 0)) or monitor["height"]
        hz = int(getattr(mode, "DisplayFrequency", 0))
    except Exception:
        width = monitor["width"]
        height = monitor["height"]
        hz = 0
    # Geometry stays in physical pixels, so its ratio to the display mode is
    # always 1. Read the effective window DPI for the Windows scaling warning.
    dpi = int(core._user32.GetDpiForWindow(core.GAME_HWND)) or 96
    return {
        "width": width,
        "height": height,
        "hz": hz,
        "scale": round(dpi / 96.0, 4),
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
    import xynmacro_core as core

    return {
        **core.COMPATIBILITY_SETTINGS,
        "startup_window_mode": "windowed" if core.WINDOWED_MODE_ON_START else "fullscreen" if core.RESTORE_FULLSCREEN_ON_START else "unchanged",
        **core.settings_schema.snapshot_settings(
            vars(core), serialize=False, sanitize_training_order=core._sanitize_training_order,
        ),
    }


def _ui_state_snapshot():
    # Re-detect game window each poll so the UI reflects live state (user toggling
    # Roblox between windowed and fullscreen, moving the window, etc.). Skip when
    # macro is running so coords don't shift mid-run.
    import xynmacro_core as core

    running = core._ui_is_running()
    if not running:
        core.update_game_window()
    with core._run_result_lock:
        last_run = None if core.LAST_RUN_RESULT is None else dict(core.LAST_RUN_RESULT)
        if last_run is not None:
            last_run["telemetry"] = dict(last_run.get("telemetry", {}))
    return {
        "version": core.APP_VERSION,
        "running": running,
        "current_state": core.CURRENT_TRAINING_STATE,
        "training_menu_visible": bool(running and core.TRAINING_MENU_VISIBLE),
        "controller_paused_for_senzu": core.SENZU_CONTROLLER_ACTIVE.is_set(),
        "healing_action": core.HEALING_ACTION,
        "controller_paused": core.CONTROLLER_PAUSED,
        "loop_metrics": dict(core.LOOP_METRICS) if running else {},
        "effective_click_button": core._effective_click_button(),
        "stop_requested": bool(running and core.UI_STOP_REQUESTED),
        "last_run": last_run,
        "shutdown": core._shutdown_snapshot(),
        "notifications": core.notifications.snapshot(),
        "progression": None,
        "senzu_remaining": core.SENZU_REMAINING,
        "senzu_active_type": core.SENZU_ACTIVE_TYPE,
        "senzu_status": core.SENZU_STATUS,
        "senzu_disabled_for_run": core.SENZU_DISABLED_FOR_RUN,
        "progression_status": (
            "complete" if running and core.PROGRESSION_TRACKED_STATE == core.CURRENT_TRAINING_STATE and core.PROGRESSION_COMPLETE
            else "tracking" if running and core.CURRENT_TRAINING_STATE and core.PROGRESSION_TRACKED_STATE == core.CURRENT_TRAINING_STATE
            else None
        ),
        "error_count": core.MACRO_ERROR_COUNT,
        "last_error": core.MACRO_LAST_ERROR,
        "config": core._ui_config_snapshot(),
        "available_stats": list(core.BUTTONS.keys()),
        "screen": core._get_screen_info(),
        "display_restore_pending": bool(
            core._DISPLAY_RESTORE is not None or core.os.path.isfile(core._display_restore_path())
        ),
        "display_confirm": core._display_confirm_state(),
        # Flag only. The log tail behind it is up to 60 lines and would be resent
        # on every poll; the bug report fetches it on demand instead.
        "previous_crash": None if core.PREVIOUS_SESSION_CRASH is None else {
            "version": core.PREVIOUS_SESSION_CRASH.get("version"),
            "started_at": core.PREVIOUS_SESSION_CRASH.get("started_at"),
        },
        "game_window": {
            "found": core.GAME_HWND is not None,
            "minimized": core.GAME_WINDOW_MINIMIZED,
            "fullscreen": core.game_window_is_fullscreen(),
            "x": core.GAME_OFFSET_X,
            "y": core.GAME_OFFSET_Y,
            "width": core.GAME_WIDTH,
            "height": core.GAME_HEIGHT,
        },
        "scan_regions": core._scan_region_payload(),
        "button_calibration": {
            "waiting": core.BUTTON_CALIBRATION_WAITING,
            "error": core.BUTTON_CALIBRATION_ERROR,
            "overrides": {k: list(v) for k, v in core.USER_BUTTON_OVERRIDES.items()},
            "current": {k: list(v) for k, v in core.BUTTONS.items()},
        },
        "region_calibration": {
            "waiting": core.REGION_CALIBRATION_WAITING,
            "error": core.REGION_CALIBRATION_ERROR,
            "overrides": {k: dict(v) for k, v in core.USER_REGION_OVERRIDES.items()},
            "current": {
                "health_box":  dict(core.HEALTH_BOX),
                "agility_box": dict(core.AGILITY_BOX),
            },
        },
        "started_at": core.MACRO_STARTED_AT if core._ui_is_running() else 0,
        "telemetry": dict(core.TELEMETRY),
    }


def _health_snapshot():
    import xynmacro_core as core

    return {"ok": True, "pid": core.os.getpid(), "version": core.APP_VERSION}


def _ui_apply_setting_unlocked(key, value):
    import xynmacro_core as core


    if key in core.COMPATIBILITY_DEFAULTS:
        core._set_compatibility_setting(key, value)
    elif key == "startup_window_mode":
        if value not in ("unchanged", "fullscreen", "windowed"):
            raise ValueError("Unknown startup window mode")
        core.RESTORE_FULLSCREEN_ON_START = value == "fullscreen"
        core.WINDOWED_MODE_ON_START = value == "windowed"
    elif key == "start_delay_sec":
        core.START_DELAY = core._bounded_float(value, 0.0, 30.0)
    elif key == "gc_gravity_target_g":
        core.GC_GRAVITY_TARGET_G = core._normalize_gravity_target(value, strict=True)
    elif key == "prevent_sleep_while_running":
        core.PREVENT_SLEEP_WHILE_RUNNING = core._ui_bool(value)
    elif key == "switch_to_game_on_start":
        core.SWITCH_TO_GAME_ON_START = core._ui_bool(value)
    elif key == "hide_roblox_chat_on_start":
        core.HIDE_ROBLOX_CHAT_ON_START = core._ui_bool(value)
    elif key == "stop_charging_after_ki_control":
        core.STOP_CHARGING_AFTER_KI_CONTROL = core._ui_bool(value)
    elif key == "ki_hp_check_mode":
        if value not in ("fixed", "adaptive"):
            raise ValueError("Choose Every 30 s or Learn from HP drain")
        core.KI_HP_CHECK_MODE = value
    elif key == "restore_fullscreen_on_start":
        core.RESTORE_FULLSCREEN_ON_START = core._ui_bool(value)
    elif key == "windowed_mode_on_start":
        core.WINDOWED_MODE_ON_START = core._ui_bool(value)
    elif key == "display_confirm_changes":
        core.DISPLAY_CONFIRM_CHANGES = core._ui_bool(value)
    elif key == "shutdown_pc_when_finished":
        core.SHUTDOWN_PC_WHEN_FINISHED = core._ui_bool(value)
        if not core.SHUTDOWN_PC_WHEN_FINISHED:
            core._cancel_pc_shutdown()
    elif key == "after_run_game_action":
        core.AFTER_RUN_GAME_ACTION = core._normalize_after_run_game_action(value, strict=True)
    elif key == "after_run_on_failure":
        core.AFTER_RUN_ON_FAILURE = core._ui_bool(value)
    elif key == "auto_retry_on_failure":
        core.AUTO_RETRY_ON_FAILURE = core._ui_bool(value)
    elif key == "auto_retry_max_attempts":
        core.AUTO_RETRY_MAX_ATTEMPTS = core._bounded_int(value, 1, 10)
    elif key == "auto_retry_recovery_mode":
        core.AUTO_RETRY_RECOVERY_MODE = core._normalize_auto_retry_recovery_mode(
            value, strict=True
        )
    elif key == "auto_retry_walk_out":
        core.AUTO_RETRY_WALK_OUT = core._ui_bool(value)
    elif key == "auto_retry_walk_seconds":
        core.AUTO_RETRY_WALK_SECONDS = core._bounded_float(value, 0.5, 10.0)
    elif key == "diagnostic_mode":
        core.DIAGNOSTIC_MODE = core._ui_bool(value)
    elif key == "after_switch_wait_sec":
        core.NEW_GAME_WAIT = core._bounded_float(value, 0.0, 10.0)
    elif key == "no_yellow_fallback_enabled":
        core.NO_YELLOW_FALLBACK_ENABLED = core._ui_bool(value)
    elif key == "no_yellow_timeout_sec":
        core.NO_YELLOW_TIMEOUT_SEC = core._bounded_float(value, 1.0, 300.0)
    elif key == "progression_stall_timeout_sec":
        core.PROGRESSION_STALL_TIMEOUT_SEC = core._bounded_float(value, 30.0, 1800.0)
    elif key == "manual_next_key":
        new_key = core._normalize_hotkey_name(value)
        core._validate_hotkey_input_conflict(new_key)
        if any(core._hotkeys_overlap(new_key, existing) for existing in (core.START_STOP_HOTKEY, core.PAUSE_HOTKEY)):
            raise ValueError("That hotkey is already assigned.")
        if new_key != core.MANUAL_NEXT_KEY:
            previous_key = core.MANUAL_NEXT_KEY
            core.MANUAL_NEXT_KEY = new_key
            if not core.register_manual_next_hotkey():
                core.MANUAL_NEXT_KEY = previous_key
                raise _HotkeyRegistrationError(_hotkey_registration_error("Skip Current Stat", new_key))
    elif key == "start_stop_hotkey":
        new_key = core._normalize_hotkey_name(value)
        core._validate_hotkey_input_conflict(new_key)
        if any(core._hotkeys_overlap(new_key, existing) for existing in (core.MANUAL_NEXT_KEY, core.PAUSE_HOTKEY)):
            raise ValueError("That hotkey is already assigned.")
        if new_key != core.START_STOP_HOTKEY:
            previous_key = core.START_STOP_HOTKEY
            core.START_STOP_HOTKEY = new_key
            if not core.register_start_stop_hotkey():
                core.START_STOP_HOTKEY = previous_key
                raise _HotkeyRegistrationError(_hotkey_registration_error("Start / Stop", new_key))
    elif key == "pause_hotkey":
        new_key = core._normalize_hotkey_name(value)
        core._validate_hotkey_input_conflict(new_key)
        if any(core._hotkeys_overlap(new_key, existing) for existing in (core.START_STOP_HOTKEY, core.MANUAL_NEXT_KEY)):
            raise ValueError("That hotkey is already assigned.")
        if new_key != core.PAUSE_HOTKEY:
            previous_key = core.PAUSE_HOTKEY
            core.PAUSE_HOTKEY = new_key
            if not core.register_pause_hotkey():
                core.PAUSE_HOTKEY = previous_key
                raise _HotkeyRegistrationError(_hotkey_registration_error("Pause / Resume", new_key))
    elif key == "health_hit_cooldown_sec":
        core.HEALTH_HIT_COOLDOWN_SEC = core._bounded_float(value, 0.0, 5.0)
    elif key == "health_mode":
        if value not in ("v1_legacy", "v2_track"):
            raise ValueError("Health mode must be v1_legacy or v2_track")
        core.HEALTH_MODE = str(value)
    elif key == "wasd_key_press_delay_sec":
        core.KEY_PRESS_DELAY = core._bounded_float(value, 0.0, 1.0)
    elif key == "wasd_stabilize_delay_sec":
        core.STABILIZE_DELAY = core._bounded_float(value, 0.0, 5.0)
    elif key == "wasd_post_burst_delay_sec":
        core.POST_COMBO_DELAY = core._bounded_float(value, 0.0, 5.0)
    elif key == "agility_mode":
        if value not in ("v1", "v2"):
            raise ValueError("Agility mode must be v1 or v2")
        core.AGILITY_MODE = str(value)
    elif key == "agility_green_observe_sec":
        core.AGILITY_GREEN_OBSERVE_SEC = core._bounded_float(value, 0.1, 5.0)
    elif key == "agility_inter_string_wait_sec":
        core.AGILITY_INTER_STRING_WAIT_SEC = core._bounded_float(value, 0.1, 10.0)
    elif key == "agility_after_green_settle_sec":
        core.AGILITY_AFTER_GREEN_SETTLE_SEC = core._bounded_float(value, 0.0, 5.0)
    elif key == "training_order":
        core.TRAINING_ORDER_CUSTOM = core._sanitize_training_order(value)
    elif key == "ki_v8_click_delay_sec":
        core.KI_V8_CLICK_DELAY_SEC = core._bounded_float(value, 0.0, 0.7)
    elif key == "ki_v8_mode":
        if value not in ("v1_time", "v2_ring"):
            raise ValueError("Ki mode must be v1_time or v2_ring")
        core.KI_V8_MODE = str(value)
    elif key == "ki_v8_v2_target_r_factor":
        core.KI_V8_V2_TARGET_R_FACTOR = min(
            3.0, max(core.KI_V8_V2_R_MIN_FACTOR, core._finite_float(value))
        )
    elif key == "ki_v8_v2_brightness_threshold":
        core.KI_V8_V2_BRIGHTNESS_THRESHOLD = core._bounded_int(value, 50, 255)
    elif key == "ki_v8_v2_bright_count_threshold":
        core.KI_V8_V2_BRIGHT_COUNT_THRESHOLD = core._bounded_int(value, 1, 32)
    elif key == "ki_latency_comp_ms":
        core.KI_LATENCY_COMP_MS = min(250, max(0, core._finite_int(value)))
    elif key == "ki_adaptive_brightness":
        core.KI_V8_ADAPTIVE_BRIGHTNESS = core._ui_bool(value)
    elif key == "trait_click_approach":
        core.TRAIT_CLICK_APPROACH_ENABLED = core._ui_bool(value)
    elif key == "senzu_enabled":
        core.SENZU_ENABLED = core._ui_bool(value)
    elif key == "heal_method":
        core.HEAL_METHOD = core._normalize_heal_method(value, strict=True)
    elif key == "majin_regen_slot":
        core.MAJIN_REGEN_SLOT = min(8, max(1, core._finite_int(value)))
    elif key == "majin_regen_senzu_reserve":
        core.MAJIN_REGEN_SENZU_RESERVE = core._ui_bool(value)
    elif key == "majin_regen_auto":
        core.MAJIN_REGEN_AUTO = core._ui_bool(value)
    elif key == "senzu_slot":
        core.SENZU_SLOT = min(4, max(1, core._finite_int(value)))
    elif key == "senzu_delay_sec":
        core.SENZU_DELAY_SEC = core._bounded_float(value, 0.0, 30.0)
    elif key == "senzu_recovery_timeout_sec":
        core.SENZU_RECOVERY_TIMEOUT_SEC = core._bounded_float(value, 1.0, 30.0)
    elif key == "senzu_preference_mode":
        new_preference = core._normalize_senzu_preference(value, strict=True)
        if new_preference != core.SENZU_PREFERENCE_MODE:
            core._invalidate_senzu_row_cache()
        core.SENZU_PREFERENCE_MODE = new_preference
    elif key == "senzu_zero_gravity_on_empty":
        core.SENZU_ZERO_GRAVITY_ON_EMPTY = core._ui_bool(value)
    else:
        raise ValueError(f"Unknown key: {key}")


def _ui_apply_setting(key, value):
    # Keep the global mutation and its persisted snapshot in one critical
    # section. Otherwise two quick UI changes can save in reverse order and
    # restore a stale value on the next launch.
    import xynmacro_core as core

    with core._config_lock:
        previous_settings = core._ui_config_snapshot()
        core._ui_apply_setting_unlocked(key, value)
        try:
            core.save_master_config()
        except Exception:
            # Keep the live state consistent with the UI and the last complete
            # config when persistence fails.
            if key == "startup_window_mode":
                for legacy_key in ("restore_fullscreen_on_start", "windowed_mode_on_start"):
                    core._ui_apply_setting_unlocked(legacy_key, previous_settings[legacy_key])
            else:
                core._ui_apply_setting_unlocked(key, previous_settings[key])
            raise


def _cleanup_stale_port_files(own_launcher_pid):
    """Delete port_<pid>.json files left behind by launchers that no longer exist.

    The Rust shell deletes its own file on clean close. The launcher PID in each
    filename lets crash leftovers be removed without touching an active instance."""
    import xynmacro_core as core

    import glob
    import re
    for path in glob.glob(core.os.path.join(core.DATA_DIR, "port_*.json")):
        m = re.fullmatch(r"port_(\d+)\.json", core.os.path.basename(path))
        if not m:
            continue
        pid = int(m.group(1))
        if pid == own_launcher_pid:
            continue
        try:
            if not core._pid_alive(pid):
                core.os.remove(path)
                print(f"[sidecar] Removed stale port file: {core.os.path.basename(path)}")
        except OSError:
            pass  # locked or already gone — another instance may own it


def _start_parent_watchdog(parent_pid):
    """Background thread that monitors the Tauri launcher PID. When the launcher
    process disappears (closed window, crash, or terminated dev command), this
    sidecar exits immediately so an orphan cannot retain global hotkeys.

    No-op if parent_pid is missing (developer ran the script directly without --pid)."""
    import xynmacro_core as core

    if not parent_pid or int(parent_pid) == core.os.getpid():
        return

    def _watch():
        pid = int(parent_pid)
        while True:
            core.time.sleep(0.25)
            try:
                alive = core._pid_alive(pid)
            except Exception:
                # If the check itself blows up, don't kill ourselves over it
                continue
            if not alive:
                print(f"[sidecar] Tauri launcher (PID {pid}) is gone. Exiting to avoid orphaning.")
                # Deliberately NOT marking the session clean here. The launcher
                # vanishing is exactly what an app crash looks like from this side,
                # and marking it clean would swallow the case the crash notice
                # exists for. An orderly close is announced by the launcher itself
                # (session_shutdown_clean) while it is still alive to say so.
                try:
                    core.os.remove(core.os.path.join(core.DATA_DIR, f"port_{pid}.json"))
                except OSError:
                    pass
                # Lift any key/button the macro is holding (W/S walk, G panel,
                # Shift charge) before vanishing; os._exit skips all cleanup.
                try:
                    core._release_macro_input()
                except Exception:
                    pass
                # os._exit skips atexit/finalizers — important because the macro thread
                # is daemon and we just want to vanish fast, not run cleanup.
                core.os._exit(0)

    core.threading.Thread(target=_watch, daemon=True, name="parent-watchdog").start()


def _ui_start_macro(allow_windowed_fallback=False, expected_generation=None):
    """Start the macro. Returns ``(ok, message, block_code)``.

    ``block_code`` names a refusal the UI can offer a way past — currently only
    "windowed_too_small" — and is None for every other outcome.
    """
    import xynmacro_core as core

    # Reserve before waiting at the Start lock, so F6 sees the pending run even
    # in that gap. Coalesced requests share its generation through setup/launch.
    with core._stop_input_gate:
        if core._sidecar_closing:
            return False, "XynMacro is closing.", None
        if expected_generation is not None and expected_generation != core._macro_start_generation:
            return False, "Start cancelled by Stop.", None
        if core._macro_pending_start_generation is None and not core._ui_is_running():
            core._macro_start_generation += 1
            core._macro_pending_start_generation = core._macro_start_generation
            core._macro_pending_start_requests = 0
        start_generation = core._macro_start_generation
        if core._macro_pending_start_generation == start_generation:
            core._macro_pending_start_requests += 1
    try:
        # Serialize setup/launch without blocking Stop or key delivery on native
        # window calls. Every waiter keeps the reservation it joined above.
        with core._macro_start_lock:
            with core._stop_input_gate:
                if core._sidecar_closing:
                    return False, "XynMacro is closing.", None
                if start_generation != core._macro_start_generation:
                    return False, "Start cancelled by Stop.", None
            core._cancel_pc_shutdown()
            with core._shutdown_lock:
                if core._shutdown_status == "requesting":
                    return False, "Windows shutdown request is still in progress.", None
                if core._shutdown_status in ("requested", "failed", "unknown"):
                    core._shutdown_generation += 1
                    core._shutdown_status = "idle"
            if core._ui_is_running():
                return True, "Already running", None
            if core._macro_pending_start_generation != start_generation:
                # The owner launched and finished before this waiter got the
                # lock. It must not launch another run with that consumed token.
                return True, "Start already handled", None
            if core._calibration_is_active():
                message = "Finish or cancel calibration first."
                print(f"[calibrate] {message}")
                return False, message, None
            if not core._sanitize_training_order(core.TRAINING_ORDER_CUSTOM):
                return False, "Add at least one stat to Training Order.", None
            # Refuse before the countdown or worker starts. Waiting until the first
            # capture makes Start look successful for several seconds and risks any
            # future startup input landing in whichever window currently has focus.
            # A minimized Roblox has no client rect; restore it instead of refusing.
            if not core.update_game_window() and not core.restore_game_window():
                return False, "Open Roblox before starting XynMacro.", None
            if core.GAME_HWND is None:
                return False, "Open Roblox before starting XynMacro.", None
            # Do this before the countdown so the scan regions are measured against
            # the window the run will actually use. Windowed Mode wins when both are on:
            # it is the more specific request, and fullscreen would undo it.
            if core.WINDOWED_MODE_ON_START:
                windowed_ok, windowed_message = core.ensure_game_windowed(
                    allow_fallback=allow_windowed_fallback, keep_position=True
                )
                if not windowed_ok:
                    return False, windowed_message, core.WINDOWED_BLOCK_CODE
            elif core.RESTORE_FULLSCREEN_ON_START:
                core.ensure_game_fullscreen()
            if (
                core._background_monitor_thread is not None
                and core._background_monitor_thread.is_alive()
                and core._background_monitor_stop is not None
                and core._background_monitor_stop.is_set()
            ):
                return False, "Previous game monitor is still stopping. Try Start again shortly.", None
            with core._stop_input_gate:
                # Stop may arrive while restoring/sizing Roblox. It must cancel
                # this Start instead of being cleared by the new run's reset.
                if core._sidecar_closing:
                    return False, "XynMacro is closing.", None
                if start_generation != core._macro_start_generation:
                    return False, "Start cancelled by Stop.", None
                core.UI_STOP_REQUESTED = False
                core.MACRO_LAST_ERROR = None
                core._begin_run_result()
                core.MACRO_STARTED_AT = core.time.time()
                core.MACRO_STARTED_MONOTONIC = core.time.monotonic()
                core._telemetry_reset()
                core.MACRO_THREAD = core.threading.Thread(target=core._run_macro_safe, daemon=True)
                core.MACRO_THREAD.start()
                # Keep the run's generation, including Stops accepted during
                # setup, but consume its reservation so waiters cannot relaunch.
                core._macro_pending_start_generation = None
                core._macro_pending_start_requests = 0
        return True, "Started", None
    finally:
        with core._stop_input_gate:
            # A refusal keeps the reservation while coalesced requests remain.
            # Cancelled requests must never clean up a newer Start's reservation.
            if core._macro_pending_start_generation == start_generation:
                core._macro_pending_start_requests -= 1
                if not core._macro_pending_start_requests:
                    core._macro_pending_start_generation = None


def _ui_stop_macro(expected_generation=None):
    import xynmacro_core as core

    with core._stop_input_gate:
        if expected_generation is not None and expected_generation != core._macro_start_generation:
            return False, "Stop request is no longer current"
        core._macro_start_generation += 1
        core._macro_pending_start_generation = None
        core._macro_pending_start_requests = 0
        core._USER_STOP_LATCHED = True
        core.UI_STOP_REQUESTED = True
    core._cancel_pc_shutdown()
    if core._ui_is_running():
        core._record_run_outcome("stopped", "User requested stop")
    return True, "Stop requested"


def _validated_auth_token(auth_token, frozen=None):
    """Return the configured launch token, or allow an unauthenticated source run.

    Installed sidecars must always be launched by Rust with a per-launch token.
    Keeping the fallback limited to an unfrozen Python process lets developers run
    this file directly without weakening the packaged app.
    """
    import xynmacro_core as core

    token = str(auth_token or "").strip()
    if token:
        return token
    is_frozen = getattr(core.sys, "frozen", False) if frozen is None else bool(frozen)
    if is_frozen:
        raise ValueError("--auth-token is required in packaged sidecar builds")
    return None


def _loopback_request_denial(host_header, origin, presented_token, auth_token):
    """Return ``(message, status)`` when a sidecar HTTP request must be denied."""
    import xynmacro_core as core

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
        if not core.hmac.compare_digest(candidate, auth_token):
            return "unauthorized", 401
    return None


def _make_exclusive_server(app):
    import socket
    from werkzeug.serving import make_server

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        return make_server("127.0.0.1", listener.getsockname()[1], app,
                           threaded=True, fd=listener.fileno())


def _scrub_tree(value):
    """Apply the shared share-redaction to every string in a JSON-like value."""
    import xynmacro_core as core

    if isinstance(value, str):
        return core._scrub_user_paths(value)
    if isinstance(value, dict):
        return {key: _scrub_tree(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_scrub_tree(item) for item in value]
    return value


def run_ui_server(sidecar_pid=None, auth_token=None):
    """Headless Flask backend for the Tauri shell.

    sidecar_pid is required. The chosen port is written to
    ``port_<sidecar_pid>.json`` next to this script so the Rust launcher
    can read it and proxy HTTP requests to /state, /command, /logs, etc.
    """
    import xynmacro_core as core

    from flask import Flask, request, jsonify, send_from_directory

    core._start_session_logging()

    ui_dir = core.os.path.join(core.BASE_DIR, "ui")
    app = Flask(__name__, static_folder=ui_dir, static_url_path="")
    app.config["MAX_CONTENT_LENGTH"] = 1024 * 1024

    # This API drives raw mouse/keyboard injection and listens on a predictable
    # loopback port, so lock it to same-origin local callers. The only legit
    # client is the Tauri Rust proxy (server-to-server: Host 127.0.0.1, no
    # Origin header). Rejecting foreign Hosts blocks DNS-rebinding, and
    # rejecting any Origin blocks a browser page from POSTing /command to start
    # the macro. (Previously the app used a wildcard CORS(app), which allowed
    # exactly that cross-origin access.)
    @app.before_request
    def _guard_loopback():
        denial = core._loopback_request_denial(
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
        snap = core._ui_state_snapshot()
        snap["save_dir"] = core.SAVE_DIR
        snap["config_dir"] = core.JSON_DIR
        return jsonify(snap)

    @app.route("/health", methods=["GET"])
    def health():
        return jsonify(core._health_snapshot())

    @app.route("/diagnostics", methods=["GET"])
    def diagnostics():
        # The report can be copied out; exception text may carry paths/emails.
        return jsonify(_scrub_tree(core._diagnostic_report()))

    @app.route("/logs", methods=["GET"])
    def logs():
        since = request.args.get("since", type=int, default=0)
        return jsonify(core._log_entries_since(since))

    @app.route("/preview", methods=["GET"])
    def preview():
        """Return a base64-PNG snapshot of a named region.
        Usage: /preview?region=health_box | agility_box | game_window | diagnostics
        Used by the Calibration tab to show users exactly what the macro sees."""
        region = (request.args.get("region") or "").strip().lower()
        try:
            with core.mss.MSS() as _sct:
                if region == "health_box":
                    box = core._reference_box(core.HEALTH_BOX)
                elif region == "agility_box":
                    box = core._reference_box(core.AGILITY_BOX)
                elif region == "game_window":
                    core.update_game_window()
                    if core.GAME_HWND is None:
                        return jsonify({"ok": False, "msg": "Roblox window not found"})
                    box = {"left": core.GAME_OFFSET_X, "top": core.GAME_OFFSET_Y,
                           "width": core.GAME_WIDTH, "height": core.GAME_HEIGHT}
                elif region == "diagnostics":
                    if core.GAME_HWND is None:
                        core.update_game_window()
                    if core.GAME_HWND is None:
                        return jsonify({"ok": False, "msg": "Roblox window not found"})
                    box = core._confirmed_game_capture_rect()
                    bgr = core._diagnostic_overlay_frame(_sct, box)
                else:
                    return jsonify({"ok": False, "msg": f"Unknown region: {region}"})

                if region != "diagnostics":
                    raw = core.np.array(_sct.grab(box))
                    bgr = raw[:, :, :3]
                ok, buf = core.cv2.imencode(".png", bgr)
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
            import time as _t
            core.os.makedirs(core.SAVE_DIR, exist_ok=True)
            stamp = _t.strftime("%Y%m%d-%H%M%S")
            suffix = 0
            while True:
                name = f"log-{stamp}{f'-{suffix}' if suffix else ''}.txt"
                path = core.os.path.join(core.SAVE_DIR, name)
                try:
                    saved = open(path, "xb")
                    break
                except FileExistsError:
                    suffix += 1
            with saved:
                session_handle = getattr(core.sys.stdout, "_file", None)
                source_path = getattr(session_handle, "name", None)
                if session_handle is not None and source_path and core.os.path.isfile(source_path):
                    session_handle.flush()
                    line_count = 0
                    with open(source_path, "r", encoding="utf-8", errors="replace") as source:
                        for line in source:
                            saved.write(core._scrub_user_paths(line).encode("utf-8"))
                            line_count += 1
                    return jsonify({
                        "ok": True,
                        "path": path,
                        "count": line_count,
                        "full_session": True,
                    })

                # Fallback for an unusual stdout wrapper that has no session file.
                for entry in core._ui_log_ring:
                    saved.write((core._shareable_log([entry]) + "\n").encode("utf-8"))
            return jsonify({
                "ok": True,
                "path": path,
                "count": len(core._ui_log_ring),
                "full_session": False,
            })
        except Exception as e:
            return jsonify({"ok": False, "msg": str(e)})

    @app.route("/copy_log", methods=["POST"])
    def copy_log():
        # The viewer holds raw lines; the clipboard gets the same redaction as
        # Save Log and bug reports.
        entries = list(core._ui_log_ring)
        return jsonify({"ok": True, "text": core._shareable_log(entries), "count": len(entries)})

    @app.route("/open_save_dir", methods=["POST"])
    def open_save_dir():
        try:
            core.os.makedirs(core.SAVE_DIR, exist_ok=True)
            core.os.startfile(core.SAVE_DIR)  # opens the folder in Explorer (Windows)
            return jsonify({"ok": True, "path": core.SAVE_DIR})
        except Exception as e:
            return jsonify({"ok": False, "msg": str(e)})

    @app.route("/open_config_dir", methods=["POST"])
    def open_config_dir():
        try:
            core.os.makedirs(core.JSON_DIR, exist_ok=True)
            core.os.startfile(core.JSON_DIR)  # settings + calibration live here
            return jsonify({"ok": True, "path": core.JSON_DIR})
        except Exception as e:
            return jsonify({"ok": False, "msg": str(e)})

    @app.route("/open_discord", methods=["POST"])
    def open_discord():
        # Fixed URL, so the page can't ask the backend to open anything else.
        try:
            core.os.startfile(DISCORD_INVITE_URL)
            return jsonify({"ok": True})
        except Exception as e:
            return jsonify({"ok": False, "msg": str(e)})

    @app.route("/pick_save_dir", methods=["POST"])
    def pick_save_dir():
        if not core._save_dir_picker_lock.acquire(blocking=False):
            return jsonify({"ok": False, "msg": "Folder picker already open"})
        try:
            import tkinter as tk
            from tkinter import filedialog
            root = tk.Tk()
            try:
                root.withdraw()
                root.attributes("-topmost", True)
                chosen = filedialog.askdirectory(initialdir=core.SAVE_DIR, title="Choose where saves go")
            finally:
                root.destroy()
            if chosen:
                core._set_save_dir(chosen)
                return jsonify({"ok": True, "path": core.SAVE_DIR})
            return jsonify({"ok": False, "msg": "cancelled"})
        except Exception as e:
            return jsonify({"ok": False, "msg": str(e)})
        finally:
            core._save_dir_picker_lock.release()

    @app.route("/command", methods=["POST"])
    def command():
        data = request.get_json(silent=True)
        if not isinstance(data, dict) or not isinstance(data.get("action"), str):
            return jsonify({"ok": False, "msg": "Expected an object with an action string"}), 400
        action = data["action"].strip().lower()
        value = data.get("value")

        try:
            if action == "start":
                allow_fallback = bool(
                    isinstance(value, dict) and value.get("allow_windowed_fallback")
                )
                ok, msg, code = core._ui_start_macro(allow_windowed_fallback=allow_fallback)
                return jsonify({"ok": ok, "msg": msg, "code": code})
            if action == "session_shutdown_clean":
                # The launcher is about to kill this process on purpose. Its own
                # parent watchdog would not get to run first.
                core._ui_shutdown_clean()
                return jsonify({"ok": True, "msg": "session marked clean"})
            if action == "stop":
                ok, msg = core._ui_stop_macro()
                return jsonify({"ok": ok, "msg": msg})
            if action == "shutdown_cancel":
                ok, msg = core._cancel_pc_shutdown()
                return jsonify({"ok": ok, "msg": msg, "shutdown": core._shutdown_snapshot()})
            if action == "notifications_save":
                state = core.notifications.configure(value)
                return jsonify({"ok": True, "msg": "Notification settings saved", "notifications": state})
            if action == "notifications_clear":
                state = core.notifications.clear()
                return jsonify({"ok": True, "msg": "Webhook removed", "notifications": state})
            if action == "notifications_test":
                queued = core.notifications.send("test")
                return jsonify({"ok": queued, "msg": "Test queued; check delivery status" if queued else "Save a webhook first, or wait if rate limited"})
            if action == "set":
                key = value.get("key") if isinstance(value, dict) else None
                val = value.get("value") if isinstance(value, dict) else value
                if key:
                    core._ui_apply_setting(key, val)
                    normalized = core._ui_config_snapshot().get(key)
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
                ok, msg = core._ui_calibrate_button_begin(str(stat))
                return jsonify({"ok": ok, "msg": msg})
            if action == "calibrate_button_cancel":
                ok, msg = core._ui_calibrate_button_cancel()
                return jsonify({"ok": ok, "msg": msg})
            if action == "calibrate_region_begin":
                region = (value or {}).get("region") if isinstance(value, dict) else value
                if not region:
                    return jsonify({"ok": False, "msg": "Missing region"})
                ok, msg = core._ui_calibrate_region_begin(str(region))
                return jsonify({"ok": ok, "msg": msg})
            if action == "calibrate_region_cancel":
                ok, msg = core._ui_calibrate_region_cancel()
                return jsonify({"ok": ok, "msg": msg})
            if action == "reset_defaults":
                if core._ui_is_running():
                    return jsonify({"ok": False, "msg": "Stop the macro before resetting settings"})
                with core._config_lock:
                    core.reset_user_settings_to_defaults()
                    core.save_master_config()
                failed = [_hotkey_registration_error(label, key) for label, key, registered in (
                    ("Start / Stop", core.START_STOP_HOTKEY, core.register_start_stop_hotkey()),
                    ("Skip Current Stat", core.MANUAL_NEXT_KEY, core.register_manual_next_hotkey()),
                    ("Pause / Resume", core.PAUSE_HOTKEY, core.register_pause_hotkey()),
                ) if not registered]
                if failed:
                    return jsonify({"ok": False, "msg": "Defaults restored. " + " ".join(failed)})
                return jsonify({"ok": True, "msg": "Defaults restored"})
            if action == "factory_reset":
                if core._ui_is_running():
                    return jsonify({"ok": False, "msg": "Stop the macro before factory reset"})
                with core._config_lock:
                    core.notifications.clear()
                    core._cancel_pc_shutdown()
                    core.factory_reset_configuration()
                failed = [_hotkey_registration_error(label, key) for label, key, registered in (
                    ("Start / Stop", core.START_STOP_HOTKEY, core.register_start_stop_hotkey()),
                    ("Skip Current Stat", core.MANUAL_NEXT_KEY, core.register_manual_next_hotkey()),
                    ("Pause / Resume", core.PAUSE_HOTKEY, core.register_pause_hotkey()),
                ) if not registered]
                if failed:
                    return jsonify({"ok": False, "msg": "Factory reset completed. " + " ".join(failed)})
                return jsonify({
                    "ok": True,
                    "msg": "Macro settings, calibration, and save location restored",
                })
            if action == "bug_report":
                data = value if isinstance(value, dict) else {}
                return jsonify(core.bug_report_payload(
                    data.get("sections"),
                    webview=data.get("webview"),
                    description=data.get("description"),
                    ui_errors=data.get("ui_errors"),
                ))
            if action == "bug_report_open":
                # Only a link built by an earlier preview can be opened: the WebView
                # names the report it showed, never a URL, and logs that arrived
                # since the preview cannot slip into what gets posted.
                report_id = value.get("report_id") if isinstance(value, dict) else None
                url = core.reviewed_bug_report_url(report_id)
                if not url:
                    return jsonify({
                        "ok": False,
                        "msg": "Preview the report again before opening it",
                    })
                try:
                    core.os.startfile(url)
                except Exception as error:
                    return jsonify({"ok": False, "msg": f"Could not open browser: {error}"})
                return jsonify({"ok": True, "msg": "Opened GitHub in your browser"})
            if action == "game_window_windowed":
                if core._ui_is_running():
                    return jsonify({
                        "ok": False,
                        "msg": "Stop the macro before resizing the game window",
                    })
                ok, msg = core.ensure_game_windowed()
                return jsonify({"ok": ok, "msg": msg})
            if action == "display_set_1080":
                if core._ui_is_running():
                    return jsonify({
                        "ok": False,
                        "msg": "Stop the macro before changing display resolution",
                    })
                ok, code = core.display_set_resolution(1920, 1080)
                core.update_game_window()
                if ok and core.DISPLAY_CONFIRM_CHANGES:
                    core._arm_display_confirm()
                    return jsonify({
                        "ok": True,
                        "msg": f"Display set to 1920x1080 — confirm within "
                               f"{int(core.DISPLAY_CONFIRM_TIMEOUT_SEC)}s or it reverts",
                        "confirm": core._display_confirm_state(),
                    })
                return jsonify({"ok": ok, "msg": "Display set to 1920x1080"
                                if ok else f"Display change failed (code {code})"})
            if action == "display_keep":
                ok, msg = core.display_keep_resolution()
                return jsonify({"ok": ok, "msg": msg})
            if action == "display_revert":
                if core._ui_is_running():
                    return jsonify({
                        "ok": False,
                        "msg": "Stop the macro before reverting display resolution",
                    })
                core._cancel_display_confirm()
                ok, code = core.display_revert_resolution()
                core.update_game_window()
                return jsonify({"ok": ok, "msg": "Display reverted to your Windows setting"
                                if ok else f"Revert failed (code {code})"})
        except _HotkeyRegistrationError as e:
            return jsonify({"ok": False, "msg": str(e)})
        except Exception as e:
            print(f"[ui] Command {action} failed: {e}")
            return jsonify({"ok": False, "msg": "Something went wrong. Check Diagnostics."})

        return jsonify({"ok": False, "msg": f"Unknown action: {action}"})

    if sidecar_pid is not None:
        # Bind before publishing the port so another process cannot take it.
        server = core._make_exclusive_server(app)
        port = server.port
        url = f"http://127.0.0.1:{port}"
        import logging as _logging
        _logging.getLogger('werkzeug').setLevel(_logging.ERROR)
        # Silence Flask's own startup banner (" * Serving Flask app", " * Debug
        # mode: off", the dev-server warning) — it's framework noise, not our log.
        import flask.cli as _flask_cli
        _flask_cli.show_server_banner = lambda *a, **k: None
        core.os.makedirs(core.DATA_DIR, exist_ok=True)
        core._cleanup_stale_port_files(int(sidecar_pid))
        port_file = core.os.path.join(core.DATA_DIR, f"port_{sidecar_pid}.json")
        try:
            with open(port_file, "w", encoding="utf-8") as f:
                core.json.dump({"port": port, "pid": core.os.getpid()}, f)
            print(f"[sidecar] Wrote port file: {port_file} (port={port})")
        except Exception as e:
            print(f"[sidecar] Failed to write port file: {e}")

        # Parent-PID watchdog — if the Tauri launcher dies (closed, crashed, or
        # killed via Ctrl+C without a clean shutdown), this sidecar would otherwise
        # become an orphan that keeps its F6 hotkey hook registered. When the user
        # later presses F6, every orphan plus the active sidecar all fire their
        # macros and click in parallel. Polling the launcher PID lets us self-exit
        # cleanly when our parent goes away.
        core._start_parent_watchdog(sidecar_pid)

        # Global hotkeys (OS-level via the `keyboard` lib) — fire even when the
        # game window has focus, unlike the Tauri WebView's local keys.
        core.register_start_stop_hotkey()
        core.register_manual_next_hotkey()
        core.register_pause_hotkey()

        print(f"[sidecar] Flask listening on {url}")
        try:
            server.serve_forever()
        finally:
            server.server_close()
        return

    raise RuntimeError("run_ui_server requires sidecar_pid; the Tauri shell owns the window now")
