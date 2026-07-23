import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';

const sourceRoot = new URL('./', import.meta.url);
const repoRoot = new URL('../../', import.meta.url);

const [html, main, styles, readme, packageText] = await Promise.all([
  readFile(new URL('index.html', sourceRoot), 'utf8'),
  readFile(new URL('main.js', sourceRoot), 'utf8'),
  readFile(new URL('styles.css', sourceRoot), 'utf8'),
  readFile(new URL('README.md', repoRoot), 'utf8'),
  readFile(new URL('package.json', new URL('../', sourceRoot)), 'utf8'),
]);
const packageMetadata = JSON.parse(packageText);

test('XynMacro branding migrates and removes legacy preference keys', () => {
  assert.doesNotMatch(main, /X(?:Macro)|x(?:macro)-/);
  assert.match(main, /const legacyPrefix = \['x', 'macro-'\]\.join\(''\)/);
  assert.match(main, /XynMacroPreferenceMigration\.migratePreferences/);
  assert.match(html, /preference-migration\.js[\s\S]*main\.js/);
});

test('custom switches and segmented controls expose their state', () => {
  const toggles = [...html.matchAll(/<button\b[^>]*class="toggle(?: active)?"[^>]*>/g)].map((match) => match[0]);
  assert.equal(toggles.length, 12);
  for (const toggle of toggles) {
    assert.match(toggle, /role="switch"/);
    assert.match(toggle, /aria-checked="(?:true|false)"/);
    assert.match(toggle, /aria-label="[^"]+"/);
  }

  for (const id of ['agilityModeSeg', 'healthModeSeg', 'kiV8ModeSeg', 'uiStyleSeg']) {
    assert.match(html, new RegExp(`id="${id}"[^>]*role="group"[^>]*aria-label=`));
  }
  assert.match(main, /setAttribute\('aria-checked', enabled \? 'true' : 'false'\)/);
  assert.match(main, /setAttribute\('aria-pressed', selected \? 'true' : 'false'\)/);
});

test('visible form fields and titlebar icon buttons have accessible names', () => {
  const formFields = [...html.matchAll(/<(?:input|select)\b[^>]*>/g)]
    .map((match) => match[0])
    .filter((tag) => !/type="(?:checkbox|file|hidden)"/.test(tag));
  assert.ok(formFields.length > 10);
  for (const field of formFields) assert.match(field, /aria-label="[^"]+"/);

  const iconButtonClasses = ['tb-announcement', 'tb-ontop', 'tb-compact', 'tb-minimize', 'tb-maximize', 'tb-close'];
  for (const className of iconButtonClasses) {
    assert.match(html, new RegExp(`<button[^>]*${className}[^>]*aria-label="[^"]+"`));
  }
  assert.match(html, /id="sidebarResize"[^>]*role="separator"[^>]*tabindex="0"/);
  assert.match(main, /sidebarHandle\.addEventListener\('keydown'/);
});

test('dialogs are labelled, modal, keyboard dismissible, and focus-managed', () => {
  const dialogs = [...html.matchAll(/<div\b[^>]*role="dialog"[^>]*>/g)].map((match) => match[0]);
  assert.equal(dialogs.length, 7);
  for (const dialog of dialogs) {
    assert.match(dialog, /aria-modal="true"/);
    assert.match(dialog, /aria-label="[^"]+"/);
    assert.match(dialog, /tabindex="-1"/);
  }

  for (const closeAction of [
    'closePalette()',
    'closeShortcuts()',
    '_cancelResolutionWarning()',
    'dismissWelcome()',
    'closeChangelog()',
    'closeAnnouncement()',
  ]) {
    assert.ok(main.includes(closeAction), `Escape wiring should include ${closeAction}`);
  }
  assert.match(main, /e\.key === 'Tab' && _trapModalFocus\(e\)/);
  assert.match(main, /_restoreModalFocus\(overlay\)/);
});

test('navigation, segmented Undo, and Senzu warning wiring stay connected', () => {
  for (const view of ['dashboard', 'controls', 'tuning', 'ki', 'logs', 'settings']) {
    assert.ok(main.includes(`run: () => openView('${view}')`));
  }
  assert.match(main, /function openView\(target\)[\s\S]*?_resetScroll\(\)/);

  for (const key of ['agility_mode', 'health_mode', 'ki_v8_mode']) {
    assert.ok(main.includes(`_pushUndo('${key}', previous, mode)`));
  }
  assert.match(main, /operationalStop === 'empty' \? 'warn' : 'err'/);
  assert.match(styles, /\.notif-toast\.warn\{/);
});

test('announcement failures and empty feeds have different messages', () => {
  assert.match(main, /let _announcementError = false/);
  assert.ok(main.includes("'Messages unavailable'"));
  assert.ok(main.includes("'No announcements'"));
  assert.ok(main.includes("'Announcements unavailable'"));
});

test('public and in-app copy matches actual admin, update, and theme behavior', () => {
  assert.doesNotMatch(html, /Run as Administrator<\/span>|Required for keyboard hooks|Updates download and install only while the macro is idle/);
  assert.match(html, /Administrator access is optional/);
  assert.match(html, /Downloads can continue while the macro runs; installation waits/);
  assert.match(readme, /eight colour themes/);
  assert.match(readme, /manual\s+update access under Settings\./);
  assert.doesNotMatch(readme, /update\s+access under Settings and the title-bar bell/);
});

test('the in-app changelog includes the shipped version', () => {
  assert.ok(main.includes(`{ version: '${packageMetadata.version}'`));
  assert.match(main, /\{ version: '1\.0\.4'[\s\S]*?W spain titlebar tag/);
});

test('start controls stay disabled until Roblox is detected', () => {
  assert.match(main, /let _gameWindowFound = false/);
  assert.match(main, /_gameWindowFound = !!state\.game_window\?\.found/);
  // A minimized Roblox reports a 0x0 client rect, so it reads as "not found".
  // It still counts as available — the backend restores the window on Start.
  assert.match(main, /let _gameWindowMinimized = false/);
  assert.match(main, /_gameWindowMinimized = !!state\.game_window\?\.minimized/);
  assert.match(main, /const gameReady = _gameWindowFound \|\| _gameWindowMinimized/);
  assert.match(main, /start\.disabled = starting \|\| stopping \|\| _macroRunning \|\| !gameReady/);
  assert.ok(main.includes("showToast('Roblox is not open — launch the game, then Start', 'err')"));
});

test('an unavailable Start always explains itself', () => {
  // Grey with no reason reads as broken. Every blocked path names its cause, and
  // the reason is visible on the page, not only in a hover tooltip.
  assert.match(main, /const blockedReason = starting \?/);
  assert.match(main, /start\.title = blockedReason/);
  assert.match(main, /'Roblox is not open\. Launch the game, then press Start\.'/);
  assert.match(main, /'The macro is already running — press STOP first\.'/);
  assert.match(main, /'Roblox is minimized\. Start will restore it first\.'/);
  assert.match(html, /id="startReason"/);
  assert.match(styles, /\.action-reason\{/);
});

test('a failed command is never silent', () => {
  // These three used `if (r.ok !== false)` with no else, so a failure changed
  // nothing on screen and looked identical to a dead button.
  for (const label of ['Agility mode change failed', 'Health mode change failed', 'Ki mode change failed']) {
    assert.ok(main.includes(label), `missing failure toast: ${label}`);
  }
  // A hung command must resolve into an error rather than awaiting forever.
  assert.match(main, /function _withTimeout\(promise, ms, label\)/);
  assert.match(main, /_withTimeout\(\s*invoke\('send_to_python'/);
  // A throw inside an inline onclick is swallowed by the WebView otherwise.
  assert.match(main, /window\.addEventListener\('error', \(e\) => _reportUiError/);
  assert.match(main, /window\.addEventListener\('unhandledrejection', \(e\) => _reportUiError/);
});

test('fullscreen restore and resolution confirm are both optional', () => {
  for (const key of ['restore_fullscreen_on_start', 'display_confirm_changes']) {
    assert.match(html, new RegExp(`toggleSetting\\('${key}',this\\)`));
    assert.ok(main.includes(`${key}: 'toggle`), `${key} missing from toggleMap`);
  }
  // The revert timer must be the backend's — a mode the monitor can't display
  // leaves nothing clickable on screen.
  assert.match(main, /the backend is what actually reverts/);
  assert.match(main, /sendCommand\('display_keep'\)/);
  assert.match(html, /id="resConfirmOverlay"/);
});

test('the resolution warning never starts the macro by itself', () => {
  // The countdown is a read-the-warning delay. Auto-confirming would launch the
  // macro at a moment the user never chose.
  assert.match(main, /remaining <= 0\) \{[\s\S]*?yes\.disabled = false;\s*yes\.textContent = 'Continue';/);
  // The entrance animation ends dimmed with fill-mode both, so clearing it is
  // what actually lets the button reach full opacity once it's live.
  assert.match(main, /yes\.style\.animation = 'none';/);
  assert.doesNotMatch(main, /remaining <= 0\)[\s\S]{0,120}done\(true\)/);
  assert.match(html, /id="warnYes" disabled/);
});

test('compact-only shortcuts expand before opening overlays', () => {
  assert.match(main, /function openPalette\(\) \{\s+if \(_isCompact\) \{\s+window\.wcCompact\(\);\s+setTimeout\(openPalette, 260\)/);
  assert.match(main, /function openShortcuts\(\) \{\s+if \(_isCompact\) \{\s+window\.wcCompact\(\);\s+setTimeout\(openShortcuts, 260\)/);
  assert.ok(main.includes("compactButton.setAttribute('aria-label', 'Expand')"));
});

test('after-run actions share one failure policy and never imply manual Stop', () => {
  assert.match(html, /id="afterRunGameAction"[\s\S]*?value="main_menu"[\s\S]*?value="close_game"[\s\S]*?value="zero_gravity"/);
  assert.match(html, /id="toggleShutdownFinished"[\s\S]*?shutdown_pc_when_finished/);
  assert.match(html, /id="afterRunFailureRow"[\s\S]*?id="toggleAfterRunFailure"[\s\S]*?after_run_on_failure/);
  assert.match(html, /Manual Stop never runs after-run actions/);
  assert.match(main, /function _syncAfterRunControls\(\)/);
  assert.match(main, /gameAction === 'none' && !shutdownEnabled/);
  assert.match(styles, /\.shutdown-dependent::before/);
});

test('training menu state is surfaced instead of pretending minigame input is active', () => {
  assert.match(main, /state\.training_menu_visible \? 'Training Menu'/);
  assert.match(main, /state\.training_menu_visible \? ' · menu open'/);
  assert.match(main, /incomplete: 'Incomplete'/);
  assert.match(main, /state\.last_run\?\.outcome === 'incomplete'/);
});

test('support diagnostics expose a live scan preview and copyable report', () => {
  assert.match(html, /id="toggleDiagnosticMode"[\s\S]*?diagnostic_mode/);
  assert.match(html, /togglePreview\('diagnostics'\)/);
  assert.match(html, /id="previewImgDiagnostics"/);
  assert.match(main, /path: '\/diagnostics'/);
  assert.match(main, /Diagnostic report copied/);
});
