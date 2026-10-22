"""October 2026 game UI: readers on live crops and the controller paths using them.

Fixtures are crops of the reference boxes captured live on 2026-10-11 with the
Roblox client at 1920x1080 (no HUD or names).
"""
import json
from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np
import pytest

import training_ui as ui
import xynmacro_core as core

FIXTURES = Path(__file__).parent / "fixtures" / "ui_2026_10"


def fixture(name):
    image = cv2.imread(str(FIXTURES / f"{name}.png"))
    assert image is not None, name
    return image


def complete_label(name):
    """Turn a live "0/20" label into "20/20" by copying the denominator image."""
    label = fixture(name)
    _grey, (a, b), (c, d) = ui.progression_spans(label)
    grow = (d - c) - (b - a)
    made = label.copy()
    made[:, :a - grow] = label[:, grow:a]
    made[:, a - grow:b] = label[:, c:d]
    return made


def ki_frame(name, left, top):
    frame = np.zeros((1080, 1920, 3), np.uint8)
    frame[:] = (0, 0, 200)  # GC floor red
    piece = fixture(name)
    frame[top:top + piece.shape[0], left:left + piece.shape[1]] = piece
    return frame


# --- readers -------------------------------------------------------------------

def test_menu_heading_only_matches_the_open_training_menu():
    template = ui.load_menu_heading()

    def score(name):
        gray = cv2.cvtColor(fixture(name), cv2.COLOR_BGR2GRAY)
        return cv2.minMaxLoc(cv2.matchTemplate(gray, template, cv2.TM_CCOEFF_NORMED))[1]

    assert score("menu_heading_open") >= ui.MENU_HEADING_MIN
    assert score("menu_heading_closed_gc") < 0.5
    assert score("menu_heading_closed_health") < 0.5


@pytest.mark.parametrize("value", range(0, 101, 10))
def test_every_gravity_label_reads_exactly(value):
    read, best, second, present = ui.read_gravity(fixture(f"gravity_{value}"), ui.load_gravity_templates())
    assert (read, present) == (value, True)
    assert best - second >= ui.GRAVITY_MATCH_MARGIN_MIN


def test_gravity_under_the_cursor_is_present_but_never_guessed():
    read, _best, _second, present = ui.read_gravity(
        fixture("gravity_hovered_cursor_10"), ui.load_gravity_templates())
    assert read is None and present


def test_gc_wall_behind_a_closed_menu_is_not_a_gravity_control():
    assert ui.read_gravity(fixture("gravity_menu_closed"), ui.load_gravity_templates())[3] is False


def test_health_bar_reads_marker_and_gold_zone():
    marker, zone = ui.health_bar(fixture("health_bar"))
    assert marker == pytest.approx(353, abs=2)
    assert zone == (180, 279)
    assert ui.health_bar(fixture("health_bar_failed")) is None


@pytest.mark.parametrize("marker, velocity, expected", [
    (230, 0, True),        # centred
    (185, 0, False),       # inside but within the edge margin
    (175, 650, True),      # still left of the zone, arrives within the input delay
    (260, 650, False),     # leaves the safe middle before the key lands
    (280, -650, True),     # sweeping back left into the zone
])
def test_health_press_projects_the_marker_over_the_input_delay(marker, velocity, expected):
    assert ui.health_should_press(marker, (180, 279), velocity, lead_s=0.03) is expected


@pytest.mark.parametrize("marker, velocity, expected", [
    (195, 650, 0.0331),           # just inside the entry edge (the old rule fired here): wait
    (150, 650, None),             # centre is several frames away
    (205, 650, 0.0177),           # next frame would overshoot the centre: wait the rest
    (240, 650, 0.0),              # a slow frame left it past the centre, still inside
    (275, 650, None),             # past the centre and about to leave
    (265, -650, 0.0346),          # sweeping back left, centre within 1.5 frames
    (230, 0, None),               # frozen marker after a hit
])
def test_health_waits_for_the_zone_centre(marker, velocity, expected):
    delay = ui.health_press_delay(marker, (180, 279), velocity, lead_s=0.02, frame_interval_s=0.03)
    if expected is None:
        assert delay is None
    else:
        assert delay == pytest.approx(expected, abs=0.001)


@pytest.mark.parametrize("key", "wasd")
def test_each_agility_letter_is_recognised(key):
    assert ui.agility_letter(fixture(f"agility_{key}"), ui.load_agility_templates())[0] == key


def test_failed_text_is_never_a_letter():
    assert ui.agility_letter(fixture("agility_failed"), ui.load_agility_templates())[0] is None


def test_round_timer_is_seen_only_while_a_round_runs():
    assert ui.round_timer_fill(fixture("timer_running")) > 250
    assert ui.round_timer_fill(fixture("timer_idle")) == 0


@pytest.mark.parametrize("name", ["progression_standard_0of20", "progression_ki_0of20"])
def test_zero_of_twenty_is_incomplete_and_twenty_of_twenty_is_complete(name):
    assert ui.progression_complete(ui.progression_numbers(fixture(name))) is False
    assert ui.progression_complete(ui.progression_numbers(complete_label(name))) is True


def test_missing_label_is_unknown_not_incomplete():
    numbers = ui.progression_numbers(fixture("progression_closed"))
    assert numbers is None and ui.progression_complete(numbers) is None


def test_signature_keeps_the_legacy_shape_and_sees_a_count_change():
    before = ui.progression_signature(ui.progression_numbers(fixture("progression_standard_0of20")))
    after = ui.progression_signature(ui.progression_numbers(complete_label("progression_standard_0of20")))
    assert len(before) == 2 and before[0][0].shape == (32, 32)
    assert core._progression_count_changed(before, after)
    assert not core._progression_count_changed(before, before)


@pytest.mark.parametrize("name, ring", [("ki_circle_2_at_479_486", 109), ("ki_circle_4_at_479_486", 73)])
def test_ki_circle_and_its_closing_ring_are_found(name, ring):
    frame = ki_frame(name, 479, 486)
    (x, y, radius), = ui.find_ki_circles(frame)
    assert (x, y) == pytest.approx((629, 637), abs=2)
    assert radius == pytest.approx(55, abs=3)
    assert ui.ki_ring_radius(frame, x, y, radius) == pytest.approx(ring, abs=4)


@pytest.mark.parametrize("point", [(100, 30), (200, 290), (20, 600), (960, 1000)])
def test_ki_never_clicks_buttons_banner_menu_tab_or_score_panel(point):
    assert ui.ki_click_blocked(*point)


def test_ki_circles_seen_in_live_play_are_clickable():
    for point in ((629, 637), (566, 575), (1234, 545), (849, 338), (1058, 740)):
        assert not ui.ki_click_blocked(*point)


# --- controller ----------------------------------------------------------------

def test_layout_switch_swaps_the_menu_coordinates():
    new = core._layout_constants(core.GAME_UI_LAYOUT if core._new_game_ui() else "2026-10")
    legacy = core._layout_constants(core.LEGACY_UI_LAYOUT)
    assert new["BUTTONS"]["Agility"] == (346, 716) and new["BUTTONS"]["Ki Control"] == (346, 508)
    assert legacy["BUTTONS"]["Agility"] == (329, 504)
    assert new["GRAVITY_CLICK_POINT"] == (532, 336)
    # Copies, so a calibration can't edit the shipped defaults.
    new["BUTTONS"]["Health"] = (0, 0)
    assert core._layout_constants("2026-10")["BUTTONS"]["Health"] == (346, 439)


@pytest.mark.new_game_ui
def test_training_menu_detection_uses_the_new_heading():
    template = core._training_menu_template()
    with patch.object(core, "_grab_reference_box", return_value=fixture("menu_heading_open")):
        assert core.detect_training_menu(None, None, template)[0]
    with patch.object(core, "_grab_reference_box", return_value=fixture("menu_heading_closed_gc")):
        assert not core.detect_training_menu(None, None, template)[0]


@pytest.mark.new_game_ui
def test_core_gravity_read_marks_a_gc_run(monkeypatch):
    monkeypatch.setattr(core, "GC_RUN_CONFIRMED", False)
    with patch.object(core, "_grab_reference_box", return_value=fixture("gravity_40")):
        value, _best, _second, present = core._read_gc_gravity(None, None, core._load_gravity_templates())
    assert (value, present, core.GC_RUN_CONFIRMED) == (40, True, True)


@pytest.mark.new_game_ui
def test_progression_state_reports_the_displayed_target():
    with patch.object(core, "_grab_reference_box", return_value=complete_label("progression_ki_0of20")):
        completion, signature, reason = core.read_progression_state(None, None, "Ki Control")
    assert (completion, reason) == (True, "displayed_target") and signature is not None
    with patch.object(core, "_grab_reference_box", return_value=fixture("progression_standard_0of20")):
        assert core.read_progression_state(None, None, "Health")[::2] == (False, None)


@pytest.mark.new_game_ui
def test_minigame_panel_is_the_training_active_signal():
    with patch.object(core, "_grab_reference_box", return_value=fixture("progression_standard_0of20")):
        assert core.detect_yellow_progress(None, "Agility")[0] == 1
    with patch.object(core, "_grab_reference_box", return_value=fixture("progression_closed")):
        assert core.detect_yellow_progress(None, "Agility")[0] == 0


@pytest.mark.new_game_ui
def test_points_left_uses_two_new_panel_captures():
    panel = fixture("panel_d2_02")
    with patch.object(core, "_grab_reference_box", return_value=panel) as grab, \
         patch.object(core.time, "sleep"):
        panels = core._capture_points_panels(None)
    assert len(panels) == 2 and all(p is panel for p in panels)
    assert [c.args for c in grab.call_args_list] == [(None, ui.TRAINING_POINTS_BOX)] * 2


@pytest.mark.new_game_ui
def test_health_taps_f_once_while_the_moving_marker_is_in_the_zone(monkeypatch):
    monkeypatch.setitem(core._HEALTH_V3_STATE, "marker", None)
    monkeypatch.setitem(core._HEALTH_V3_STATE, "pressed_at", 0.0)
    readings = iter([(150.0, (180, 279)), (175.0, (180, 279)), (225.0, (180, 279)), (226.0, (180, 279))])
    clock = iter([100.0, 100.04, 100.08, 100.12])
    with patch.object(core, "_grab_reference_box"), \
         patch.object(core.training_ui, "health_bar", side_effect=lambda _bar: next(readings)), \
         patch.object(core.time, "monotonic", side_effect=lambda: next(clock)), \
         patch.object(core, "hardware_tap", return_value=True) as tap:
        for _ in range(4):
            core._logic_health_v3(None, {"left": 0, "top": 0, "width": 1920, "height": 1080})
    # Third reading is centred and moving; the fourth is the frozen marker after a hit.
    assert tap.call_args_list == [(("f",),)]


@pytest.mark.new_game_ui
def test_agility_presses_each_round_once(monkeypatch):
    monkeypatch.setattr(core, "_AGILITY_V4_TEMPLATES", {"w": None})
    monkeypatch.setattr(core, "_AGILITY_V4_STATE", {"key": None, "cleared": True, "pressed_at": 0.0})
    letters = iter(["w", "w", None, "w", "a", "a"])
    clock = iter([10.0, 10.05, 10.1, 10.15, 10.2, 10.25])
    with patch.object(core, "_grab_reference_box"), \
         patch.object(core.training_ui, "agility_letter_at", side_effect=lambda *_: (next(letters), 0.9, 100.0)), \
         patch.object(core.time, "monotonic", side_effect=lambda: next(clock)), \
         patch.object(core, "hardware_tap", return_value=True) as tap:
        for _ in range(6):
            core.logic_agility_v4(None)
    # W; the same W still on screen; a blank; the next round's W; A; A still on screen.
    assert [call.args[0] for call in tap.call_args_list] == ["w", "w", "a"]


@pytest.mark.new_game_ui
@pytest.mark.parametrize("name, clicks", [("ki_circle_2_at_479_486", 0), ("ki_circle_4_at_479_486", 0)])
def test_ki_waits_until_the_ring_closes(monkeypatch, name, clicks):
    monkeypatch.setattr(core, "_KI_V9_STATE", {"clicked": None})
    geometry = {"left": 0, "top": 0, "width": 1920, "height": 1080}
    with patch.object(core, "_grab_reference_box", return_value=ki_frame(name, 479, 486)), \
         patch.object(core, "_ki_click") as click:
        core.logic_ki_v9(None, geometry)
    assert click.call_count == clicks


@pytest.mark.new_game_ui
def test_ki_clicks_a_circle_at_its_deadline_and_only_once(monkeypatch):
    monkeypatch.setattr(core, "_KI_V9_STATE", {})
    monkeypatch.setattr(core, "_KI_V9_SCHEDULER", core._KiDeadlineScheduler())
    monkeypatch.setattr(core, "_ki_v9_context_valid", lambda _: True)
    monkeypatch.setattr(core, "_game_has_focus", lambda: True)
    monkeypatch.setattr(core, "check_exit", lambda: None)
    monkeypatch.setattr(core, "_start_ki_v9_worker", lambda: None)
    monkeypatch.setattr(core.training_ui, "progression_spans", lambda _label: object())
    monkeypatch.setattr(core, "GAME_OFFSET_X", 0)
    monkeypatch.setattr(core, "GAME_OFFSET_Y", 0)
    monkeypatch.setattr(core, "GAME_WIDTH", 1920)
    monkeypatch.setattr(core, "GAME_HEIGHT", 1080)
    clock = [100.0]
    monkeypatch.setattr(core.time, "perf_counter", lambda: clock[0])
    geometry = {"left": 0, "top": 0, "width": 1920, "height": 1080}
    frame = ki_frame("ki_circle_4_at_479_486", 800, 486)
    grab_box = lambda _sct, box, geometry=None: frame[box[1]:box[1] + box[3], box[0]:box[0] + box[2]]
    with patch.object(core, "_grab_reference_box", side_effect=grab_box), \
         patch.object(core.training_ui, "ki_ring_radius", return_value=60.0), \
         patch.object(core, "_ki_click") as click:
        core.logic_ki_v9(None, geometry)
        core.logic_ki_v9(None, geometry)
        click.assert_not_called()
        clock[0] = core._KI_V9_SCHEDULER.next_deadline() + .001
        core._ki_v9_dispatch_due()
        core._ki_v9_dispatch_due()
    assert click.call_count == 1
    assert click.call_args.args == pytest.approx((950, 637), abs=2)


@pytest.mark.new_game_ui
def test_old_menu_calibration_is_ignored_and_new_one_applies(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "JSON_DIR", str(tmp_path))
    monkeypatch.setattr(core, "USER_BUTTON_OVERRIDES", {})
    calibration = tmp_path / "button_calibration.json"
    calibration.write_text(json.dumps({"_coordinate_space": "reference-1920x1080",
                                       "Agility": [325, 489]}), encoding="utf-8")
    core.load_button_overrides()
    assert core.BUTTONS["Agility"] == (346, 716)

    core.USER_BUTTON_OVERRIDES = {"Agility": [350, 720]}
    core.save_button_overrides()
    assert json.loads(calibration.read_text(encoding="utf-8"))["_ui_layout"] == "2026-10"
    monkeypatch.setattr(core, "USER_BUTTON_OVERRIDES", {})
    core.load_button_overrides()
    assert core.BUTTONS["Agility"] == (350, 720)


def test_shipped_default_calibration_is_for_the_new_menu():
    shipped = json.loads((Path(core.BASE_DIR) / "defaults" / "button_calibration.json").read_text(encoding="utf-8"))
    assert shipped["_ui_layout"] == "2026-10"
    assert {name: tuple(shipped[name]) for name in ui.STAT_BUTTONS} == ui.STAT_BUTTONS


def test_hp_is_read_as_the_length_of_the_bar():
    full = fixture("hp_full")
    assert ui.hp_fill_fraction(full) > 0.95
    assert ui.hp_fill_fraction(fixture("hp_hidden_ki")) is None
    low = full.copy()
    low[:, 130:] = (30, 30, 30)  # dark, as the empty part of a shrunken bar
    assert ui.hp_fill_fraction(low) == pytest.approx(0.25, abs=0.02)


@pytest.mark.new_game_ui
def test_hp_helpers_use_the_bar_length_only_while_the_hud_shows(monkeypatch):
    full = fixture("hp_full")
    low = full.copy()
    low[:, 100:] = (30, 30, 30)

    def screen(bar, labels):
        return lambda _sct, box, *a, **k: labels if box == ui.HUD_LABELS_BOX else bar

    shown, hidden = fixture("hud_labels_a"), fixture("hud_labels_hidden")
    with patch.object(core, "_grab_reference_box", side_effect=screen(low, shown)):
        assert core._hp_bar_is_red(None) and not core._hp_bar_is_green(None)
        assert not core._hp_above_red(None)
    with patch.object(core, "_grab_reference_box", side_effect=screen(full, shown)):
        assert core._hp_bar_is_green(None) and not core._hp_bar_is_red(None)
        assert core._hp_above_red(None)
    # Live bug: with the HUD hidden the red GC floor read as a full bar.
    with patch.object(core, "_grab_reference_box", side_effect=screen(full, hidden)):
        assert not core._hp_bar_is_red(None) and not core._hp_bar_is_green(None)
        assert not core._hp_above_red(None)


@pytest.mark.parametrize("name, shown", [("hud_labels_a", True), ("hud_labels_b", True),
                                         # live: a dark portrait behind the labels hid HP checks
                                         ("hud_labels_dark_portrait", True),
                                         ("hud_labels_hidden", False)])
def test_hud_labels_tell_whether_hp_is_drawn(name, shown):
    assert ui.hud_visible(fixture(name)) is shown


@pytest.mark.parametrize("name, expected", [("open_prompt_red", True), ("open_prompt_grey", True),
                                            ("stop_prompt_red", False)])
def test_open_training_prompt_reads_on_any_background(name, expected):
    assert ui.open_training_prompt(fixture(name)) is expected


def test_gc_banner_marks_the_room_from_any_camera_angle():
    template = ui.load_gc_banner()
    assert ui.gc_banner_visible(fixture("banner_gc"), template)
    assert not ui.gc_banner_visible(fixture("banner_north_earth"), template)


def test_majin_transform_panel_finds_super_regeneration():
    majin = ui.MajinTransform
    held, closed = fixture("majin_panel_held"), fixture("majin_panel_closed")
    assert majin.panel_visible(held) and not majin.panel_visible(closed)
    assert majin.find_regen_slot(held) == 3 and majin.find_regen_slot(closed) is None
    assert [majin.slot_state(held, slot) for slot in range(1, 9)] == [None, None, "ready"] + [None] * 5


def paste(pieces):
    frame = np.zeros((1080, 1920, 3), np.uint8)
    frame[:] = (0, 0, 200)
    for name, (x, y, _w, _h) in pieces:
        piece = fixture(name)
        frame[y:y + piece.shape[0], x:x + piece.shape[1]] = piece
    return frame


def test_side_menu_pages_and_senzu_row():
    menus = ui.SenzuMenus
    assert menus.side_menu_state(paste([("side_tab_open", menus.SIDE_TAB_OPEN_BOX)])) == "open"
    assert menus.side_menu_state(paste([("side_tab_closed", menus.SIDE_TAB_CLOSED_BOX)])) == "closed"
    assert menus.side_menu_state(paste([])) is None
    for name in menus.HEADINGS:
        assert menus.heading(paste([(f"heading_{name}", menus.HEADING_BOX)])) == name
    assert menus.senzu_row_y(paste([("items_list_senzu", menus.LIST_BOX)])) == pytest.approx(671, abs=3)
    assert menus.senzu_row_y(paste([("items_list_no_senzu", menus.LIST_BOX)])) is None
    assert [menus.slot_row_y(slot) for slot in (1, 4)] == [485, 671]


def test_items_hotbar_shows_the_loaded_slot():
    menus = ui.SenzuMenus
    loaded = paste([("hotbar_senzu_slot1", menus.HOTBAR_BOX)])
    empty = paste([("hotbar_empty", menus.HOTBAR_BOX)])
    assert menus.hotbar_visible(loaded) and menus.hotbar_visible(empty)
    assert menus.hotbar_slot_has_senzu(loaded, 1)
    assert not menus.hotbar_slot_has_senzu(loaded, 2) and not menus.hotbar_slot_has_senzu(empty, 1)
    assert not menus.hotbar_visible(paste([]))


@pytest.mark.parametrize("name", ["dead_a", "dead_b"])
def test_death_dialog_and_respawn_point(name):
    dead = paste([(name, (680, 460, 560, 165))])
    assert ui.death_dialog_visible(dead)
    assert not ui.death_dialog_visible(paste([]))  # the red GC floor alone
    band_only = dead.copy()
    band_only[532:618, 680:1240] = (0, 0, 200)     # title without the buttons
    assert not ui.death_dialog_visible(band_only)
    assert ui.RESPAWN_POINT[1] < 584 and ui.RESPAWN_POINT[1] >= 532
    # The legacy point (960, 610) would land on GO TO OTHER WORLD.
    assert core._layout_constants("2026-10")["GC_RESPAWN_POINT"] == ui.RESPAWN_POINT


@pytest.mark.new_game_ui
def test_core_majin_uses_the_new_panel_and_prompt():
    assert core._majin_panel_vision() is ui.MajinTransform
    with patch.object(core, "_grab_reference_box", return_value=fixture("open_prompt_red")):
        assert core._majin_enter_prompt(None)
    with patch.object(core, "_grab_reference_box", return_value=fixture("stop_prompt_red")):
        assert not core._majin_enter_prompt(None)


@pytest.mark.new_game_ui
def test_eat_v2_refuses_to_eat_when_the_slot_never_loads(monkeypatch):
    monkeypatch.setattr(core, "UI_STOP_REQUESTED", False)
    with patch.object(core, "_leave_training_to_gameplay", return_value=True), \
         patch.object(core, "_hotbar_slot_loaded", return_value=False), \
         patch.object(core, "_close_hotbar"), \
         patch.object(core, "_refill_senzu_slot_v2", return_value=True), \
         patch.object(core, "_record_run_outcome"), \
         patch.object(core, "_tap_key_unchecked") as tap:
        assert core._eat_senzu_v2(None) is False
    assert not any(call.args[0] == str(core.SENZU_SLOT) for call in tap.call_args_list)
    assert core.UI_STOP_REQUESTED


@pytest.mark.new_game_ui
def test_eat_v2_stuck_slot_requests_the_reset_recovery(monkeypatch):
    monkeypatch.setattr(core, "UI_STOP_REQUESTED", False)
    with patch.object(core, "_leave_training_to_gameplay", return_value=True), \
         patch.object(core, "_hotbar_slot_loaded", return_value=True), \
         patch.object(core, "_focus_game_for_senzu", return_value=True), \
         patch.object(core, "_tap_key_unchecked"), \
         patch.object(core, "_wait_for_frame", return_value=False), \
         patch.object(core, "_request_stuck_senzu_recovery", return_value=False) as stuck:
        assert core._eat_senzu_v2(None) is False
    stuck.assert_called_once()


@pytest.mark.new_game_ui
def test_debug_regions_accept_the_new_tuple_boxes():
    # A tuple box made /state fail with a 500 on the first live start.
    regions = core._scan_region_payload()
    assert {region["name"] for region in regions} >= {"Health", "Gravity", "HP"}
    assert all(isinstance(region["left"], int) for region in regions)


@pytest.mark.new_game_ui
def test_cover_guard_watches_the_new_minigame_readers(monkeypatch):
    # Review finding: a window over the new Health bar/letter went unnoticed.
    for name, value in dict(GAME_HWND=42, GAME_OFFSET_X=0, GAME_OFFSET_Y=0,
                            GAME_WIDTH=1920, GAME_HEIGHT=1080).items():
        monkeypatch.setattr(core, name, value)
    monkeypatch.setattr(core, "_windows_above_game", lambda: [((900, 870, 1020, 930), "PiP")])
    assert core._game_window_covering_title() == "PiP"


@pytest.mark.new_game_ui
def test_health_frozen_marker_after_a_slow_frame_is_not_pressed_again(monkeypatch):
    monkeypatch.setitem(core._HEALTH_V3_STATE, "marker", None)
    monkeypatch.setitem(core._HEALTH_V3_STATE, "pressed_at", 0.0)
    monkeypatch.setattr(core, "HEALTH_HIT_COOLDOWN_SEC", 0.2)
    readings = iter([(180.0, (180, 279)), (230.0, (180, 279))] + [(230.0, (180, 279))] * 4)
    clock = iter([10.0, 10.08, 10.33, 10.58, 10.83, 11.08])
    with patch.object(core, "_grab_reference_box"), \
         patch.object(core.training_ui, "health_bar", side_effect=lambda _bar: next(readings)), \
         patch.object(core.time, "monotonic", side_effect=lambda: next(clock)), \
         patch.object(core, "hardware_tap", return_value=True) as tap:
        for _ in range(6):
            core._logic_health_v3(None, {"left": 0, "top": 0, "width": 1920, "height": 1080})
    assert tap.call_count == 1


@pytest.mark.new_game_ui
@pytest.mark.parametrize("fraction, healed", [(0.47, True), (0.62, False), (None, False)])
def test_ki_hp_check_heals_only_when_low_and_always_resumes(monkeypatch, fraction, healed):
    monkeypatch.setattr(core, "HEALING_ACTION", "majin_regen")
    with patch.object(core, "_leave_training_to_gameplay", return_value=True), \
         patch.object(core, "_new_ui_hp_fraction", return_value=fraction), \
         patch.object(core, "use_majin_regen") as regen, \
         patch.object(core, "_grab_reference_box"), \
         patch.object(core.training_ui, "open_training_prompt", return_value=not healed), \
         patch.object(core, "_ensure_training_menu_for_senzu", return_value=True), \
         patch.object(core, "_resume_training_after_senzu", return_value=True) as resume, \
         patch.object(core, "_menu_cursor_guard"):
        assert core._ki_hp_check(None) is healed
    assert regen.called is healed
    # Healing resumes by itself; otherwise the check resumes the stat.
    assert resume.called is (not healed)


@pytest.mark.new_game_ui
def test_respawn_walk_backs_off_the_pad_once_the_banner_is_steady(monkeypatch):
    monkeypatch.setattr(core, "AUTO_RETRY_WALK_SECONDS", 1.5)
    monkeypatch.setattr(core, "GC_RECOVERY_VERIFY_PENDING", False)
    banner = iter([False] * 3 + [True] * 20)
    with patch.object(core.mss, "MSS"), \
         patch.object(core, "_gc_recovery_ready", return_value=True), \
         patch.object(core, "_confirmed_game_capture_rect"), \
         patch.object(core, "_grab_reference_box"), \
         patch.object(core, "_gc_death_dialog_visible", return_value=False), \
         patch.object(core.training_ui, "open_training_prompt", side_effect=lambda _line: next(banner)), \
         patch.object(core, "_auto_retry_wait", return_value=True), \
         patch.object(core, "_gc_recovery_hold", side_effect=lambda d, key: d) as hold:
        assert core._walk_off_respawn_pad()
    assert {call.kwargs["key"] for call in hold.call_args_list} == {"s"}
    assert sum(call.args[0] for call in hold.call_args_list) == pytest.approx(1.5)
    assert core.GC_RECOVERY_VERIFY_PENDING


@pytest.mark.new_game_ui
def test_respawn_walk_sends_nothing_without_the_banner(monkeypatch):
    with patch.object(core.mss, "MSS"), \
         patch.object(core, "_gc_recovery_ready", return_value=True), \
         patch.object(core, "_confirmed_game_capture_rect"), \
         patch.object(core, "_grab_reference_box"), \
         patch.object(core, "_gc_death_dialog_visible", return_value=False), \
         patch.object(core.training_ui, "open_training_prompt", return_value=False), \
         patch.object(core, "_auto_retry_wait", return_value=True), \
         patch.object(core, "_gc_recovery_hold") as hold:
        assert core._walk_off_respawn_pad() is False
    hold.assert_not_called()


# --- Review 2026-10-13 regressions ---------------------------------------

@pytest.mark.new_game_ui
@pytest.mark.parametrize("before, after, recovered", [(0.47, 0.47, False), (0.47, 0.55, True),
                                                     (0.20, 0.95, True), (0.47, None, False)])
def test_majin_new_ui_needs_hp_to_rise_after_the_full_channel(monkeypatch, before, after, recovered):
    readings = iter([before, after])
    waits = []
    monkeypatch.setattr(core, "MAJIN_REGEN_AUTO", True)
    monkeypatch.setattr(core, "MAJIN_REGEN_READY_AT", 0.0)
    vision = core.training_ui.MajinTransform
    with patch.object(core, "_majin_enter_prompt", return_value=True), \
         patch.object(core, "_hold_majin_panel"), \
         patch.object(core, "_grab_reference_box"), \
         patch.object(vision, "panel_visible", return_value=True), \
         patch.object(vision, "find_regen_slot", return_value=3), \
         patch.object(vision, "slot_state", return_value="ready"), \
         patch.object(core, "_majin_wait_for", side_effect=lambda fn, want, timeout=1.0: waits.append(timeout) or fn() == want), \
         patch.object(core, "_majin_tap"), \
         patch.object(core, "_majin_check"), \
         patch.object(core, "_new_ui_hp_fraction", side_effect=lambda _sct: next(readings)):
        outcome = core._attempt_majin_regen(None)
    assert outcome == ("success" if recovered else "failed")
    assert 5.3 in waits  # the channel is always waited out


@pytest.mark.new_game_ui
def test_senzu_v2_stops_when_hp_never_heals(monkeypatch):
    monkeypatch.setattr(core, "UI_STOP_REQUESTED", False)
    with patch.object(core, "_leave_training_to_gameplay", return_value=True), \
         patch.object(core, "_hotbar_slot_loaded", return_value=True), \
         patch.object(core, "_focus_game_for_senzu", return_value=True), \
         patch.object(core, "_tap_key_unchecked"), \
         patch.object(core, "_wait_for_frame", return_value=True), \
         patch.object(core, "_close_hotbar"), \
         patch.object(core, "_refill_senzu_slot_v2", return_value=True), \
         patch.object(core, "_wait_for_green_hp", return_value=False), \
         patch.object(core, "_record_run_outcome"), \
         patch.object(core, "_resume_training_after_senzu") as resume:
        assert core._eat_senzu_v2(None) is False
    resume.assert_not_called()
    assert core.UI_STOP_REQUESTED


@pytest.mark.new_game_ui
def test_senzu_v2_never_eats_when_full_beans_are_excluded(monkeypatch):
    monkeypatch.setattr(core, "SENZU_PREFERENCE_MODE", "half_only")
    monkeypatch.setattr(core, "SENZU_DISABLED_FOR_RUN", False)
    with patch.object(core, "_tap_key_unchecked") as tap, \
         patch.object(core, "_leave_training_to_gameplay") as leave:
        assert core._eat_senzu_v2(None) is True
    tap.assert_not_called(); leave.assert_not_called()
    assert core.SENZU_DISABLED_FOR_RUN


@pytest.mark.new_game_ui
def test_senzu_v2_empty_stock_reopens_training_before_the_shared_resume():
    order = []
    with patch.object(core, "_ensure_training_menu_for_senzu", side_effect=lambda *a: order.append("menu") or True), \
         patch.object(core, "_resume_after_senzu_stock_empty", side_effect=lambda *a, **k: order.append("resume") or True):
        assert core._resume_after_empty_v2(None, {})
    assert order == ["menu", "resume"]


@pytest.mark.new_game_ui
def test_gravity_read_retries_while_the_menu_animates(monkeypatch):
    monkeypatch.setattr(core, "GC_RUN_CONFIRMED", False)  # the read sets it
    frames = iter([fixture("gravity_hovered_cursor_10"), fixture("gravity_10")])
    with patch.object(core, "_grab_reference_box", side_effect=lambda *a, **k: next(frames)), \
         patch.object(core.time, "sleep"):
        value, _best, _second, present = core._read_gc_gravity(None, None, core._load_gravity_templates())
    assert (value, present) == (10, True)


def test_panels_read_in_both_text_sizes():
    # Live 2026-10-14: the same panels came up with smaller titles and labels.
    majin = ui.MajinTransform
    small = fixture("majin_panel_held_small")
    assert majin.panel_visible(small) and majin.find_regen_slot(small) == 3
    assert majin.slot_state(small, 3) == "ready"
    hotbar = paste([("hotbar_small_senzu_slot1", ui.SenzuMenus.HOTBAR_BOX)])
    assert ui.SenzuMenus.hotbar_visible(hotbar) and ui.SenzuMenus.hotbar_slot_has_senzu(hotbar, 1)
    assert not ui.SenzuMenus.hotbar_slot_has_senzu(hotbar, 2)
    assert not majin.panel_visible(ui.crop(hotbar, majin.PANEL_BOX))


@pytest.mark.new_game_ui
def test_gravity_never_clicks_again_without_reading_the_unchanged_value(monkeypatch):
    # Live 2026-10-14: retries on an unreadable label overshot 0G to 100G.
    import itertools
    reads = itertools.chain([(60, 1.0, 0.5, True)], itertools.repeat((None, 0.4, 0.3, True)))
    with patch.object(core, "_load_gravity_templates", return_value={v: None for v in range(0, 101, 10)}), \
         patch.object(core, "_read_gc_gravity", side_effect=lambda *a: next(reads)), \
         patch.object(core, "robust_move"), \
         patch.object(core, "safe_sleep"), \
         patch.object(core, "check_exit"), \
         patch.object(core, "click_at") as click:
        assert core._cycle_gc_gravity_to_zero(None, (0, 0, 1920, 1080)) is False
    assert click.call_count == 1


@pytest.mark.parametrize("readings, expected", [
    ([], 30.0),
    ([(0.0, 0.90), (30.0, 0.90)], 30.0),          # not dropping
    ([(0.0, 0.90), (30.0, 0.72)], 30.0),          # 10G pace: 0.6%/s, 70 s to critical
    ([(0.0, 0.90), (30.0, 0.55)], 12.86),         # fast drop: check sooner
    ([(0.0, 0.90), (10.0, 0.60)], 8.0),           # very fast: floor
])
def test_ki_hp_check_spacing_follows_the_drain(monkeypatch, readings, expected):
    monkeypatch.setattr(core, "_KI_HP_READINGS", list(readings))
    assert core._ki_hp_interval() == pytest.approx(expected, abs=0.05)


def test_longer_names_ending_in_senzu_bean_are_not_senzu():
    menus = ui.SenzuMenus
    frame = paste([("items_list_senzu", menus.LIST_BOX)])
    shifted = frame.copy()
    x, y, w, h = menus.LIST_BOX
    shifted[y:y + h, x + 80:x + w] = frame[y:y + h, x:x + w - 80]   # name no longer at the row start
    shifted[y:y + h, x:x + 80] = frame[y:y + h, x:x + 1].repeat(80, axis=1)
    assert menus.senzu_row_y(frame) is not None
    assert menus.senzu_row_y(shifted) is None


@pytest.mark.new_game_ui
def test_ki_never_clicks_without_its_minigame_label(monkeypatch):
    monkeypatch.setattr(core, "_KI_V9_STATE", {"clicked": None, "label_seen_at": 0.0})
    with patch.object(core, "_grab_reference_box", return_value=ki_frame("ki_circle_4_at_479_486", 479, 486)), \
         patch.object(core.training_ui, "ki_ring_radius", return_value=60.0), \
         patch.object(core, "_ki_click") as click:
        core.logic_ki_v9(None, {"left": 0, "top": 0, "width": 1920, "height": 1080})
    click.assert_not_called()


def test_majin_cooldown_row_turns_red_and_reads_as_cooldown():
    # Captured live 2026-10-14 right after a cast.
    panel = fixture("majin_panel_cooldown")
    majin = ui.MajinTransform
    assert majin.panel_visible(panel) and majin.find_regen_slot(panel) == 3
    assert majin.slot_state(panel, 3) == "cooldown"


def test_progression_label_ignores_bright_scenery_at_the_box_edge():
    # Live GC frame: a sliver of lit wall at the right edge used to be taken for
    # the numbers, so the label (and progress tracking) vanished for whole runs.
    label = fixture("progression_label_scenery_edge")
    assert ui.progression_spans(label) is not None
    assert ui.read_progression_level(label) == (3, 20)
    assert ui.progression_complete(ui.progression_numbers(label)) is False


@pytest.mark.new_game_ui
def test_agility_presses_a_repeated_letter_once_the_highlight_moves(monkeypatch):
    monkeypatch.setattr(core, "_AGILITY_V4_TEMPLATES", {"d": None})
    monkeypatch.setattr(core, "_AGILITY_V4_STATE", {"key": None, "cleared": True, "pressed_at": 0.0, "x": None})
    # "W D D": first D at x=100 (pressed, still white for a frame), then the second D at x=170.
    readings = iter([("d", 0.9, 100.0), ("d", 0.9, 101.0), ("d", 0.9, 170.0), ("d", 0.9, 170.0)])
    clock = iter([10.0, 10.03, 10.06, 10.09])
    with patch.object(core, "_grab_reference_box"), \
         patch.object(core.training_ui, "agility_letter_at", side_effect=lambda *_: next(readings)), \
         patch.object(core.time, "monotonic", side_effect=lambda: next(clock)), \
         patch.object(core, "hardware_tap", return_value=True) as tap:
        for _ in range(4):
            core.logic_agility_v4(None)
    assert [call.args[0] for call in tap.call_args_list] == ["d", "d"]



@pytest.mark.parametrize("name", ["progression_label_ki_htc", "progression_label_htc_beige",
                                  "progression_label_ki_damage_htc",
                                  "progression_label_ki_damage_yellow"])
def test_progression_label_reads_on_htc_light_floors(name):
    # Live HTC crops: the gold Ki label fades into a cream floor right at "0/50",
    # and the white standard label sits on a beige wall brighter than the old cut.
    label = fixture(name)
    assert ui.progression_spans(label) is not None
    assert ui.progression_complete(ui.progression_numbers(label)) is False


@pytest.mark.parametrize("name, shown", [("progression_label_ki_htc", True),
                                         ("progression_label_ki_damage_yellow", True),
                                         ("progression_label_htc_beige", False)])
def test_ki_label_is_recognised_by_its_multiplier_word(name, shown):
    label = fixture(name)
    if label.shape[0] != ui.PROGRESSION_LABEL_BOXES["ki"][3]:
        label = np.zeros((26, 520, 3), np.uint8)   # a non-Ki screen at the Ki label spot
    assert ui.ki_label_visible(label) is shown


def test_first_ki_check_uses_the_drain_learned_outside_ki(monkeypatch):
    monkeypatch.setattr(core, "KI_HP_CHECK_MODE", "adaptive")
    # 1.0 -> 0.9 over 10 s outside Ki: 1%/s. Projected 20 s later: 0.7 HP, so the
    # check comes before the 30 s default.
    core._note_hp_reading(1.0, now=0.0)
    core._note_hp_reading(0.9, now=10.0)
    assert abs(core._HP_DRAIN_RATE - 0.01) < 1e-9
    critical = core.training_ui.HP_CRITICAL_FRACTION
    expected = max(8.0, min(30.0, (0.7 - critical) / 0.01 * 0.6))
    assert abs(core._ki_hp_interval(now=30.0) - expected) < 1e-6
    assert core._ki_hp_interval(now=30.0) < 30.0


def test_healing_restarts_the_drain_and_unknown_drain_keeps_30_seconds():
    assert core._ki_hp_interval() == 30.0
    core._note_hp_reading(0.4, now=0.0)
    core._note_hp_reading(0.95, now=5.0)   # healed: not a drain
    assert core._HP_DRAIN_RATE is None


def test_each_ki_death_brings_checks_forward_down_to_a_floor(monkeypatch):
    monkeypatch.setattr(core, "KI_HP_CHECK_MODE", "adaptive")
    core._note_ki_death()
    assert core._ki_hp_interval() == 30.0 * 0.75
    for _ in range(10):
        core._note_ki_death()
    assert core._KI_CHECK_SCALE == core.KI_CHECK_SCALE_MIN
    assert core._ki_hp_interval() == 30.0 * core.KI_CHECK_SCALE_MIN

def test_fixed_timing_ignores_learned_drain_and_deaths(monkeypatch):
    monkeypatch.setattr(core, "KI_HP_CHECK_MODE", "fixed")
    core._note_hp_reading(1.0, now=0.0)
    core._note_hp_reading(0.5, now=10.0)
    core._note_ki_death()
    assert core._KI_CHECK_SCALE == 1.0
    assert core._ki_hp_interval(now=11.0) == 30.0


def test_dark_death_buttons_are_still_a_death_screen():
    # Live capture, 20 Oct 2026: buttons at green ~110 over a dark body; only the
    # dialog areas are kept in the fixture.
    frame = cv2.imread(str(FIXTURES / "death_dark_buttons.png"))
    assert core.training_ui.death_dialog_visible(frame)


@pytest.mark.parametrize("targets, gc_seen, expected", [
    ({"Ki Control": (44, 50)}, False, True),    # HTC total, no GC sign
    ({"Ki Control": (20, 20)}, False, False),   # GC total
    ({}, False, False),                         # Points Left not read: keep checking
    ({"Ki Control": (44, 50)}, True, False),    # GC seen this run wins
    ({"Ki Damage": (44, 50)}, False, False),    # another stat's total doesn't count
])
def test_ki_hp_checks_skip_only_when_sure_it_is_htc(monkeypatch, targets, gc_seen, expected):
    monkeypatch.setattr(core, "PROGRESSION_TARGETS", dict(targets))
    monkeypatch.setattr(core, "GC_RUN_CONFIRMED", gc_seen)
    assert core._in_htc("Ki Control") is expected
