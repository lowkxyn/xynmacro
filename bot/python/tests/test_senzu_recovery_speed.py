import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, call

import cv2
import numpy as np
import pytest

import gc_recovery
import xynmacro_core as core


@pytest.fixture(autouse=True)
def points_left_read_is_out_of_scope(monkeypatch):
    # These tests pin every capture in the healing flow; the Points Left read on
    # resume has its own tests in test_points_on_resume.py.
    monkeypatch.setattr(core, "_capture_points_panels", lambda _sct, _stat=None: [])


@pytest.fixture
def flow(monkeypatch):
    state = SimpleNamespace(now=0.0)

    def sleep(seconds):
        state.now += seconds

    for name, value in {
        "UI_STOP_REQUESTED": False, "_USER_STOP_LATCHED": False,
        "_AFTER_ACTIONS_BLOCKED": False, "_background_monitor_stop": None,
        "MAJIN_CONTROLLER_ACTIVE": False, "CONTROLLER_PAUSED": False,
        "PAUSE_TOGGLE_REQUESTED": False, "CURRENT_TRAINING_STATE": "Health",
        # Cursor restores only run while Roblox is focused; never depend on the real desktop.
        "_game_has_focus": lambda: True,
        "SENZU_SLOT": 1, "SENZU_ACTIVE_TYPE": "full", "SENZU_STATUS": "ready",
        "SENZU_PREFERENCE_MODE": "full_only", "SENZU_STUCK_RESETS_USED": 0,
        "SENZU_STUCK_RESET_REQUESTED": False, "GC_RUN_CONFIRMED": False,
        "GC_RECOVERY_VERIFY_PENDING": False, "PREVENT_SLEEP_WHILE_RUNNING": False,
        "AUTO_RETRY_ON_FAILURE": False, "SENZU_CONTROLLER_ACTIVE": threading.Event(),
        "SENZU_CONTROLLER_RESUME_REQUIRED": threading.Event(),
        "PROGRESSION_COMPLETE_REQUESTED": threading.Event(),
        "_CURRENT_RUN_OUTCOME": None, "_CURRENT_RUN_REASON": None,
        "_CURRENT_RUN_CATEGORY": None, "_CURRENT_RUN_RETRYABLE": False,
        "TELEMETRY": dict.fromkeys(core.TELEMETRY, 0),
        "GAME_OFFSET_X": 0, "GAME_OFFSET_Y": 0, "GAME_WIDTH": 1920, "GAME_HEIGHT": 1080,
        "MACRO_LAST_ERROR": None,
    }.items():
        monkeypatch.setattr(core, name, value)
    monkeypatch.setattr(core.time, "monotonic", lambda: state.now)
    monkeypatch.setattr(core.time, "sleep", sleep)
    monkeypatch.setattr(core, "_refresh_game_window_if_stale", Mock())
    monkeypatch.setattr(core.win32api, "GetCursorPos", lambda: (1300, 800))
    monkeypatch.setattr(core._user32, "SetCursorPos", Mock())
    monkeypatch.setattr(core, "_nudge_pointer", Mock())
    monkeypatch.setattr(core, "_focus_game_for_senzu", Mock(return_value=True))
    monkeypatch.setattr(core, "_majin_enter_prompt", Mock(return_value=False))
    monkeypatch.setattr(core, "_template_in_reference_box", Mock(return_value=(False, 0.1, None)))
    monkeypatch.setattr(core, "_tap_key_unchecked", Mock(side_effect=lambda _key: sleep(0.14)))
    state.sleep = sleep
    return state


@pytest.fixture
def stuck(flow, monkeypatch):
    assets = dict.fromkeys(("training", "slot", "inventory", "game_menu"), object())
    monkeypatch.setattr(core, "_senzu_assets", lambda: assets)
    monkeypatch.setattr(core, "_ensure_training_menu_for_senzu", Mock(return_value=True))
    monkeypatch.setattr(core, "_wait_for_training_menu_for_senzu", Mock(return_value=(True, 0.99)))
    monkeypatch.setattr(core, "_stable_senzu_slot_state", Mock(return_value=(True, 0.99)))
    monkeypatch.setattr(core, "_consume_open_senzu_slot", Mock(return_value=(False, 0.99)))
    monkeypatch.setattr(core, "_refill_senzu_slot", Mock(return_value=True))
    monkeypatch.setattr(core, "click_at", Mock())
    return flow


def test_loaded_slot_requests_reset_without_h_row_click_or_inventory_fallback(stuck, capsys):
    assert not core.eat_senzu(Mock())
    assert core.SENZU_STUCK_RESET_REQUESTED
    assert core.SENZU_STUCK_RESETS_USED == 1
    assert core.UI_STOP_REQUESTED
    assert core.TELEMETRY["senzu_eaten"] == 0
    assert core._tap_key_unchecked.call_args_list == [call("tab"), call("h")]
    core.click_at.assert_not_called()
    core._refill_senzu_slot.assert_not_called()
    assert "[SENZU] Eat key ignored with a loaded slot; resetting the character to clear the game's stuck item state" in capsys.readouterr().out


def test_majin_reserve_hands_the_stuck_item_to_recovery_and_releases_ownership(stuck, monkeypatch):
    for name, value in {
        "MAJIN_REGEN_READY_AT": 30.0, "MAJIN_REGEN_CHANNEL_UNTIL": 0.0,
        "MAJIN_REGEN_SENZU_RESERVE": True, "SENZU_ENABLED": True,
        "SENZU_DISABLED_FOR_RUN": False, "HEALING_ACTION": None,
    }.items():
        monkeypatch.setattr(core, name, value)
    monkeypatch.setattr(core, "_hp_bar_is_red", lambda _: True)
    with pytest.raises(core.QuitException):
        core.use_majin_regen(Mock())
    assert core.SENZU_STUCK_RESET_REQUESTED
    assert core.SENZU_STUCK_RESETS_USED == 1
    assert not core.MAJIN_CONTROLLER_ACTIVE
    assert core.HEALING_ACTION is None
    core.click_at.assert_not_called()


def test_unstable_loaded_slot_keeps_hp_inventory_confirmation_instead_of_reset(stuck, monkeypatch):
    core._stable_senzu_slot_state.side_effect = [(True, 0.99), (None, 0.76)]
    monkeypatch.setattr(core.cv2, "imwrite", Mock())
    monkeypatch.setattr(core, "_grab_reference_box", Mock())
    monkeypatch.setattr(core, "_wait_for_green_hp", Mock(return_value=True))
    monkeypatch.setattr(core, "_resume_training_after_senzu", Mock(return_value=True))
    assert core.eat_senzu(Mock())
    assert not core.SENZU_STUCK_RESET_REQUESTED
    assert core.SENZU_STUCK_RESETS_USED == 0
    core._refill_senzu_slot.assert_called_once()


def test_lost_digit_focus_stops_without_requesting_reset(stuck):
    core._consume_open_senzu_slot.return_value = (None, 0.0)
    assert not core.eat_senzu(Mock())
    assert core.UI_STOP_REQUESTED
    assert not core.SENZU_STUCK_RESET_REQUESTED
    assert core.SENZU_STUCK_RESETS_USED == 0


@pytest.mark.parametrize("recoverable", [True, False])
def test_controller_recovers_stuck_item_with_auto_retry_off(stuck, monkeypatch, recoverable, capsys):
    attempts = []

    def controller():
        attempts.append(1)
        if len(attempts) == 1:
            core.eat_senzu(Mock())
        else:
            core._record_run_outcome("completed", "Training finished")

    recover = Mock(return_value=recoverable)
    monkeypatch.setattr(core, "run_master_controller", controller)
    monkeypatch.setattr(core, "_auto_retry_recover", recover)
    monkeypatch.setattr(core, "_stop_background_game_monitor", Mock(return_value=True))
    monkeypatch.setattr(core, "_finalize_run_result", Mock())
    core._run_macro_safe()
    recover.assert_called_once_with(stuck_senzu=True)
    assert len(attempts) == (2 if recoverable else 1)
    assert core._CURRENT_RUN_OUTCOME == ("completed" if recoverable else "error")
    assert core.TELEMETRY["recovery_attempts"] == 1
    assert not core.SENZU_STUCK_RESET_REQUESTED
    if not recoverable:
        assert "Character reset or GC return could not be confirmed; stopping safely" in capsys.readouterr().out


def test_persistently_ignored_eat_stops_after_one_reset_even_with_auto_retry_on(stuck, monkeypatch, capsys):
    monkeypatch.setattr(core, "AUTO_RETRY_ON_FAILURE", True)
    monkeypatch.setattr(core, "run_master_controller", lambda: core.eat_senzu(Mock()))
    recover = Mock(return_value=True)
    monkeypatch.setattr(core, "_auto_retry_recover", recover)
    monkeypatch.setattr(core, "_stop_background_game_monitor", Mock(return_value=True))
    monkeypatch.setattr(core, "_finalize_run_result", Mock())
    core._run_macro_safe()
    recover.assert_called_once_with(stuck_senzu=True)
    assert core.SENZU_STUCK_RESETS_USED == 1
    assert core._CURRENT_RUN_OUTCOME == "error"
    assert not core._CURRENT_RUN_RETRYABLE
    assert "one-reset-per-run limit was reached" in capsys.readouterr().out


def test_manual_stop_cancels_the_stuck_reset_handoff(stuck, monkeypatch):
    def controller():
        core.eat_senzu(Mock())
        core._USER_STOP_LATCHED = True

    monkeypatch.setattr(core, "run_master_controller", controller)
    recover = Mock()
    monkeypatch.setattr(core, "_auto_retry_recover", recover)
    monkeypatch.setattr(core, "_stop_background_game_monitor", Mock(return_value=True))
    monkeypatch.setattr(core, "_finalize_run_result", Mock())
    core._run_macro_safe()
    recover.assert_not_called()


def test_new_start_renews_the_stuck_reset_allowance(flow, monkeypatch):
    monkeypatch.setattr(core, "SENZU_STUCK_RESETS_USED", 1)
    monkeypatch.setattr(core, "SENZU_STUCK_RESET_REQUESTED", True)
    monkeypatch.setattr(core, "run_master_controller", Mock())
    monkeypatch.setattr(core, "_stop_background_game_monitor", Mock(return_value=True))
    monkeypatch.setattr(core, "_finalize_run_result", Mock())
    core._run_macro_safe()
    assert core.SENZU_STUCK_RESETS_USED == 0
    assert not core.SENZU_STUCK_RESET_REQUESTED


def test_stuck_item_recovery_forces_existing_reset_and_gc_return(flow, monkeypatch):
    monkeypatch.setattr(core, "AUTO_RETRY_RECOVERY_MODE", "wait_for_death")
    monkeypatch.setattr(core, "AUTO_RETRY_WALK_OUT", False)
    reset = Mock(return_value=True)
    walk = Mock(return_value=True)
    death = Mock()
    monkeypatch.setattr(core, "_auto_retry_reset_character", reset)
    monkeypatch.setattr(core, "_auto_retry_walk_forward", walk)
    monkeypatch.setattr(core, "_auto_retry_wait_for_death", death)
    assert core._auto_retry_recover(stuck_senzu=True)
    reset.assert_called_once_with()
    walk.assert_called_once_with()
    death.assert_not_called()


def test_all_ignored_digits_keep_the_original_acceptance_budget_and_never_click(flow, monkeypatch):
    monkeypatch.setattr(core, "_senzu_slot_has_bean", Mock(return_value=(True, 0.9)))
    click = Mock()
    monkeypatch.setattr(core, "click_at", click)
    assert core._consume_open_senzu_slot(Mock(), 1, object(), "full") == (False, 0.9)
    assert flow.now == pytest.approx(3 * (0.14 + 0.77))
    assert core._tap_key_unchecked.call_args_list == [call("1")] * 3
    click.assert_not_called()


def test_late_clear_is_confirmed_before_any_digit_retry(flow, monkeypatch):
    monkeypatch.setattr(core, "_senzu_slot_has_bean", lambda *_a, **_k: (flow.now < 0.69, 0.9))
    assert core._consume_open_senzu_slot(Mock(), 1, object(), "full")[0]
    assert flow.now < 0.91
    core._tap_key_unchecked.assert_called_once_with("1")


@pytest.mark.parametrize("step", ["screen", "training", "hp", "resume", "assignment", "slot", "menu", "majin"])
def test_ready_confirmations_use_fifty_ms_polls_and_retain_frame_guards(flow, monkeypatch, step):
    assets = dict.fromkeys(("training", "slot", "inventory", "game_menu"), object())
    monkeypatch.setattr(core, "_template_in_reference_box", Mock(return_value=(True, 0.99, (0, 0))))
    monkeypatch.setattr(core, "_training_menu_visible_for_senzu", Mock(return_value=(True, 0.99)))
    monkeypatch.setattr(core, "_hp_bar_is_green", Mock(return_value=True))
    monkeypatch.setattr(core, "_senzu_slot_has_bean", Mock(return_value=(True, 0.99)))
    monkeypatch.setattr(core, "_find_senzu_row_in_frame", Mock(return_value=(520, 96, 0.99)))
    monkeypatch.setattr(core, "_grab_reference_box", Mock())
    monkeypatch.setattr(core, "_game_has_focus", lambda: True)
    monkeypatch.setattr(core, "_button_screen_point", lambda _stat: (500, 500))
    monkeypatch.setattr(core, "click_at", Mock())
    if step == "screen":
        assert core._wait_for_senzu_screen(Mock(), object(), (0, 0, 50, 50))[0] is not None
        expected = 0.0
    elif step == "training":
        assert core._wait_for_training_menu_for_senzu(Mock(), object())[0]
        expected = 0.05
    elif step == "hp":
        assert core._wait_for_green_hp(Mock(), 7.0)
        expected = 0.05
    elif step == "resume":
        core._training_menu_visible_for_senzu.side_effect = [(True, 0.99), (False, 0.1), (False, 0.1)]
        assert core._resume_training_after_senzu(Mock(), assets)
        expected = 0.05
    elif step == "assignment":
        assert core._wait_for_senzu_assignment(Mock(), assets, 520, 96, "full", 1.2)[0]
        expected = 0.10
    elif step == "slot":
        assert core._stable_senzu_slot_state(Mock(), 1, object(), hotbar=True, timeout=0.46)[0]
        expected = 0.10
    elif step == "menu":
        assert core._wait_for_senzu_inventory_or_game_menu(Mock(), assets)[0] == "inventory"
        expected = 0.05
    else:
        assert core._majin_wait_for(lambda: True, True)
        expected = 0.05
    assert flow.now == pytest.approx(expected)


@pytest.mark.parametrize("step", ["training", "hp", "majin"])
def test_confirmation_poll_does_not_extend_its_timeout(flow, monkeypatch, step):
    monkeypatch.setattr(core, "_training_menu_visible_for_senzu", Mock(return_value=(False, 0.1)))
    monkeypatch.setattr(core, "_hp_bar_is_green", Mock(return_value=False))
    monkeypatch.setattr(core, "_game_has_focus", lambda: True)
    if step == "training":
        assert not core._wait_for_training_menu_for_senzu(Mock(), object(), timeout=0.11)[0]
    elif step == "hp":
        assert not core._wait_for_green_hp(Mock(), 0.11)
    else:
        assert not core._majin_wait_for(lambda: False, True, timeout=0.11)
    assert flow.now == pytest.approx(0.11)


@pytest.mark.parametrize("original, expected", [
    ((220, -100), (1400, 420)),
    ((500, 0), (1400, 420)),
    ((600, 500), (600, 500)),
    ((-1000, 200), (-1000, 200)),
    (None, (1400, 420)),
])
def test_cursor_guard_parks_outside_chat_and_restores_safe_positions(flow, monkeypatch, original, expected):
    geometry = (200, -120, 1920, 1080)
    monkeypatch.setattr(core, "_safe_get_cursor_pos", lambda: original)
    with pytest.raises(RuntimeError, match="menu failed"):
        with core._menu_cursor_guard(geometry):
            core._user32.SetCursorPos.assert_called_with(1400, 420)
            raise RuntimeError("menu failed")
    core._user32.SetCursorPos.assert_called_with(*expected)
    assert core._nudge_pointer.call_count == 2


def test_scaled_client_chat_position_is_not_restored(flow):
    core._restore_menu_cursor((150, 220), (100, 200, 960, 540))
    core._user32.SetCursorPos.assert_called_once_with(700, 470)


def test_missing_original_cursor_position_has_no_top_left_fallback(flow, monkeypatch):
    monkeypatch.setattr(core.win32api, "GetCursorPos", Mock(side_effect=OSError("unavailable")))
    assert core._safe_get_cursor_pos() is None


def test_gc_room_remains_recognizable_when_chat_covers_the_top_left():
    path = Path(__file__).parent / "fixtures/gc_recovery/entry_facing_room.png"
    frame = cv2.imread(str(path))
    assert gc_recovery.room_visible(frame)
    frame[:420, :480] = 0
    assert gc_recovery.room_visible(frame)


@pytest.mark.parametrize("frame", [
    np.zeros((1080, 1920, 3), np.uint8),
    np.full((1080, 1920, 3), (0, 0, 180), np.uint8),
    np.zeros((540, 960, 3), np.uint8),
])
def test_gc_room_requires_red_floor_and_corroborating_windows(frame):
    assert not gc_recovery.room_visible(frame)


@pytest.mark.parametrize("after_run", [False, True])
@pytest.mark.parametrize("known_gc, visible_gc, expected", [(True, False, False), (False, True, False), (False, False, True)])
def test_missing_gc_control_fails_for_gc_and_is_a_noop_outside_gc(flow, monkeypatch, after_run, known_gc, visible_gc, expected, capsys):
    geometry = (0, 0, 1920, 1080)
    monkeypatch.setattr(core, "GC_RUN_CONFIRMED", known_gc)
    monkeypatch.setattr(core, "_load_gravity_templates", lambda: dict.fromkeys(range(0, 101, 10), object()))
    monkeypatch.setattr(core, "_read_gc_gravity", lambda *_: (None, 0.0, 0.0, False))
    monkeypatch.setattr(core, "_grab_reference_box", Mock())
    monkeypatch.setattr(core.gc_recovery, "room_visible", lambda _frame: visible_gc)
    monkeypatch.setattr(core, "robust_move", Mock())
    monkeypatch.setattr(core, "safe_sleep", flow.sleep)
    click = Mock()
    monkeypatch.setattr(core, "click_at", click)
    monkeypatch.setattr(core, "_click_sendinput_abs", click)
    assert core._cycle_gc_gravity_to_zero(Mock(), geometry, after_run=after_run) is expected
    click.assert_not_called()
    if not expected:
        assert "0G could not be confirmed" in capsys.readouterr().out


def test_after_run_reports_failure_for_an_occluded_gc_control(flow, monkeypatch):
    geometry = (0, 0, 1920, 1080)
    monkeypatch.setattr(core, "GC_RUN_CONFIRMED", True)
    monkeypatch.setattr(core, "focus_game_window", lambda: True)
    monkeypatch.setattr(core, "_confirmed_game_capture_rect", lambda: geometry)
    capture = Mock(__enter__=Mock(return_value=Mock()), __exit__=Mock(return_value=False))
    monkeypatch.setattr(core.mss, "MSS", Mock(return_value=capture))
    monkeypatch.setattr(core, "_after_run_open_training_menu", lambda *_: True)
    monkeypatch.setattr(core, "_load_gravity_templates", lambda: dict.fromkeys(range(0, 101, 10), object()))
    monkeypatch.setattr(core, "_read_gc_gravity", lambda *_: (None, 0.0, 0.0, False))
    assert not core._after_run_set_zero_gravity()


def test_observed_gc_control_is_remembered_until_the_next_run(flow, monkeypatch):
    monkeypatch.setattr(core, "_grab_reference_box", Mock())
    monkeypatch.setattr(core, "_gravity_mask_from_frame", Mock())
    monkeypatch.setattr(core, "_gravity_value_from_mask", lambda *_: (10, 0.99, 0.3))
    monkeypatch.setattr(core, "_gravity_control_glyph_count", lambda _: 3)
    assert core._read_gc_gravity(Mock(), (0, 0, 1920, 1080), {})[-1]
    assert core.GC_RUN_CONFIRMED
    core._begin_run_result()
    assert not core.GC_RUN_CONFIRMED


def test_menu_detection_requires_two_consecutive_frames_of_the_same_route(flow, monkeypatch):
    assets = {name: object() for name in ("inventory", "game_menu")}

    def visible(_sct, template, _box):
        route = "game_menu" if 0.04 < flow.now < 0.09 else "inventory"
        matched = template is assets[route]
        return matched, 0.99 if matched else 0.1, (0, 0)

    monkeypatch.setattr(core, "_template_in_reference_box", visible)
    assert core._wait_for_senzu_inventory_or_game_menu(Mock(), assets)[0] == "inventory"
    assert flow.now == pytest.approx(0.15)


def test_resuming_training_parks_the_cursor_before_menu_confirmation(flow, monkeypatch):
    checks = []

    def training_visible(*_args):
        checks.append(1)
        if len(checks) == 1:
            return True, 0.99
        core._user32.SetCursorPos.assert_called_with(1200, 540)
        return False, 0.1

    monkeypatch.setattr(core, "_training_menu_visible_for_senzu", training_visible)
    monkeypatch.setattr(core, "_button_screen_point", lambda _stat: (420, 360))
    monkeypatch.setattr(core, "click_at", Mock())
    assert core._resume_training_after_senzu(Mock(), {"training": object()})
    assert len(checks) == 3


def test_inventory_back_parks_outside_chat_before_waiting_for_game_menu(flow, monkeypatch):
    assets = {name: object() for name in ("training", "inventory", "game_menu")}
    monkeypatch.setattr(core, "_training_menu_visible_for_senzu", lambda *_: (False, 0.1))
    monkeypatch.setattr(core, "_wait_for_senzu_inventory_or_game_menu", lambda *_a, **_k: ("inventory", 0.1, 0.99))
    monkeypatch.setattr(core, "_wait_for_training_menu_for_senzu", Mock(side_effect=[(False, 0.1), (True, 0.99)]))
    monkeypatch.setattr(core, "click_at", Mock())

    def screen(_sct, template, _box, **_kwargs):
        if template is assets["game_menu"]:
            core._user32.SetCursorPos.assert_called_with(1200, 540)
        return (0, 0), 0.99

    monkeypatch.setattr(core, "_wait_for_senzu_screen", screen)
    assert core._close_inventory_to_training(Mock(), assets)
    core.click_at.assert_called_once_with(80, 377)


def test_after_run_menu_click_parks_outside_chat_immediately(flow, monkeypatch):
    monkeypatch.setattr(core, "_ensure_game_focus_for_input", lambda: True)
    click = Mock()
    monkeypatch.setattr(core, "_click_sendinput_abs", click)
    core._after_run_click_reference(80, 377, (0, 0, 1920, 1080))
    click.assert_called_once_with(80, 377)
    core._user32.SetCursorPos.assert_called_once_with(1200, 540)


def test_failed_run_zero_gravity_remains_independent_of_auto_raise(flow, monkeypatch):
    monkeypatch.setattr(core, "AFTER_RUN_GAME_ACTION", "none")
    monkeypatch.setattr(core, "AFTER_RUN_ON_FAILURE", False)
    monkeypatch.setattr(core, "GC_GRAVITY_TARGET_G", 100)
    assert core._failed_run_needs_zero_gravity("error")
    assert not core._failed_run_needs_zero_gravity("completed")


def test_gc_room_is_remembered_without_the_gravity_control(flow, monkeypatch):
    path = Path(__file__).parent / "fixtures/gc_recovery/entry_facing_room.png"
    grab = Mock(return_value=cv2.imread(str(path)))
    monkeypatch.setattr(core, "_grab_reference_box", grab)
    assert core._gc_room_confirmed(Mock(), (0, 0, 1920, 1080))
    assert core.GC_RUN_CONFIRMED
    grab.reset_mock()
    assert core._gc_room_confirmed(Mock(), (0, 0, 1920, 1080))
    grab.assert_not_called()


def test_training_wait_requires_two_gameplay_prompt_frames_when_allowed(flow, monkeypatch):
    monkeypatch.setattr(core, "_training_menu_visible_for_senzu", lambda *_: (False, 0.1))
    core._majin_enter_prompt.side_effect = [True, False, True, True]
    assert core._wait_for_training_menu_for_senzu(
        Mock(), object(), allow_gameplay=True
    )[0] is None
    assert flow.now == pytest.approx(0.15)


def test_training_wait_never_accepts_gameplay_without_explicit_permission(flow, monkeypatch):
    monkeypatch.setattr(core, "_training_menu_visible_for_senzu", lambda *_: (False, 0.1))
    core._majin_enter_prompt.return_value = True
    assert core._wait_for_training_menu_for_senzu(Mock(), object(), timeout=0.11)[0] is False
    assert flow.now == pytest.approx(0.11)
    core._majin_enter_prompt.assert_not_called()


def test_confirmed_gameplay_skips_absent_menu_waits_before_tab(flow, monkeypatch):
    core._majin_enter_prompt.return_value = True
    monkeypatch.setattr(core, "_training_menu_visible_for_senzu",
                        lambda *_: (core._tap_key_unchecked.called, 0.99))
    menus = Mock()
    monkeypatch.setattr(core, "_wait_for_senzu_inventory_or_game_menu", menus)
    assets = {name: object() for name in ("training", "inventory", "game_menu")}
    assert core._ensure_training_menu_for_senzu(Mock(), assets)
    core._tap_key_unchecked.assert_called_once_with("tab")
    menus.assert_not_called()
    assert flow.now == pytest.approx(0.24)


def test_inventory_close_to_gameplay_polls_the_prompt_and_reopens_training(flow, monkeypatch):
    assets = {name: object() for name in ("training", "inventory", "game_menu")}
    monkeypatch.setattr(core, "_wait_for_senzu_inventory_or_game_menu", lambda *_a, **_k: ("inventory", 0.1, 0.99))
    monkeypatch.setattr(core, "_wait_for_senzu_screen", Mock(side_effect=AssertionError("absent-menu wait")))
    core._majin_enter_prompt.return_value = True

    def training_visible(*_args):
        taps = [entry.args[0] for entry in core._tap_key_unchecked.call_args_list]
        return "tab" in taps, 0.99

    monkeypatch.setattr(core, "_training_menu_visible_for_senzu", training_visible)
    assert core._close_inventory_to_training(Mock(), assets)
    assert core._tap_key_unchecked.call_args_list == [call("m"), call("tab")]
    assert flow.now == pytest.approx(0.38)


@pytest.mark.parametrize("menu", ["inventory", "game_menu"])
def test_world_prompt_behind_an_open_menu_is_not_gameplay_confirmation(flow, monkeypatch, menu):
    assets = {name: object() for name in ("training", "inventory", "game_menu")}
    monkeypatch.setattr(core, "_senzu_assets", lambda: assets)
    monkeypatch.setattr(core, "_training_menu_visible_for_senzu", lambda *_: (False, 0.1))
    core._majin_enter_prompt.return_value = True
    monkeypatch.setattr(core, "_template_in_reference_box", lambda _sct, template, _box: (
        template is assets[menu], 0.99, (0, 0)
    ))
    assert not core._senzu_gameplay_visible(Mock(), assets)
    assert core._wait_for_training_menu_for_senzu(
        Mock(), assets["training"], timeout=0.11, allow_gameplay=True
    )[0] is False
    assert flow.now == pytest.approx(0.11)
    core._tap_key_unchecked.assert_not_called()
