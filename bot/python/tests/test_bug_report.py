"""A bug report is posted publicly to GitHub by the user, so two things matter:
it must never leak who they are, and it must contain what actually decides whether
a bug is fixable."""
from unittest.mock import patch

import xynmacro_core as core


class TestScrubbing:
    def test_replaces_the_profile_path(self):
        with patch.dict("os.environ", {"USERPROFILE": r"C:\Users\deray", "USERNAME": "deray"}):
            out = core._scrub_user_paths(r"failed to read C:\Users\deray\AppData\x.json")
        assert "deray" not in out
        assert "%USERPROFILE%" in out

    def test_replaces_a_bare_username(self):
        with patch.dict("os.environ", {"USERPROFILE": "", "USERNAME": "deray"}):
            assert "deray" not in core._scrub_user_paths("logged in as deray")

    def test_is_case_insensitive(self):
        with patch.dict("os.environ", {"USERPROFILE": "", "USERNAME": "deray"}):
            assert "DERAY" not in core._scrub_user_paths(r"C:\USERS\DERAY\file")

    def test_leaves_short_usernames_alone(self):
        # A 1-2 char name would match inside ordinary words and shred the report.
        with patch.dict("os.environ", {"USERPROFILE": "", "USERNAME": "xy"}):
            assert core._scrub_user_paths("the xylophone") == "the xylophone"

    def test_handles_empty_and_none(self):
        assert core._scrub_user_paths("") == ""
        assert core._scrub_user_paths(None) is None


class TestReportBody:
    def test_always_names_the_app_and_version(self):
        body = core.build_bug_report([])
        assert body.startswith("**XynMacro**")
        # The launcher supplies APP_VERSION; running the sidecar alone leaves it None.
        assert str(core.APP_VERSION or "unknown version") in body

    def test_records_the_users_description(self):
        assert "it froze" in core.build_bug_report([], description="it froze")

    def test_marks_a_missing_description_rather_than_leaving_it_blank(self):
        assert "not described" in core.build_bug_report([])

    def test_includes_only_the_requested_sections(self):
        body = core.build_bug_report(["display"])
        assert "### Display" in body
        assert "### Settings" not in body

    def test_ignores_unknown_section_names(self):
        assert "###" not in core.build_bug_report(["../etc/passwd", "nope"]).split("What happened")[1]

    def test_reports_the_webview_version_because_it_broke_every_button_once(self):
        body = core.build_bug_report(["system"], webview="Chrome/150.0.0.0")
        assert "Chrome/150.0.0.0" in body

    def test_scrubs_the_whole_body_including_the_description(self):
        with patch.dict("os.environ", {"USERPROFILE": "", "USERNAME": "deray"}):
            assert "deray" not in core.build_bug_report([], description="I am deray")

    def test_game_section_works_with_roblox_closed(self):
        # People file bugs exactly when things are not running.
        with patch.object(core, "update_game_window", return_value=False), \
             patch.object(core, "GAME_HWND", None):
            assert "not found" in core.build_bug_report(["game"]) or \
                   "minimized" in core.build_bug_report(["game"])


class TestIssueUrl:
    def test_builds_a_link_to_the_public_repo(self):
        payload = core.bug_report_payload([], description="hi")
        assert payload["url"].startswith(core.ISSUE_URL + "?")
        assert payload["too_long"] is False

    def test_refuses_a_link_that_github_would_truncate(self):
        payload = core.bug_report_payload([], description="x" * (core.ISSUE_URL_LIMIT + 500))
        assert payload["url"] is None
        assert payload["too_long"] is True

    def test_percent_encodes_the_body(self):
        payload = core.bug_report_payload([], description="a b&c=d")
        assert " " not in payload["url"].split("?", 1)[1]
        assert "&c=d" not in payload["url"]

    def test_markdown_matches_what_the_link_carries(self):
        payload = core.bug_report_payload(["display"], description="same")
        assert payload["markdown"] == core.build_bug_report(["display"], description="same")


class TestSystemProfile:
    def test_never_raises(self):
        assert isinstance(core._system_profile(), dict)

    def test_reports_whether_this_is_a_packaged_build(self):
        assert "frozen" in core._system_profile()


class TestDisplaySection:
    def test_omits_unknown_sizes_instead_of_printing_zero_by_zero(self):
        # "0x0" in a public issue reads as a measurement, not as missing data.
        with patch.object(core, "_get_screen_info", return_value={"width": 0, "height": 0}), \
             patch.object(core, "_current_game_monitor_info", return_value={}):
            assert "0x0" not in core.build_bug_report(["display"])

    def test_reports_real_sizes_when_known(self):
        with patch.object(core, "_get_screen_info", return_value={"width": 2560, "height": 1600}), \
             patch.object(core, "_current_game_monitor_info", return_value={}):
            assert "2560x1600" in core.build_bug_report(["display"])


class TestInterfaceErrors:
    def test_includes_errors_the_backend_never_sees(self):
        # A CSP change once killed every button while the sidecar log stayed clean.
        body = core.build_bug_report(["logs"], ui_errors=["12:00:01 UI error: boom"])
        assert "Interface errors" in body
        assert "boom" in body

    def test_omits_the_row_when_there_were_none(self):
        assert "Interface errors" not in core.build_bug_report(["logs"])

    def test_caps_how_many_are_carried(self):
        body = core.build_bug_report(["logs"], ui_errors=[f"err{i}" for i in range(40)])
        assert "err39" in body
        assert "err0\n" not in body

    def test_scrubs_them_like_everything_else(self):
        with patch.dict("os.environ", {"USERPROFILE": "", "USERNAME": "deray"}):
            body = core.build_bug_report(["logs"], ui_errors=["failed for deray"])
        assert "deray" not in body
