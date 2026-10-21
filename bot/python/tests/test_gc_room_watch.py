from unittest.mock import Mock, patch

import pytest

import xynmacro_core as core


def watch_over(room_frames, death=False, gc_run=True, step=1.0):
    """Feed one room_visible answer per second; return the first time it fires."""
    watch = core.GcRoomWatch()
    answers = iter(room_frames)
    with patch.object(core, "GC_RUN_CONFIRMED", gc_run), \
         patch.object(core, "_grab_reference_box", return_value=Mock()), \
         patch.object(core, "_gc_death_dialog_visible", return_value=death), \
         patch.object(core.gc_recovery, "room_visible", side_effect=lambda _f: next(answers)):
        for index in range(len(room_frames)):
            if watch.left_room(Mock(), {}, 100.0 + index * step):
                return index
    return None


def test_session_end_stops_after_eight_seconds_without_the_room():
    assert watch_over([True] * 5 + [False] * 12) == 5 + 8


def test_brief_gaps_never_stop_the_run():
    assert watch_over(([True] * 3 + [False] * 7) * 3) is None


def test_death_screen_belongs_to_recovery_not_the_watch():
    assert watch_over([False] * 20, death=True) is None


def test_htc_runs_are_not_watched():
    assert watch_over([False] * 20, gc_run=False) is None


def test_checks_are_throttled_to_once_a_second():
    watch = core.GcRoomWatch()
    with patch.object(core, "GC_RUN_CONFIRMED", True), \
         patch.object(core, "_grab_reference_box", return_value=Mock()) as grab, \
         patch.object(core, "_gc_death_dialog_visible", return_value=False), \
         patch.object(core.gc_recovery, "room_visible", return_value=True):
        for tick in range(20):
            watch.left_room(Mock(), {}, 100.0 + tick * 0.1)
    assert grab.call_count == 2


def test_leaving_the_room_is_an_error_with_no_after_run_keys(monkeypatch):
    monkeypatch.setattr(core, "UI_STOP_REQUESTED", False)
    monkeypatch.setattr(core, "_AFTER_ACTIONS_BLOCKED", False)
    with patch.object(core, "_record_run_outcome") as record, \
         patch.object(core, "_release_macro_input"):
        core._stop_for_leaving_gc_room()
    assert core.UI_STOP_REQUESTED and core._AFTER_ACTIONS_BLOCKED
    outcome, reason = record.call_args.args
    assert outcome == "error" and "time limit" in reason
    assert not core._failed_run_needs_zero_gravity("error")


@pytest.mark.parametrize("frames", [[True] * 30])
def test_a_full_healthy_session_never_fires(frames):
    assert watch_over(frames) is None
