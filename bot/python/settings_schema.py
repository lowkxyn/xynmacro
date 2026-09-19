"""Shared names and snapshot types for the existing user settings.

Validation, legacy migration and runtime side effects remain in the core.
This module only reads the namespace passed to it; it never loads user files.
"""

# (public setting key, runtime global, persisted/default type)
SETTING_FIELDS = (
    ('start_delay_sec', 'START_DELAY', float),
    ('gc_gravity_target_g', 'GC_GRAVITY_TARGET_G', int),
    ('prevent_sleep_while_running', 'PREVENT_SLEEP_WHILE_RUNNING', bool),
    ('restore_fullscreen_on_start', 'RESTORE_FULLSCREEN_ON_START', bool),
    ('windowed_mode_on_start', 'WINDOWED_MODE_ON_START', bool),
    ('display_confirm_changes', 'DISPLAY_CONFIRM_CHANGES', bool),
    ('shutdown_pc_when_finished', 'SHUTDOWN_PC_WHEN_FINISHED', bool),
    ('after_run_game_action', 'AFTER_RUN_GAME_ACTION', str),
    ('after_run_on_failure', 'AFTER_RUN_ON_FAILURE', bool),
    ('auto_retry_on_failure', 'AUTO_RETRY_ON_FAILURE', bool),
    ('auto_retry_max_attempts', 'AUTO_RETRY_MAX_ATTEMPTS', int),
    ('auto_retry_recovery_mode', 'AUTO_RETRY_RECOVERY_MODE', str),
    ('auto_retry_walk_out', 'AUTO_RETRY_WALK_OUT', bool),
    ('auto_retry_walk_seconds', 'AUTO_RETRY_WALK_SECONDS', float),
    ('diagnostic_mode', 'DIAGNOSTIC_MODE', bool),
    ('after_switch_wait_sec', 'NEW_GAME_WAIT', float),
    ('no_yellow_timeout_sec', 'NO_YELLOW_TIMEOUT_SEC', float),
    ('no_yellow_fallback_enabled', 'NO_YELLOW_FALLBACK_ENABLED', bool),
    ('manual_next_key', 'MANUAL_NEXT_KEY', str),
    ('start_stop_hotkey', 'START_STOP_HOTKEY', str),
    ('pause_hotkey', 'PAUSE_HOTKEY', str),
    ('health_hit_cooldown_sec', 'HEALTH_HIT_COOLDOWN_SEC', float),
    ('health_mode', 'HEALTH_MODE', str),
    ('wasd_key_press_delay_sec', 'KEY_PRESS_DELAY', float),
    ('wasd_stabilize_delay_sec', 'STABILIZE_DELAY', float),
    ('wasd_post_burst_delay_sec', 'POST_COMBO_DELAY', float),
    ('agility_mode', 'AGILITY_MODE', str),
    ('agility_green_observe_sec', 'AGILITY_GREEN_OBSERVE_SEC', float),
    ('agility_inter_string_wait_sec', 'AGILITY_INTER_STRING_WAIT_SEC', float),
    ('agility_after_green_settle_sec', 'AGILITY_AFTER_GREEN_SETTLE_SEC', float),
    ('training_order', 'TRAINING_ORDER_CUSTOM', list),
    ('ki_v8_mode', 'KI_V8_MODE', str),
    ('ki_v8_click_delay_sec', 'KI_V8_CLICK_DELAY_SEC', float),
    ('ki_v8_v2_target_r_factor', 'KI_V8_V2_TARGET_R_FACTOR', float),
    ('ki_v8_v2_brightness_threshold', 'KI_V8_V2_BRIGHTNESS_THRESHOLD', int),
    ('ki_v8_v2_bright_count_threshold', 'KI_V8_V2_BRIGHT_COUNT_THRESHOLD', int),
    ('ki_latency_comp_ms', 'KI_LATENCY_COMP_MS', int),
    ('ki_adaptive_brightness', 'KI_V8_ADAPTIVE_BRIGHTNESS', bool),
    ('trait_click_approach', 'TRAIT_CLICK_APPROACH_ENABLED', bool),
    ('senzu_enabled', 'SENZU_ENABLED', bool),
    ('senzu_slot', 'SENZU_SLOT', int),
    ('senzu_delay_sec', 'SENZU_DELAY_SEC', float),
    ('senzu_recovery_timeout_sec', 'SENZU_RECOVERY_TIMEOUT_SEC', float),
    ('senzu_preference_mode', 'SENZU_PREFERENCE_MODE', str),
    ('senzu_zero_gravity_on_empty', 'SENZU_ZERO_GRAVITY_ON_EMPTY', bool),
)


def default_settings(namespace):
    settings = {}
    for key, runtime_name, value_type in SETTING_FIELDS:
        source_name = "DEFAULT_TRAINING_ORDER" if key == "training_order" else runtime_name
        settings[key] = value_type(namespace[source_name])
    return settings


def snapshot_settings(namespace, *, serialize, sanitize_training_order):
    settings = {}
    for key, runtime_name, value_type in SETTING_FIELDS:
        value = namespace[runtime_name]
        if key == "training_order":
            value = list(sanitize_training_order(value))
        elif serialize and key != "after_run_game_action":
            # The saved after-run action has historically been passed through.
            value = value_type(value)
        settings[key] = value
    return settings
