"""Verify that Tauri's updater public key matches the configured private key."""

import base64
import hashlib
import os
import sys
from pathlib import Path

from nacl.signing import VerifyKey


def decode_minisign_signature(path):
    encoded = Path(path).read_text(encoding="utf-8").strip()
    text = base64.b64decode(encoded, validate=True).decode("utf-8")
    lines = text.splitlines()
    if (len(lines) != 4 or not lines[0].startswith("untrusted comment:")
            or not lines[2].startswith("trusted comment: ")):
        raise ValueError("Tauri produced an invalid updater signature file")
    packet = base64.b64decode(lines[1], validate=True)
    global_signature = base64.b64decode(lines[3], validate=True)
    if len(packet) != 74 or len(global_signature) != 64:
        raise ValueError("Tauri produced an invalid updater signature packet")
    return packet, lines[2][len("trusted comment: "):].encode("utf-8"), global_signature


def decode_minisign_public_key(value):
    # Tauri decodes outer base64, then a two-line Minisign document. Accepting
    # a bare packet or raw document here would approve an unusable release key.
    try:
        lines = base64.b64decode(value.strip(), validate=True).decode("utf-8").splitlines()
    except (ValueError, UnicodeDecodeError) as error:
        raise ValueError("TAURI_UPDATER_PUBLIC_KEY must be the base64 Tauri public-key document") from error
    if len(lines) != 2 or not lines[0].startswith("untrusted comment:"):
        raise ValueError("TAURI_UPDATER_PUBLIC_KEY must be the base64 Tauri public-key document")
    packet = base64.b64decode(lines[1], validate=True)
    if len(packet) != 42:
        raise ValueError("TAURI_UPDATER_PUBLIC_KEY is not a Minisign public key")
    return packet


def verify_keypair(public_value, probe, signature_path):
    public_packet = decode_minisign_public_key(public_value)
    signature_packet, trusted_comment, global_signature = decode_minisign_signature(signature_path)
    if public_packet[:2] not in (b"Ed", b"ED"):
        raise ValueError("Unsupported updater public-key algorithm")
    if signature_packet[:2] not in (b"Ed", b"ED"):
        raise ValueError("Unsupported updater signature algorithm")
    if public_packet[2:10] != signature_packet[2:10]:
        raise ValueError("Updater public and private keys have different key IDs")

    message = (
        hashlib.blake2b(probe, digest_size=64).digest()
        if signature_packet[:2] == b"ED"
        else probe
    )
    key = VerifyKey(public_packet[10:])
    key.verify(message, signature_packet[10:])
    key.verify(signature_packet[10:] + trusted_comment, global_signature)


def main():
    if len(sys.argv) != 3:
        raise SystemExit("usage: verify_updater_keypair.py PROBE SIGNATURE")
    public_value = os.environ.get("TAURI_UPDATER_PUBLIC_KEY", "").strip()
    verify_keypair(public_value, Path(sys.argv[1]).read_bytes(), sys.argv[2])
    print("Updater signing keypair verified")


if __name__ == "__main__":
    main()
