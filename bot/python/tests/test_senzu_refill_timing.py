"""Offline ordering, timing and safety checks for Auto-Senzu inventory recovery."""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, call

import cv2
import numpy as np
import pytest

import xynmacro_core as core


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        assert seconds >= 0
        self.now += seconds


@pytest.fixture
def clock(monkeypatch):
    clock = FakeClock()
    monkeypatch.setattr(core.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(core.time, "sleep", clock.sleep)
    monkeypatch.setattr(core, "_senzu_abort_requested", lambda: False)
    return clock


@pytest.fixture
def refill(monkeypatch, clock):
    assets = {name: object() for name in (
        "training", "game_menu", "inventory", "bean", "slot", "digits"
    )}
    state = SimpleNamespace(
        clock=clock, assets=assets, clicks=[], events=[], count=96,
        find_helper=core._find_senzu_row,
        close_helper=core._close_inventory_to_training,
        menu_helper=core._wait_for_senzu_inventory_or_game_menu,
    )
    for name, value in (
        ("SENZU_SLOT", 1), ("SENZU_STATUS", "idle"), ("SENZU_ACTIVE_TYPE", "full"),
        ("SENZU_REMAINING", 96), ("SENZU_RECOVERY_TIMEOUT_SEC", 7.0),
        ("SENZU_PREFERENCE_MODE", "full_only"), ("SENZU_ROW_CACHE", {}),
        ("TELEMETRY", dict(core.TELEMETRY)),
    ):
        monkeypatch.setattr(core, name, value)

    def click(x, y):
        state.clicks.append((clock.now, x, y))
        state.events.append(("click", x, y))
        clock.sleep(0.055)  # Existing SendInput click pacing, with input mocked.

    def find_preferred(*_args):
        state.events.append(("scan",))
        return 520, state.count, 0.91, "full"

    state.click = Mock(side_effect=click)
    state.preferred = Mock(side_effect=find_preferred)
    state.find = Mock(return_value=(520, 96, 0.91))
    state.read_row = Mock(side_effect=lambda *_a, **_k: (520, state.count, 0.91))
    state.slot = Mock(return_value=(False, 0.1))
    state.close = Mock(return_value=True)
    state.focus = Mock(return_value=True)
    state.tap = Mock(side_effect=lambda _key: clock.sleep(
        core.UTILITY_KEY_HOLD_SEC + core.UTILITY_KEY_GAP_SEC
    ))
    for name, value in (
        ("_focus_game_for_senzu", state.focus),
        ("_park_menu_cursor", Mock()),
        ("_restore_menu_cursor", Mock()),
        ("_wait_for_senzu_inventory_or_game_menu", Mock(return_value=("inventory", 0.1, 0.99))),
        ("_wait_for_senzu_screen", Mock(return_value=((0, 0), 0.99))),
        ("_reference_point", lambda x, y: (x, y)), ("click_at", state.click),
        ("_tap_key_unchecked", state.tap), ("_find_preferred_senzu_row", state.preferred),
        ("_find_senzu_row", state.find), ("_find_senzu_row_in_frame", state.read_row),
        ("_senzu_slot_has_bean", state.slot),
        ("_inventory_quantity_is_red", Mock(return_value=True)),
        ("_close_inventory_to_training", state.close),
        ("_template_in_reference_box", Mock(return_value=(True, 0.99, (0, 0)))),
        ("_grab_reference_box", Mock()),
    ):
        monkeypatch.setattr(core, name, value)
    monkeypatch.setattr(core.cv2, "imwrite", Mock())
    state.run = lambda **kwargs: core._refill_senzu_slot(Mock(), assets, **kwargs)
    return state


def clicks_at(state, x):
    return [entry for entry in state.clicks if entry[1] == x]


@pytest.mark.parametrize("stop_during_scan", [False, True])
def test_assignment_deadline_bounds_real_retry_scan(refill, monkeypatch, stop_during_scan):
    scan_started = []
    frames = [0]
    stopped = [False]

    def grab(*_args):
        refill.clock.sleep(0.06)
        frames[0] += 1
        if stop_during_scan and frames[0] == 3:
            stopped[0] = True
        return np.full((335, 650, 3), frames[0] * 5 % 255, dtype=np.uint8)

    def match(*_args, **_kwargs):
        refill.clock.sleep(0.04)
        return None, None, 0.1

    def scan(*args, deadline):
        scan_started.append((refill.clock.now, deadline))
        return refill.find_helper(*args, deadline=deadline)

    monkeypatch.setattr(core, "_find_senzu_row", scan)
    monkeypatch.setattr(core, "_wait_for_senzu_assignment", lambda *_a, timeout: (
        refill.clock.sleep(timeout) or False, 0.1, 96))
    monkeypatch.setattr(core, "_grab_reference_box", grab)
    monkeypatch.setattr(core, "_find_senzu_row_in_frame", match)
    monkeypatch.setattr(core, "robust_move", lambda *_: refill.clock.sleep(0.015))
    monkeypatch.setattr(core._user32, "SendInput", Mock(return_value=1))
    monkeypatch.setattr(core, "_senzu_abort_requested", lambda: stopped[0])
    assert not refill.run(require_assignment=True)
    assert len(scan_started) == 1
    # One in-flight capture/match can finish at the boundary; no new retry starts.
    assert refill.clock.now <= scan_started[0][1] + 0.2
    assert len(clicks_at(refill, 1720)) == 1
    if stop_during_scan:
        assert refill.clock.now - scan_started[0][0] < 0.4
        refill.close.assert_not_called()


def test_visible_row_skips_items_even_without_cache(refill):
    refill.read_row.side_effect = lambda *_a, **_k: (520, 95, 0.91)
    assert refill.run(require_assignment=True)
    assert not clicks_at(refill, 414)
    assert len(clicks_at(refill, 311)) == len(clicks_at(refill, 1720)) == 1
    assert refill.events[0] == ("scan",)
    assert core.SENZU_REMAINING == 95
    refill.find.assert_not_called()


def test_missing_row_selects_items_only_after_scanning(refill):
    def scan(*_args):
        refill.events.append(("scan",))
        if len(clicks_at(refill, 414)):
            return 520, 96, 0.91, "full"
        return None, None, 0.1, None
    refill.preferred.side_effect = scan
    refill.read_row.side_effect = lambda *_a, **_k: (520, 95, 0.91)
    assert refill.run(require_assignment=True)
    assert refill.events[:3] == [("scan",), ("click", 414, 758), ("scan",)]
    assert len(clicks_at(refill, 414)) == 1


def test_missing_stock_keeps_single_items_retry_and_single_reopen(refill):
    refill.preferred.side_effect = None
    refill.preferred.return_value = (None, None, 0.1, None)
    assert not refill.run(require_assignment=True)
    assert refill.tap.call_args_list == [call("m"), call("m")]
    assert len(clicks_at(refill, 414)) == 4  # Initial click + retry, on each open.
    assert refill.preferred.call_count == 6  # Includes the scan before each tab click.
    assert refill.close.call_count == 2
    assert core.SENZU_STATUS == "empty"
    assert core.SENZU_REMAINING == 0
    assert not clicks_at(refill, 311) and not clicks_at(refill, 1720)


def test_delayed_count_confirms_in_place_without_row_or_slot_reclick(refill):
    def read(*_args, **_kwargs):
        slot_time = clicks_at(refill, 1720)[0][0]
        count = 95 if refill.clock.now - slot_time >= 0.70 else 96
        return 520, count, 0.91
    refill.read_row.side_effect = read
    assert refill.run(require_assignment=True)
    assert len(clicks_at(refill, 311)) == len(clicks_at(refill, 1720)) == 1
    assert refill.read_row.call_count > 3
    assert core.SENZU_REMAINING == 95
    refill.find.assert_not_called()
    assert refill.clock.now - clicks_at(refill, 1720)[0][0] < 1.2


def test_failed_window_reselects_row_and_slot_then_confirms(refill):
    refill.read_row.side_effect = lambda *_a, **_k: (
        520, 95 if len(clicks_at(refill, 1720)) == 2 else 96, 0.91
    )
    assert refill.run(require_assignment=True)
    rows = clicks_at(refill, 311)
    slots = clicks_at(refill, 1720)
    assert len(rows) == len(slots) == 2
    assert rows[1][0] - slots[0][0] >= 1.2
    # A failed retry no longer contains three scrolling scans or fixed .55/.35 waits.
    assert slots[1][0] - slots[0][0] < 1.7
    refill.find.assert_called_once()
    assert core.SENZU_REMAINING == 95


def test_late_count_during_retry_recheck_prevents_reselect(refill):
    refill.find.return_value = (520, 95, 0.91)
    assert refill.run(require_assignment=True)
    assert len(clicks_at(refill, 311)) == len(clicks_at(refill, 1720)) == 1
    refill.find.assert_called_once()


def test_late_slot_during_retry_recheck_never_searches_or_reselects(refill):
    def slot(*_args, **_kwargs):
        slots = clicks_at(refill, 1720)
        return bool(slots and refill.clock.now - slots[0][0] >= 1.24), 0.91
    refill.slot.side_effect = slot
    assert refill.run(require_assignment=True)
    assert len(clicks_at(refill, 311)) == len(clicks_at(refill, 1720)) == 1
    refill.find.assert_not_called()


def test_slot_confirmation_keeps_three_frames_and_no_extra_count_scan(refill):
    refill.read_row.side_effect = lambda *_a, **_k: (520, None, 0.91)
    refill.slot.side_effect = lambda *_a, **_k: (bool(clicks_at(refill, 1720)), 0.91)
    assert refill.run(require_assignment=True)
    assert refill.slot.call_count == 6  # Three ghost-clear frames + three loaded frames.
    assert refill.read_row.call_count == 3
    assert core.SENZU_REMAINING == 95
    refill.find.assert_not_called()


@pytest.mark.parametrize("remaining", [None, 96, 94, 0])
def test_unknown_unchanged_or_wrong_count_drop_cannot_confirm(refill, remaining):
    refill.read_row.side_effect = lambda *_a, **_k: (520, remaining, 0.91)
    assert not refill.run(require_assignment=False)
    assert core.SENZU_STATUS == "error"
    assert len(clicks_at(refill, 1720)) == 1
    assert refill.clock.now - clicks_at(refill, 1720)[0][0] == pytest.approx(0.5)


def test_count_drop_to_zero_confirms_the_last_inventory_bean(refill):
    refill.count = 1
    refill.read_row.side_effect = lambda *_a, **_k: (520, 0, 0.91)
    assert refill.run(require_assignment=True)
    assert core.SENZU_STATUS == "ready"
    assert core.SENZU_REMAINING == 0


def test_transient_missing_row_during_poll_is_not_empty(refill):
    refill.read_row.side_effect = lambda *_a, **_k: (None, None, 0.1)
    assert not refill.run(require_assignment=False)
    assert core.SENZU_STATUS == "error"
    assert core.SENZU_REMAINING == 96


def test_loaded_slot_flicker_is_not_assignment_confirmation(refill):
    refill.slot.side_effect = [(False, 0.1)] * 3 + [(True, 0.9), (False, 0.1)] * 8
    assert not refill.run(require_assignment=False)
    assert core.SENZU_STATUS == "error"


def test_missing_inventory_header_cannot_confirm_even_with_loaded_slot(refill, monkeypatch):
    refill.slot.side_effect = lambda *_a, **_k: (bool(clicks_at(refill, 1720)), 0.91)
    monkeypatch.setattr(core, "_template_in_reference_box", Mock(return_value=(False, 0.1, None)))
    assert not refill.run(require_assignment=False)
    refill.read_row.assert_not_called()
    assert core.SENZU_STATUS == "error"


def test_loaded_ghost_check_still_rejects_unconfirmed_consumption(refill):
    refill.slot.return_value = (True, 0.91)
    assert not refill.run(require_assignment=True)
    assert core.SENZU_STATUS == "not_consumed"
    assert len(clicks_at(refill, 311)) == 1
    assert not clicks_at(refill, 1720)
    refill.read_row.assert_not_called()


def test_focus_failure_prevents_all_refill_input(refill):
    refill.focus.return_value = False
    assert not refill.run(require_assignment=True)
    assert core.SENZU_STATUS == "error"
    refill.tap.assert_not_called()
    refill.click.assert_not_called()


def test_stop_during_confirmation_does_not_reselect_or_close(refill, monkeypatch):
    def aborted():
        slots = clicks_at(refill, 1720)
        return bool(slots and refill.clock.now - slots[0][0] >= 0.25)
    monkeypatch.setattr(core, "_senzu_abort_requested", aborted)
    assert not refill.run(require_assignment=True)
    assert len(clicks_at(refill, 311)) == len(clicks_at(refill, 1720)) == 1
    refill.find.assert_not_called()
    refill.close.assert_not_called()
    assert refill.clock.now - clicks_at(refill, 1720)[0][0] < 0.35


def test_recovery_retry_budget_stays_bounded(refill):
    assert not refill.run(require_assignment=True)
    assert core.SENZU_STATUS == "error"
    assert refill.clock.now - clicks_at(refill, 1720)[0][0] == pytest.approx(7.0)
    assert all(entry[0] < refill.clock.now for entry in refill.clicks)


@pytest.mark.parametrize("route", ["inventory", "game_menu"])
def test_menu_detection_accepts_either_route_without_absent_screen_timeout(clock, monkeypatch, route):
    assets = {name: object() for name in ("inventory", "game_menu")}
    def screen(_sct, template, _box):
        return template is assets[route], 0.99, (0, 0)
    monkeypatch.setattr(core, "_template_in_reference_box", screen)
    assert core._wait_for_senzu_inventory_or_game_menu(Mock(), assets)[0] == route
    assert clock.now == pytest.approx(0.05)


def test_menu_detection_retains_full_combined_timeout(clock, monkeypatch):
    monkeypatch.setattr(core, "_template_in_reference_box", Mock(return_value=(False, 0.1, None)))
    assert core._wait_for_senzu_inventory_or_game_menu(
        Mock(), {name: object() for name in ("inventory", "game_menu")}
    )[0] is None
    assert clock.now == pytest.approx(1.8)


@pytest.mark.parametrize("pattern, timeout, expected, elapsed", [
    ([(True, 0.9)] * 3, 0.46, True, 0.10),
    ([(False, 0.1)] * 3 + [(True, 0.9)] * 3, 0.46, True, 0.25),
    ([(False, 0.1)] * 10, 0.46, False, 0.46),
    ([(True, 0.9), (False, 0.1), (True, 0.9)], None, None, 0.10),
])
def test_h_readiness_polls_without_weakening_stability(clock, monkeypatch, pattern, timeout, expected, elapsed):
    slot = Mock(side_effect=pattern)
    monkeypatch.setattr(core, "_senzu_slot_has_bean", slot)
    assert core._stable_senzu_slot_state(
        Mock(), 1, object(), hotbar=True, timeout=timeout
    )[0] is expected
    assert clock.now == pytest.approx(elapsed)


def test_visible_row_scan_reads_saved_frame_without_pointer_or_scroll(clock, monkeypatch):
    folder = Path(__file__).resolve().parents[1]
    frame = cv2.imread(str(folder / "tests/fixtures/inventory_items_live.png"))
    assets = {
        "bean": cv2.imread(str(folder / "tpl_senzu_bean.png"), cv2.IMREAD_GRAYSCALE),
        "digits": cv2.imread(str(folder / "tpl_inventory_digits.png"), cv2.IMREAD_GRAYSCALE),
        "inventory": object(),
    }
    monkeypatch.setattr(core, "SENZU_ROW_CACHE", {})
    monkeypatch.setattr(core, "_wait_for_senzu_screen", Mock(return_value=((0, 0), 0.99)))
    monkeypatch.setattr(core, "_grab_reference_box", Mock(return_value=frame))
    move = Mock()
    wheel = Mock()
    monkeypatch.setattr(core, "robust_move", move)
    monkeypatch.setattr(core._user32, "SendInput", wheel)
    row_y, count, score = core._find_senzu_row(Mock(), assets, "full")
    assert row_y == 519 and count == 97 and score >= 0.80
    assert core._find_senzu_row_in_frame(frame, assets, "full", row_y=519)[1] == 97
    assert core._find_senzu_row_in_frame(frame, assets, "full", row_y=424)[0] is None
    assert core._find_senzu_row_in_frame(frame, assets, "half", row_y=519)[0] is None
    move.assert_not_called()
    wheel.assert_not_called()
    assert clock.now == 0


@pytest.mark.parametrize("samples", [0, -1])
def test_invalid_stability_sample_budget_is_unknown_without_input(clock, monkeypatch, samples):
    slot = Mock()
    monkeypatch.setattr(core, "_senzu_slot_has_bean", slot)
    assert core._stable_senzu_slot_state(Mock(), 1, object(), samples=samples)[0] is None
    slot.assert_not_called()


def test_eat_refill_close_resume_cycle_with_delayed_count_stays_under_six_seconds(refill, monkeypatch):
    phase = {"screen": "minigame", "consumed": False}
    monkeypatch.setattr(core, "CURRENT_TRAINING_STATE", "Ki Control")
    monkeypatch.setattr(core, "CONTROLLER_PAUSED", False)
    monkeypatch.setattr(core, "_senzu_assets", lambda: refill.assets)
    monkeypatch.setattr(core, "_close_inventory_to_training", refill.close_helper)
    monkeypatch.setattr(core, "_wait_for_senzu_inventory_or_game_menu", refill.menu_helper)
    monkeypatch.setattr(core, "_ensure_training_menu_for_senzu", lambda *_a: True)
    monkeypatch.setattr(core, "_training_menu_visible_for_senzu", lambda *_a: (
        phase["screen"] == "training", 0.99
    ))
    monkeypatch.setattr(core, "_button_screen_point", lambda _category: (420, 360))
    monkeypatch.setattr(core, "_hp_bar_is_green", lambda _sct: True)

    def tap(key):
        if key == "tab":
            phase["screen"] = "training"
        elif key == "h":
            phase["screen"] = "training" if phase["screen"] == "hotbar" else "hotbar"
        elif key == "1":
            phase["consumed"] = True
        elif key == "m":
            phase["screen"] = "training" if phase["screen"] == "inventory" else "inventory"
        refill.clock.sleep(core.UTILITY_KEY_HOLD_SEC + core.UTILITY_KEY_GAP_SEC)

    original_click = refill.click.side_effect
    def click(x, y):
        original_click(x, y)
        if x == 420:
            phase["screen"] = "minigame"
    refill.click.side_effect = click
    refill.tap.side_effect = tap

    def slot(_sct, _slot, _template, *, hotbar=False, **_kwargs):
        if hotbar:
            return not phase["consumed"], 0.91
        return False, 0.1  # Count confirms before the lagging slot label redraw.
    refill.slot.side_effect = slot

    def screen(_sct, template, _box, *_args):
        visible = template is refill.assets["inventory"] and phase["screen"] == "inventory"
        return visible, 0.99 if visible else 0.1, (0, 0)
    monkeypatch.setattr(core, "_template_in_reference_box", screen)
    refill.read_row.side_effect = lambda *_a, **_k: (
        520, 95 if refill.clock.now - clicks_at(refill, 1720)[0][0] >= 0.30 else 96, 0.91
    )

    assert core.eat_senzu(Mock())
    assert phase["screen"] == "minigame"
    assert core.SENZU_STATUS == "ready"
    assert core.SENZU_REMAINING == 95
    assert len(clicks_at(refill, 311)) == len(clicks_at(refill, 1720)) == 1
    assert not clicks_at(refill, 414)
    assert refill.tap.call_args_list == [call(key) for key in ("tab", "h", "1", "h", "m", "m")]
    assert core.TELEMETRY["senzu_eaten"] >= 1
    assert refill.clock.now < 3.0  # Leaves room for capture/matching latency within the 6s goal.
    print(f"offline cycle with 0.30s count lag: {refill.clock.now:.3f}s of simulated pacing")
