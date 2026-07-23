# Changelog

All notable changes to XynMacro. This file is generated from the in-app
"What's new" notes by `scripts/changelog.mjs` — edit those, not this file.

## 1.2.0

### Fixes
- Settings toggles and the v1/v2 mode buttons now always respond; a failed change reports why instead of doing nothing.
- The resolution heads-up no longer starts the macro on its own — at zero the Continue button simply becomes clickable.
- Removed the broken-image icon that flashed in the scan previews before the first capture.

### Window handling
- A minimized Roblox is recognized instead of read as closed; Start restores it, and when it isn't running the button says so.
- Fullscreen On Start puts a windowed Roblox back to fullscreen so the scan regions line up (toggle in Settings).
- Set 1080p now asks you to confirm and reverts automatically after 10 seconds if you don't — the timer runs in the backend so an unreadable screen still recovers.

## 1.1.0

### Error Recovery
- Added bounded retry-after-error controls with a configurable retry limit, recovery method, and walk duration.
- GC death is detected directly, the Respawn dialog is confirmed before clicking, and completed stats are rechecked after recovery.
- Starting the macro while already on GC's death screen is detected before any menu input is sent.

### Safety
- Manual Stop never retries, stale monitor input is stopped before recovery, and after-run failure actions wait until retries are exhausted.
- Standardized remaining internal module, browser-state, and build names under XynMacro with a one-time local preference migration.

## 1.0.5

### Interface
- Added After Run choices for Main Menu, closing Roblox, staying in GC at 0G, and optional PC shutdown.
- Added Support Diagnostics with a live labelled vision preview and copyable environment report.

### Fixes
- Training Mode is detected during a run, so minigame input stops and an unfinished stat resumes safely.
- Manually skipped stats now mark the order incomplete and never trigger successful after-run actions.

## 1.0.4

### Interface
- Added the W spain titlebar tag and one-time launch celebration.

## 1.0.3

### Fixes
- Removed a stray empty notification pill that could briefly appear in the top-right corner.

## 1.0.2

### Fixes
- Auto-Senzu no longer misfires on startup (the stray Tab press right after a category begins).
- In-game clicks now land immediately without needing a mouse wiggle first.
- Aero style: hover tooltips no longer render behind panels.

## 1.0.1

### Fixes
- Notifications no longer overlap the window buttons in the top-right corner.
- Fixed the 1080p monitor switch failing on secondary monitors (display error -2).

## 1.0.0

### Training automation
- Automates Health, Agility, Physical Damage, Ki Control, and Ki Damage in HTC and GC.
- Tracks each stat’s progression and advances through your chosen training order.
- Starts safely from gameplay, the Game Menu, the Training menu, or an active minigame.

### Auto-Senzu and gravity
- Detects red HP, consumes and refills Senzu Beans, and resumes the interrupted stat.
- Supports full beans, half beans, and configurable preference order.
- Can raise GC gravity automatically and return it to 0G when beans run out.

### Desktop app
- Classic and Aero interface styles, eight colour themes, animated backgrounds, and compact pill mode.
- Live telemetry, session logs, calibration tools, configurable hotkeys, and monitor-aware 1080p switching.
- Signed automatic updates, release notes, and owner announcements through the title-bar bell.
