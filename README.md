# XynMacro

XynMacro automates training in **Dragon Ball Online Generations** (DBOG, now Dragon
Generations) on Roblox. It plays every Hyperbolic Time Chamber and Gravity Chamber
minigame, manages GC gravity, restores health with Senzu Beans, and advances through a
training order you choose.

Windows 10/11 only. Roblox can be on any monitor; detection uses a 1920×1080 reference.

## Download

Download the latest `XynMacro_<version>_x64-setup.exe` from
[GitHub Releases](https://github.com/lowkxyn/xynmacro/releases/latest).

Launch XynMacro normally. If its global hotkeys or game inputs are blocked, close it and
try **Run as administrator**. The installer includes the app and automation engine; Python,
Node.js, and Rust are not required.

## Features

- Health, Agility, Physical Damage, Ki Control, and Ki Damage automation
- HTC and GC support, including starts from gameplay, menus, or an active minigame
- Per-stat progression tracking and automatic training-order advancement
- Auto-Senzu with full-bean, half-bean, and preference-order modes
- Optional bounded error recovery with confirmed GC respawn and configurable walk-out
- GC gravity control from 0G to 100G, with optional 0G fallback when beans run out
- Live telemetry, session time, saved logs, and progression status
- Monitor-aware capture, calibration tools, and display-specific 1080p switching
- Configurable hotkeys, countdown, timing, training order, and safe power management
- Classic and Aero layouts, eight colour themes, backgrounds, and compact pill mode
- Cryptographically signed automatic updates and owner announcements

## Quick start

1. Open Roblox and enter HTC or GC training.
2. If Roblox is not 1920×1080, use **Set 1080p** on the Dashboard. Only the display
   containing Roblox is changed, and XynMacro can restore its previous mode.
3. Choose a training order under **Controls**.
4. Review Auto-Senzu and GC gravity under **Controls**.
5. Press **Start** or the default **F6** hotkey.

| Default hotkey | Action |
| --- | --- |
| F6 | Start or stop |
| L | Skip the current stat |
| U | Pause or resume |

Hotkeys can be changed under **Controls → Hotkeys**.

## Auto-Senzu

XynMacro reacts only when the HP bar enters its red state. It opens the required menus,
uses the configured slot, refills that slot from Inventory, confirms health recovery, and
returns to the interrupted task. Inventory search supports scrolling and remembers a
confirmed item position until the game UI changes.

Bean preference can be set to full only, half only, full then half, or half then full. If
no allowed beans remain, Auto-Senzu disables itself for that run instead of selecting an
unwanted item. In GC, **Set gravity to 0G when out of beans** can prevent further drain.

## Appearance

Open **Settings → Interface → UI style** to switch between:

- **Classic**, the dense desktop layout
- **Aero**, a floating glass dock with roomier cards

Themes recolour Classic. Aero has its own violet and cyan glass palette. Appearance,
window size and position, macro settings, and calibration are restored by **Factory reset**.
Deleting `macro_config.json` also restores the shipped defaults on the next launch.

## Settings and logs

Installed settings, calibration, and logs are stored under:

```text
%APPDATA%\com.htcgc.xyn\json\
```

The exact folder is shown under **Settings → Configuration**. Session logs rotate
automatically and can be copied to your chosen save folder from the Logs page.

## Updates and announcements

Automatic update checks are enabled by default. Release packages are verified with the
embedded updater public key before installation. **Later** postpones a release until the
next launch or manual check; **Don't show again** hides that version while keeping manual
update access under Settings.

The title-bar bell shows owner announcements such as game compatibility notices. Opening
an announcement marks it as read on that PC.

## Windows warnings

XynMacro is not Authenticode code-signed, so Windows SmartScreen may show **Windows
protected your PC** on first launch. Choose **More info → Run anyway** if you downloaded it
from this repository.

Some antivirus products also flag `XynMacro-core.exe`, the bundled Python automation engine.
PyInstaller packaging, continuous screen capture, synthetic input, and global hotkeys are
all normal for this macro but resemble behaviours antivirus heuristics monitor. The source
is public here. Runtime communication stays on `127.0.0.1`; external requests are limited
to GitHub releases and `announcements.json`.

## Troubleshooting

| Problem | Fix |
| --- | --- |
| Backend not running or blank window | Restore quarantined `XynMacro-core.exe` if antivirus removed it, or reinstall XynMacro |
| Hotkeys or game input do not work | Close XynMacro and try **Run as administrator** |
| Roblox is not detected | Use the standard Windows Roblox client and keep its window open before starting |
| Clicks land in the wrong place | Set the Roblox display to 1920×1080, then recalibrate the affected points or region |
| Ki clicks early or late | Adjust **Ring Brightness Min**, **Ring Pixel Min**, or latency compensation under Tuning |
| Auto-Senzu stops the run | Check the configured slot and allowed bean type, then review the saved session log |

## License

Copyright © 2026 Xyn. Source-available for personal use; redistribution and commercial use
require permission. See [LICENSE](LICENSE).
