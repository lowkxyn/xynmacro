import sys
import unittest
from pathlib import Path


PYTHON_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PYTHON_DIR))

import xmacro_core as core


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


if __name__ == "__main__":
    unittest.main()
