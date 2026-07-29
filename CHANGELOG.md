# Changelog

All notable changes to XynMacro. This file is generated from the in-app
"What's new" notes by `scripts/changelog.mjs` — edit those, not this file.

## 1.6.0

### Ki detection: Adaptive Brightness (beta)
- New toggle in Tuning under Ki Detection & Timing, off by default. Turn it on if Ki Control and Ki Damage never click while every other stat works fine.
- The dot detector finds the black "1" inside the orange dot by comparing it against a fixed brightness value, and that value had no headroom — measured against known-good dots, a 2% lift anywhere in your display pipeline stops it detecting entirely. BIOS "game mode" and vibrance profiles, HDR, a monitor colour profile and Night Light all do that, and every other stat keeps working, so it looks like Ki alone is broken.
- With the toggle on, the "1" is measured against the dot's own brightness instead of a fixed number, which holds through those shifts. Tested against known-good dots at brightness lifts up to 1.5x, where the fixed value reads nothing at all.
- Marked beta because the false-positive side has not been tested against a wide set of non-Ki screens yet. It is off unless you turn it on, and nothing changes if you leave it alone.

## 1.5.1

### Fixes
- Fixed the new crash notice never appearing for the most common kind of crash. If the app itself died, the shutdown was being recorded as if you had closed it normally, so the next launch said nothing and the crash log was not offered.

## 1.5.0

### Catching problems before you do
- The app now checks its own buttons on startup. If your browser engine ever blocks them again the way it did in 1.3.1, you get a red banner saying so instead of a window where nothing happens.
- If XynMacro closes unexpectedly, the next launch says so and keeps that session's log ready in Report a bug — the log the crash is actually in, which no report could reach before.

### Display scaling
- Start now warns when Windows display scaling is not 100%. XynMacro reads screen pixels directly, so at 125% or 150% every click lands short of the button — and the resolution readout looked perfectly fine the whole time.
- The resolution pill shows the scaling instead of a tick when it is not 100%.

### Windowed Mode
- On a display too small for a 1920x1080 window, Windowed Mode no longer just refuses. It warns that detection will be scaled and lets you continue if you want to, the same way the non-1080p warning works.

### Debug HUD fix
- Fixed the docked HUD breaking the very detection it is there to check. The macro reads screen pixels, so the boxes drawn over Roblox were being scanned as if they were part of the game.
- Starting a run now pops the HUD out and parks it clear of the game window, and it cannot be re-docked until you stop. If it ends up over Roblox anyway, it says so in red.

## 1.4.0

### Report a bug
- New Report a bug screen in Settings and in the Logs diagnostics, so a problem can actually be diagnosed instead of guessed at.
- You choose what to include with tick boxes — PC specs, display and scaling, the Roblox window, macro settings, recent log — and you can read the exact text before anything leaves your PC.
- Copy instead puts the report on your clipboard to share however you like. It stays on your PC and identifies nothing about you.
- Posting to GitHub is optional and opens a pre-filled issue in your browser. Because that is public and permanent it asks you to confirm first, after a pause to read the warning, and it takes three deliberate clicks.
- Nothing is ever sent automatically. Your Windows username is replaced with a placeholder everywhere it appears, including in what you type.

## 1.3.1

### Fixes
- Fixed every button, toggle and window control being dead. A Microsoft Edge WebView2 update began enforcing a stricter content security rule that blocked the way most of the UI was wired, so clicks silently did nothing — the sidebar kept working because it is wired differently. This affected all recent versions, not just the newest.
- Minimize, maximize and close now report a problem instead of failing silently.
- Dragging the window by its title no longer logs a permission error.

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
