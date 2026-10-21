"""Deadline timing, cancellation and native-input boundaries, without live input."""
import threading
from unittest.mock import Mock

import numpy as np
import cv2
import pytest

import training_ui as ui
import xynmacro_core as core


@pytest.fixture(autouse=True)
def fixed_landing(monkeypatch):
    # These tests check the scheduler's mechanics with round numbers; the live landing
    # target is calibrated separately and pinned by test_landing_target_is_calibrated.
    monkeypatch.setattr(core, 'KI_V9_LAND_AFTER_CLOSE', .085)
    monkeypatch.setattr(core, 'KI_V9_LAND_LATEST', .120)


def test_landing_target_is_calibrated():
    source = open(core.__file__, encoding='utf-8').read()
    assert 'KI_V9_LAND_AFTER_CLOSE = 0.11' in source
    assert 'KI_V9_LAND_LATEST = KI_V9_LAND_AFTER_CLOSE + 0.06' in source


class Clock:
    now = 100.0

    def __call__(self):
        return self.now


def circle(at, close=100.6, speed=2.2, x=1000., radius=55.):
    return (x, 500., radius, radius*(1+max(0., close-at)*speed))


@pytest.mark.parametrize('interval', [.04, .08, .13, .20, .25])
@pytest.mark.parametrize('latency', [.005, .04, .10])
def test_deadlines_fire_between_slow_observations(interval, latency):
    clock = Clock()
    scheduler = core._KiDeadlineScheduler()
    input_latency = .055
    due = 100.6+.085-input_latency
    fired = []
    while clock.now < 101:
        captured = clock.now
        ready = captured+latency
        if scheduler.next_deadline() is not None and scheduler.next_deadline() <= ready:
            when = scheduler.next_deadline()
            target = scheduler.pop_due(when, input_latency)
            if target:
                fired.append(when+input_latency)
        scheduler.observe(captured, [circle(captured)], ready, input_latency)
        clock.now += interval
    assert fired == pytest.approx([100.685])
    assert due < 100.75  # the 250 ms loop's next observation is too late


def test_two_looks_fit_speed_instead_of_using_prior():
    scheduler = core._KiDeadlineScheduler()
    for at in [100., 100.2]:
        scheduler.observe(at, [circle(at, speed=2.8)], at+.03, .055)
    assert scheduler.next_deadline() == pytest.approx(100.630)


def test_single_late_look_uses_prior_and_fires_without_next_look():
    scheduler = core._KiDeadlineScheduler()
    scheduler.observe(100.55, [circle(100.55)], 100.57, .055)
    assert scheduler.next_deadline() == pytest.approx(100.630)
    assert scheduler.pop_due(100.630, .055) is not None
    assert scheduler.pop_due(100.9, .055) is None


def test_disappearance_cancels_and_reappearance_is_a_new_track():
    scheduler = core._KiDeadlineScheduler()
    scheduler.observe(100., [circle(100.)], 100.02, .055)
    scheduler.observe(100.2, [], 100.22, .055)
    assert scheduler.next_deadline() is None
    scheduler.observe(100.4, [circle(100.4, close=101.)], 100.42, .055)
    assert scheduler.next_deadline() == pytest.approx(101.03)


def test_missing_ring_keeps_prediction_only_while_fill_is_present():
    scheduler = core._KiDeadlineScheduler()
    scheduler.observe(100., [circle(100.)], 100.02, .055)
    scheduler.observe(100.3, [(1000., 500., 55., None)], 100.32, .055)
    assert scheduler.pop_due(100.630, .055) is not None


@pytest.mark.parametrize('when', [100.68, 101.])
def test_late_dispatch_drops_target_instead_of_clicking_stale_pixels(when):
    scheduler = core._KiDeadlineScheduler()
    scheduler.observe(100.5, [circle(100.5)], 100.52, .055)
    assert scheduler.pop_due(when, .055) is None
    assert scheduler.next_deadline() is None


def test_lease_expires_when_capture_loop_stalls():
    scheduler = core._KiDeadlineScheduler()
    scheduler.observe(100., [circle(100.)], 100.02, .055)
    assert scheduler.pop_due(100.630, .055) is None


def test_repeated_and_stale_samples_do_not_move_deadline():
    scheduler = core._KiDeadlineScheduler()
    scheduler.observe(100., [circle(100.)], 100.02, .055)
    scheduler.observe(100.2, [circle(100.2)], 100.22, .055)
    expected = scheduler.next_deadline()
    scheduler.observe(100.2, [circle(100.0)], 100.30, .055)
    scheduler.observe(100.1, [], 100.31, .055)
    assert scheduler.next_deadline() == expected


def test_outward_jump_cancels_old_deadline_until_new_ring_is_observed():
    scheduler = core._KiDeadlineScheduler()
    scheduler.observe(100., [circle(100.)], 100.02, .055)
    scheduler.observe(100.2, [circle(100.2)], 100.22, .055)
    scheduler.observe(100.3, [circle(100.0)], 100.32, .055)
    assert scheduler.next_deadline() is None
    scheduler.observe(100.4, [circle(100.4, close=101.)], 100.42, .055)
    assert scheduler.next_deadline() == pytest.approx(101.03)


def test_stationary_border_does_not_drag_close_time_forward():
    scheduler = core._KiDeadlineScheduler()
    for at in [100., 100.2, 100.4, 100.6, 100.62]:
        scheduler.observe(at, [circle(at)], at+.005, .055)
    assert scheduler.next_deadline() == pytest.approx(100.630)


def test_multiple_circles_are_serialized_and_suppressed_individually():
    scheduler = core._KiDeadlineScheduler()
    circles = [circle(100.3), circle(100.3, close=100.65, x=1200)]
    scheduler.observe(100.3, circles, 100.32, .055)
    assert scheduler.pop_due(100.630, .055)['x'] == 1000
    assert scheduler.pop_due(100.68, .055)['x'] == 1200
    scheduler.observe(100.7, [circle(100.7), circle(100.7, x=1200)], 100.72, .055)
    assert scheduler.next_deadline() is None


@pytest.mark.parametrize('replacement', [(1050, 500, 55, None), (1000, 500, 80, None)])
def test_centre_or_fill_mismatch_cannot_inherit_a_deadline(replacement):
    scheduler = core._KiDeadlineScheduler()
    scheduler.observe(100., [circle(100.)], 100.02, .055)
    scheduler.observe(100.2, [replacement], 100.22, .055)
    assert scheduler.next_deadline() is None


@pytest.fixture
def runtime(monkeypatch):
    clock = Clock()
    monkeypatch.setattr(core.time, 'perf_counter', clock)
    monkeypatch.setattr(core, '_KI_V9_SCHEDULER', core._KiDeadlineScheduler())
    context = (7, 'Ki Control', (0, 0, 1920, 1080), 'sendinput_abs')
    memo = dict(label_seen_at=100., context=context, input_latency=.055,
                input_method='sendinput_abs', restart_pending=False, error=None)
    monkeypatch.setattr(core, '_KI_V9_STATE', memo)
    for key, value in dict(PROGRESSION_GENERATION=7, CURRENT_TRAINING_STATE='Ki Control',
                           GAME_OFFSET_X=0, GAME_OFFSET_Y=0, GAME_WIDTH=1920, GAME_HEIGHT=1080,
                           MOUSE_METHOD='sendinput_abs', ENABLE_KI_MINIGAME=True,
                           _USER_STOP_LATCHED=False, UI_STOP_REQUESTED=False, CONTROLLER_PAUSED=False,
                           PAUSE_TOGGLE_REQUESTED=False, MANUAL_NEXT_REQUESTED=False,
                           MAJIN_CONTROLLER_ACTIVE=False, _sidecar_closing=False).items():
        monkeypatch.setattr(core, key, value)
    monkeypatch.setattr(core, 'PROGRESSION_COMPLETE_REQUESTED', threading.Event())
    monkeypatch.setattr(core, '_new_game_ui', lambda: True)
    monkeypatch.setattr(core, '_controller_decisions_suspended', lambda: False)
    monkeypatch.setattr(core, '_game_has_focus', lambda: True)
    monkeypatch.setattr(core, 'check_exit', lambda: None)
    monkeypatch.setattr(core, '_start_ki_v9_worker', Mock())
    monkeypatch.setattr(core, 'TELEMETRY', dict(ki_dots_found=0, ki_clicks=0))
    click = Mock(side_effect=lambda *_: setattr(clock, 'now', clock.now+.055))
    monkeypatch.setattr(core, '_ki_click', click)
    core._KI_V9_SCHEDULER.observe(100., [circle(100., close=100.2)], 100., .055)
    clock.now = 100.231
    return clock, click


def test_dispatch_measures_latency_and_queues_restart_receipt(runtime):
    clock, click = runtime
    core._ki_v9_dispatch_due()
    click.assert_called_once_with(1000, 500)
    assert core._KI_V9_STATE['input_latency'] == pytest.approx(.055)
    assert core._KI_V9_STATE['restart_pending']
    assert core.TELEMETRY['ki_clicks'] == 1
    core._ki_v9_dispatch_due()
    assert click.call_count == 1


def test_measured_slow_input_updates_other_queued_deadlines(runtime):
    clock, click = runtime
    core._KI_V9_SCHEDULER.observe(100.1,
        [circle(100.1, close=100.2), circle(100.1, close=100.8, x=1200)], 100.1, .055)
    click.side_effect = lambda *_: setattr(clock, 'now', clock.now+.25)
    core._ki_v9_dispatch_due()
    measured = .055+.25*(.25-.055)
    assert core._KI_V9_STATE['input_latency'] == pytest.approx(measured)
    assert core._KI_V9_SCHEDULER.next_deadline() == pytest.approx(100.8+.085-measured)


@pytest.mark.parametrize('key,value', [('UI_STOP_REQUESTED', True), ('_USER_STOP_LATCHED', True),
    ('CONTROLLER_PAUSED', True), ('PAUSE_TOGGLE_REQUESTED', True), ('MANUAL_NEXT_REQUESTED', True),
    ('MAJIN_CONTROLLER_ACTIVE', True), ('ENABLE_KI_MINIGAME', False), ('_sidecar_closing', True),
    ('CURRENT_TRAINING_STATE', 'Health'), ('PROGRESSION_GENERATION', 8), ('GAME_WIDTH', 1280),
    ('MOUSE_METHOD', 'setcursor_mouseevent')])
def test_dispatch_cancels_on_state_stop_pause_or_geometry_change(runtime, monkeypatch, key, value):
    monkeypatch.setattr(core, key, value)
    core._ki_v9_dispatch_due()
    runtime[1].assert_not_called()
    assert core._KI_V9_SCHEDULER.next_deadline() is None


@pytest.mark.parametrize('reason', ['focus', 'recovery', 'completion', 'label', 'blocked'])
def test_dispatch_requires_fresh_authorization(runtime, monkeypatch, reason):
    if reason == 'focus':
        monkeypatch.setattr(core, '_game_has_focus', lambda: False)
    elif reason == 'recovery':
        monkeypatch.setattr(core, '_controller_decisions_suspended', lambda: True)
    elif reason == 'completion':
        core.PROGRESSION_COMPLETE_REQUESTED.set()
    elif reason == 'label':
        core._KI_V9_STATE['label_seen_at'] = 98.
    else:
        monkeypatch.setattr(core, '_ki_click_blocked', lambda *_: True)
    core._ki_v9_dispatch_due()
    runtime[1].assert_not_called()


def test_reset_clears_v9_tracks_labels_touching_cache_and_restart(runtime):
    ui._TOUCHING_CACHE.update(at=100., circles=[(1000, 500, 55)])
    core._KI_V9_STATE['restart_pending'] = True
    core._reset_minigame_detection()
    assert core._KI_V9_SCHEDULER.next_deadline() is None
    assert core._KI_V9_STATE['label_seen_at'] is None
    assert core._KI_V9_STATE['context'] is None
    assert not core._KI_V9_STATE['restart_pending']
    assert ui._TOUCHING_CACHE['circles'] == []


def test_worker_checks_deadline_without_another_capture(runtime, monkeypatch):
    clock, click = runtime
    clock.now = 100.02
    waits = []

    def wait(seconds):
        waits.append(seconds)
        clock.now += seconds

    def fire(*_):
        clock.now += .055
        monkeypatch.setattr(core, '_sidecar_closing', True)

    click.side_effect = fire
    monkeypatch.setattr(core._KI_V9_CONDITION, 'wait', wait)
    core._ki_v9_worker()
    assert click.call_count == 1
    assert max(waits) <= .01
    assert clock.now == pytest.approx(100.285)


def test_observation_uses_capture_start_not_detection_finish(runtime, monkeypatch):
    clock, _ = runtime
    core._KI_V9_SCHEDULER.clear()
    clock.now = 100.
    frame = np.zeros((1080, 1920, 3), np.uint8)

    def capture(*_, **__):
        clock.now += .12
        return frame

    monkeypatch.setattr(core, '_grab_reference_box', capture)
    monkeypatch.setattr(ui, 'progression_spans', lambda _: object())
    monkeypatch.setattr(ui, 'find_ki_circles', lambda _: [(1000., 500., 55.)])
    monkeypatch.setattr(ui, 'ki_ring_radius', lambda *_: circle(100.)[3])
    core.logic_ki_v9(None, (0, 0, 1920, 1080))
    assert core._KI_V9_SCHEDULER.next_deadline() == pytest.approx(100.630)


def test_input_failure_is_returned_to_controller(runtime):
    runtime[1].side_effect = RuntimeError('injection failed')
    core._ki_v9_dispatch_due()
    assert isinstance(core._KI_V9_STATE['error'], RuntimeError)
    assert core.TELEMETRY['ki_clicks'] == 0
    assert core._KI_V9_SCHEDULER.next_deadline() is None


def test_touching_cache_cannot_resurrect_disappeared_circle(monkeypatch):
    frame = np.zeros((830, 1120, 3), np.uint8)
    dark = np.full(frame.shape[:2], 255, np.uint8)
    monkeypatch.setattr(ui.time, 'monotonic', lambda: 100.)
    monkeypatch.setattr(ui, '_TOUCHING_CACHE', dict(at=99.95, circles=[(500, 400, 55)], shape=dark.shape))
    assert ui._touching_circles_cached(frame, frame, dark) == []


def test_stop_while_worker_waits_for_input_lock_cancels_packet(runtime, monkeypatch):
    entered = threading.Event()

    def dispatch():
        entered.set()
        core._ki_v9_dispatch_due()

    with core._input_lock:
        worker = threading.Thread(target=dispatch)
        worker.start()
        assert entered.wait(1.)
        with core._stop_input_gate:
            monkeypatch.setattr(core, '_USER_STOP_LATCHED', True)
    worker.join(1.)
    assert not worker.is_alive()
    runtime[1].assert_not_called()
    assert core._KI_V9_SCHEDULER.next_deadline() is None


def test_state_transition_cancels_queued_click_before_input(runtime):
    core._set_training_state('Ki Damage')
    core._ki_v9_dispatch_due()
    runtime[1].assert_not_called()
    assert core._KI_V9_SCHEDULER.next_deadline() is None


def test_one_worker_is_reused_across_resets(monkeypatch):
    worker = Mock()
    worker.is_alive.return_value = True
    factory = Mock(return_value=worker)
    monkeypatch.setattr(core, '_KI_V9_WORKER', None)
    monkeypatch.setattr(core.threading, 'Thread', factory)
    with core._KI_V9_CONDITION:
        core._start_ki_v9_worker()
        core._reset_ki_v9()
        core._start_ki_v9_worker()
    assert factory.call_count == 1
    worker.start.assert_called_once()


def test_ki_click_has_no_blocking_post_click_sleep(monkeypatch):
    click = Mock()
    monkeypatch.setattr(core, 'click_at', click)
    monkeypatch.setattr(core, 'safe_sleep', Mock(side_effect=AssertionError('blocking wait')))
    core._ki_click(1000, 500)
    click.assert_called_once_with(1000, 500)


@pytest.mark.parametrize('centre', [(160.3, 150.7), (30.1, 90.4), (285.4, 250.3)])
def test_vector_ring_samples_match_scalar_reader_at_edges(centre):
    x, y = centre
    frame = np.full((320, 320, 3), (65, 130, 190), np.uint8)
    cv2.circle(frame, (round(x), round(y)), 95, (150, 220, 255), 3)
    radius = 54.8

    def sample(r):
        xs = np.clip((x+r*np.cos(ui._RING_ANGLES)).astype(int), 0, 319)
        ys = np.clip((y+r*np.sin(ui._RING_ANGLES)).astype(int), 0, 319)
        return frame[ys, xs].astype(int)

    best_hits, expected = 0, None
    for r in np.arange(radius, radius*ui.KI_RING_MAX_FACTOR, 2.):
        line = sample(r)
        hits = ((line[:, 2]>225) & (line[:, 1]>150)
                & (np.abs(line-sample(r-5)).sum(1)>40)
                & (np.abs(line-sample(r+5)).sum(1)>40)).sum()
        if hits > best_hits:
            best_hits, expected = hits, r
    assert best_hits >= ui.KI_RING_MIN_HITS
    assert ui.ki_ring_radius(frame, x, y, radius) == expected


def test_median_fill_radius_does_not_fit_contour_jitter_as_shrink():
    scheduler = core._KiDeadlineScheduler()
    for at, radius in [(100., 57.), (100.1, 53.), (100.2, 55.), (100.3, 55.)]:
        x, y, _, ring = circle(at)
        scheduler.observe(at, [(x, y, radius, ring)], at+.01, .055)
    assert scheduler.next_deadline() == pytest.approx(100.630)
