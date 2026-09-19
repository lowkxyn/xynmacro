# XynMacro developer guide

## 1.8 beta

The beta adds respawn settling delay, mouse-button compatibility, optional Health/Ki
restart-after-hit, main-loop performance controls and a reorganised interface.
Restart-after-hit is off by default pending actual DBOG acceptance.

Training contains the queue, startup, recovery and after-run behaviour. Input & Display
contains mouse choice, window mode and hotkeys. Minigames groups modes and timing;
Diagnostics contains logs and measured loop rate. Calibration remains available.

Beta 2 adds workspace zoom (Input & Display, or Ctrl + / minus / 0), removes the
W spain startup decoration, and adds optional Discord notifications under Training.
Webhook URLs are encrypted for the current Windows user and never included in state
snapshots. Notifications are off until configured. Final outcomes and optional progress
messages contain only status, elapsed time and the current trait.

Shutdown now counts down inside XynMacro for 60 seconds. Cancel is available in normal
and compact views; Stop, another Start or closing the app also cancels before the Windows
request is sent. See [beta 2 decisions and remaining game work](docs/1.8-beta2-plan.md)
for limits and the deferred chat, Ki recharge, race healing and weight-equipping ideas.

Tagged prereleases install as **XynMacro Beta**, using the separate
`com.htcgc.xyn.beta` app-data profile. Do not run beta and stable at the same time.
The beta updater is pinned to its tag; install later beta versions manually. Stable
users retain the normal latest-release feed. This separation is applied by the CI
overlay; a plain local build uses the base configuration.

See [audit and acceptance](docs/1.8-beta-audit.md) and the
[portable style guide](docs/style-kit/STYLE-GUIDE.md). The latter includes a copyable
design brief, scoped CSS and a static layout/HUD reference for other projects.

XynMacro is a Windows Tauri application with a WebView2 frontend and a Python computer-
vision sidecar. The sidecar captures Roblox with MSS/OpenCV, detects game state, and sends
input through Windows APIs. The shell owns the window, sidecar lifecycle, updates, and IPC.

## Structure

| Path | Responsibility |
| --- | --- |
| `src/` | HTML, CSS, JavaScript, frontend tests |
| `src-tauri/` | Rust shell, Tauri configuration, native window and update handling |
| `python/xynmacro_core.py` | Screen detection, automation, settings, logs, and local HTTP API |
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
