"""The session marker is how a crash is noticed at all: it is written unclean at
startup and only flipped on the way out, so anything that stops the sidecar without
running the shutdown path leaves it behind for the next launch to find."""
import json
import os

from unittest.mock import patch

import pytest

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


class TestMarkerLogPath:
    """The marker is a plain file and its log tail can end up in a posted bug report,
    so it may only name one of this app's own session logs."""

    def _crash_from(self, tmp_path, log):
        _write_marker(tmp_path, log=log)
        with patch.object(core, "JSON_DIR", str(tmp_path)), \
                patch.object(core, "_pid_alive", return_value=False):
            core.PREVIOUS_SESSION_CRASH = None
            core._claim_session_marker(None)
        crash, core.PREVIOUS_SESSION_CRASH = core.PREVIOUS_SESSION_CRASH, None
        return crash

    def test_a_real_session_log_is_read(self, tmp_path):
        logs = tmp_path / "logs"
        logs.mkdir()
        log = logs / "session_2026-11-01_10-00-00_pid4321.log"
        log.write_text("the last line before the crash\n", encoding="utf-8")
        crash = self._crash_from(tmp_path, str(log))
        assert "the last line before the crash" in crash["log_tail"]

    @pytest.mark.parametrize("where", ["outside", "wrong_name", "escape"])
    def test_any_other_file_is_ignored(self, tmp_path, where):
        logs = tmp_path / "logs"
        logs.mkdir()
        secret = tmp_path / "notes.txt"
        secret.write_text("something private\n", encoding="utf-8")
        (logs / "notes.txt").write_text("something private\n", encoding="utf-8")
        path = {
            "outside": str(secret),
            "wrong_name": str(logs / "notes.txt"),
            "escape": str(logs / ".." / "notes.txt"),
        }[where]
        crash = self._crash_from(tmp_path, path)
        assert crash is not None          # the crash is still reported
        assert crash["log"] is None
        assert "private" not in (crash["log_tail"] or "")


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


class TestDeliberateShutdown:
    """The launcher kills the sidecar with taskkill /F, which beats the parent
    watchdog's 250ms poll. If the launcher could not mark the session clean itself,
    every ordinary close would be reported as a crash on the next launch."""

    def test_the_shutdown_command_marks_the_session_clean(self, tmp_path):
        with patch.object(core, "JSON_DIR", str(tmp_path)):
            core._claim_session_marker(None)
            with open(_marker(tmp_path), encoding="utf-8") as fh:
                assert json.load(fh)["clean"] is False
            core._release_session_marker()
            with open(_marker(tmp_path), encoding="utf-8") as fh:
                assert json.load(fh)["clean"] is True

    def test_the_sidecar_exposes_the_clean_shutdown_command(self):
        import inspect
        core_source = inspect.getsource(core.run_ui_server)
        assert 'action == "session_shutdown_clean"' in core_source

    def test_the_launcher_has_a_way_to_call_it(self):
        # The Rust side posts this action just before terminating the sidecar; the
        # handler and the caller have to keep agreeing on the name.
        import inspect
        import pathlib
        core_source = inspect.getsource(core.run_ui_server)
        rust_path = pathlib.Path(core.__file__).parents[1] / "src-tauri" / "src" / "lib.rs"
        if not rust_path.parents[1].exists():
            pytest.skip("Rust launcher source is not included in this Python-only checkout")
        rust = rust_path.read_text(encoding="utf-8")
        assert 'action == "session_shutdown_clean"' in core_source
        assert '"session_shutdown_clean"' in rust
        assert "mark_sidecar_shutdown_clean(&app);" in rust
        # It must run before the kill, not after.
        assert rust.index("mark_sidecar_shutdown_clean(&app);") < \
            rust.index("terminate_tracked_child(&app, Duration::from_millis(650));")

    def test_the_watchdog_does_not_launder_a_crash_into_a_clean_exit(self):
        # The watchdog fires when the launcher PID disappears — which is precisely
        # what an app crash looks like from the sidecar. Marking clean there made a
        # force-killed app report a clean exit, so the crash notice never fired.
        import inspect
        watchdog = inspect.getsource(core._start_parent_watchdog)
        assert "_release_session_marker()" not in watchdog
        assert "_release_session_marker" not in watchdog
