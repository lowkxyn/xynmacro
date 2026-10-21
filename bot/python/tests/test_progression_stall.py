"""Offline count-stall, controller, and settings regressions."""
import json
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
import pytest

import xynmacro_core as core
from test_fix_batch3b import _controller_setup
from test_progression_detection import progression_frame


STAT = "Physical Damage"
SETTING = "progression_stall_timeout_sec"


@pytest.fixture
def signature():
    return core._progression_signature_from_frame(progression_frame(right_matches=False))


def test_stall_arms_only_after_tracking_locks(signature):
    for tracked in (None, "Ki Damage"):
        timer, stalled = core._progression_stall_state(
            STAT, tracked, False, signature, 10.0, None
        )
        assert timer is None and not stalled
        timer, stalled = core._progression_stall_state(
            STAT, tracked, False, signature, 1000.0, timer
        )
        assert timer is None and not stalled


def test_unchanged_incomplete_count_fires_at_timeout(monkeypatch, signature):
    monkeypatch.setattr(core, "PROGRESSION_STALL_TIMEOUT_SEC", 120.0)
    timer, stalled = core._progression_stall_state(STAT, STAT, False, signature, 10.0, None)
    assert timer[2] == 10.0 and not stalled
    timer, stalled = core._progression_stall_state(STAT, STAT, False, signature, 129.99, timer)
    assert not stalled
    _, stalled = core._progression_stall_state(STAT, STAT, False, signature, 130.0, timer)
    assert stalled


@pytest.mark.parametrize("side", [0, 1])
def test_change_to_either_side_of_signature_resets_timer(monkeypatch, signature, side):
    monkeypatch.setattr(core, "PROGRESSION_STALL_TIMEOUT_SEC", 120.0)
    timer, _ = core._progression_stall_state(STAT, STAT, False, signature, 10.0, None)
    changed = list(signature)
    changed[side] = (np.roll(signature[side][0], 16, axis=1),)
    changed = tuple(changed)
    timer, stalled = core._progression_stall_state(STAT, STAT, False, changed, 129.0, timer)
    assert timer[2] == 129.0 and not stalled
    _, stalled = core._progression_stall_state(STAT, STAT, False, changed, 249.0, timer)
    assert stalled


def test_stat_switch_resets_timer_even_with_identical_glyphs(signature):
    timer, _ = core._progression_stall_state(STAT, STAT, False, signature, 10.0, None)
    timer, stalled = core._progression_stall_state(
        "Ki Damage", "Ki Damage", False, signature, 130.0, timer
    )
    assert timer[0] == "Ki Damage" and timer[2] == 130.0 and not stalled


@pytest.mark.parametrize("completion,readable,suspended", [
    (True, True, False), (None, True, False),
    (False, False, False), (False, True, True),
])
def test_completion_missing_label_and_suspension_clear_timer(
    signature, completion, readable, suspended
):
    timer, _ = core._progression_stall_state(STAT, STAT, False, signature, 10.0, None)
    timer, stalled = core._progression_stall_state(
        STAT, STAT, completion, signature if readable else None, 130.0, timer,
        suspended=suspended,
    )
    assert timer is None and not stalled
    timer, stalled = core._progression_stall_state(STAT, STAT, False, signature, 500.0, timer)
    assert timer[2] == 500.0 and not stalled


def test_completion_and_signature_use_the_same_captured_frame(monkeypatch):
    frame = progression_frame(right_matches=False)
    capture = Mock(return_value=frame)
    monkeypatch.setattr(core, "_grab_reference_box", capture)
    completion, signature, reason = core.read_progression_state("capture", "geometry")
    assert completion is False and signature is not None and reason is None
    capture.assert_called_once_with("capture", core.PROG_SEARCH_BOX, geometry="geometry")


def _run_monitor(monkeypatch, polls, **overrides):
    clock = SimpleNamespace(now=0.0)
    sample = {}
    poll_index = 0
    incomplete = (False, core._progression_signature_from_frame(progression_frame(False)))
    for name, value in {
        "CURRENT_TRAINING_STATE": STAT, "PROGRESSION_STATE_STARTED_AT": 0.0,
        "PROGRESSION_TRACKED_STATE": None, "PROGRESSION_COMPLETE": None,
        "PROGRESSION_CAPPED_STATE": None, "PROGRESSION_STALL_TIMEOUT_SEC": 120.0,
        "CONTROLLER_PAUSED": False, "PAUSE_TOGGLE_REQUESTED": False,
        "TRAINING_MENU_VISIBLE": False, "GC_RECOVERY_VERIFY_PENDING": False,
        "SENZU_ENABLED": False, "SENZU_DISABLED_FOR_RUN": False,
        "SENZU_DELAY_SEC": 0.0, "UI_STOP_REQUESTED": False, "_USER_STOP_LATCHED": False,
    }.items():
        monkeypatch.setattr(core, name, value)
    event_names = (
        "PROGRESSION_COMPLETE_REQUESTED", "PROGRESSION_STALL_RESET_REQUESTED",
        "SENZU_CONTROLLER_ACTIVE", "SENZU_CONTROLLER_RESUME_REQUIRED",
    )
    for name in event_names:
        monkeypatch.setattr(core, name, core.threading.Event())
    requests = []
    real_set = core.PROGRESSION_COMPLETE_REQUESTED.set

    def request_switch():
        requests.append(clock.now)
        real_set()

    request = Mock(side_effect=request_switch)
    monkeypatch.setattr(core.PROGRESSION_COMPLETE_REQUESTED, "set", request)

    def next_poll():
        nonlocal poll_index, sample
        if poll_index == len(polls):
            return True
        clock.now, sample = polls[poll_index]
        poll_index += 1
        for name, value in sample.items():
            if name in event_names:
                event = getattr(core, name)
                event.set() if value else event.clear()
            elif hasattr(core, name):
                setattr(core, name, value)
        return False

    stop = Mock(is_set=Mock(side_effect=next_poll))
    read = Mock(side_effect=lambda *_: sample.get("reading", incomplete))
    death = Mock()
    eat = Mock(side_effect=lambda *_: setattr(clock, "now", clock.now + 600.0))
    monkeypatch.setattr(core.time, "monotonic", lambda: clock.now)
    monkeypatch.setattr(core.mss, "MSS", lambda: nullcontext(object()))
    monkeypatch.setattr(core, "update_game_window", lambda: sample.get("window_available", True))
    monkeypatch.setattr(core, "_game_geometry", lambda: (0, 0, 1920, 1080))
    monkeypatch.setattr(core, "_game_has_focus", lambda: sample.get("focused", True))
    monkeypatch.setattr(core, "_gc_death_dialog_visible", lambda *_: sample.get("death_visible", False))
    monkeypatch.setattr(core, "read_progression_state", read)
    monkeypatch.setattr(core, "_hp_bar_is_red", lambda *_: sample.get("red", False))
    monkeypatch.setattr(core, "eat_senzu", eat)
    monkeypatch.setattr(core, "_stop_for_game_death", death)
    monkeypatch.setattr(core, "_record_run_outcome", Mock())
    monkeypatch.setattr(core, "_release_macro_input", Mock())
    monkeypatch.setattr(core, "_grab_reference_box", Mock(return_value=np.zeros((1080, 1920, 4), np.uint8)))
    monkeypatch.setattr(core.gc_recovery, "progress_fill", lambda *_: None)
    for name, value in overrides.items():
        monkeypatch.setattr(core, name, value)
    read_sample = core.read_progression_state

    def with_reason(*args):
        sample = read_sample(*args)
        return sample if len(sample) == 3 else (*sample, "displayed_target" if sample[0] else None)

    monkeypatch.setattr(core, "read_progression_state", with_reason)
    core._background_game_monitor(stop)
    return SimpleNamespace(requests=requests, request=request, read=read, death=death, eat=eat)


def test_monitor_publishes_one_cap_without_claiming_real_completion(monkeypatch, capsys):
    result = _run_monitor(monkeypatch, [(10.0, {}), (129.99, {}), (130.0, {}), (200.0, {})])
    assert result.requests == [130.0]
    assert result.read.call_count == 3
    assert core.PROGRESSION_CAPPED_STATE == STAT
    assert core.PROGRESSION_COMPLETE is False
    assert core.PROGRESSION_COMPLETE_REQUESTED.is_set()
    assert f"[PROG] {STAT} stuck at the same count for 120s" in capsys.readouterr().out


@pytest.mark.parametrize("flag", [
    "CONTROLLER_PAUSED", "PAUSE_TOGGLE_REQUESTED", "TRAINING_MENU_VISIBLE",
    "SENZU_CONTROLLER_ACTIVE", "SENZU_CONTROLLER_RESUME_REQUIRED",
    "GC_RECOVERY_VERIFY_PENDING", "death_visible",
])
def test_monitor_resets_across_pause_menus_senzu_and_recovery(monkeypatch, flag):
    result = _run_monitor(monkeypatch, [
        (10.0, {}), (129.0, {flag: True}), (500.0, {flag: False}),
        (619.99, {}), (620.0, {}),
    ])
    assert result.requests == [620.0]


@pytest.mark.parametrize("flag", ["focused", "window_available"])
def test_monitor_does_not_count_time_without_a_focused_game_capture(monkeypatch, flag):
    result = _run_monitor(monkeypatch, [
        (10.0, {}), (129.0, {flag: False}), (500.0, {flag: True}), (620.0, {}),
    ])
    assert result.requests == [620.0]


def test_brief_pause_reset_survives_between_monitor_polls(monkeypatch):
    result = _run_monitor(monkeypatch, [
        (10.0, {}), (130.0, {"PROGRESSION_STALL_RESET_REQUESTED": True}),
        (249.99, {}), (250.0, {}),
    ])
    assert result.requests == [250.0]


def test_same_stat_menu_recovery_or_restart_timestamp_resets_tracking(monkeypatch):
    result = _run_monitor(monkeypatch, [
        (10.0, {}), (129.0, {"PROGRESSION_STATE_STARTED_AT": 129.0}),
        (131.0, {}), (250.99, {}), (251.0, {}),
    ])
    assert result.requests == [251.0]


def test_monitor_resets_on_stat_switch(monkeypatch):
    result = _run_monitor(monkeypatch, [
        (10.0, {}), (129.0, {"CURRENT_TRAINING_STATE": None}),
        (500.0, {"CURRENT_TRAINING_STATE": "Ki Damage"}), (620.0, {}),
    ])
    assert result.requests == [620.0]
    assert core.PROGRESSION_CAPPED_STATE == "Ki Damage"


def test_monitor_requires_a_fresh_lock_after_label_loss(monkeypatch):
    result = _run_monitor(monkeypatch, [
        (10.0, {}), (80.0, {"reading": (None, None)}),
        (89.0, {"reading": (None, None)}), (100.0, {}), (220.0, {}),
    ])
    assert result.requests == [220.0]


def test_monitor_never_caps_before_the_first_readable_label(monkeypatch):
    result = _run_monitor(monkeypatch, [
        (10.0, {"reading": (None, None)}), (500.0, {"reading": (None, None)}),
        (600.0, {}), (719.99, {}), (720.0, {}),
    ])
    assert result.requests == [720.0]


def test_confirmed_death_stops_without_publishing_a_cap(monkeypatch):
    result = _run_monitor(monkeypatch, [
        (10.0, {}), (130.0, {"death_visible": True}),
        (130.25, {"death_visible": True}), (130.5, {"death_visible": True}),
    ])
    assert result.requests == []
    result.death.assert_called_once_with()


def test_real_completion_keeps_the_existing_stable_read_path(monkeypatch):
    completed = (True, core._progression_signature_from_frame(progression_frame()))
    result = _run_monitor(monkeypatch, [
        (10.0, {}), (20.0, {"reading": completed}),
        (20.25, {"reading": completed}), (20.5, {"reading": completed}),
    ])
    assert result.requests == [20.5]
    assert core.PROGRESSION_COMPLETE is True
    assert core.PROGRESSION_CAPPED_STATE is None


def test_synchronous_senzu_invalidates_a_stall_sample_before_publishing(monkeypatch):
    result = _run_monitor(monkeypatch, [
        (9.0, {}), (128.75, {"red": True}), (129.0, {"red": True}),
        (731.0, {"SENZU_CONTROLLER_RESUME_REQUIRED": False}),
        (850.99, {}), (851.0, {}),
    ], SENZU_ENABLED=True)
    result.eat.assert_called_once()
    assert result.requests == [851.0]


def test_pause_between_capture_and_publication_discards_stale_timeout(monkeypatch):
    reading = (False, core._progression_signature_from_frame(progression_frame(False)))
    reads = 0

    def read(*_args):
        nonlocal reads
        reads += 1
        if reads == 2:
            core.PROGRESSION_STALL_RESET_REQUESTED.set()
        return reading

    result = _run_monitor(monkeypatch, [
        (10.0, {}), (130.0, {}), (250.0, {}), (370.0, {}),
    ], read_progression_state=read)
    assert result.requests == [370.0]


def test_restart_resets_the_timer_before_navigating_menus(monkeypatch):
    event = core.threading.Event()
    menu = {"open": False}
    monkeypatch.setattr(core, "PROGRESSION_STALL_RESET_REQUESTED", event)
    monkeypatch.setattr(core, "check_exit", Mock())
    monkeypatch.setattr(core, "PAUSE_TOGGLE_REQUESTED", False)
    monkeypatch.setattr(core, "_game_has_focus", lambda: True)
    monkeypatch.setattr(core, "COMPATIBILITY_SETTINGS", {
        **core.COMPATIBILITY_SETTINGS, "restart_after_hit_delay_sec": 0.0,
    })
    monkeypatch.setattr(core, "TELEMETRY", dict(core.TELEMETRY))
    monkeypatch.setattr(core, "_reset_minigame_detection", Mock())

    def open_menu(*_args):
        assert event.is_set()
        menu["open"] = True

    monkeypatch.setattr(core, "hardware_tap", open_menu)
    reselect = Mock(return_value=True)
    core._restart_minigame_after_hit(STAT, lambda: menu["open"], reselect)
    reselect.assert_called_once()
    assert event.is_set()


def _setup_controller(monkeypatch):
    _controller_setup(monkeypatch)
    for name, value in {
        "CURRENT_TRAINING_STATE": None, "PROGRESSION_CAPPED_STATE": None,
        "GC_RECOVERY_VERIFY_PENDING": False,
        "PROGRESSION_STALL_RESET_REQUESTED": core.threading.Event(),
    }.items():
        monkeypatch.setattr(core, name, value)
    menu = {"open": True}
    monkeypatch.setattr(core, "click_at", Mock(side_effect=lambda *_: menu.update(open=False)))
    monkeypatch.setattr(core, "hardware_tap", Mock(side_effect=lambda *_: menu.update(open=not menu["open"])))
    monkeypatch.setattr(core, "detect_training_menu", lambda *_: (menu["open"], 1.0))
    monkeypatch.setattr(core, "_finish_minigame_cycle", Mock())
    outcome = Mock()
    monkeypatch.setattr(core, "_record_run_outcome", outcome)
    return outcome


def test_controller_requests_timer_reset_on_both_pause_and_resume(monkeypatch):
    _setup_controller(monkeypatch)
    monkeypatch.setattr(core, "PAUSE_TOGGLE_REQUESTED", True)
    event = core.PROGRESSION_STALL_RESET_REQUESTED
    reset = Mock(wraps=event.set)
    monkeypatch.setattr(event, "set", reset)

    def resume(*_args):
        assert event.is_set()
        event.clear()
        core.PAUSE_TOGGLE_REQUESTED = True

    monkeypatch.setattr(core.time, "sleep", resume)
    monkeypatch.setattr(core, "logic_health", Mock(side_effect=core.QuitException()))
    with pytest.raises(core.QuitException):
        core.run_master_controller()
    assert reset.call_count == 2
    assert event.is_set()


@pytest.mark.parametrize("statuses", [
    ("capped", "completed"), ("completed", "capped"),
    ("capped", "capped"), ("skipped", "capped"),
])
def test_controller_advances_caps_and_reports_them_with_skips(monkeypatch, capsys, statuses):
    outcome = _setup_controller(monkeypatch)

    def finish_stat(status, stat):
        if status == "skipped":
            core.MANUAL_NEXT_REQUESTED = True
        else:
            assert core._publish_progression_completion(
                core._progression_token(), "stall" if status == "capped" else "displayed_target")
        raise core.SkipMinigameException()

    monkeypatch.setattr(core, "logic_health", lambda *_: finish_stat(statuses[0], "Health"))
    monkeypatch.setattr(core, "logic_agility_v2", lambda *_: finish_stat(statuses[1], "Agility"))
    core.run_master_controller()
    skipped = [stat for stat, status in zip(("Health", "Agility"), statuses) if status == "skipped"]
    capped = [stat for stat, status in zip(("Health", "Agility"), statuses) if status == "capped"]
    outcome.assert_called_once_with(*core._training_order_result(skipped, capped), "Agility")
    assert outcome.call_args.args[0] == "incomplete"
    assert "capped stats" in outcome.call_args.args[1]
    for stat in capped:
        assert stat in outcome.call_args.args[1]
    assert "ALL DONE!" not in capsys.readouterr().out
    assert [call.args[0] for call in core._confirm_trait_click.call_args_list] == ["Health", "Agility"]


@pytest.mark.parametrize("enabled,tracked,present", [
    (True, False, False), (False, False, False),
    (True, True, False), (True, False, True),
])
def test_yellow_fallback_switches_only_when_enabled_untracked_and_absent(
    monkeypatch, enabled, tracked, present
):
    outcome = _setup_controller(monkeypatch)
    monkeypatch.setattr(core, "TRAINING_ORDER_CUSTOM", ["Health"])
    monkeypatch.setattr(core, "NO_YELLOW_FALLBACK_ENABLED", enabled)
    monkeypatch.setattr(core, "PROGRESSION_TRACKED_STATE", "Health" if tracked else None)
    monkeypatch.setattr(core, "NO_YELLOW_TIMEOUT_SEC", 0.0)
    monkeypatch.setattr(core, "TRAINING_LOAD_GRACE_SEC", 0.0)
    monkeypatch.setattr(core, "YELLOW_SAMPLE_INTERVAL_SEC", 0.0)
    def yellow(*_):
        core.PROGRESSION_TRACKED_STATE = "Health" if tracked else None
        return int(present), None, None

    monkeypatch.setattr(core, "detect_yellow_progress", yellow)
    monkeypatch.setattr(core, "logic_health", Mock(side_effect=core.QuitException()))
    if enabled and not tracked and not present:
        core.run_master_controller()
        outcome.assert_called_once_with("completed", "Training order completed", "Health")
    else:
        with pytest.raises(core.QuitException):
            core.run_master_controller()
        outcome.assert_not_called()


@pytest.mark.parametrize("value,expected", [
    (-1, 30.0), (29.99, 30.0), (30, 30.0), (120.5, 120.5),
    (1800, 1800.0), (1801, 1800.0), ("240", 240.0),
])
@pytest.mark.parametrize("source", ["set", "load"])
def test_stall_timeout_bounds_in_settings_paths(monkeypatch, tmp_path, source, value, expected):
    monkeypatch.setattr(core, "PROGRESSION_STALL_TIMEOUT_SEC", 120.0)
    config = tmp_path / "macro_config.json"
    monkeypatch.setattr(core, "JSON_DIR", str(tmp_path))
    monkeypatch.setattr(core, "MACRO_CONFIG_FILE", str(config))
    if source == "set":
        core._ui_apply_setting(SETTING, value)
    else:
        config.write_text(json.dumps({SETTING: value}), encoding="utf-8")
        core.load_master_config()
    assert core.PROGRESSION_STALL_TIMEOUT_SEC == expected
    assert core._ui_config_snapshot()[SETTING] == expected
    assert core._master_config_snapshot()[SETTING] == expected
    assert json.loads(config.read_text(encoding="utf-8"))[SETTING] == expected


@pytest.mark.parametrize("value", ["nan", "inf", "-inf", "broken"])
def test_stall_timeout_rejects_nonfinite_and_invalid_updates(monkeypatch, value):
    monkeypatch.setattr(core, "PROGRESSION_STALL_TIMEOUT_SEC", 120.0)
    save = Mock()
    monkeypatch.setattr(core, "save_master_config", save)
    with pytest.raises(ValueError):
        core._ui_apply_setting(SETTING, value)
    assert core.PROGRESSION_STALL_TIMEOUT_SEC == 120.0
    save.assert_not_called()
