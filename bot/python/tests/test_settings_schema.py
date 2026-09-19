"""Compatibility checks against snapshots captured from the shipped beta.3."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import settings_schema
import xynmacro_core as core

BASELINE = json.loads(
    (Path(__file__).parent / "fixtures/settings-beta3.json").read_text(encoding="utf-8")
)


class SettingsSchemaTests(unittest.TestCase):
    def setUp(self):
        # Every mutation below is in memory or a temporary directory.
        runtime = {name: getattr(core, name) for _, name, _ in settings_schema.SETTING_FIELDS}
        self.runtime_patch = patch.multiple(core, **runtime)
        self.runtime_patch.start()
        self.addCleanup(self.runtime_patch.stop)
        compatibility = patch.dict(core.COMPATIBILITY_SETTINGS)
        compatibility.start()
        self.addCleanup(compatibility.stop)
        core.reset_user_settings_to_defaults()

    def test_defaults_and_snapshots_match_the_shipped_schema_and_types(self):
        snapshots = {
            "defaults": core.DEFAULT_USER_SETTINGS,
            "master": core._master_config_snapshot(),
            "ui": core._ui_config_snapshot(),
        }
        for name, snapshot in snapshots.items():
            with self.subTest(name=name):
                self.assertEqual(snapshot, BASELINE[name])
                self.assertEqual(list(snapshot), list(BASELINE[name]))
                for key, value in snapshot.items():
                    self.assertIs(type(value), type(BASELINE[name][key]), key)

    def test_each_field_is_unique_and_has_a_real_global(self):
        fields = settings_schema.SETTING_FIELDS
        keys = [key for key, _, _ in fields]
        names = [name for _, name, _ in fields]
        self.assertEqual(len(set(keys)), len(keys))
        self.assertEqual(len(set(names)), len(names))
        self.assertFalse(set(keys) & set(core.COMPATIBILITY_SETTINGS))
        self.assertEqual(set(keys) | set(core.COMPATIBILITY_SETTINGS), set(BASELINE['defaults']))
        for name in names:
            self.assertTrue(hasattr(core, name), name)

    def test_master_serializes_while_ui_preserves_raw_runtime_types(self):
        core.START_DELAY = 2
        core.SENZU_ENABLED = 1
        core.GC_GRAVITY_TARGET_G = 10.0
        core.AFTER_RUN_GAME_ACTION = object()
        master, ui = core._master_config_snapshot(), core._ui_config_snapshot()
        self.assertIs(type(master['start_delay_sec']), float)
        self.assertIs(type(ui['start_delay_sec']), int)
        self.assertIs(master['senzu_enabled'], True)
        self.assertIs(type(ui['senzu_enabled']), int)
        self.assertIs(type(master['gc_gravity_target_g']), int)
        self.assertIs(type(ui['gc_gravity_target_g']), float)
        self.assertIs(master['after_run_game_action'], core.AFTER_RUN_GAME_ACTION)

    def test_training_order_is_sanitized_and_snapshots_do_not_share_its_list(self):
        original = list(core.DEFAULT_TRAINING_ORDER)
        self.assertIsNot(core.DEFAULT_USER_SETTINGS['training_order'], core.DEFAULT_TRAINING_ORDER)
        core.TRAINING_ORDER_CUSTOM = ['Ki Damage', 'Health', 'Ki Damage', 'invalid']
        expected = core._sanitize_training_order(core.TRAINING_ORDER_CUSTOM)
        master, ui = core._master_config_snapshot(), core._ui_config_snapshot()
        self.assertEqual(master['training_order'], expected)
        self.assertEqual(ui['training_order'], expected)
        master['training_order'].clear()
        ui['training_order'].clear()
        self.assertEqual(core.TRAINING_ORDER_CUSTOM, ['Ki Damage', 'Health', 'Ki Damage', 'invalid'])
        defaults = settings_schema.default_settings(vars(core))
        defaults['training_order'].clear()
        self.assertEqual(core.DEFAULT_TRAINING_ORDER, original)
        self.assertEqual(core.DEFAULT_USER_SETTINGS['training_order'], original)

    def test_startup_mode_is_derived_only_in_ui_with_windowed_precedence(self):
        for fullscreen, windowed, expected in (
            (False, False, 'unchanged'), (True, False, 'fullscreen'),
            (False, True, 'windowed'), (True, True, 'windowed'),
        ):
            with self.subTest(fullscreen=fullscreen, windowed=windowed):
                core.RESTORE_FULLSCREEN_ON_START = fullscreen
                core.WINDOWED_MODE_ON_START = windowed
                self.assertEqual(core._ui_config_snapshot()['startup_window_mode'], expected)
                self.assertNotIn('startup_window_mode', core._master_config_snapshot())

    def test_saved_nondefaults_survive_reload_and_reset_preserves_shipped_defaults(self):
        changes = {
            'start_delay_sec': 2.75, 'wasd_key_press_delay_sec': 0.09,
            'health_mode': 'v1_legacy', 'ki_v8_mode': 'v1_time',
            'ki_latency_comp_ms': 123, 'ki_adaptive_brightness': True,
            'senzu_enabled': False, 'senzu_slot': 3, 'senzu_recovery_timeout_sec': 12.0,
            'mouse_click_button': 'right', 'respawn_settle_sec': 4.5,
            'scan_rate_limit_hz': 60, 'restart_ki_after_hit': True,
            'startup_window_mode': 'windowed',
            'training_order': ['Ki Damage', 'Health', 'Agility'],
        }
        with tempfile.TemporaryDirectory() as folder, patch.object(core, 'JSON_DIR', folder), patch.object(
            core, 'MACRO_CONFIG_FILE', str(Path(folder) / 'config.json')
        ):
            for key, value in changes.items():
                core._ui_apply_setting(key, value)
            expected_master = core._master_config_snapshot()
            expected_ui = core._ui_config_snapshot()
            core.reset_user_settings_to_defaults()
            self.assertEqual(core._master_config_snapshot(), BASELINE['master'])
            core.load_master_config()
            self.assertEqual(core._master_config_snapshot(), expected_master)
            self.assertEqual(core._ui_config_snapshot(), expected_ui)
            saved = json.loads(Path(core.MACRO_CONFIG_FILE).read_text(encoding='utf-8'))
            self.assertEqual(saved, expected_master)

    def test_legacy_keys_keep_their_existing_precedence(self):
        cases = [
            ({'wsad_key_press_delay_sec': 0.12}, 0.12),
            ({'agility_key_press_delay_sec': 0.13}, 0.13),
            ({'wasd_key_press_delay_sec': 0.14, 'wsad_key_press_delay_sec': 0.12,
              'agility_key_press_delay_sec': 0.13}, 0.14),
        ]
        with tempfile.TemporaryDirectory() as folder, patch.object(core, 'JSON_DIR', folder), patch.object(
            core, 'MACRO_CONFIG_FILE', str(Path(folder) / 'config.json')
        ):
            for values, expected in cases:
                with self.subTest(values=values):
                    Path(core.MACRO_CONFIG_FILE).write_text(json.dumps(values), encoding='utf-8')
                    core.load_master_config()
                    self.assertEqual(core.KEY_PRESS_DELAY, expected)
                    saved = json.loads(Path(core.MACRO_CONFIG_FILE).read_text(encoding='utf-8'))
                    self.assertEqual(saved['wasd_key_press_delay_sec'], expected)
                    self.assertNotIn('wsad_key_press_delay_sec', saved)
                    self.assertNotIn('agility_key_press_delay_sec', saved)


if __name__ == '__main__':
    unittest.main()
