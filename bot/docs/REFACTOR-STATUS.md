# Refactor status — 1.8.0-beta.4 (19 September 2026)

## Done and wired
- Frontend split: `bot/src/appearance-controls.js` (themes, presets, custom colour, animated background), `notification-controls.js`, `shutdown-controls.js`. `index.html:1092-1095` loads all three before `main.js`; `main.js` builds one instance of each (`XynMacroAppearanceControls.create` at `main.js:292`, shutdown/notification at `main.js:613-614`) and delegates at `main.js:315`, `1813-1814`, `2392-2393`. `showToast` is passed as a lazy wrapper because `main.js` defines it further down.
- Python settings: `bot/python/settings_schema.py` holds one 45-field table plus `default_settings` and `snapshot_settings`; `xynmacro_core.py` uses it for `DEFAULT_USER_SETTINGS` (`:795`) and both config snapshots (`:4625`, `:6877`).

## Partial on purpose
- Six compatibility settings stay in `COMPATIBILITY_DEFAULTS` (`xynmacro_core.py:798`) and merge into the defaults afterwards, so the user-visible total is 51 keys, not 45.
- Validation, `load_master_config`, `save_master_config`, `_ui_apply_setting` and `reset_user_settings_to_defaults` remain in the core, which is still about 9,150 lines. `main.js` is still about 3,600 lines.

## Not done
No TypeScript exists in the project: no `.ts` files, no `tsconfig`. A full conversion, removing the 58 inline `on*` handlers in `index.html`, and stricter development authentication are separate follow-ups.

## Verified vs untested
Verified earlier and not re-run for this note: 62 frontend tests (including `run-controls.test.mjs` and `appearance-controls.test.mjs`), the Python suites including `test_settings_schema.py` and `test_compatibility.py`, a headless Edge smoke pass, and a Fable review that found no confirmed introduced regression.

Untested: live gameplay and native installed-app use. Beta.4 is now published: all release stages passed, including 334 Python tests plus 55 subtests, 62 frontend tests, 5 Rust tests, dependency audits and packaged-backend checks. Existing Rust maintenance/unsound/yanked warnings remain documented. The downloaded installer matched its published size and SHA-256, and updater metadata/signature matched. Stable remains v1.7.3. The installer was not launched locally.

[Download beta.4](https://github.com/lowkxyn/xynmacro/releases/tag/v1.8.0-beta.4). It is ready to install and try when convenient, with beta status and live-game testing limits still applying.

## When you do test
Follow the checklist in `CHANGES-AND-TESTING.md`: layout, zoom and persistence first, then one gameplay change at a time (click mode, respawn wait, hit-restart, scan limit). Run a single instance; beta and stable are separate installs and should not run together. Chat auto-hide is deferred at your request. Ki charging to dark blue, Majin/Bio-Android healing and re-equipping weights are not implemented and need game evidence first.
