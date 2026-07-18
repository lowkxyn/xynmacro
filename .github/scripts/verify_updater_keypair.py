"""Verify that Tauri's updater public key matches the configured private key."""

import base64
import hashlib
import os
import sys
from pathlib import Path

from nacl.signing import VerifyKey


def decode_minisign_signature(path):
    encoded = Path(path).read_text(encoding="utf-8").strip()
    try:
        text = base64.b64decode(encoded, validate=True).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        text = encoded
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(lines) < 2 or not lines[0].startswith("untrusted comment:"):
        raise ValueError("Tauri produced an invalid updater signature file")
    packet = base64.b64decode(lines[1], validate=True)
    if len(packet) != 74:
        raise ValueError("Tauri produced an invalid updater signature packet")
    return packet


def decode_minisign_public_key(value):
    value = value.strip()
    try:
        decoded = base64.b64decode(value, validate=True)
    except ValueError:
        decoded = value.encode("utf-8")
    if len(decoded) == 42:
        return decoded
    try:
        lines = [
            line.strip()
            for line in decoded.decode("utf-8").splitlines()
            if line.strip() and not line.startswith("untrusted comment:")
        ]
    except UnicodeDecodeError as error:
        raise ValueError("TAURI_UPDATER_PUBLIC_KEY is not a Minisign public key") from error
    if len(lines) != 1:
        raise ValueError("TAURI_UPDATER_PUBLIC_KEY is not a Minisign public key")
    packet = base64.b64decode(lines[0], validate=True)
    if len(packet) != 42:
        raise ValueError("TAURI_UPDATER_PUBLIC_KEY is not a Minisign public key")
    return packet


def main():
    if len(sys.argv) != 3:
        raise SystemExit("usage: verify_updater_keypair.py PROBE SIGNATURE")
    public_value = os.environ.get("TAURI_UPDATER_PUBLIC_KEY", "").strip()
    public_packet = decode_minisign_public_key(public_value)

    signature_packet = decode_minisign_signature(sys.argv[2])
    if public_packet[:2] not in (b"Ed", b"ED"):
        raise ValueError("Unsupported updater public-key algorithm")
    if signature_packet[:2] not in (b"Ed", b"ED"):
        raise ValueError("Unsupported updater signature algorithm")
    if public_packet[2:10] != signature_packet[2:10]:
        raise ValueError("Updater public and private keys have different key IDs")

    probe = Path(sys.argv[1]).read_bytes()
    message = (
        hashlib.blake2b(probe, digest_size=64).digest()
        if signature_packet[:2] == b"ED"
        else probe
    )
    VerifyKey(public_packet[10:]).verify(message, signature_packet[10:])
    print("Updater signing keypair verified")


if __name__ == "__main__":
    main()
