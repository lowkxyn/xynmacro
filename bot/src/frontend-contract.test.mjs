import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';

const sourceRoot = new URL('./', import.meta.url);
const repoRoot = new URL('../../', import.meta.url);

const [html, main, styles, readme] = await Promise.all([
  readFile(new URL('index.html', sourceRoot), 'utf8'),
  readFile(new URL('main.js', sourceRoot), 'utf8'),
  readFile(new URL('styles.css', sourceRoot), 'utf8'),
  readFile(new URL('README.md', repoRoot), 'utf8'),
]);

test('custom switches and segmented controls expose their state', () => {
  const toggles = [...html.matchAll(/<button\b[^>]*class="toggle(?: active)?"[^>]*>/g)].map((match) => match[0]);
  assert.equal(toggles.length, 5);
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
  assert.equal(dialogs.length, 6);
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
