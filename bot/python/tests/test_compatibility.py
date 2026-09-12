import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import xynmacro_core as core


@pytest.fixture(autouse=True)
def isolate():
    settings = dict(core.COMPATIBILITY_SETTINGS)
    flags = {name: getattr(core, name) for name in (
        '_USER_STOP_LATCHED', 'UI_STOP_REQUESTED', 'MANUAL_NEXT_REQUESTED',
        'PAUSE_TOGGLE_REQUESTED', 'AUTO_RETRY_WALK_OUT')}
    telemetry = dict(core.TELEMETRY)
    health = dict(core._HEALTH_V2_STATE)
    ki = dict(core._ki_v8_state)
    metrics = dict(core.LOOP_METRICS)
    completion = core.PROGRESSION_COMPLETE_REQUESTED.is_set()
    core.PROGRESSION_COMPLETE_REQUESTED.clear()
    core.COMPATIBILITY_SETTINGS.update(core.COMPATIBILITY_DEFAULTS)
    for name in flags:
        setattr(core, name, name == 'AUTO_RETRY_WALK_OUT')
    with patch.object(core, '_controller_decisions_suspended', return_value=False):
        yield
    core.COMPATIBILITY_SETTINGS.update(settings)
    core.TELEMETRY.update(telemetry)
    core._HEALTH_V2_STATE.update(health)
    core._ki_v8_state.update(ki)
    core.LOOP_METRICS.update(metrics)
    for name, value in flags.items():
        setattr(core, name, value)
    if completion:
        core.PROGRESSION_COMPLETE_REQUESTED.set()
    else:
        core.PROGRESSION_COMPLETE_REQUESTED.clear()


@pytest.mark.parametrize('key,value', [
    ('mouse_click_button', 'middle'), ('scan_rate_limit_hz', 5),
    ('scan_rate_limit_hz', float('nan')), ('respawn_settle_sec', float('inf')),
    ('restart_after_hit_delay_sec', 'oops'),
])
def test_invalid_settings_do_not_mutate(key, value):
    before = dict(core.COMPATIBILITY_SETTINGS)
    with pytest.raises((ValueError, TypeError)):
        core._ui_apply_setting_unlocked(key, value)
    assert core.COMPATIBILITY_SETTINGS == before


def test_save_failure_rolls_back_and_snapshots_include_settings():
    with patch.object(core, 'save_master_config', side_effect=OSError('full')):
        with pytest.raises(OSError):
            core._ui_apply_setting('mouse_click_button', 'right')
    assert core._ui_config_snapshot()['mouse_click_button'] == 'left'
    core._ui_apply_setting_unlocked('respawn_settle_sec', 99)
    assert core._master_config_snapshot()['respawn_settle_sec'] == 30
    core.reset_user_settings_to_defaults()
    assert core.COMPATIBILITY_SETTINGS == core.COMPATIBILITY_DEFAULTS


@pytest.mark.parametrize('choice,swapped,expected', [
    ('left', 1, (2, 4)), ('right', 0, (8, 16)),
    ('windows', 1, (8, 16)), ('windows', 0, (2, 4)),
])
def test_button_selection(choice, swapped, expected):
    core.COMPATIBILITY_SETTINGS['mouse_click_button'] = choice
    with patch.object(core._user32, 'GetSystemMetrics', return_value=swapped):
        assert core._mouse_button_flags() == expected


def test_partial_click_releases_original_button_even_after_setting_change():
    packets = []
    core.COMPATIBILITY_SETTINGS['mouse_click_button'] = 'right'
    def send(count, inputs, size):
        packets.append([inputs[i].mi.dwFlags for i in range(count)])
        core.COMPATIBILITY_SETTINGS['mouse_click_button'] = 'left'
        return 1
    with patch.object(core._user32, 'SendInput', side_effect=send):
        with pytest.raises(RuntimeError, match='complete mouse click'):
            core._send_click_buttons()
    assert packets == [[8, 16], [16]]


def test_respawn_delay_happens_before_any_movement_and_is_cancellable():
    core.COMPATIBILITY_SETTINGS['respawn_settle_sec'] = 4
    with patch.object(core, '_auto_retry_wait', return_value=False) as wait, \
         patch.object(core, 'focus_game_window') as focus, \
         patch.object(core.pydirectinput, 'keyDown') as down:
        assert not core._auto_retry_walk_forward()
    wait.assert_called_once_with(4)
    focus.assert_not_called()
    down.assert_not_called()


@pytest.mark.parametrize('failure', ['focus', 'exception'])
def test_walk_releases_w_on_focus_loss_or_injection_exception(failure):
    with patch.object(core, '_auto_retry_wait', return_value=True), \
         patch.object(core, 'focus_game_window', return_value=True), \
         patch.object(core, '_game_has_focus', side_effect=[True, False]), \
         patch.object(core.pydirectinput, 'keyDown', side_effect=OSError('blocked') if failure == 'exception' else None), \
         patch.object(core.pydirectinput, 'keyUp') as up:
        if failure == 'exception':
            with pytest.raises(OSError):
                core._auto_retry_walk_forward()
        else:
            assert not core._auto_retry_walk_forward()
    up.assert_called_once_with('w')


@pytest.mark.parametrize('interrupt', ['stop', 'pause', 'complete', 'senzu', 'focus'])
def test_restart_never_sends_input_when_interrupted(interrupt):
    if interrupt == 'stop': core._USER_STOP_LATCHED = True
    if interrupt == 'pause': core.PAUSE_TOGGLE_REQUESTED = True
    if interrupt == 'complete': core.PROGRESSION_COMPLETE_REQUESTED.set()
    with patch.object(core, '_game_has_focus', return_value=interrupt != 'focus'), \
         patch.object(core, '_controller_decisions_suspended', return_value=interrupt == 'senzu'), \
         patch.object(core, 'hardware_tap') as tap:
        reselect = MagicMock()
        with pytest.raises((core.QuitException, core.SkipMinigameException, RuntimeError)):
            core._restart_minigame_after_hit('Health', MagicMock(), reselect)
        reselect.assert_not_called()
        tap.assert_not_called()


def test_restart_confirms_menu_reselects_same_trait_and_clears_old_target():
    clock = iter([0, 1, 2])
    core._HEALTH_V2_STATE['armed'] = False
    core._ki_v8_state['last_dot'] = (12, 30, 1)
    menu = MagicMock(side_effect=[False, True])
    reselect = MagicMock(return_value=True)
    with patch.object(core.time, 'monotonic', side_effect=lambda: next(clock)), \
         patch.object(core, '_game_has_focus', return_value=True), \
         patch.object(core, 'hardware_tap') as tap:
        core._restart_minigame_after_hit('Ki Damage', menu, reselect)
    tap.assert_called_once_with('tab')
    assert reselect.call_args.args == ('Ki Damage', '[RESTART]')
    assert callable(reselect.call_args.kwargs['interrupt_check'])
    assert core._ki_v8_state['last_dot'] is None
    assert core._HEALTH_V2_STATE['armed']


def test_restart_menu_timeout_is_bounded_and_does_not_reclick():
    clock = iter([0, 1, 2, 6])
    with patch.object(core.time, 'monotonic', side_effect=lambda: next(clock)), \
         patch.object(core, '_game_has_focus', return_value=True), \
         patch.object(core, 'hardware_tap') as tap:
        reselect = MagicMock()
        with pytest.raises(RuntimeError, match='did not open'):
            core._restart_minigame_after_hit('Health', lambda: False, reselect)
        tap.assert_called_once_with('tab')
        reselect.assert_not_called()


def test_scan_limit_accounts_for_processing_time():
    core.COMPATIBILITY_SETTINGS['scan_rate_limit_hz'] = 20
    with patch.object(core.time, 'perf_counter', side_effect=[1.02, 1.05]), \
         patch.object(core, 'safe_sleep') as sleep:
        core._finish_minigame_cycle(1.0)
    assert sleep.call_args.args[0] == pytest.approx(.03)
    assert core.LOOP_METRICS['loops_per_second'] == pytest.approx(20)


def test_window_mode_save_failure_preserves_legacy_combination():
    with patch.object(core, 'RESTORE_FULLSCREEN_ON_START', True), \
         patch.object(core, 'WINDOWED_MODE_ON_START', True), \
         patch.object(core, 'save_master_config', side_effect=OSError('full')):
        with pytest.raises(OSError):
            core._ui_apply_setting('startup_window_mode', 'unchanged')
        assert core.RESTORE_FULLSCREEN_ON_START and core.WINDOWED_MODE_ON_START


def test_startup_window_mode_changes_both_flags_together():
    with patch.object(core, 'RESTORE_FULLSCREEN_ON_START', True), \
         patch.object(core, 'WINDOWED_MODE_ON_START', True):
        core._ui_apply_setting_unlocked('startup_window_mode', 'fullscreen')
        assert core.RESTORE_FULLSCREEN_ON_START and not core.WINDOWED_MODE_ON_START
        core._ui_apply_setting_unlocked('startup_window_mode', 'windowed')
        assert not core.RESTORE_FULLSCREEN_ON_START and core.WINDOWED_MODE_ON_START


def test_real_command_route_rejects_malformed_envelopes_without_inputs():
    # Compile only the actual route into an isolated Flask app: no server, log
    # files, global hotkeys, watcher threads or native input are started.
    import ast
    from flask import Flask, jsonify, request
    tree = ast.parse(Path(core.__file__).read_text(encoding='utf-8'))
    handler = next(node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == 'command')
    app = Flask('isolated-command-test')
    namespace = {'app': app, 'jsonify': jsonify, 'request': request}
    exec(compile(ast.Module(body=[handler], type_ignores=[]), core.__file__, 'exec'), namespace)
    client = app.test_client()
    for payload in [[], True, 42, {'action': []}, {'action': 2}, {}]:
        response = client.post('/command', json=payload)
        assert response.status_code == 400
        assert response.json['ok'] is False
