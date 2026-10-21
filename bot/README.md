# XynMacro 2.0 developer guide

XynMacro is a Windows Tauri app with an HTML/JavaScript interface, a Rust shell,
and a Python automation engine. The shell manages windows, engine startup, and
updates. The engine detects game state and sends input through Windows APIs.

## Folders

| Path | Contents |
| --- | --- |
| `src/` | HTML, CSS, JavaScript, and UI tests |
| `src-tauri/` | Rust shell, Tauri configuration, and native app handling |
| `python/` | Automation engine, local HTTP API, calibration defaults, and Python tests |
| `../setup/` | Branded Windows setup app (kept outside `bot/`) |
| `scripts/` | Packaging, local tester builds, and release checks |

## Development

Install Node.js 20+, Rust with the MSVC toolchain, and Python 3.12+.
From `bot/`, run:

```powershell
npm install
py -m pip install -r python/requirements.txt
npm run dev
```

Development runs the Python engine from source. Close any installed copy first
so the two instances do not compete for global hotkeys or game input.

## Tests

From the repository root:

```powershell
cd bot
npm test
py -m pytest python/tests -q
```

## Local tester build

From `bot/`, run:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/build_local_tester.ps1
```

The script packages the engine, builds an unsigned installer with automatic updates
disabled, and wraps it in the branded setup app. It also remaps source paths and
checks the app and setup executables for local user-folder paths. The resulting
installers are for local testing only.

Public releases come from the GitHub release workflow in
[release.yml](../.github/workflows/release.yml). It runs checks, packages the app,
and signs updater artifacts using repository configuration and Actions secrets.
