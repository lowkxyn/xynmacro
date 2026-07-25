"""The session marker is how a crash is noticed at all: it is written unclean at
startup and only flipped on the way out, so anything that stops the sidecar without
running the shutdown path leaves it behind for the next launch to find."""
import json
import os

from unittest.mock import patch

import xynmacro_core as core


def _marker(tmp_path):
    return os.path.join(str(tmp_path), "last_session.json")


def _write_marker(tmp_path, **fields):
    data = {"pid": 999999, "version": "1.4.0", "started_at": 1.0,
            "log": None, "clean": False}
    data.update(fields)
    with open(_marker(tmp_path), "w", encoding="utf-8") as fh:
        json.dump(data, fh)


class TestSessionMarker:
    def test_an_unclean_marker_from_a_dead_pid_is_a_crash(self, tmp_path):
        _write_marker(tmp_path)
        with patch.object(core, "JSON_DIR", str(tmp_path)), \
                patch.object(core, "_pid_alive", return_value=False):
            core.PREVIOUS_SESSION_CRASH = None
            core._claim_session_marker(None)
        assert core.PREVIOUS_SESSION_CRASH is not None
        assert core.PREVIOUS_SESSION_CRASH["version"] == "1.4.0"

    def test_a_clean_marker_is_not_a_crash(self, tmp_path):
        _write_marker(tmp_path, clean=True)
        with patch.object(core, "JSON_DIR", str(tmp_path)), \
                patch.object(core, "_pid_alive", return_value=False):
            core.PREVIOUS_SESSION_CRASH = None
            core._claim_session_marker(None)
        assert core.PREVIOUS_SESSION_CRASH is None

    def test_a_second_running_copy_is_not_reported_as_a_crash(self, tmp_path):
        # Two sidecars at once leaves an unclean marker owned by a live process.
        # Calling that a crash would nag the user every single launch.
        _write_marker(tmp_path)
        with patch.object(core, "JSON_DIR", str(tmp_path)), \
                patch.object(core, "_pid_alive", return_value=True):
            core.PREVIOUS_SESSION_CRASH = None
            core._claim_session_marker(None)
        assert core.PREVIOUS_SESSION_CRASH is None

    def test_a_first_ever_run_has_no_previous_session(self, tmp_path):
        with patch.object(core, "JSON_DIR", str(tmp_path)):
            core.PREVIOUS_SESSION_CRASH = None
            core._claim_session_marker(None)
        assert core.PREVIOUS_SESSION_CRASH is None
        with open(_marker(tmp_path), encoding="utf-8") as fh:
            assert json.load(fh)["clean"] is False

    def test_release_marks_this_session_clean(self, tmp_path):
        with patch.object(core, "JSON_DIR", str(tmp_path)):
            core._claim_session_marker(None)
            core._release_session_marker()
        with open(_marker(tmp_path), encoding="utf-8") as fh:
            assert json.load(fh)["clean"] is True

    def test_release_never_overwrites_a_newer_sidecars_marker(self, tmp_path):
        # A restart claims the marker under a new PID. The old process exiting
        # afterwards must not mark the new session clean on its behalf.
        _write_marker(tmp_path, pid=os.getpid() + 1, clean=False)
        with patch.object(core, "JSON_DIR", str(tmp_path)):
            core._release_session_marker()
        with open(_marker(tmp_path), encoding="utf-8") as fh:
            assert json.load(fh)["clean"] is False


class TestCrashSection:
    def test_the_crash_section_carries_the_previous_log(self, tmp_path):
        log = os.path.join(str(tmp_path), "session_old.log")
        with open(log, "w", encoding="utf-8") as fh:
            fh.write("\n".join(f"line {i}" for i in range(200)))
        core.PREVIOUS_SESSION_CRASH = {
            "version": "1.4.0", "started_at": 1.0, "log": log,
            "log_tail": core._read_log_tail(log),
        }
        try:
            sections = core._bug_report_sections(["crash"])
        finally:
            core.PREVIOUS_SESSION_CRASH = None
        rows = dict(sections["Previous session (ended unexpectedly)"])
        assert "line 199" in rows["Tail"]
        assert "line 100" not in rows["Tail"]   # tail only, not the whole file

    def test_no_crash_section_without_a_crash(self):
        core.PREVIOUS_SESSION_CRASH = None
        assert core._bug_report_sections(["crash"]) == {}
