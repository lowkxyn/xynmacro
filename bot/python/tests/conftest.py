"""Unit tests must explicitly replace operations that can touch the desktop."""
import ctypes
import os
import platform
import subprocess
import sys
from pathlib import Path
from unittest.mock import Mock, patch

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
# Importing the sidecar initializes DPI awareness. Keep that process-local
# initialization inert here; the dedicated startup test exercises its contract.
with patch.object(ctypes.windll.user32, "SetProcessDpiAwarenessContext", return_value=True):
    import xynmacro_core as core


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "inert_python_child: allow the explicitly mocked DPI startup subprocess"
    )
    config.addinivalue_line(
        "markers", "new_game_ui: run with the October 2026 game UI layout"
    )


@pytest.fixture(autouse=True)
def native_operations_require_mocks(monkeypatch, request):
    attempted = []

    def blocked(name):
        def reject(*_args, **_kwargs):
            attempted.append(name)
            raise AssertionError(f"Unmocked native operation: {name}")
        return Mock(side_effect=reject)

    for name in (
        "SendInput", "SetCursorPos", "SetForegroundWindow", "ShowWindow",
        "SetWindowPos", "PostMessageW", "GetDpiForWindow",
    ):
        monkeypatch.setattr(core._user32, name, blocked(f"user32.{name}"))
    for name in ("SetCursorPos", "mouse_event", "keybd_event", "ChangeDisplaySettingsEx"):
        monkeypatch.setattr(core.win32api, name, blocked(f"win32api.{name}"))
    for name in ("keyDown", "keyUp", "press", "click", "moveTo", "moveRel"):
        monkeypatch.setattr(core.pydirectinput, name, blocked(f"pydirectinput.{name}"))
    for name in ("hook", "add_hotkey", "hook_key", "on_press", "on_release"):
        monkeypatch.setattr(core.keyboard, name, blocked(f"keyboard.{name}"))
    monkeypatch.setattr(core.mss, "MSS", blocked("mss.MSS"))
    # The coverage check walks the real desktop z-order. With Roblox open on the
    # test machine it found real windows and stalled a monitor test's loop.
    # Healing tests predate Majin auto-detect and pin the method they exercise;
    # the auto tests switch it back on themselves.
    monkeypatch.setattr(core, "MAJIN_REGEN_AUTO", False)
    monkeypatch.setattr(core, "MAJIN_REGEN_UNAVAILABLE_FOR_RUN", False)
    if request.module.__name__ != "test_phase2_windows":  # tests the walker itself
        monkeypatch.setattr(core, "_windows_above_game", lambda: iter(()))
    # Start-flow tests predate the chat-button check and pass a bare object as the
    # screen grabber; test_roblox_chat exercises the real one.
    # Learned HP drain and the death scale outlive a run on purpose; not a test.
    monkeypatch.setattr(core, "_HP_LAST_SEEN", None)
    monkeypatch.setattr(core, "_HP_DRAIN_RATE", None)
    monkeypatch.setattr(core, "_KI_CHECK_SCALE", 1.0)
    if request.module.__name__ != "test_roblox_chat":
        monkeypatch.setattr(core, "_close_roblox_chat", lambda *_args: None)
    # Tests written before the October 2026 game UI use its screenshots and
    # coordinates; only the new-UI tests run with the current layout.
    layout = (core.GAME_UI_LAYOUT if request.node.get_closest_marker("new_game_ui")
              else core.LEGACY_UI_LAYOUT)
    for name, value in core._layout_constants(layout).items():
        monkeypatch.setattr(core, name, value)
    monkeypatch.setattr(core, "GAME_UI_LAYOUT", layout)
    monkeypatch.setattr(ctypes.windll.kernel32, "SetThreadExecutionState",
                        blocked("kernel32.SetThreadExecutionState"))
    monkeypatch.setattr(os, "system", blocked("os.system"))
    monkeypatch.setattr(os, "startfile", blocked("os.startfile"))

    real_popen = subprocess.Popen
    allow_child = (request.node.get_closest_marker("inert_python_child") is not None
                   and request.node.name == "test_dpi_awareness_is_per_monitor_before_and_after_first_capture")

    def guarded_popen(args, *popen_args, **popen_kwargs):
        if (allow_child and isinstance(args, (list, tuple)) and len(args) == 3
                and args[0] == sys.executable and args[1] == "-c"):
            popen_kwargs["creationflags"] = (popen_kwargs.get("creationflags", 0)
                                            | subprocess.CREATE_NO_WINDOW)
            return real_popen(args, *popen_args, **popen_kwargs)
        return blocked("subprocess.Popen")(args, *popen_args, **popen_kwargs)

    monkeypatch.setattr(subprocess, "Popen", guarded_popen)
    # platform.platform/processor can spawn Windows commands. Unit tests need a
    # stable description, not measurements from the machine running the suite.
    monkeypatch.setattr(platform, "platform", lambda: "Windows-test")
    monkeypatch.setattr(platform, "processor", lambda: "test-processor")
    yield
    # Some runtime handlers catch failures. Catch swallowed native attempts too.
    assert not attempted, "Unmocked native operations: " + ", ".join(attempted)
