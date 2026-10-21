"""In-memory synthetic keys only; no signing environment variables are read."""
import base64
import hashlib
import tempfile
import unittest
from pathlib import Path

from nacl.exceptions import BadSignatureError
from nacl.signing import SigningKey

from verify_updater_keypair import decode_minisign_public_key, verify_keypair


def b64(value):
    return base64.b64encode(value).decode("ascii")


class UpdaterKeypairTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="xynmacro-signature-test-")
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "synthetic.sig"
        self.key = SigningKey(bytes(range(32)))
        self.probe = b"synthetic updater probe"
        self.key_id = b"test-key"
        self.public_packet = b"Ed" + self.key_id + bytes(self.key.verify_key)
        self.public_document = "untrusted comment: synthetic public key\n" + b64(self.public_packet) + "\n"
        self.public_value = b64(self.public_document.encode())

    def write_signature(self, algorithm=b"ED"):
        message = hashlib.blake2b(self.probe, digest_size=64).digest() if algorithm == b"ED" else self.probe
        signature = self.key.sign(message).signature
        comment = b"timestamp:0\tfile:synthetic.bin"
        global_signature = self.key.sign(signature + comment).signature
        self.lines = [
            "untrusted comment: synthetic signature",
            b64(algorithm + self.key_id + signature),
            "trusted comment: " + comment.decode(),
            b64(global_signature),
        ]
        self.save_lines()

    def save_lines(self):
        self.path.write_text(b64(("\n".join(self.lines) + "\n").encode()), encoding="utf-8")

    def test_accepts_tauri_prehash_and_legacy_signatures(self):
        for algorithm in (b"ED", b"Ed"):
            with self.subTest(algorithm=algorithm):
                self.write_signature(algorithm)
                verify_keypair(self.public_value, self.probe, self.path)

    def test_rejects_encodings_the_updater_cannot_consume(self):
        for value in (b64(self.public_packet), self.public_document,
                      b64(b64(self.public_packet).encode())):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    decode_minisign_public_key(value)

    def test_rejects_changed_payload(self):
        self.write_signature()
        with self.assertRaises(BadSignatureError):
            verify_keypair(self.public_value, self.probe + b"changed", self.path)

    def test_rejects_wrong_key_even_with_same_id(self):
        self.write_signature()
        wrong_packet = b"Ed" + self.key_id + bytes(SigningKey(b"x" * 32).verify_key)
        wrong_value = b64(("untrusted comment: test\n" + b64(wrong_packet)).encode())
        with self.assertRaises(BadSignatureError):
            verify_keypair(wrong_value, self.probe, self.path)

    def test_rejects_wrong_key_id(self):
        self.write_signature()
        self.lines[1] = b64(b"EDotherkey" + base64.b64decode(self.lines[1])[10:])
        self.save_lines()
        with self.assertRaisesRegex(ValueError, "different key IDs"):
            verify_keypair(self.public_value, self.probe, self.path)

    def test_rejects_missing_global_signature(self):
        self.write_signature()
        self.lines = self.lines[:2]
        self.save_lines()
        with self.assertRaises(ValueError):
            verify_keypair(self.public_value, self.probe, self.path)

    def test_rejects_changed_trusted_comment(self):
        self.write_signature()
        self.lines[2] += " changed"
        self.save_lines()
        with self.assertRaises(BadSignatureError):
            verify_keypair(self.public_value, self.probe, self.path)

    def test_rejects_changed_global_signature(self):
        self.write_signature()
        self.lines[3] = b64(b"x" * 64)
        self.save_lines()
        with self.assertRaises(BadSignatureError):
            verify_keypair(self.public_value, self.probe, self.path)

    def test_rejects_unsupported_signature_algorithm(self):
        self.write_signature()
        self.lines[1] = b64(b"XX" + base64.b64decode(self.lines[1])[2:])
        self.save_lines()
        with self.assertRaisesRegex(ValueError, "Unsupported updater signature"):
            verify_keypair(self.public_value, self.probe, self.path)


if __name__ == "__main__":
    unittest.main()
