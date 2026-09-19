# XynMacro: changes and checks for later

Published build: [1.8.0-beta.3](https://github.com/lowkxyn/xynmacro/releases/tag/v1.8.0-beta.3).
The refactor described below is newer source on `feature/1.8-beta`; it is not in that installer. Stable remains 1.7.3. Nothing needs testing right now.

## What changed across the 1.8 betas

- **Faster training restart, optional and off by default.** Health and Ki can register a hit, wait briefly, press Tab and reopen the same trait. The delay is adjustable. Confirm that the game actually credits each hit before relying on the speed-up.
- **Left/right click compatibility.** Input & Display offers Left button, Right button and Follow Windows. It applies to game menus and recovery as well as minigames. A third-party remapper may need an explicit Left or Right choice.
- **Respawn settling.** Wait after respawn accepts 0–30 seconds before walking; walking duration remains separate. This is a configurable wait, not automatic proof that the character has loaded. Stop/focus/error paths release held input.
- **Performance controls.** Adjustable scan limit, loop measurements and reduced visual effects. Loop measurements include waits and recovery; they are not Roblox FPS. Slower-PC improvement still needs a real comparison.
- **Layout cleanup.** Reorganized settings, clearer labels, responsive trait ordering and a corrected resize button. Workspace zoom offers 80/90/100/110/125%, buttons and Ctrl +/-/0, remembered between launches. It changes the macro workspace; Roblox and the compact HUD stay their own size.
- **Removed “W spain.”** Removed the title tag and startup celebration. Respawn walking is still available.
- **Discord notifications.** Optional finished/stopped/error messages and progress intervals. Disabled until configured and enabled; Send test is separate. The webhook is encrypted for the Windows user and kept out of normal settings/diagnostics. Messages exclude screenshots, chat, logs and mentions.
- **Cancellable shutdown.** XynMacro owns a 60-second countdown with Cancel in expanded and compact views. Stop, another Start or an orderly app close cancels before dispatch. After Windows receives the request, cancellation is no longer promised.
- **Safety fixes.** Input release, invalid command/theme handling, settings rollback and webhook concurrency fixes. Beta.3 also fixes the Rust TLS advisory that blocked beta.2. Existing dependency warnings remain documented.

The reusable appearance kit is in [style-kit/STYLE-GUIDE.md](style-kit/STYLE-GUIDE.md), with CSS and a sample page beside it.

## Refactor after beta.3

Notification and shutdown controls now live in separate frontend modules with their own tests. One settings table now supplies the defaults, saved settings and settings sent to the interface. This reduces duplicated mappings without changing the setting names, default values, game detection or timing.

Settings validation, migration and reset side effects remain explicit in the Python core. A complete TypeScript conversion, removal of inline handlers and stricter development authentication are separate follow-ups, not completed work.

Refactor verification: 334 Python tests plus 55 subtests, 53 frontend tests, and headless Edge checks passed. Both snapshot forms were also compared with the shipped implementation using different values for all 45 mapped fields. The sidecar rebuilt successfully and its executable archive contains the new settings module. The rebuilt sidecar was not launched, so this is not a fresh packaged-startup test. No installed app was replaced.

## Quick check when you have time

Run only one macro instance, including stable and beta.

- [ ] Try both themes, a narrower window and 80%/125% zoom. Labels, buttons and trait ordering should stay readable. Drag a trait, then check keyboard reordering too.
- [ ] Close and reopen the app. Zoom, trait order, click choice and respawn delay should be remembered.
- [ ] Confirm the resize button works and no “W spain” tag/celebration appears.
- [ ] Start a short normal session with optional hit-restart disabled. Confirm existing training still works. Press Stop and confirm no keys/buttons remain held.

## Focused gameplay checks, one change at a time

- [ ] With the actual remapper enabled, try the suitable click choice. Check trait selection, a Ki circle and a recovery/menu click. A successful UI click alone does not prove game clicks work.
- [ ] Set a longer respawn wait, then observe a normal recovery. W should begin after the selected wait and release after its duration. Check Stop during recovery and focus loss during a hold.
- [ ] Try Health restart first, then Ki restart separately. Check credited progress before and after Tab/re-entry, wrong-trait selection and missed hits. If progress is lost, turn restart off or increase the registration wait.
- [ ] On the slower PC, compare the same short training task with one scan-limit change at a time. Note misses and responsiveness as well as loop measurements. Faster scanning is not automatically more accurate.

## Optional notification and shutdown checks

- [ ] Use a webhook for a channel you control. Save with routine messages disabled, then Send test. Confirm it reaches the right channel; do not share the URL in screenshots or reports.
- [ ] Edit notification controls and wait for a status refresh. Unsaved choices should stay put. Save, reopen the app, then check the choices persisted. Remove should show Not configured.
- [ ] If using routine messages, check one short run and a manual Stop. Expect only outcome, duration and trait information. Progress can be delayed during recovery.
- [ ] Only when deliberately ready, test the shutdown countdown and **cancel well before zero**. Check expanded and compact Cancel on separate attempts. Actual power-off is not needed for acceptance; leave automatic shutdown disabled until you want to test it.

Development checks use simulated commands, synthetic webhook data and temporary settings. They do not establish real gameplay success, real Discord delivery or native installed-app behavior.

## Still waiting for game evidence

Chat auto-hide is deferred as requested. Ki charging to dark blue/max, Majin/Bio-Android healing and re-equipping weights are not implemented. They need actual controls and visible state cues; the claimed dark-blue benefit is still unverified. Future controls belong near Senzu and must prevent overlapping recovery actions.

For a bug report, note the build, relevant setting, expected result and actual result. A cropped screenshot or short recording helps, without webhook URLs or private chat.
