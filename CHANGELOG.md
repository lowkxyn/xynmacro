# Changelog

All notable changes to XynMacro. This file is generated from the in-app
"What's new" notes by `scripts/changelog.mjs` — edit those, not this file.

## 1.3.0

### Windowed Mode
- New Windowed Mode sizes Roblox to an exact 1920x1080 window, centred and clear of the taskbar, so the scan regions line up without changing your desktop resolution (toggle in Settings, off by default).
- Size Window Now does it immediately, so you can check the scan boxes without starting a run.
- When both Windowed Mode and Fullscreen On Start are on, Windowed Mode wins — it no longer gets undone by fullscreen.
- A display too small to fit a 1920x1080 window blocks Start with a message instead of scanning a skewed screen.

### Debug HUD
- New Debug HUD sits over Roblox showing live window size, scaling, run state and every scan region drawn where the macro actually looks. It's click-through, so it never eats a click meant for the game.
- It flags the real problem directly: if the window isn't 16:9 the scaling turns red and tells you the regions are skewed.
- Pop out moves the HUD to its own draggable window for a second monitor.

## 1.2.1

### Fixes
- Fixed the buttons going dead after the app had been open for a long time — backend requests no longer run on the window's own thread, so a single slow one can't block every click behind it.
- The status and log refreshes skip a tick instead of stacking up when the backend is slow to answer.
- A scan preview left running now stops when you leave Calibration instead of capturing in the background forever.

### Security
- The key that lets XynMacro talk to its own backend is no longer visible in the Windows process list — it's handed over privately at startup instead.
- Backend requests refuse to follow redirects, so that key can only ever be sent to XynMacro's own local backend.

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
