import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import cv2
import numpy as np
import pytest

import majin_vision
import xynmacro_core as core


@pytest.fixture(autouse=True)
def points_left_read_is_out_of_scope(monkeypatch):
    # These tests pin every capture in the healing flow; the Points Left read on
    # resume has its own tests in test_points_on_resume.py.
    monkeypatch.setattr(core, "_capture_points_panels", lambda _sct, _stat=None: [])


FIXTURES = Path(__file__).parent / "fixtures" / "majin"


@pytest.fixture
def flow(monkeypatch):
    state = SimpleNamespace(now=1000.0, training=True, held=False, cast_at=None,
                            heals=True, heal_after=4.5, hp_reads=[], panel="115005_0", events=[], on_sleep=None)
    for name, value in {
        "HEAL_METHOD": "majin_regen", "MAJIN_REGEN_SLOT": 3,
        "MAJIN_REGEN_SENZU_RESERVE": True, "MAJIN_REGEN_READY_AT": 0.0,
        "MAJIN_REGEN_CHANNEL_UNTIL": 0.0,
        "MAJIN_CONTROLLER_ACTIVE": False, "SENZU_ENABLED": True,
        "SENZU_DISABLED_FOR_RUN": False, "SENZU_TRAIT_RESELECT_DEFERRED": False,
        "CURRENT_TRAINING_STATE": "Health", "UI_STOP_REQUESTED": False,
        "_USER_STOP_LATCHED": False, "CONTROLLER_PAUSED": False,
        "PAUSE_TOGGLE_REQUESTED": False, "_background_monitor_stop": None,
        "_background_monitor_thread": threading.current_thread(),
        "SENZU_CONTROLLER_ACTIVE": threading.Event(),
        "SENZU_CONTROLLER_RESUME_REQUIRED": threading.Event(),
        "PROGRESSION_COMPLETE_REQUESTED": threading.Event(),
        "TELEMETRY": dict.fromkeys(core.TELEMETRY, 0),
    }.items():
        monkeypatch.setattr(core, name, value)

    def sleep(seconds):
        state.now += seconds
        if state.on_sleep:
            state.on_sleep()

    def down(key):
        state.events.append((state.now, "down", key))
        if key == "g":
            state.held = True
        elif key == "tab":
            state.training = not state.training
        elif key.isdigit():
            assert state.held and not state.training
            state.cast_at = state.now

    def up(key):
        state.events.append((state.now, "up", key))
        if key == "g":
            state.held = False

    def read(name, region):
        return cv2.imread(str(FIXTURES / f"{name}_{region}.png"))

    def grab(_sct, box, *args, **kwargs):
        if box == majin_vision.PANEL_BOX:
            return read(state.panel if state.held else "115000_2", "panel")
        if box == majin_vision.ENTER_PROMPT_BOX:
            return read("114958_0" if state.training else "115005_0", "enter")
        if box == core.SENZU_HP_FILL_BOX:
            healed = state.heals and state.cast_at is not None and state.now - state.cast_at >= state.heal_after
            state.hp_reads.append((state.now, healed))
            return read("115012_0" if healed else "115000_2", "hp")
        if isinstance(box, dict):
            # The progression-stall watcher reads the label area; keep it blank.
            return np.zeros((box["height"], box["width"], 3), np.uint8)
        pytest.fail(f"Unexpected capture: {box}")

    def click(*_args):
        state.events.append((state.now, "click", "Health"))
        assert state.training
        state.training = False

    monkeypatch.setattr(core, "_park_menu_cursor", Mock())
    monkeypatch.setattr(core, "_restore_menu_cursor", Mock())
    monkeypatch.setattr(core.time, "monotonic", lambda: state.now)
    monkeypatch.setattr(core.time, "sleep", sleep)
    monkeypatch.setattr(core, "_game_has_focus", lambda: True)
    monkeypatch.setattr(core, "_ensure_game_focus_for_input", lambda: True)
    monkeypatch.setattr(core, "_focus_game_for_senzu", lambda: True)
    monkeypatch.setattr(core, "_grab_reference_box", grab)
    monkeypatch.setattr(core.pydirectinput, "keyDown", Mock(side_effect=down))
    monkeypatch.setattr(core.pydirectinput, "keyUp", Mock(side_effect=up))
    monkeypatch.setattr(core, "_senzu_assets", lambda: dict.fromkeys(("training", "inventory", "game_menu"), object()))
    monkeypatch.setattr(core, "_training_menu_visible_for_senzu", lambda *_args: (state.training, 0.99))
    monkeypatch.setattr(core, "_wait_for_senzu_screen", lambda *_args, **_kwargs: (None, 0.0))
    monkeypatch.setattr(core, "_wait_for_senzu_inventory_or_game_menu", lambda *_args, **_kwargs: (None, 0.1, 0.1))
    monkeypatch.setattr(core, "_template_in_reference_box", lambda *_args: (False, 0.1, None))
    monkeypatch.setattr(core, "_button_screen_point", lambda _stat: (10, 20))
    monkeypatch.setattr(core, "click_at", Mock(side_effect=click))
    monkeypatch.setattr(core, "eat_senzu", Mock(return_value=True))
    return state


def test_success_releases_g_then_waits_without_input_before_reselect(flow):
    assert core.use_majin_regen(Mock())
    assert core.TELEMETRY["majin_regens"] == 1
    assert core.TELEMETRY["majin_regen_failures"] == 0
    assert 59 < core.MAJIN_REGEN_READY_AT - flow.now <= 60
    assert [(kind, key) for _, kind, key in flow.events] == [
        ("down", "tab"), ("up", "tab"), ("down", "g"),
        ("down", "3"), ("up", "3"), ("up", "g"),
        ("down", "tab"), ("up", "tab"), ("click", "Health"),
    ]
    released = next(t for t, kind, key in flow.events if (kind, key) == ("up", "g"))
    returned = next(t for t, kind, key in flow.events if t > released and kind == "down")
    assert returned >= flow.hp_reads[-1][0]
    assert flow.hp_reads[-2][1] and flow.hp_reads[-1][1]
    assert not flow.held
    assert not core.MAJIN_CONTROLLER_ACTIVE
    core.eat_senzu.assert_not_called()


@pytest.mark.parametrize("action", ["majin_regen", "majin_cooldown", "reserve_senzu"])
def test_healing_action_describes_execution_and_clears_on_exit(flow, monkeypatch, action):
    observed = []
    if action == "majin_regen":
        flow.on_sleep = lambda: observed.append(core.HEALING_ACTION)
    else:
        flow.panel = "115007_2"
        monkeypatch.setattr(core, "MAJIN_REGEN_SENZU_RESERVE", action == "reserve_senzu")
        if action == "reserve_senzu":
            monkeypatch.setattr(core, "eat_senzu", lambda *_a, **_k: observed.append(core.HEALING_ACTION) or True)
        else:
            monkeypatch.setattr(core, "_resume_training_after_senzu",
                                lambda *_: observed.append(core.HEALING_ACTION) or True)
    assert core.use_majin_regen(Mock())
    assert action in observed
    assert core.HEAL_METHOD == "majin_regen"
    assert core.HEALING_ACTION is None


def test_grey_slot_uses_reserve_only_after_restoring_training(flow):
    flow.panel = "115007_2"
    assert core.use_majin_regen(Mock())
    assert flow.training
    assert not flow.held
    assert flow.cast_at is None
    assert 59 < core.MAJIN_REGEN_READY_AT - flow.now <= 60
    core.eat_senzu.assert_called_once()
    assert core.eat_senzu.call_args.kwargs == {"exit_active_minigame": False}
    assert core.TELEMETRY["majin_regen_failures"] == 0


@pytest.mark.parametrize("disabled_setting", ["reserve", "senzu", "stock"])
def test_grey_slot_without_available_reserve_resumes(flow, monkeypatch, disabled_setting):
    flow.panel = "115009_9"
    if disabled_setting == "reserve":
        monkeypatch.setattr(core, "MAJIN_REGEN_SENZU_RESERVE", False)
    elif disabled_setting == "senzu":
        monkeypatch.setattr(core, "SENZU_ENABLED", False)
    else:
        monkeypatch.setattr(core, "SENZU_DISABLED_FOR_RUN", True)
    assert core.use_majin_regen(Mock())
    assert flow.events[-1][1:] == ("click", "Health")
    core.eat_senzu.assert_not_called()


@pytest.mark.parametrize("reserve", [True, False])
def test_internal_cooldown_does_not_open_transformations(flow, monkeypatch, reserve):
    monkeypatch.setattr(core, "MAJIN_REGEN_READY_AT", flow.now + 30)
    monkeypatch.setattr(core, "MAJIN_REGEN_SENZU_RESERVE", reserve)
    assert core.use_majin_regen(Mock())
    assert flow.events == []
    assert core.eat_senzu.call_count == int(reserve)
    if reserve:
        assert core.eat_senzu.call_args.kwargs == {"exit_active_minigame": True}


@pytest.mark.parametrize("reserve", [True, False])
def test_hp_not_raised_sets_failure_cooldown(flow, monkeypatch, reserve):
    flow.heals = False
    monkeypatch.setattr(core, "MAJIN_REGEN_SENZU_RESERVE", reserve)
    assert core.use_majin_regen(Mock())
    assert 179 < core.MAJIN_REGEN_READY_AT - flow.now <= 180
    assert core.TELEMETRY["majin_regen_failures"] == 1
    assert core.TELEMETRY["majin_regens"] == 0
    assert core.eat_senzu.call_count == int(reserve)


@pytest.mark.parametrize("stage", ["tab", "panel", "channel"])
def test_stop_during_each_stage_releases_g_and_sends_no_cleanup_inputs(flow, stage):
    def stop():
        at_stage = {"tab": not flow.training, "panel": flow.held,
                    "channel": flow.cast_at is not None and not flow.held}[stage]
        if at_stage:
            core.UI_STOP_REQUESTED = True
    flow.on_sleep = stop
    with pytest.raises(core.QuitException):
        core.use_majin_regen(Mock())
    assert not flow.held
    assert not core.MAJIN_CONTROLLER_ACTIVE
    assert not any(kind == "click" for _, kind, _ in flow.events)
    if stage == "panel":
        assert flow.events[-1][1:] == ("up", "g")
        assert flow.cast_at is None
    if stage == "channel":
        assert core.MAJIN_REGEN_READY_AT == flow.now + 180
        assert core.TELEMETRY["majin_regen_failures"] == 1
    core.eat_senzu.assert_not_called()


@pytest.mark.parametrize("stage", ["tab", "panel", "channel", "restore"])
def test_pause_releases_g_and_defers_all_input_until_resume(flow, stage):
    requested = False
    paused_events = None
    paused_since = None

    def pause_then_resume():
        nonlocal requested, paused_events, paused_since
        if core.CONTROLLER_PAUSED:
            assert not flow.held
            if paused_events is None:
                paused_events, paused_since = list(flow.events), flow.now
            assert flow.events == paused_events
            if flow.now - paused_since >= 0.3:
                core.PAUSE_TOGGLE_REQUESTED = True
            return
        at_stage = {
            "tab": not flow.training,
            "panel": flow.held,
            "channel": flow.cast_at is not None and not flow.held,
            "restore": flow.cast_at is not None and flow.training,
        }[stage]
        if not requested and at_stage:
            requested = True
            core.PAUSE_TOGGLE_REQUESTED = True

    flow.on_sleep = pause_then_resume
    assert core.use_majin_regen(Mock())
    assert requested and paused_events is not None
    assert not core.CONTROLLER_PAUSED
    assert not core.PAUSE_TOGGLE_REQUESTED
    assert not flow.held
    if stage == "channel":
        assert core.TELEMETRY["majin_regen_failures"] == 1
        assert 174 < core.MAJIN_REGEN_READY_AT - flow.now <= 180
        released = next(t for t, kind, key in flow.events if (kind, key) == ("up", "g"))
        returned = next(t for t, kind, key in flow.events if t > released and kind == "down")
        assert returned >= flow.cast_at + flow.heal_after
        assert flow.hp_reads[-2][1] and flow.hp_reads[-1][1]
        core.eat_senzu.assert_not_called()  # The uninterrupted game channel healed during Pause.


def test_stop_while_paused_ends_without_resuming(flow):
    def pause_then_stop():
        if core.CONTROLLER_PAUSED:
            assert not flow.held
            core.UI_STOP_REQUESTED = True
        elif flow.held:
            core.PAUSE_TOGGLE_REQUESTED = True
    flow.on_sleep = pause_then_stop
    with pytest.raises(core.QuitException):
        core.use_majin_regen(Mock())
    assert flow.events[-1][1:] == ("up", "g")


def test_unconfirmed_training_exit_never_holds_g(flow, monkeypatch):
    monkeypatch.setattr(core, "_majin_enter_prompt", lambda _sct: False)
    with pytest.raises(RuntimeError, match="Enter Training Mode prompt"):
        core.use_majin_regen(Mock())
    assert not any(key == "g" for _, _, key in flow.events)


def test_wrong_panel_never_sends_slot_and_releases_g(flow):
    flow.panel = "115000_2"
    with pytest.raises(RuntimeError, match="Transformations panel not confirmed"):
        core.use_majin_regen(Mock())
    assert flow.cast_at is None
    assert flow.events[-1][1:] == ("up", "g")


def test_empty_configured_slot_never_casts(flow, monkeypatch):
    monkeypatch.setattr(core, "MAJIN_REGEN_SLOT", 8)
    with pytest.raises(RuntimeError, match="empty or unreadable"):
        core.use_majin_regen(Mock())
    assert flow.cast_at is None
    assert not flow.held


def test_focus_failure_never_sends_a_key(flow, monkeypatch):
    monkeypatch.setattr(core, "_ensure_game_focus_for_input", lambda: False)
    with pytest.raises(RuntimeError, match="focus not confirmed"):
        core.use_majin_regen(Mock())
    assert flow.events == []


def test_keydown_error_still_releases_g(flow, monkeypatch):
    flow.training = False
    def broken_down(key):
        if key == "g":
            raise RuntimeError("input failed")
    monkeypatch.setattr(core.pydirectinput, "keyDown", broken_down)
    with pytest.raises(RuntimeError, match="input failed"):
        core.use_majin_regen(Mock())
    core.pydirectinput.keyUp.assert_called_once_with("g")


def test_majin_trigger_does_not_depend_on_senzu_enabled(flow, monkeypatch):
    monkeypatch.setattr(core, "SENZU_ENABLED", False)
    monkeypatch.setattr(core, "SENZU_DISABLED_FOR_RUN", True)
    assert core._healing_monitor_enabled()
    monkeypatch.setattr(core, "HEAL_METHOD", "senzu")
    assert not core._healing_monitor_enabled()


def test_monitor_owns_controller_and_rearms_after_cooldown(flow, monkeypatch):
    stop = threading.Event()
    capture = Mock(__enter__=Mock(return_value=Mock()), __exit__=Mock(return_value=False))
    monkeypatch.setattr(core.mss, "MSS", Mock(return_value=capture))
    monkeypatch.setattr(core, "update_game_window", lambda: True)
    monkeypatch.setattr(core, "_game_geometry", lambda: (0, 0, 1920, 1080))
    monkeypatch.setattr(core, "_gc_death_dialog_visible", lambda *_args: False)
    monkeypatch.setattr(core, "GC_RECOVERY_VERIFY_PENDING", False)
    monkeypatch.setattr(core, "TRAINING_MENU_VISIBLE", False)
    monkeypatch.setattr(core, "PROGRESSION_STATE_STARTED_AT", 0.0)
    monkeypatch.setattr(core, "PROGRESSION_TRACKED_STATE", None)
    monkeypatch.setattr(core, "PROGRESSION_COMPLETE", None)
    monkeypatch.setattr(core, "read_progression_completion", lambda *_args: None)
    monkeypatch.setattr(core, "_hp_bar_is_red", lambda *_args: True)
    monkeypatch.setattr(core, "SENZU_DELAY_SEC", 0.0)
    monkeypatch.setattr(core, "SENZU_ENABLED", False)
    def controller_tick(interval):
        flow.now += interval
        if not stop.is_set():
            core.SENZU_CONTROLLER_RESUME_REQUIRED.clear()

    monkeypatch.setattr(stop, "wait", controller_tick)
    calls = []

    def heal(_sct):
        assert core.SENZU_CONTROLLER_ACTIVE.is_set()
        assert core.SENZU_CONTROLLER_RESUME_REQUIRED.is_set()
        calls.append(flow.now)
        core.MAJIN_REGEN_READY_AT = flow.now + 2.0
        if len(calls) == 2:
            stop.set()
        return True

    monkeypatch.setattr(core, "use_majin_regen", heal)
    core._background_game_monitor(stop)
    assert len(calls) == 2
    assert calls[1] - calls[0] >= 2.0
    assert not core.SENZU_CONTROLLER_ACTIVE.is_set()
    assert core.SENZU_CONTROLLER_RESUME_REQUIRED.is_set()
    core.eat_senzu.assert_not_called()


def test_two_tabs_leave_an_active_minigame_before_g(monkeypatch):
    # Live run: the first Tab only opened the Training Mode menu.
    current = {"screen": "minigame"}
    taps = []

    def tap(key):
        taps.append(key)
        current["screen"] = "menu" if len(taps) == 1 else "out"

    monkeypatch.setattr(core, "_majin_tap", tap)
    monkeypatch.setattr(core, "_majin_check", lambda: None)
    monkeypatch.setattr(core, "_majin_wait", lambda _seconds: None)
    monkeypatch.setattr(core, "_game_has_focus", lambda: True)
    monkeypatch.setattr(core, "_majin_enter_prompt", lambda _sct: current["screen"] == "out")
    monkeypatch.setattr(core, "_senzu_assets", lambda: {"training": object()})
    monkeypatch.setattr(core, "_training_menu_visible_for_senzu",
                        lambda *_args: (current["screen"] == "menu", 0.99))
    monkeypatch.setattr(core, "_hold_majin_panel", Mock(side_effect=RuntimeError("stop here")))
    with pytest.raises(RuntimeError, match="stop here"):
        core._attempt_majin_regen(Mock())
    assert taps == ["tab", "tab"]


def test_early_confirmed_hp_resumes_without_the_fixed_channel_sleep(flow):
    flow.heal_after = 0.25
    assert core.use_majin_regen(Mock())
    released = next(t for t, kind, key in flow.events if (kind, key) == ("up", "g"))
    returned = next(t for t, kind, key in flow.events if t > released and kind == "down")
    assert returned - released < 0.5
    assert flow.hp_reads[-2][1] and flow.hp_reads[-1][1]
    assert returned >= flow.hp_reads[-1][0]
    assert core.MAJIN_REGEN_CHANNEL_UNTIL == 0.0
    assert core.TELEMETRY["majin_regens"] == 1


def test_unrecovered_hp_keeps_the_full_original_channel_and_verification_budget(flow):
    flow.heals = False
    assert core._attempt_majin_regen(Mock()) == "failed"
    released = next(t for t, kind, key in flow.events if (kind, key) == ("up", "g"))
    assert flow.now - released == pytest.approx(5.3)
    assert core.TELEMETRY["majin_regen_failures"] == 1
    assert core.TELEMETRY["majin_regens"] == 0


def test_auto_uses_the_row_named_super_regeneration_not_the_slot_setting(flow, monkeypatch):
    monkeypatch.setattr(core, "MAJIN_REGEN_AUTO", True)
    monkeypatch.setattr(core, "MAJIN_REGEN_SLOT", 8)  # empty row on this panel
    assert core.use_majin_regen(Mock())
    assert flow.cast_at is not None
    assert ("down", "3") in [event[1:] for event in flow.events]


def test_auto_without_super_regeneration_falls_back_for_the_rest_of_the_run(flow, monkeypatch):
    monkeypatch.setattr(core, "MAJIN_REGEN_AUTO", True)
    monkeypatch.setattr(core, "HEAL_METHOD", "senzu")
    flow.panel = "115000_2"  # normal Moves panel: no transformations
    core.use_majin_regen(Mock())
    assert flow.cast_at is None
    assert not flow.held
    assert core.MAJIN_REGEN_UNAVAILABLE_FOR_RUN
    assert core._effective_heal_method() == "senzu"


def test_effective_heal_method():
    for auto, unavailable, chosen, expected in (
        (True, False, "senzu", "majin_regen"),
        (True, True, "senzu", "senzu"),
        (True, True, "majin_regen", "majin_regen"),
        (False, False, "senzu", "senzu"),
        (False, False, "majin_regen", "majin_regen"),
    ):
        with patch.multiple(core, MAJIN_REGEN_AUTO=auto,
                            MAJIN_REGEN_UNAVAILABLE_FOR_RUN=unavailable, HEAL_METHOD=chosen):
            assert core._effective_heal_method() == expected
