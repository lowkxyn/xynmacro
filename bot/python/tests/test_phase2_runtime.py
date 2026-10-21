from pathlib import Path
from types import SimpleNamespace
import threading
from unittest.mock import Mock

import cv2
import pytest

import xynmacro_core as core
from test_progression_stall import _run_monitor, _setup_controller


@pytest.fixture(autouse=True)
def control_state(monkeypatch):
    for name, value in {
        "CURRENT_TRAINING_STATE": "Health", "PROGRESSION_STATE_STARTED_AT": 0.0,
        "PROGRESSION_GENERATION": 0, "PROGRESSION_COMPLETION": None,
        "PROGRESSION_COMPLETE": None, "PROGRESSION_TRACKED_STATE": None,
        "PROGRESSION_CAPPED_STATE": None, "CONTROLLER_PAUSED": False,
        "PAUSE_TOGGLE_REQUESTED": False, "MANUAL_NEXT_REQUESTED": False,
        "UI_STOP_REQUESTED": False, "_USER_STOP_LATCHED": False,
        "TRAINING_MENU_VISIBLE": False, "GC_RECOVERY_VERIFY_PENDING": False,
        "_CHARGE_HELD": False, "_CHARGING_NOW": None,
        "HEAL_METHOD": "senzu", "SENZU_ENABLED": True, "SENZU_DISABLED_FOR_RUN": False,
        "HEALING_ACTION": None,
    }.items():
        monkeypatch.setattr(core, name, value)
    for name in ("SENZU_CONTROLLER_ACTIVE", "SENZU_CONTROLLER_RESUME_REQUIRED",
                 "PROGRESSION_COMPLETE_REQUESTED", "PROGRESSION_STALL_RESET_REQUESTED"):
        monkeypatch.setattr(core, name, threading.Event())
    monkeypatch.setattr(core, "_game_has_focus", lambda: True)
    monkeypatch.setattr(core.pydirectinput, "keyDown", Mock())
    monkeypatch.setattr(core.pydirectinput, "keyUp", Mock())
    monkeypatch.setattr(core, "_destroy_cv_windows", Mock())
    monkeypatch.setattr(core, "_game_window_covering_title", lambda: None)


@pytest.mark.parametrize("replacement", [None, "Agility", "Physical Damage"])
def test_inflight_completion_cannot_publish_into_switch_or_same_stat_restart(monkeypatch, replacement):
    reads = 0

    def read(*_args):
        nonlocal reads
        reads += 1
        if reads == 3:
            core._set_training_state(replacement)
        return True, None, "displayed_target"

    result = _run_monitor(monkeypatch, [(10, {}), (11, {}), (12, {})], read_progression_state=read)
    assert result.requests == []
    assert core._pending_progression_completion() is None
    core.check_exit()


def test_consumer_discards_stale_payload_even_if_delivered_after_reset(monkeypatch):
    token = core._progression_token()
    core._set_training_state("Health")
    monkeypatch.setattr(core, "PROGRESSION_COMPLETION", (token, "displayed_target"))
    core.PROGRESSION_COMPLETE_REQUESTED.set()
    core.check_exit()
    assert core._pending_progression_completion(consume=True) is None
    assert not core.PROGRESSION_COMPLETE_REQUESTED.is_set()


def test_confirmed_points_cap_survives_monitor_controller_and_after_run_policy(monkeypatch):
    frame = cv2.imread(str(Path(__file__).parent / "fixtures/progression_16of20.png"))
    monkeypatch.setattr(core, "PROGRESSION_TARGETS", {"Physical Damage": (16, 20)})
    reason = core._progression_completion_reason(frame, False, "Physical Damage")
    assert reason == "points_left"
    result = _run_monitor(monkeypatch, [(10, {}), (11, {}), (12, {})],
                          read_progression_state=lambda *_: (True, None, reason))
    assert result.requests == [12]
    assert core._pending_progression_completion(consume=True) == "points_left"
    outcome = _setup_controller(monkeypatch)
    monkeypatch.setattr(core, "TRAINING_ORDER_CUSTOM", ["Health"])

    def finish(*_args):
        assert core._publish_progression_completion(core._progression_token(), "points_left")
        raise core.SkipMinigameException()

    monkeypatch.setattr(core, "logic_health", finish)
    core.run_master_controller()
    assert outcome.call_args.args[0] == "incomplete"
    assert "confirmed Points Left limit" in outcome.call_args.args[1]
    assert "displayed target" not in outcome.call_args.args[1]


def test_charge_revalidates_ownership_after_waiting_for_input_gate(monkeypatch):
    monkeypatch.setitem(core.COMPATIBILITY_SETTINGS, "charge_hold_mode", "third")
    monkeypatch.setattr(core, "_CHARGING_NOW", "controller")
    ready = threading.Event()

    def controller():
        want = core._charge_wanted("Health")
        ready.set()
        core._set_charge_hold(want)

    with core._stop_input_gate:
        worker = threading.Thread(target=controller)
        worker.start()
        assert ready.wait(1)
        assert core._claim_healing(core._progression_token())
    worker.join(1)
    assert not worker.is_alive()
    assert not core._CHARGE_HELD
    core.pydirectinput.keyDown.assert_not_called()


@pytest.mark.parametrize("trigger", ["disabled", "focus", "healing"])
def test_charge_releases_without_waiting_for_outer_controller_loop(monkeypatch, trigger):
    monkeypatch.setitem(core.COMPATIBILITY_SETTINGS, "charge_hold_mode", "third")
    monkeypatch.setattr(core, "_CHARGING_NOW", "controller")
    core._set_charge_hold(True)
    assert core._CHARGE_HELD
    if trigger == "disabled":
        core._set_compatibility_setting("charge_hold_mode", "off")
    elif trigger == "focus":
        monkeypatch.setattr(core, "_game_has_focus", lambda: False)
        core.check_exit()
    else:
        assert core._claim_healing(core._progression_token())
    assert not core._CHARGE_HELD
    core.pydirectinput.keyUp.assert_called_once_with("shift")


def test_failed_charge_release_does_not_leave_healing_ownership_latched(monkeypatch):
    monkeypatch.setattr(core, "_CHARGE_HELD", True)
    core.pydirectinput.keyUp.side_effect = OSError("release failed")
    with pytest.raises(OSError, match="release failed"):
        core._claim_healing(core._progression_token())
    assert not core.SENZU_CONTROLLER_ACTIVE.is_set()
    assert not core.SENZU_CONTROLLER_RESUME_REQUIRED.is_set()
    assert core.HEALING_ACTION is None
    assert core._CHARGE_HELD


@pytest.mark.parametrize("key", ["shift", "left shift", "right shift", "g", "G", "5", "6", "7", "8",
                                "ctrl+shift+f6", "f8, g", "f8, 8"])
def test_injected_keys_and_parsed_aliases_are_rejected_on_load_and_rebind(monkeypatch, key):
    assert core._loaded_hotkey(key, "l") == "l"
    register = Mock()
    monkeypatch.setattr(core, "register_pause_hotkey", register)
    with pytest.raises(ValueError, match="XynMacro sends"):
        core._ui_apply_setting_unlocked("pause_hotkey", key.lower())
    register.assert_not_called()


def test_healing_method_switch_rearms_senzu_after_majin_cooldown(monkeypatch):
    majin = Mock(side_effect=lambda *_: setattr(core, "MAJIN_REGEN_READY_AT", 1000))
    result = _run_monitor(monkeypatch, [
        (10, {"red": True}), (11, {"red": True}),
        (30, {"red": True, "HEAL_METHOD": "senzu", "SENZU_CONTROLLER_RESUME_REQUIRED": False}),
        (31, {"red": True}), (32, {"red": True}),
    ], HEAL_METHOD="majin_regen", use_majin_regen=majin, SENZU_ENABLED=True, SENZU_DELAY_SEC=1)
    majin.assert_called_once()
    result.eat.assert_called_once()
    assert core.HEALING_ACTION is None


@pytest.mark.parametrize("action", [None, "senzu", "majin_regen", "majin_cooldown", "reserve_senzu"])
def test_state_publishes_executing_healing_action_separately(monkeypatch, action):
    monkeypatch.setattr(core, "HEALING_ACTION", action)
    monkeypatch.setattr(core, "HEAL_METHOD", "majin_regen")
    monkeypatch.setattr(core, "_ui_is_running", lambda: True)
    monkeypatch.setattr(core, "_get_screen_info", lambda: {})
    monkeypatch.setattr(core, "game_window_is_fullscreen", lambda: False)
    state = core._ui_state_snapshot()
    assert state["healing_action"] == action
    assert state["config"]["heal_method"] == "majin_regen"


def test_pause_between_healing_eligibility_and_claim_rejects_majin(monkeypatch):
    monkeypatch.setattr(core, "HEAL_METHOD", "majin_regen")
    assert core._healing_monitor_enabled()
    core.PAUSE_TOGGLE_REQUESTED = True
    assert core._consume_pause_toggle()
    assert not core._claim_healing(core._progression_token())
    assert not core.SENZU_CONTROLLER_ACTIVE.is_set()


def test_majin_resume_is_seen_by_already_paused_controller(monkeypatch):
    _setup_controller(monkeypatch)
    monkeypatch.setattr(core, "PAUSE_TOGGLE_REQUESTED", True)
    resumed = []

    def resume(_duration):
        assert core.CONTROLLER_PAUSED
        core.PAUSE_TOGGLE_REQUESTED = True
        core._majin_wait_for_resume()
        resumed.append(True)

    monkeypatch.setattr(core.time, "sleep", resume)
    monkeypatch.setattr(core, "logic_health", Mock(side_effect=core.QuitException()))
    with pytest.raises(core.QuitException):
        core.run_master_controller()
    assert resumed == [True]
    assert not core.CONTROLLER_PAUSED


@pytest.mark.parametrize("values,expected", [([1, 4, 5, 6], True), ([1, 1, 1, 1], False),
                                          ([1, 4, None, 6], False), ([10, 2, 2, 2], False)])
def test_fractional_progress_requires_stable_growth(values, expected):
    previous = None
    for fill in values:
        previous, growing = core._progression_meter_growth(fill, previous)
    assert growing is expected


def test_monitor_meter_growth_defers_only_inferred_stall(monkeypatch):
    fills = iter([4, 7, 8, 9])
    vision = SimpleNamespace(progress_fill=lambda *_: next(fills))
    result = _run_monitor(monkeypatch, [(10, {}), (120, {}), (125, {}), (129, {}), (130, {})],
                          gc_recovery=vision)
    assert result.requests == []


@pytest.mark.parametrize("lost", ["prompt", "death", "scene"])
def test_fallback_rechecks_cues_before_each_pulse(monkeypatch, lost):
    monkeypatch.setitem(core.COMPATIBILITY_SETTINGS, "auto_retry_walk_fallback", True)
    frame_index = [0]

    def capture(*_):
        frame_index[0] += 1
        return object()

    monkeypatch.setattr(core, "_gc_recovery_ready", lambda: True)
    monkeypatch.setattr(core, "_auto_retry_wait", lambda _: True)
    monkeypatch.setattr(core, "_confirmed_game_capture_rect", lambda: {})
    monkeypatch.setattr(core, "_grab_reference_box", capture)
    monkeypatch.setattr(core, "_gc_death_dialog_visible", lambda *_: lost == "death" and frame_index[0] >= 2)
    monkeypatch.setattr(core.gc_recovery, "enter_prompt_visible", lambda *_: lost != "prompt" or frame_index[0] < 2)
    monkeypatch.setattr(core.gc_recovery, "room_landmarks",
                        lambda *_: None if lost == "scene" and frame_index[0] >= 2 else (None, 500))
    hold = Mock(return_value=0.2)
    monkeypatch.setattr(core, "_gc_recovery_hold", hold)
    assert not core._gc_fallback_walk("test", 2, object(), object())
    hold.assert_called_once()
    assert not core.GC_RECOVERY_VERIFY_PENDING


def test_lost_scene_after_near_edge_never_confirms_crossing(monkeypatch):
    monkeypatch.setattr(core, "_gc_recovery_ready", lambda: True)
    monkeypatch.setattr(core, "_auto_retry_wait", lambda _: True)
    monkeypatch.setattr(core, "_confirmed_game_capture_rect", lambda: {})
    monkeypatch.setattr(core, "_grab_reference_box", Mock())
    monkeypatch.setattr(core, "_gc_death_dialog_visible", lambda *_: False)
    monkeypatch.setattr(core.gc_recovery, "enter_prompt_visible", lambda *_: True)
    monkeypatch.setattr(core.gc_recovery, "room_landmarks", Mock(side_effect=[(None, 950), None]))
    hold = Mock()
    monkeypatch.setattr(core, "_gc_recovery_hold", hold)
    assert not core._gc_fallback_walk("test", 2, object(), object())
    hold.assert_not_called()
    assert not core.GC_RECOVERY_VERIFY_PENDING


def test_recovery_pause_after_capture_requires_another_frame_before_movement(monkeypatch):
    def pause_at_gate():
        core.PAUSE_TOGGLE_REQUESTED = True
        return True

    monkeypatch.setattr(core, "_gc_recovery_ready", pause_at_gate)
    monkeypatch.setattr(core, "_auto_retry_cancelled", lambda: False)
    assert core._gc_recovery_hold(0.2) == 0
    core.pydirectinput.keyDown.assert_not_called()


@pytest.mark.parametrize("action", ["key", "click"])
def test_healing_claim_between_focus_check_and_input_blocks_old_owner(monkeypatch, action):
    def focus():
        assert core._claim_healing(core._progression_token())
        return True

    monkeypatch.setattr(core, "_ensure_game_focus_for_input", focus)
    mouse = Mock()
    monkeypatch.setattr(core, "_click_sendinput_abs_unchecked", mouse)
    with pytest.raises(core.SenzuControllerPause):
        if action == "key":
            core.hardware_tap("w")
        else:
            core.click_at(10, 20, method="sendinput_abs")
    core.pydirectinput.keyDown.assert_not_called()
    mouse.assert_not_called()


@pytest.mark.parametrize("flag", ["CONTROLLER_PAUSED", "death_visible", "focused"])
def test_completion_needs_new_stable_frames_after_pause_death_or_focus_loss(monkeypatch, flag):
    blocked = False if flag == "focused" else True
    result = _run_monitor(monkeypatch, [
        (10, {}), (11, {}), (12, {flag: blocked}), (13, {flag: not blocked}), (14, {}), (15, {}),
    ], read_progression_state=lambda *_: (True, None, "displayed_target"))
    assert result.requests == [15]
