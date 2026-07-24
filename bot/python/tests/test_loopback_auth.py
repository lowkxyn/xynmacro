import io
import sys
import unittest
from pathlib import Path


PYTHON_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PYTHON_DIR))

import xynmacro_core as core


class LoopbackAuthTests(unittest.TestCase):
    def test_matching_launch_token_is_accepted(self):
        denial = core._loopback_request_denial(
            "127.0.0.1:8765", None, "launch-secret", "launch-secret"
        )
        self.assertIsNone(denial)

    def test_missing_or_wrong_launch_token_is_unauthorized(self):
        self.assertEqual(
            core._loopback_request_denial(
                "127.0.0.1:8765", None, None, "launch-secret"
            ),
            ("unauthorized", 401),
        )
        self.assertEqual(
            core._loopback_request_denial(
                "127.0.0.1:8765", None, "wrong", "launch-secret"
            ),
            ("unauthorized", 401),
        )

    def test_host_and_origin_guards_still_apply_with_auth(self):
        self.assertEqual(
            core._loopback_request_denial(
                "evil.example:8765", None, "launch-secret", "launch-secret"
            ),
            ("forbidden host", 403),
        )
        self.assertEqual(
            core._loopback_request_denial(
                "localhost:8765", "https://evil.example", "launch-secret", "launch-secret"
            ),
            ("cross-origin request blocked", 403),
        )

    def test_ipv6_loopback_hosts_are_accepted(self):
        self.assertIsNone(
            core._loopback_request_denial(
                "[::1]:8765", None, "launch-secret", "launch-secret"
            )
        )
        self.assertIsNone(
            core._loopback_request_denial(
                "::1", None, "launch-secret", "launch-secret"
            )
        )

    def test_packaged_sidecar_requires_token(self):
        with self.assertRaisesRegex(ValueError, "--auth-token is required"):
            core._validated_auth_token(None, frozen=True)

    def test_direct_source_launch_can_use_dev_fallback(self):
        self.assertIsNone(core._validated_auth_token(None, frozen=False))
        self.assertEqual(
            core._validated_auth_token(" launch-secret ", frozen=False),
            "launch-secret",
        )

    def test_token_is_read_from_the_first_stdin_line(self):
        self.assertEqual(
            core._read_auth_token_from_stdin(io.StringIO("launch-secret\nignored\n")),
            "launch-secret",
        )

    def test_closed_or_empty_stdin_fails_closed(self):
        # The launcher closes the pipe after one line; nothing there must not become
        # an unauthenticated backend.
        self.assertEqual(core._read_auth_token_from_stdin(io.StringIO("")), "")
        with self.assertRaises(ValueError):
            core._validated_auth_token(
                core._read_auth_token_from_stdin(io.StringIO("")), frozen=True
            )

    def test_unreadable_stdin_fails_closed(self):
        closed = io.StringIO("launch-secret\n")
        closed.close()
        self.assertEqual(core._read_auth_token_from_stdin(closed), "")


if __name__ == "__main__":
    unittest.main()
