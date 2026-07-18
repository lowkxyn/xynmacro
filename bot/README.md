# XynMacro developer guide

XynMacro is a Windows Tauri application with a WebView2 frontend and a Python computer-
vision sidecar. The sidecar captures Roblox with MSS/OpenCV, detects game state, and sends
input through Windows APIs. The shell owns the window, sidecar lifecycle, updates, and IPC.

## Structure

| Path | Responsibility |
| --- | --- |
| `src/` | HTML, CSS, JavaScript, frontend tests |
| `src-tauri/` | Rust shell, Tauri configuration, native window and update handling |
| `python/xmacro_core.py` | Screen detection, automation, settings, logs, and local HTTP API |
| `python/defaults/` | Calibration seeded into app data on first launch |
| `python/tests/` | Python unit and regression tests |
| `scripts/` | Sidecar packaging, icon generation, and release smoke checks |

The shell and sidecar communicate through a loopback HTTP port written to a short-lived
port file. Installed data is stored in `%APPDATA%\com.htcgc.xyn\json\`; development data
uses `python/json/`.

## Prerequisites

- Node.js 20+
- Rust stable with the MSVC toolchain
- Python 3.12+
- Python packages from `python/requirements.txt`

## Development

```powershell
npm install
python -m pip install -r python/requirements.txt
npm run dev
```

Development runs the Python sidecar from source. Avoid running a second copy while a built
XynMacro instance is open because both register the same global hotkeys.

## Tests

Run these from `bot/`:

```powershell
python -m pytest python/tests -q
npm test
Push-Location src-tauri; cargo test --quiet; Pop-Location
```

## Local release build

```powershell
python -m pip install pyinstaller
powershell -ExecutionPolicy Bypass -File scripts/build_sidecar.ps1
npm run tauri build -- --bundles nsis --config src-tauri/tauri.local.conf.json
```

The NSIS installer is written to
`src-tauri/target/release/bundle/nsis/XynMacro_<version>_x64-setup.exe`.
The local overlay disables updater artifacts, so a signing key is not required and the
result is suitable for local testing only. Signed public releases are built by GitHub
Actions with the updater endpoint and public key injected from repository configuration.

## GitHub release

Pushing a tag matching the version in both `src-tauri/tauri.conf.json` and
`src-tauri/Cargo.toml` starts `.github/workflows/release.yml`. The workflow:

1. validates the tag and versions;
2. runs Python, frontend, and Rust tests;
3. freezes the sidecar;
4. builds the NSIS installer and updater archive;
5. signs updater artifacts and publishes `latest.json`;
6. publishes a SHA-256 installer checksum.

The repository must define `TAURI_SIGNING_PRIVATE_KEY` and
`TAURI_SIGNING_PRIVATE_KEY_PASSWORD` as Actions secrets and
`TAURI_UPDATER_PUBLIC_KEY` as an Actions variable. Never commit the private key or password.
