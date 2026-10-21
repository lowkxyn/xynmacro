import sys
from pathlib import Path

import cv2
import numpy as np
import pytest
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import xynmacro_core as core
import gc_recovery
from test_progression_detection import progression_frame


@pytest.fixture(autouse=True)
def restore_recovery_flags():
    flags = {key: getattr(core, key) for key in (
        '_USER_STOP_LATCHED', 'UI_STOP_REQUESTED', 'AUTO_RETRY_WALK_OUT',
        'PAUSE_TOGGLE_REQUESTED', 'CONTROLLER_PAUSED', 'GC_RECOVERY_VERIFY_PENDING')}
    core.PAUSE_TOGGLE_REQUESTED = core.CONTROLLER_PAUSED = False
    yield
    for key, value in flags.items():
        setattr(core, key, value)


def test_progress_change_requires_the_same_target_and_readable_label():
    before = core._progression_signature_from_frame(progression_frame())
    after_frame = progression_frame()
    after_frame[68:92, 280:290] = 0
    cv2.rectangle(after_frame, (280, 68), (289, 71), (230, 230, 230, 255), -1)
    cv2.rectangle(after_frame, (286, 68), (289, 91), (230, 230, 230, 255), -1)
    after = core._progression_signature_from_frame(after_frame)
    assert core._progression_count_changed(before, after)
    assert not core._progression_count_changed(before, before)
    assert not core._progression_count_changed(before, None)
    assert not core._progression_count_changed(None, after)
    assert not core._progression_count_changed(before, (after[0], after[0]))


def test_camera_movement_does_not_count_as_progress():
    frame = progression_frame()
    moved = cv2.warpAffine(frame, np.float32([[1, 0, 19], [0, 1, 37]]), (700, 500))
    assert not core._progression_count_changed(
        core._progression_signature_from_frame(frame),
        core._progression_signature_from_frame(moved),
    )


def test_live_wall_fixture_has_a_readable_unchanged_signature():
    fixture = Path(__file__).parent / 'fixtures' / 'progression_20of20_over_wall.png'
    signature = core._progression_signature_from_frame(cv2.imread(str(fixture)))
    assert signature is not None
    assert core._progression_shapes_match(signature[0], signature[1])
    assert not core._progression_count_changed(signature, signature)


FIXTURES = Path(__file__).parent / 'fixtures' / 'gc_recovery'
PROMPT = cv2.imread(str(Path(core.BASE_DIR) / 'tpl_gc_enter.png'), 0)


def live_frame(name):
    return cv2.imread(str(FIXTURES / (name + '.png')))


def test_real_respawn_and_crossing_landmarks():
    # Facing LEAVE: the box's near edge is visible but the column is not, so no walking.
    assert gc_recovery.room_landmarks(live_frame('respawn_02'), PROMPT)[0] is None
    # This client renders the boundary glow lavender instead of cyan-cored.
    pillar, edge = gc_recovery.room_landmarks(live_frame('respawn_lavender_edges'), PROMPT)
    assert 700 < pillar < 780 and 440 < edge < 520
    pillar, edge = gc_recovery.room_landmarks(live_frame('respawn_facing_room'), PROMPT)
    assert 780 < pillar < 850 and 500 < edge < 580
    pillar, edge = gc_recovery.room_landmarks(live_frame('respawn_walk14'), PROMPT)
    assert 780 < pillar < 850 and edge >= 910
    assert gc_recovery.room_landmarks(live_frame('entry_01'), PROMPT) is None
    assert gc_recovery.room_landmarks(live_frame('training_menu_01'), PROMPT) is None


def test_real_meter_grows_before_the_integer_changes():
    first, last = live_frame('progress_first'), live_frame('progress_last')
    assert gc_recovery.progress_fill(first, 'Physical Damage') == 4
    assert gc_recovery.progress_fill(last, 'Physical Damage') == 11
    assert not core._progression_count_changed(
        core._progression_signature_from_frame(first[300:800, 700:1400]),
        core._progression_signature_from_frame(last[300:800, 700:1400]),
    )
    assert gc_recovery.progress_fill(live_frame('respawn_walk14'), 'Physical Damage') is None
    assert gc_recovery.progress_fill(live_frame('training_menu_01'), 'Physical Damage') is None


def settled(frames):
    """The respawn wait needs the first frame to hold steady before moving."""
    return [frames[0]] * (core.GC_RESPAWN_STABLE_FRAMES - 1) + frames


def test_stale_prompt_before_the_loading_screen_sends_no_movement():
    # Live 2026-10-04: after a reset the old scene (prompt included) showed for
    # a moment, then the loading screen; walking on the stale frame failed.
    core._USER_STOP_LATCHED = False
    core.AUTO_RETRY_WALK_OUT = True
    context = MagicMock()
    context.__enter__.return_value = object()
    loading = np.zeros_like(live_frame('respawn_02'))
    frames = ([live_frame('respawn_02')] * 3
              + [loading] * (core.GC_RESPAWN_LOADING_FRAMES - 3))
    with patch.object(core, '_auto_retry_wait', return_value=True), \
         patch.object(core, 'focus_game_window', return_value=True), \
         patch.object(core, '_gc_recovery_ready', return_value=True), \
         patch.object(core, '_confirmed_game_capture_rect', return_value={}), \
         patch.object(core.mss, 'MSS', return_value=context), \
         patch.object(core, '_gc_death_dialog_visible', return_value=False), \
         patch.object(core, '_grab_reference_box', side_effect=frames), \
         patch.object(core, '_gc_recovery_mouse') as mouse, \
         patch.object(core, '_gc_recovery_hold') as hold:
        assert not core._auto_retry_walk_forward()
    mouse.assert_not_called()
    hold.assert_not_called()


def test_navigation_never_moves_without_confirmed_room():
    core._USER_STOP_LATCHED = False
    core.AUTO_RETRY_WALK_OUT = True
    context = MagicMock()
    context.__enter__.return_value = object()
    with patch.object(core, '_auto_retry_wait', return_value=True), \
         patch.object(core, 'focus_game_window', return_value=True), \
         patch.object(core, '_confirmed_game_capture_rect', return_value={}), \
         patch.object(core.mss, 'MSS', return_value=context), \
         patch.object(core, '_gc_death_dialog_visible', return_value=False), \
         patch.object(core, '_grab_reference_box', return_value=live_frame('entry_01')), \
         patch.object(core, '_gc_recovery_mouse') as mouse, \
         patch.object(core, '_gc_recovery_hold') as hold:
        assert not core._auto_retry_walk_forward()
    mouse.assert_not_called()
    hold.assert_not_called()


def test_navigation_checks_crossing_after_each_short_step():
    core._USER_STOP_LATCHED = False
    core.AUTO_RETRY_WALK_OUT = True
    core.GC_RECOVERY_VERIFY_PENDING = False
    context = MagicMock()
    context.__enter__.return_value = object()
    frames = [live_frame(name) for name in ('respawn_02', 'respawn_02',
              'respawn_facing_room', 'walked_06', 'respawn_walk14', 'respawn_walk14')]
    with patch.object(core, '_auto_retry_wait', return_value=True), \
         patch.object(core, 'focus_game_window', return_value=True), \
         patch.object(core, '_gc_recovery_ready', return_value=True), \
         patch.object(core, '_confirmed_game_capture_rect', return_value={}), \
         patch.object(core.mss, 'MSS', return_value=context), \
         patch.object(core, '_gc_death_dialog_visible', return_value=False), \
         patch.object(core, '_grab_reference_box', side_effect=settled(frames)), \
         patch.object(core.win32api, 'GetCursorPos', return_value=(960, 540)), \
         patch.object(core, '_gc_recovery_mouse', return_value=True) as mouse, \
         patch.object(core, '_gc_recovery_hold', return_value=.2) as hold:
        assert core._auto_retry_walk_forward()
    assert core.GC_RECOVERY_VERIFY_PENDING
    assert hold.call_count == 2
    assert all(call.args[0] <= .2 for call in hold.call_args_list)
    assert mouse.call_args_list[-1].args == (-75,)
    core.GC_RECOVERY_VERIFY_PENDING = False


def test_edge_dropping_below_the_frame_confirms_the_crossing():
    # Live walk capture: the line went 740 -> 961 -> past the bottom in one step.
    core._USER_STOP_LATCHED = False
    core.AUTO_RETRY_WALK_OUT = True
    core.GC_RECOVERY_VERIFY_PENDING = False
    context = MagicMock()
    context.__enter__.return_value = object()
    frames = [live_frame(name) for name in ('walk_edge_961', 'walk_edge_740',
              'walk_edge_961', 'walk_edge_below_frame')]
    with patch.object(core, '_auto_retry_wait', return_value=True), \
         patch.object(core, 'focus_game_window', return_value=True), \
         patch.object(core, '_gc_recovery_ready', return_value=True), \
         patch.object(core, '_confirmed_game_capture_rect', return_value={}), \
         patch.object(core.mss, 'MSS', return_value=context), \
         patch.object(core, '_gc_death_dialog_visible', return_value=False), \
         patch.object(core, '_grab_reference_box', side_effect=settled(frames)), \
         patch.object(core.win32api, 'GetCursorPos', return_value=(960, 540)), \
         patch.object(core, '_gc_recovery_mouse', return_value=True), \
         patch.object(core, '_gc_recovery_hold', return_value=.2) as hold:
        assert core._auto_retry_walk_forward()
    assert core.GC_RECOVERY_VERIFY_PENDING
    assert hold.call_count == 1
    core.GC_RECOVERY_VERIFY_PENDING = False


def test_edge_lost_far_from_the_line_falls_back_to_holding_w():
    # The line vanished without a close sighting first: vision can't confirm the
    # crossing, so the rest of the movement budget is spent holding W and the
    # restarted training's progress check makes the final call.
    core._USER_STOP_LATCHED = False
    core.AUTO_RETRY_WALK_OUT = True
    core.AUTO_RETRY_WALK_SECONDS = 2.5
    core.GC_RECOVERY_VERIFY_PENDING = False
    context = MagicMock()
    context.__enter__.return_value = object()
    # Vision: 740 (step), then the line vanishes. Fallback walk: the line
    # reappears close and drops below the frame, so it stops early.
    names = (['walk_edge_740', 'walk_edge_740', 'walk_edge_below_frame']
             + ['walk_edge_740'] * 3 + ['walk_edge_961', 'walk_edge_below_frame'])
    frames = [live_frame(name) for name in names]
    with patch.object(core, '_auto_retry_wait', return_value=True),          patch.object(core, 'focus_game_window', return_value=True),          patch.object(core, '_gc_recovery_ready', return_value=True),          patch.object(core, '_confirmed_game_capture_rect', return_value={}),          patch.object(core.mss, 'MSS', return_value=context),          patch.object(core, '_gc_death_dialog_visible', return_value=False),          patch.object(core, '_grab_reference_box', side_effect=settled(frames)),          patch.object(core.win32api, 'GetCursorPos', return_value=(960, 540)),          patch.object(core, '_gc_recovery_mouse', return_value=True),          patch.object(core, '_gc_recovery_hold', side_effect=lambda seconds: seconds) as hold:
        assert core._auto_retry_walk_forward()
    assert core.GC_RECOVERY_VERIFY_PENDING
    steps = [call.args[0] for call in hold.call_args_list]
    assert steps[0] == pytest.approx(.08)
    assert len(steps) == 4 and all(step == pytest.approx(.2) for step in steps[1:])
    core.GC_RECOVERY_VERIFY_PENDING = False


def test_fallback_walk_never_starts_outside_the_gc():
    core._USER_STOP_LATCHED = False
    core.AUTO_RETRY_WALK_OUT = True
    context = MagicMock()
    context.__enter__.return_value = object()
    with patch.object(core, '_auto_retry_wait', return_value=True),          patch.object(core, 'focus_game_window', return_value=True),          patch.object(core, '_gc_recovery_ready', return_value=True),          patch.object(core, '_confirmed_game_capture_rect', return_value={}),          patch.object(core.mss, 'MSS', return_value=context),          patch.object(core, '_gc_death_dialog_visible', return_value=False),          patch.object(core, '_grab_reference_box', return_value=live_frame('training_menu_01')),          patch.object(core, '_gc_recovery_hold') as hold:
        assert not core._auto_retry_walk_forward()
    hold.assert_not_called()


def test_pause_during_a_step_releases_movement_before_waiting():
    core._USER_STOP_LATCHED = False
    core.PAUSE_TOGGLE_REQUESTED = False
    def request_pause(_key):
        core.PAUSE_TOGGLE_REQUESTED = True
    with patch.object(core, '_gc_recovery_ready', return_value=True), \
         patch.object(core, '_game_has_focus', return_value=True), \
         patch.object(core.pydirectinput, 'keyDown', side_effect=request_pause), \
         patch.object(core.pydirectinput, 'keyUp') as up:
        assert core._gc_recovery_hold(.2) < .2
    up.assert_called_once_with('w')


def test_pause_during_camera_turn_waits_without_injecting():
    core.PAUSE_TOGGLE_REQUESTED = True
    core._USER_STOP_LATCHED = False
    ready_calls = []
    def ready():
        ready_calls.append(True)
        if len(ready_calls) == 2:
            core.PAUSE_TOGGLE_REQUESTED = False
        return True
    with patch.object(core, '_gc_recovery_ready', side_effect=ready), \
         patch.object(core, '_game_has_focus', return_value=True), \
         patch.object(core, '_auto_retry_wait', return_value=True), \
         patch.object(core._user32, 'SendInput', return_value=1) as send:
        assert core._gc_recovery_mouse(75)
    assert len(ready_calls) == 2
    send.assert_called_once()


def test_pause_at_recovery_handoff_survives_preparing_the_next_attempt():
    core._USER_STOP_LATCHED = False
    core.GC_RECOVERY_VERIFY_PENDING = True
    core.PAUSE_TOGGLE_REQUESTED = True
    with patch.object(core, '_invalidate_senzu_row_cache'):
        assert core._prepare_controller_attempt()
    assert core.PAUSE_TOGGLE_REQUESTED


def test_monitor_requires_real_progress_growth_and_fails_when_it_never_counts(monkeypatch):
    for key, value in {'_USER_STOP_LATCHED': False, 'UI_STOP_REQUESTED': False,
                       'CURRENT_TRAINING_STATE': 'Physical Damage', 'TRAINING_MENU_VISIBLE': False,
                       'CONTROLLER_PAUSED': False, 'SENZU_ENABLED': False,
                       'PROGRESSION_STATE_STARTED_AT': 0}.items():
        monkeypatch.setattr(core, key, value)
    for event in (core.SENZU_CONTROLLER_ACTIVE, core.SENZU_CONTROLLER_RESUME_REQUIRED):
        monkeypatch.setattr(event, 'is_set', lambda: False)
    monkeypatch.setattr(core, 'update_game_window', lambda: True)
    monkeypatch.setattr(core, '_game_geometry', lambda: (0, 0, 1920, 1080))
    monkeypatch.setattr(core, '_game_has_focus', lambda: True)
    monkeypatch.setattr(core, '_gc_death_dialog_visible', lambda *_: False)
    monkeypatch.setattr(core, 'read_progression_state', lambda *_: (False, None, None))
    with patch.object(core.mss, 'MSS', return_value=MagicMock()), \
         patch.object(core, '_release_macro_input') as release:
        for grows in (True, False):
            core.GC_RECOVERY_VERIFY_PENDING = True
            core.UI_STOP_REQUESTED = False
            stop = MagicMock()
            stop.is_set.side_effect = [False] * 4 + [True]
            frames = [live_frame('progress_first')] + [live_frame('progress_last' if grows else 'progress_first')] * 3
            ticks = [0, 1, 2, 3, 4] if grows else [0, 20, 40, 60, 80]
            # The clock accounts for capped one-second polling gaps.
            with patch.object(core, '_grab_reference_box', side_effect=frames), \
                 patch.object(core.time, 'monotonic', side_effect=ticks), \
                 patch.object(core, 'GC_RECOVERY_VERIFY_TIMEOUT_SEC', 4), \
                 patch.object(core, '_record_run_outcome') as outcome:
                core._background_game_monitor(stop)
            assert core.GC_RECOVERY_VERIFY_PENDING is not grows
            assert core.UI_STOP_REQUESTED is not grows
            assert outcome.called is not grows
        release.assert_called_once()
