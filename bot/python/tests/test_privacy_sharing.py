"""Copied logs, saved logs and bug reports leave the machine; all of it is fictional here."""
import json
import os
import sys
from collections import deque
from unittest import mock

import pytest

import xynmacro_core as core


@pytest.fixture(autouse=True)
def synthetic_profile(monkeypatch):
    monkeypatch.setenv("USERPROFILE", r"C:\Users\SyntheticUser")
    monkeypatch.setenv("USERNAME", "SyntheticUser")
    monkeypatch.setattr(core, "GAME_HWND", None)
    monkeypatch.setattr(core, "update_game_window", lambda: False)


def _app(tmp_path):
    apps = []
    with mock.patch.object(core, "DATA_DIR", str(tmp_path)), \
         mock.patch.object(core, "SAVE_DIR", str(tmp_path)), \
         mock.patch.object(core.sys, "stdout", core._TeeBuffer(sys.stdout, deque())), \
         mock.patch.object(core, "_start_parent_watchdog"), \
         mock.patch.object(core, "register_start_stop_hotkey"), \
         mock.patch.object(core, "register_manual_next_hotkey"), \
         mock.patch.object(core, "register_pause_hotkey"), \
         mock.patch.object(core, "_make_exclusive_server",
                           side_effect=lambda app: apps.append(app) or mock.Mock(port=8765)):
        core.run_ui_server(sidecar_pid=os.getpid())
    return apps[0]


class TestRedaction:
    @pytest.mark.parametrize("line", [
        r"open C:\Users\Avery Example\Documents\x.json",
        r"open d:/users/AveryExample/x.json",
        r'{"dir": "C:\\Users\\AveryExample\\AppData"}',
    ])
    def test_any_profile_folder_is_replaced_but_the_path_shape_stays(self, line):
        out = core._scrub_user_paths(line)
        assert "Avery" not in out
        assert "<user>" in out

    def test_emails_and_credential_shaped_values(self):
        out = core._scrub_user_paths(
            "mail avery@example.test via https://bot:" + "hunter22" + "@proxy.example.test/x"
            " ?token=abc123secret Authorization: Bearer abcdefghijklmnop12"
            " ghp_" + "Z" * 36)
        for leaked in ("avery@", "hunter22", "abc123secret", "abcdefghijklmnop12", "Z" * 36):
            assert leaked not in out
        assert "proxy.example.test" in out   # diagnostic structure survives

    @pytest.mark.parametrize("pair, secret", [
        ('{"password": "fictionalSensitivePassword123"}', "fictionalSensitivePassword123"),
        ('{"token": "fictionalSensitiveToken123"}', "fictionalSensitiveToken123"),
        ('{"api_key": "fictionalSensitiveApiKey123"}', "fictionalSensitiveApiKey123"),
        ("{'Client_Secret': 'fictional value with spaces'}", "fictional value"),
        ('{"access-token":"fictional\\"Escaped123"}', "Escaped123"),
        ("API-KEY = fictionalBare123", "fictionalBare123"),
    ])
    def test_quoted_and_named_secret_values(self, pair, secret):
        out = core._scrub_user_paths(pair)
        assert secret not in out
        assert "<removed>" in out

    def test_secret_removal_keeps_the_json_shape(self):
        out = core._scrub_user_paths('{"token": "fictionalSensitiveToken123", "tokens": 4}')
        assert out == '{"token": "<removed>", "tokens": 4}'

    def test_ordinary_diagnostics_are_untouched(self):
        line = "[SENZU] HP 42% at (120, 880) after 3.5s; template score 0.91"
        assert core._scrub_user_paths(line) == line


class TestCoveredWindow:
    def test_the_covering_title_never_reaches_the_log_or_outcome(self, monkeypatch, capsys):
        monkeypatch.setattr(core, "_game_window_covering_title", lambda: "Private Doc - Browser")
        outcomes = []
        monkeypatch.setattr(core, "_record_run_outcome", lambda *a, **k: outcomes.append(a))
        monkeypatch.setattr(core, "_release_macro_input", lambda: None)
        monkeypatch.setattr(core, "UI_STOP_REQUESTED", False)
        monkeypatch.setattr(core, "_AFTER_ACTIONS_BLOCKED", False)
        assert core._stop_if_game_covered() is True
        assert core.UI_STOP_REQUESTED and core._AFTER_ACTIONS_BLOCKED
        assert "Private Doc" not in capsys.readouterr().out
        assert "Private Doc" not in repr(outcomes)


class TestSharingEndpoints:
    def test_copy_uses_the_same_redaction_as_save(self, tmp_path):
        app = _app(tmp_path)
        ring = deque([{"t": 0, "seq": 1, "msg": r"wrote C:\Users\Avery Example\a.txt for avery@example.test"}])
        with mock.patch.object(core, "_ui_log_ring", ring), \
             mock.patch.object(core, "SAVE_DIR", str(tmp_path)):
            copied = app.test_client().post("/copy_log").json
            saved = app.test_client().post("/save_log").json
        exported = open(saved["path"], encoding="utf-8").read()
        assert copied["ok"] and copied["count"] == 1
        assert copied["text"].strip() == exported.strip()
        assert "Avery" not in copied["text"] and "avery@" not in copied["text"]

    @pytest.mark.parametrize("key, secret", [
        ("password", "fictionalSensitivePassword123"),
        ("token", "fictionalSensitiveToken123"),
        ("api_key", "fictionalSensitiveApiKey123"),
    ])
    def test_quoted_json_secrets_leave_no_sharing_route(self, tmp_path, key, secret):
        app = _app(tmp_path)
        ring = deque([{"t": 0, "seq": 1, "msg": json.dumps({key: secret})}])
        with mock.patch.object(core, "_ui_log_ring", ring), \
             mock.patch.object(core, "SAVE_DIR", str(tmp_path)):
            client = app.test_client()
            copied = client.post("/copy_log").json
            saved = client.post("/save_log").json
            report = client.post("/command", json={"action": "bug_report",
                                                    "value": {"sections": ["logs"]}}).json
        exported = open(saved["path"], encoding="utf-8").read()
        assert copied["ok"] and saved["ok"] and report["ok"]
        for shared in (copied["text"], exported, report["markdown"], report["url"]):
            assert secret not in shared
        assert f'"{key}": "<removed>"' in copied["text"]

    def test_open_posts_the_previewed_report_even_after_logs_change(self, tmp_path, monkeypatch):
        app = _app(tmp_path)
        opened = mock.Mock()
        monkeypatch.setattr(core.os, "startfile", opened)
        ring = deque([{"t": 0, "seq": 1, "msg": "before preview"}])
        with mock.patch.object(core, "_ui_log_ring", ring):
            client = app.test_client()
            preview = client.post("/command", json={"action": "bug_report",
                                                     "value": {"sections": ["logs"]}}).json
            ring.append({"t": 1, "seq": 2, "msg": "after preview"})
            result = client.post("/command", json={
                "action": "bug_report_open", "value": {"report_id": preview["report_id"]}}).json
        assert result["ok"]
        opened.assert_called_once_with(preview["url"])
        assert preview["url"].startswith(core.ISSUE_URL + "?")
        assert "after+preview" not in preview["url"]

    @pytest.mark.parametrize("value", [
        {"report_id": "https://attacker.example/"}, {"url": "https://attacker.example/"},
        {"report_id": ["x"]}, None,
    ])
    def test_open_refuses_anything_but_a_previewed_report(self, tmp_path, monkeypatch, value):
        app = _app(tmp_path)
        opened = mock.Mock()
        monkeypatch.setattr(core.os, "startfile", opened)
        result = app.test_client().post("/command", json={"action": "bug_report_open",
                                                          "value": value}).json
        assert result["ok"] is False
        opened.assert_not_called()
