"""Slow-PC timing: every screen grab takes about 250 ms and the game redraws late.

A fake clock stands in for time so each test is exact and instant. Each grab
advances the clock by GRAB_SEC and returns the clock value as the "frame", so a
reader can say what the game showed at that moment.
"""
import sys
from unittest.mock import Mock, patch

import numpy as np
import pytest

import xynmacro_core as core

GRAB_SEC = 0.25


class SlowClock:
    def __init__(self):
        self.now = 100.0

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += max(0.0, seconds)

    def grab(self, *_args, **_kwargs):
        self.now += GRAB_SEC
        return self.now


@pytest.fixture
def clock(monkeypatch):
    # Nothing left over from other tests may count as Stop for these waits.
    monkeypatch.setattr(core, "UI_STOP_REQUESTED", False)
    monkeypatch.setattr(core, "_background_monitor_stop", None)
    monkeypatch.setattr(core, "MAJIN_CONTROLLER_ACTIVE", False)
    slow = SlowClock()
    with patch.object(core.time, "monotonic", side_effect=slow.monotonic), \
         patch.object(core.time, "sleep", side_effect=slow.sleep), \
         patch.object(core, "_client_frame", side_effect=slow.grab):
        yield slow


def _eat_with_slot_reader(monkeypatch, clock, slot_has_bean):
    monkeypatch.setattr(core, "UI_STOP_REQUESTED", False)
    menus = core.training_ui.SenzuMenus
    with patch.object(core, "_leave_training_to_gameplay", return_value=True), \
         patch.object(core, "_hotbar_slot_loaded", return_value=True), \
         patch.object(core, "_focus_game_for_senzu", return_value=True), \
         patch.object(core, "_tap_key_unchecked",
                      side_effect=lambda key: setattr(clock, "pressed", clock.now)), \
         patch.object(menus, "hotbar_slot_has_senzu",
                      side_effect=lambda frame, _slot: slot_has_bean(frame - clock.pressed)), \
         patch.object(core, "_close_hotbar"), \
         patch.object(core, "_refill_senzu_slot_v2", return_value=True), \
         patch.object(core, "_wait_for_green_hp", return_value=True), \
         patch.object(core, "_ensure_training_menu_for_senzu", return_value=True), \
         patch.object(core, "_resume_training_after_senzu", return_value=True), \
         patch.object(core, "_request_stuck_senzu_recovery", return_value=False) as stuck:
        result = core._eat_senzu_v2(None)
    return result, stuck


def test_late_slot_redraw_after_eating_is_not_a_stuck_item(monkeypatch, clock):
    # Finding 5: the bean leaves the slot 1 s after the key, seen through 250 ms grabs.
    result, stuck = _eat_with_slot_reader(monkeypatch, clock, lambda since_key: since_key < 1.0)
    stuck.assert_not_called()
    assert result is True


def test_slot_that_keeps_the_bean_still_requests_the_reset(monkeypatch, clock):
    result, stuck = _eat_with_slot_reader(monkeypatch, clock, lambda since_key: True)
    stuck.assert_called_once()
    assert result is False


def test_unreadable_slot_after_eating_does_not_reset_the_character(monkeypatch, clock):
    # Flickering reads prove neither an eat nor a refusal; HP decides, no reset.
    reads = iter([True, False] * 50)
    result, stuck = _eat_with_slot_reader(monkeypatch, clock, lambda _since_key: next(reads))
    stuck.assert_not_called()
    assert result is True


# --- Finding 1: Health at a 250 ms look interval ------------------------------

BAR_END = 460.0
SWEEP = 650.0
ZONE = (180, 279)


def _marker_at(t):
    """The marker sweeps 0..BAR_END and back at SWEEP px/s, bouncing at both ends."""
    travel = (t * SWEEP) % (2 * BAR_END)
    return travel if travel <= BAR_END else 2 * BAR_END - travel


def _fresh_health_memo(monkeypatch):
    for key, value in {"marker": None, "seen_at": 0.0, "velocity": 0.0, "pressed_at": 0.0,
                       "frame_interval": 0.02, "lead": 0.02, "grab": 0.0,
                       "landing": None, "speed": 650.0}.items():
        monkeypatch.setitem(core._HEALTH_V3_STATE, key, value)
    monkeypatch.setattr(core, "HEALTH_HIT_COOLDOWN_SEC", 0.8)


def test_health_still_presses_when_the_bar_is_seen_every_250ms(monkeypatch, clock):
    _fresh_health_memo(monkeypatch)
    presses = []
    # A hit freezes the marker for the rest of the round (1 s here), then it sweeps on.
    frozen = {"until": 0.0, "at": 0.0, "paused": 0.0}

    def position():
        if clock.now < frozen["until"]:
            return frozen["at"]
        return _marker_at(clock.now - frozen["paused"])

    def read_bar(_bar):
        return position(), ZONE

    def tap(_key):
        x = position()
        presses.append(x)
        frozen.update(until=clock.now + 1.0, at=x, paused=frozen["paused"] + 1.0)
        return True

    with patch.object(core, "_grab_reference_box", side_effect=clock.grab), \
         patch.object(core.training_ui, "health_bar", side_effect=read_bar), \
         patch.object(core, "hardware_tap", side_effect=tap):
        for _ in range(80):   # 20 s of looks
            core._logic_health_v3(None, {"left": 0, "top": 0, "width": 1920, "height": 1080})
    # The old 0.2 s velocity gate never pressed at this interval.
    assert len(presses) >= 8
    assert all(ZONE[0] <= x <= ZONE[1] for x in presses), presses


def test_health_does_not_aim_from_two_looks_across_a_bounce(monkeypatch):
    _fresh_health_memo(monkeypatch)
    # Seen at 440 moving right; 250 ms later it hit the end (460) and came back
    # to 318, which reads as 488 px/s leftward: slower than the real sweep.
    monkeypatch.setitem(core._HEALTH_V3_STATE, "marker", 440.0)
    monkeypatch.setitem(core._HEALTH_V3_STATE, "seen_at", 10.0)
    with patch.object(core, "_grab_reference_box"), \
         patch.object(core.training_ui, "health_bar", return_value=(318.0, (250, 349))), \
         patch.object(core.time, "monotonic", return_value=10.25), \
         patch.object(core, "hardware_tap", return_value=True) as tap:
        core._logic_health_v3(None, {"left": 0, "top": 0, "width": 1920, "height": 1080})
    tap.assert_not_called()
    assert core._HEALTH_V3_STATE["velocity"] == 0.0


def test_health_landing_check_waits_three_slow_looks_for_the_freeze(capsys):
    # Pressed at 10.0; at 250 ms looks the freeze is confirmed on the third look (10.7 s).
    memo = {"marker": 200.0, "frame_interval": 0.25, "speed": 650.0, "lead": 0.02, "grab": 0.25,
            "landing": {"zone": ZONE, "pressed_at": 10.0, "direction": 1, "speed": 650.0,
                        "furthest": None, "still_since": None}}
    for now, marker in ((10.2, 232.0), (10.45, 232.0), (10.7, 232.0)):
        core._health_v3_check_landing(memo, marker, now)
        memo["marker"] = marker
    out = capsys.readouterr().out
    assert "Landed" in out and "didn't stop" not in out


# --- Finding 6: the H list must be seen closed before the refill's M ----------

def test_close_hotbar_waits_for_a_slow_close(clock):
    menus = core.training_ui.SenzuMenus
    pressed = []
    with patch.object(core, "_focus_game_for_senzu", return_value=True), \
         patch.object(core, "_tap_key_unchecked", side_effect=lambda key: pressed.append(clock.now)), \
         patch.object(menus, "hotbar_visible",
                      side_effect=lambda frame: not pressed or frame - pressed[0] < 1.0):
        assert core._close_hotbar(None) is True
    assert len(pressed) == 1   # one H; a second would reopen the closing list


def test_eat_does_not_refill_while_the_hotbar_stays_open(monkeypatch, clock):
    monkeypatch.setattr(core, "UI_STOP_REQUESTED", False)
    menus = core.training_ui.SenzuMenus
    with patch.object(core, "_leave_training_to_gameplay", return_value=True), \
         patch.object(core, "_hotbar_slot_loaded", return_value=True), \
         patch.object(core, "_focus_game_for_senzu", return_value=True), \
         patch.object(core, "_tap_key_unchecked"), \
         patch.object(menus, "hotbar_slot_has_senzu", return_value=False), \
         patch.object(menus, "hotbar_visible", return_value=True), \
         patch.object(core, "_refill_senzu_slot_v2", return_value=True) as refill, \
         patch.object(core, "_wait_for_green_hp", return_value=True), \
         patch.object(core, "_record_run_outcome"), \
         patch.object(core, "_death_dialog_confirmed", return_value=False), \
         patch.object(core, "_resume_training_after_senzu") as resume:
        assert core._eat_senzu_v2(None) is False
    refill.assert_not_called()
    resume.assert_not_called()


# --- Finding 11: a slot change mid-heal applies to the next heal --------------

def test_slot_setting_changed_mid_heal_does_not_redirect_it(monkeypatch, clock):
    monkeypatch.setattr(core, "UI_STOP_REQUESTED", False)
    monkeypatch.setattr(core, "SENZU_SLOT", 1)
    menus = core.training_ui.SenzuMenus
    checked = []

    def tap(key):
        if key == "1":
            core.SENZU_SLOT = 2   # the user picks slot 2 while slot 1 is being eaten

    def slot_reader(_frame, slot):
        checked.append(slot)
        return False

    with patch.object(core, "_leave_training_to_gameplay", return_value=True), \
         patch.object(core, "_hotbar_slot_loaded", return_value=True), \
         patch.object(core, "_focus_game_for_senzu", return_value=True), \
         patch.object(core, "_tap_key_unchecked", side_effect=tap), \
         patch.object(menus, "hotbar_slot_has_senzu", side_effect=slot_reader), \
         patch.object(core, "_close_hotbar", return_value=True), \
         patch.object(core, "_refill_senzu_slot_v2", return_value=True) as refill, \
         patch.object(core, "_wait_for_green_hp", return_value=True), \
         patch.object(core, "_ensure_training_menu_for_senzu", return_value=True), \
         patch.object(core, "_resume_training_after_senzu", return_value=True):
        assert core._eat_senzu_v2(None) is True
    assert set(checked) == {1}
    assert refill.call_args.args[1] == 1


# --- Finding 12: a manual skip is kept if healing claims input first ----------

def test_manual_skip_survives_healing_claiming_input_first(monkeypatch, capsys):
    from test_focus_pause_retry import _controller_setup
    _controller_setup(monkeypatch)
    calls = {"health": 0, "healing": False}

    def health(*_args):
        calls["health"] += 1
        if calls["health"] > 1:
            raise core.QuitException()   # the old code lost the skip and came back here
        core.MANUAL_NEXT_REQUESTED = True
        calls["healing"] = True

    def suspended():
        # Healing holds input for the first switch attempt after the skip only.
        held, calls["healing"] = calls["healing"], False
        return held

    monkeypatch.setattr(core, "logic_health", health)
    # The switch's Tab: no real key, and the switch stops there.
    monkeypatch.setattr(core, "hardware_tap", Mock(side_effect=RuntimeError(
        "Training Mode menu was not confirmed after Tab; switch cancelled")))
    monkeypatch.setattr(core, "_tap_key_unchecked", Mock())
    monkeypatch.setattr(core, "_controller_decisions_suspended", suspended)
    for name in ("logic_agility_v4", "logic_agility_v3", "logic_agility_v2", "logic_agility_v1"):
        monkeypatch.setattr(core, name, Mock(side_effect=core.QuitException()))
    # The switch itself then fails on the fake menu; only that it was attempted matters.
    with pytest.raises((core.QuitException, RuntimeError)):
        core.run_master_controller()
    assert capsys.readouterr().out.count("[SKIP] Manual next") == 2
    assert calls["health"] == 1


# --- Finding 8: Stop during a hotkey Start's setup ---------------------------

def test_stop_hotkey_during_a_slow_hotkey_start_cancels_it(monkeypatch):
    monkeypatch.setattr(core, "_hotkey_toggle_busy", True)   # worker inside _ui_start_macro
    monkeypatch.setattr(core, "_hotkey_toggle_last_accepted", 0.0)
    monkeypatch.setattr(core, "_macro_start_generation", 7)
    monkeypatch.setattr(core, "_macro_pending_start_generation", 7)   # its reservation
    stop = Mock()
    monkeypatch.setattr(core, "_ui_stop_macro", stop)
    started = []
    monkeypatch.setattr(core.threading, "Thread",
                        lambda target, kwargs, **_kw: Mock(start=lambda: started.append(target(**kwargs))))
    core._hotkey_toggle_macro()
    stop.assert_called_once_with(expected_generation=7)


def test_busy_toggle_without_a_pending_start_is_still_dropped(monkeypatch):
    monkeypatch.setattr(core, "_hotkey_toggle_busy", True)
    monkeypatch.setattr(core, "_hotkey_toggle_last_accepted", 0.0)
    monkeypatch.setattr(core, "_macro_pending_start_generation", None)
    stop = Mock()
    monkeypatch.setattr(core, "_ui_stop_macro", stop)
    queue = core.queue.SimpleQueue()
    monkeypatch.setattr(core, "_hotkey_toggle_queue", queue)
    core._hotkey_toggle_macro()
    stop.assert_not_called()
    assert queue.empty()


# --- Finding 9: healing on the monitor thread checks for covering windows ----

def test_healing_input_waits_for_a_window_that_appeared_mid_heal(monkeypatch):
    covering = iter(["Popup", "Popup", None, None])
    monkeypatch.setattr(core, "_GAME_COVERED_SINCE", None)
    monkeypatch.setattr(core, "_background_monitor_thread", core.threading.current_thread())
    monkeypatch.setattr(core, "_game_window_covering_title", lambda: next(covering))
    monkeypatch.setattr(core, "_USER_STOP_LATCHED", False)
    monkeypatch.setattr(core, "UI_STOP_REQUESTED", False)
    monkeypatch.setattr(core, "_set_charge_hold", lambda _held: None)
    waits = []
    monkeypatch.setattr(core.time, "sleep", waits.append)
    core._wait_while_game_covered()
    assert waits == [0.1]   # held input until the window went
    assert core._GAME_COVERED_SINCE is None


def test_a_ki_click_is_dropped_instead_of_waiting_with_the_stop_gate(monkeypatch):
    # The monitor marked the game covered just after the Ki click's own check.
    monkeypatch.setattr(core, "_GAME_COVERED_SINCE", 5.0)
    monkeypatch.setattr(core, "_game_window_covering_title", lambda: "a window")
    monkeypatch.setattr(core.time, "sleep", lambda _s: pytest.fail("waited with the Stop gate held"))
    core._cover_wait.drop_instead = True
    try:
        with pytest.raises(core.SkipMinigameException):
            core._wait_while_game_covered()
    finally:
        core._cover_wait.drop_instead = False


def test_training_thread_keeps_relying_on_the_monitor(monkeypatch):
    monkeypatch.setattr(core, "_GAME_COVERED_SINCE", None)
    monkeypatch.setattr(core, "_background_monitor_thread", None)
    look = Mock(return_value="Popup")
    monkeypatch.setattr(core, "_game_window_covering_title", look)
    core._wait_while_game_covered()
    look.assert_not_called()   # minigame input stays as fast as before


# --- Finding 10: no minigame key aimed at a frame from before a refocus -------

@pytest.mark.parametrize("key, state, sent", [
    ("f", "Health", False),        # Health press after a refocus: dropped
    ("d", "Agility", False),       # letter after a refocus: dropped
    ("tab", "Health", True),       # menu keys still go through after refocus
    ("f", None, True),             # outside training (e.g. recovery) unchanged
])
def test_minigame_key_is_dropped_after_restoring_focus(monkeypatch, key, state, sent):
    focused = {"now": False}
    monkeypatch.setattr(core, "CURRENT_TRAINING_STATE", state)
    monkeypatch.setattr(core, "check_exit", lambda: None)
    monkeypatch.setattr(core, "_game_has_focus", lambda: focused["now"])
    monkeypatch.setattr(core, "_wait_while_game_covered", lambda: None)
    monkeypatch.setattr(core, "focus_game_window", lambda: focused.update(now=True) or True)
    tap = Mock()
    monkeypatch.setattr(core, "_tap_key_unchecked", tap)
    assert core.hardware_tap(key) is sent
    assert tap.called is sent
    assert focused["now"]   # focus was restored either way


# --- Finding 4: critical HP first, from the frame the monitor already has ------

def _client_frame_with_hp(fill_px):
    import cv2
    from pathlib import Path
    fixtures = Path(__file__).parent / "fixtures" / "ui_2026_10"
    frame = np.zeros((1080, 1920, 3), np.uint8)
    x, y, w, h = core.training_ui.HUD_LABELS_BOX
    frame[y:y + h, x:x + w] = cv2.imread(str(fixtures / "hud_labels_a.png"))[:h, :w]
    bar = cv2.imread(str(fixtures / "hp_full.png"))
    bar[:, fill_px:] = (30, 30, 30)
    x, y, w, h = core.SENZU_HP_FILL_BOX   # a tuple in the October 2026 layout
    frame[y:y + h, x:x + w] = bar[:h, :w]
    return frame


@pytest.mark.new_game_ui
@pytest.mark.parametrize("fill_px, red", [(100, True), (525, False)])
def test_critical_hp_reads_the_frame_already_grabbed(monkeypatch, fill_px, red):
    grab = Mock(side_effect=AssertionError("no extra grab"))
    monkeypatch.setattr(core, "_grab_reference_box", grab)
    assert core._hp_bar_is_red(None, _client_frame_with_hp(fill_px)) is red


@pytest.mark.new_game_ui
def test_monitor_heals_before_stepping_out_to_charge(monkeypatch):
    from test_progression_stall import _run_monitor
    order = []
    monkeypatch.setitem(core.COMPATIBILITY_SETTINGS, "charge_hold_mode", "second")
    seen_frames = []

    def red(_sct, frame=None):
        seen_frames.append(frame)
        return True

    _run_monitor(
        monkeypatch,
        [(10.0, {}), (10.3, {}), (10.6, {})],
        SENZU_ENABLED=True,
        STOP_CHARGING_AFTER_KI_CONTROL=False,
        GC_RECOVERY_VERIFY_PENDING=False,
        _hp_bar_is_red=red,
        _ki_charge_level=lambda *_: 0.0,
        _ki_charge_drained=lambda *_: True,
        _claim_healing=lambda *_a, **_k: True,
        _mid_stat_recharge=lambda *_: order.append("charge") or True,
        eat_senzu=lambda *_: order.append("eat"),
    )
    assert order[:1] == ["eat"]
    # The HP read used the full frame grabbed for the death check, not new grabs.
    assert seen_frames and all(frame is not None for frame in seen_frames)


# --- Finding 7: the Items list search on a PC that redraws late ---------------

class ItemsList:
    """Items list that scrolls one step per wheel, between offsets 0 and `last`,
    and redraws `redraw` seconds after the wheel. Senzu shows only at `senzu_at`."""

    def __init__(self, clock, *, start, last, senzu_at, redraw):
        self.clock, self.last, self.senzu_at, self.redraw = clock, last, senzu_at, redraw
        self.offset, self.pending = start, []
        self.heading, self.menu, self.clicks = "items", "open", []

    def shown(self):
        due = [target for at, target in self.pending if at <= self.clock.now]
        if due:
            self.offset = due[-1]
            self.pending = [(at, t) for at, t in self.pending if at > self.clock.now]
        return self.offset

    def wheel(self, notches):
        queued = self.pending[-1][1] if self.pending else self.offset
        target = min(self.last, max(0, queued + (1 if notches < 0 else -1)))
        self.pending.append((self.clock.now + self.redraw, target))
        return True

    def frame(self):
        self.clock.now += GRAB_SEC
        frame = np.zeros((1080, 1920, 3), np.uint8)
        x, y, w, h = core.training_ui.SenzuMenus.LIST_BOX
        frame[y:y + h, x:x + w] = 20 * self.shown() + 10
        frame[0, 0, 0] = self.shown()   # lets the fake readers see the offset
        return frame

    def click(self, x, y):
        self.clicks.append((x, y))
        if x == core.training_ui.SenzuMenus.LIST_ROW_X:
            self.heading = "senzu_bean"
        else:
            self.heading = "items"


def _refill_with(monkeypatch, clock, items_list):
    menus = core.training_ui.SenzuMenus
    monkeypatch.setattr(core, "_client_frame", lambda _sct: items_list.frame())
    monkeypatch.setattr(core, "_scroll_side_list", items_list.wheel)
    monkeypatch.setattr(menus, "senzu_row_y",
                        lambda frame: 609 if frame[0, 0, 0] == items_list.senzu_at else None)
    monkeypatch.setattr(menus, "heading", lambda _frame: items_list.heading)
    monkeypatch.setattr(menus, "side_menu_state", lambda _frame: items_list.menu)
    monkeypatch.setattr(core, "_focus_game_for_senzu", lambda: True)
    monkeypatch.setattr(core, "_park_menu_cursor", lambda *a: None)
    monkeypatch.setattr(core, "_reference_point", lambda x, y, *a: (x, y))
    monkeypatch.setattr(core, "click_at", items_list.click)
    monkeypatch.setattr(core, "_tap_key_unchecked",
                        lambda key: key == "m" and setattr(items_list, "menu", "closed"))
    started = clock.now
    result = core._refill_senzu_slot_v2(None, 1)
    return result, clock.now - started


def test_late_list_redraw_still_finds_senzu_below_the_fold(monkeypatch, clock):
    # Redraws 0.6 s after each wheel; Senzu is six steps down from the top.
    items_list = ItemsList(clock, start=0, last=6, senzu_at=6, redraw=0.6)
    result, _took = _refill_with(monkeypatch, clock, items_list)
    assert result is True and core.SENZU_STATUS == "ready"
    assert (core.training_ui.SenzuMenus.LIST_ROW_X, 609) in items_list.clicks


def test_short_list_without_senzu_is_still_empty_stock(monkeypatch, clock):
    items_list = ItemsList(clock, start=0, last=0, senzu_at=None, redraw=0.05)
    result, _took = _refill_with(monkeypatch, clock, items_list)
    assert result is False and core.SENZU_STATUS == "empty"


def test_fast_pc_list_search_is_no_slower(monkeypatch, clock):
    # 15 ms grabs, quick redraw, list already at the top: the old search took
    # 1.2 s of reads plus 12 scrolls x 0.25 s before calling stock empty.
    monkeypatch.setattr(sys.modules[__name__], "GRAB_SEC", 0.015)
    items_list = ItemsList(clock, start=0, last=0, senzu_at=None, redraw=0.05)
    result, took = _refill_with(monkeypatch, clock, items_list)
    assert core.SENZU_STATUS == "empty"
    # Four scrolls that move nothing, each watched for SENZU_LIST_STILL_SEC.
    assert took < 1.2 + 4 * (core.SENZU_LIST_STILL_SEC + 0.15)


def test_quick_pc_with_a_slow_game_redraw_still_finds_senzu(monkeypatch, clock):
    # 15 ms grabs but the list redraws 0.6 s after the wheel. Calling "did not
    # move" at 0.25 s ended the search with beans six steps down, and a false
    # empty stock turns healing off for the run.
    monkeypatch.setattr(sys.modules[__name__], "GRAB_SEC", 0.015)
    items_list = ItemsList(clock, start=0, last=6, senzu_at=6, redraw=0.6)
    result, _took = _refill_with(monkeypatch, clock, items_list)
    assert result is True
    assert core.SENZU_STATUS == "ready"


# --- The Items list (H) can open late or miss the first H ----------------------

def test_hotbar_open_waits_for_a_slow_list(clock):
    menus = core.training_ui.SenzuMenus
    pressed = []
    with patch.object(core, "_focus_game_for_senzu", return_value=True), \
         patch.object(core, "_tap_key_unchecked", side_effect=lambda key: pressed.append(clock.now)), \
         patch.object(menus, "hotbar_visible", side_effect=lambda frame: bool(pressed) and frame - pressed[0] >= 1.0), \
         patch.object(menus, "hotbar_slot_has_senzu", return_value=True):
        assert core._hotbar_slot_loaded(None, 1) is True
    assert len(pressed) == 1


def test_hotbar_open_presses_h_again_when_the_first_is_lost(clock):
    menus = core.training_ui.SenzuMenus
    pressed = []
    with patch.object(core, "_focus_game_for_senzu", return_value=True), \
         patch.object(core, "_tap_key_unchecked", side_effect=lambda key: pressed.append(clock.now)), \
         patch.object(menus, "hotbar_visible", side_effect=lambda frame: len(pressed) >= 2), \
         patch.object(menus, "hotbar_title_score", return_value=0.1), \
         patch.object(menus, "hotbar_slot_has_senzu", return_value=False):
        assert core._hotbar_slot_loaded(None, 1) is False
    assert len(pressed) == 2


def test_hotbar_open_keeps_a_half_seen_list_open(clock):
    # A second H on a list that is open but not recognised closes it again.
    menus = core.training_ui.SenzuMenus
    pressed = []
    with patch.object(core, "_focus_game_for_senzu", return_value=True), \
         patch.object(core, "_tap_key_unchecked", side_effect=lambda key: pressed.append(key)), \
         patch.object(menus, "hotbar_visible", return_value=False), \
         patch.object(menus, "hotbar_title_score", return_value=0.49):
        assert core._hotbar_slot_loaded(None, 1) is None
    assert pressed == ["h"]


def test_hotbar_open_gives_up_after_two_h(clock):
    menus = core.training_ui.SenzuMenus
    pressed = []
    with patch.object(core, "_focus_game_for_senzu", return_value=True), \
         patch.object(core, "_tap_key_unchecked", side_effect=lambda key: pressed.append(key)), \
         patch.object(menus, "hotbar_visible", return_value=False), \
         patch.object(menus, "hotbar_title_score", return_value=0.0):
        assert core._hotbar_slot_loaded(None, 1) is None
    assert pressed == ["h", "h"]
