import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import { runInNewContext } from 'node:vm';

const sourceRoot = new URL('./', import.meta.url);
const repoRoot = new URL('../../', import.meta.url);

const [html, main, hudHtml, hudMain, styles, readme, packageText, tauriConfText] = await Promise.all([
  readFile(new URL('index.html', sourceRoot), 'utf8'),
  readFile(new URL('main.js', sourceRoot), 'utf8'),
  readFile(new URL('hud.html', sourceRoot), 'utf8'),
  readFile(new URL('hud.js', sourceRoot), 'utf8'),
  readFile(new URL('styles.css', sourceRoot), 'utf8'),
  readFile(new URL('README.md', repoRoot), 'utf8'),
  readFile(new URL('package.json', new URL('../', sourceRoot)), 'utf8'),
  readFile(new URL('src-tauri/tauri.conf.json', new URL('../', sourceRoot)), 'utf8'),
]);
const packageMetadata = JSON.parse(packageText);

function startStopFixture({ compact = false, warning = false, hudWait = null, startWait = null } = {}) {
  const elements = new Map();
  for (const id of ['startWarnOverlay', 'warnResMsg', 'warnYes', 'warnCancel', 'startDelay']) {
    const classes = new Set();
    elements.set(id, { value: '0', style: {}, focus() {},
      classList: { add: value => classes.add(value), remove: value => classes.delete(value),
        contains: value => classes.has(value) } });
  }
  const calls = [];
  const context = {
    window: {}, document: { getElementById: id => elements.get(id) },
    _isCompact: compact, _gameWindowFound: true, _gameWindowMinimized: false,
    _startTask: null, _stopTask: null, _macroRunning: false, _macroActionSeq: 0,
    _macroUiAction: '', _compactAlert: '', _screenRes: { w: 2560, h: 1600, signature: 'screen' },
    _acceptedDisplaySignature: null, _acceptedDpiSignature: null,
    XynMacroScreenState: {
      needsResolutionWarning: (_screen, accepted) => warning && !accepted,
      needsDpiWarning: () => false,
    },
    _setMacroUiAction: value => { context._macroUiAction = value; },
    _hideStartCountdown() {}, _showStartCountdown() {}, _focusModal() {}, _restoreModalFocus() {},
    _hud: async () => hudWait ? hudWait.promise : { open: false },
    sendCommand: async action => {
      calls.push(action);
      if (action === 'start' && startWait) return startWait.promise;
      return { ok: true };
    },
    getState: async () => null, showToast() {}, applyState() {}, _updateFooter() {},
    setInterval: () => 1, clearInterval() {}, setTimeout: callback => queueMicrotask(callback),
  };
  let expansions = 0;
  context.window.wcCompact = () => { context._isCompact = false; expansions += 1; };
  const begin = main.indexOf('  /* Actions */');
  const end = main.indexOf('  window.toggleMacroFromHud =', begin);
  assert.ok(begin >= 0 && end > begin);
  runInNewContext(main.slice(begin, end), context);
  return { context, elements, calls, expansions: () => expansions };
}

function deferred() {
  let resolve;
  const promise = new Promise(done => { resolve = done; });
  return { promise, resolve };
}

test('backend errors display untrusted markup as text', () => {
  let listener;
  const created = [];
  const document = {
    getElementById: () => null,
    createElement: tag => {
      const element = { tag, dataset: {}, style: {}, children: [],
        append: (...children) => element.children.push(...children) };
      Object.defineProperty(element, 'innerHTML', { set: () => assert.fail('error text reached an HTML sink') });
      created.push(element);
      return element;
    },
    body: { appendChild() {} },
  };
  const begin = main.indexOf("window.addEventListener('backend-error'");
  const end = main.indexOf("window.addEventListener('DOMContentLoaded'", begin);
  runInNewContext(main.slice(begin, end), { document,
    window: { addEventListener: (_name, handler) => { listener = handler; } } });
  const markup = '<img src=x onerror="window.compromised=true">';
  listener({ detail: { message: markup } });
  assert.deepEqual(created.map(element => element.tag), ['div', 'strong']);
  assert.ok(created[0].children[1].includes(markup));
  assert.equal(created[1].textContent, "XynMacro's engine is not running.");
});

test('Stop dismisses a pending Start warning and never starts the macro', async () => {
  const f = startStopFixture({ compact: true, warning: true });
  const start = f.context.window.startMacro();
  assert.equal(f.expansions(), 1);
  assert.equal(f.elements.get('startWarnOverlay').classList.contains('open'), true);

  await f.context.window.stopMacro();
  await start;

  assert.equal(f.elements.get('startWarnOverlay').classList.contains('open'), false);
  assert.deepEqual(f.calls, ['stop']);
  assert.equal(f.context._macroUiAction, '');
});

test('Stop reaches the backend while Start is waiting for HUD state', async () => {
  const hudWait = deferred();
  const f = startStopFixture({ hudWait });
  const start = f.context.window.startMacro();
  await new Promise(setImmediate);
  const stop = f.context.window.stopMacro();
  assert.deepEqual(f.calls, ['set', 'stop']);
  hudWait.resolve({ open: false });
  await Promise.all([start, stop]);
  assert.deepEqual(f.calls, ['set', 'stop']);
});

test('Stop covers both native setup and a previously sent Start arriving late', async () => {
  const startWait = deferred();
  const f = startStopFixture({ startWait });
  const start = f.context.window.startMacro();
  await new Promise(setImmediate);
  const stop = f.context.window.stopMacro();
  assert.deepEqual(f.calls, ['set', 'start', 'stop']);
  startWait.resolve({ ok: true });
  await Promise.all([start, stop]);
  assert.deepEqual(f.calls, ['set', 'start', 'stop', 'stop']);
  assert.equal(f.context._macroRunning, false);
});

function undoFixture() {
  const button = {};
  const requests = [];
  const context = {
    window: {}, document: { getElementById: id => id === 'undoButton' ? button : null,
      querySelector: () => null }, entryMap: {}, toggleMap: {}, showToast() {},
    _syncAfterRunControls() {}, settingControls: { sync() {} },
    _syncAgilityModeSeg() {}, _syncHealthModeSeg() {}, _syncKiV8ModeSeg() {},
    sendCommand: (...args) => {
      const request = deferred();
      requests.push({ ...request, args });
      return request.promise;
    },
  };
  const begin = main.indexOf('  // === Undo stack');
  const end = main.indexOf('  window.toggleSetting =', begin);
  runInNewContext(main.slice(begin, end) + '\nglobalThis.undoEntries = _undoStack;', context);
  return { context, button, requests };
}

test('rapid Undo clicks submit one request and restore controls after failure', async () => {
  const f = undoFixture();
  f.context._pushUndo('health_mode', 'v1_legacy', 'v2_track');
  const first = f.context.window.undoLastChange();
  await f.context.window.undoLastChange();
  assert.equal(f.requests.length, 1);
  assert.equal(f.button.disabled, true);
  f.requests[0].resolve({ ok: false });
  await first;
  assert.equal(f.button.disabled, false);
  assert.equal(f.context.undoEntries.length, 1);
});

test('Undo preserves a newer setting change saved during the request', async () => {
  const f = undoFixture();
  f.context._pushUndo('health_mode', 'v1_legacy', 'v2_track');
  const first = f.context.window.undoLastChange();
  f.context._pushUndo('start_delay_sec', 5, 2);
  f.requests[0].resolve({ ok: true });
  await first;
  assert.deepEqual(Array.from(f.context.undoEntries, entry => entry.key), ['start_delay_sec']);
});

test('training order rejects overlapping saves and releases its controls after failure', async () => {
  const requests = [], renders = [];
  const context = {
    _sanitizeOrder: order => order, _sig: JSON.stringify, statOrder: ['Health'],
    lastOrderSig: '', showToast() {},
    renderTrainingOrder: () => renders.push(true),
    sendCommand: (...args) => {
      const request = deferred(); requests.push({ ...request, args }); return request.promise;
    },
  };
  const begin = main.indexOf('  let _trainingOrderBusy = false;');
  const end = main.indexOf('  /* Visible countdown overlay', begin);
  runInNewContext(main.slice(begin, end), context);
  const first = context.saveTrainingOrder(['Health', 'Agility']);
  await context.saveTrainingOrder(['Ki Damage']);
  assert.equal(requests.length, 1);
  requests[0].resolve({ ok: false }); await first;
  assert.deepEqual(context.statOrder, ['Health']);
  const retry = context.saveTrainingOrder(['Ki Damage']);
  requests[1].resolve({ ok: true }); await retry;
  assert.deepEqual(context.statOrder, ['Ki Damage']);
  assert.equal(renders.length, 4);
});

function autoSaveFixture(withUndo = false) {
  const listeners = {};
  const requests = [], undo = [];
  const button = {};
  const element = { value: '5', addEventListener: (name, handler) => { listeners[name] = handler; },
    classList: { add() {}, remove() {} } };
  const context = {
    window: {},
    entryMap: { start_delay_sec: 'startDelay' },
    toggleMap: {}, settingControls: { sync() {} },
    document: { getElementById: id => id === 'startDelay' ? element : button,
      querySelector: () => null },
    _readInputValue: input => Number(input.value),
    _pushUndo: (...args) => undo.push(args), showToast() {}, _syncAfterRunControls() {},
    _syncAgilityModeSeg() {}, _syncHealthModeSeg() {}, _syncKiV8ModeSeg() {},
    setTimeout: () => 1,
    sendCommand: (_action, value) => {
      const request = deferred(); requests.push({ ...request, value: value.value }); return request.promise;
    },
  };
  if (withUndo) {
    const undoBegin = main.indexOf('  // === Undo stack');
    const undoEnd = main.indexOf('  window.toggleSetting =', undoBegin);
    runInNewContext(main.slice(undoBegin, undoEnd) + '\nglobalThis.undoEntries = _undoStack;', context);
  }
  const begin = main.indexOf('  function _queueFieldSave(');
  const end = main.indexOf('  // === Keybind capture', begin);
  runInNewContext(main.slice(begin, end), context);
  context._setupAutoSave();
  const input = value => { element.value = String(value); listeners.input(); };
  return { context, element, requests, undo, input,
    change: value => { input(value); return listeners.change(); } };
}

test('delayed setting saves keep the newest draft and persist changes in order', async () => {
  const f = autoSaveFixture();
  const first = f.change(8), second = f.change(9);
  await new Promise(setImmediate);
  assert.deepEqual(f.requests.map(request => request.value), [8]);
  f.requests[0].resolve({ ok: true, value: 8 });
  await new Promise(setImmediate);
  assert.equal(f.element.value, '9');
  assert.deepEqual(f.requests.map(request => request.value), [8, 9]);
  f.requests[1].resolve({ ok: true, value: 9 });
  await Promise.all([first, second]);
  assert.equal(f.element.value, 9);
  assert.equal(f.element._prevValue, 9);
  assert.deepEqual(f.undo, [['start_delay_sec', 5, 8], ['start_delay_sec', 8, 9]]);
});

test('returning to the previous value while a save is pending still gets persisted', async () => {
  const f = autoSaveFixture();
  const first = f.change(8), second = f.change(5);
  await new Promise(setImmediate);
  f.requests[0].resolve({ ok: true, value: 8 });
  await new Promise(setImmediate);
  assert.deepEqual(f.requests.map(request => request.value), [8, 5]);
  f.requests[1].resolve({ ok: true, value: 5 });
  await Promise.all([first, second]);
  assert.equal(f.element._prevValue, 5);
});

test('a rejected newer setting restores the last successful value and permits retry', async () => {
  const f = autoSaveFixture();
  const first = f.change(8), second = f.change(9);
  await new Promise(setImmediate);
  f.requests[0].resolve({ ok: true, value: 7 });
  await new Promise(setImmediate);
  f.requests[1].resolve({ ok: false });
  await Promise.all([first, second]);
  assert.equal(f.element.value, 7);
  assert.equal(f.element._prevValue, 7);
  assert.equal(f.element._saveTask, null);
  assert.deepEqual(f.undo, [['start_delay_sec', 5, 7]]);
});

for (const ok of [true, false]) {
  test(`typing without blur survives an older ${ok ? 'successful' : 'rejected'} save`, async () => {
    const f = autoSaveFixture();
    const first = f.change(8);
    await new Promise(setImmediate);
    f.input(9);
    f.requests[0].resolve({ ok, value: 8 });
    await first;
    assert.equal(f.element.value, '9');
    assert.equal(f.element._prevValue, ok ? 8 : 5);
    const next = f.change(9);
    await new Promise(setImmediate);
    f.requests[1].resolve({ ok: true, value: 9 });
    await next;
    assert.equal(f.element._prevValue, 9);
  });

  test(`a same-field edit waits for ${ok ? 'successful' : 'rejected'} Undo and retains history`, async () => {
    const f = autoSaveFixture(true);
    f.element.value = '8';
    f.element._prevValue = 8;
    f.context._pushUndo('start_delay_sec', 5, 8);
    const undone = f.context.window.undoLastChange();
    await new Promise(setImmediate);
    const edited = f.change(9);
    assert.deepEqual(f.requests.map(request => request.value), [5]);
    f.requests[0].resolve({ ok, value: 5 });
    await new Promise(setImmediate);
    assert.equal(f.element.value, '9');
    assert.deepEqual(f.requests.map(request => request.value), [5, 9]);
    f.requests[1].resolve({ ok: true, value: 9 });
    await Promise.all([undone, edited]);
    assert.equal(f.element._prevValue, 9);
    const history = Array.from(f.context.undoEntries, entry => [entry.oldValue, entry.newValue]);
    assert.deepEqual(history, ok ? [[5, 9]] : [[5, 8], [8, 9]]);
  });
}

test('Undo waits for an earlier field save and reverts its newest completed change', async () => {
  const f = autoSaveFixture(true);
  f.element.value = '8';
  f.element._prevValue = 8;
  f.context._pushUndo('start_delay_sec', 5, 8);
  const saved = f.change(9);
  await new Promise(setImmediate);
  const undone = f.context.window.undoLastChange();
  f.input(10);
  f.requests[0].resolve({ ok: true, value: 9 });
  await new Promise(setImmediate);
  assert.deepEqual(f.requests.map(request => request.value), [9, 8]);
  f.requests[1].resolve({ ok: true, value: 8 });
  await Promise.all([saved, undone]);
  assert.equal(f.element.value, '10');
  assert.equal(f.element._prevValue, 8);
  assert.deepEqual(Array.from(f.context.undoEntries, entry => [entry.oldValue, entry.newValue]), [[5, 8]]);
});

test('splash recognizes an already running backend after reload', async () => {
  const splashSource = main.slice(main.indexOf("window.addEventListener('DOMContentLoaded', () => {"),
    main.indexOf('const launchCelebration'));
  const listeners = new Map();
  const timers = [];
  const splash = { classList: { add: (name) => { splash.hidden = name === 'hide'; } }, remove() {} };
  let checkedPort = false;
  const window = {
    addEventListener: (name, fn) => listeners.set(name, fn),
    __TAURI__: { core: { invoke: async (name) => {
      assert.equal(name, 'get_backend_port');
      checkedPort = true;
      return 8765;
    } } },
  };
  runInNewContext(splashSource, {
    window,
    document: { getElementById: (id) => id === 'splash' ? splash : null },
    Date: { now: () => 0 },
    setInterval: () => 1,
    clearInterval: () => {},
    setTimeout: (fn, ms) => { timers.push({ fn, ms }); },
    celebrateWSpain: () => {},
  });
  listeners.get('DOMContentLoaded')();
  await Promise.resolve();
  assert.equal(checkedPort, true);
  const earlyHide = timers.find((timer) => timer.ms === 900);
  assert.ok(earlyHide, 'a healthy backend should use the minimum splash hold');
  earlyHide.fn();
  assert.equal(splash.hidden, true);
});

test('XynMacro branding migrates and removes legacy preference keys', () => {
  assert.doesNotMatch(main, /X(?:Macro)|x(?:macro)-/);
  assert.match(main, /const legacyPrefix = \['x', 'macro-'\]\.join\(''\)/);
  assert.match(main, /XynMacroPreferenceMigration\.migratePreferences/);
  assert.match(html, /preference-migration\.js[\s\S]*main\.js/);
});

test('custom switches and segmented controls expose their state', () => {
  const toggles = [...html.matchAll(/<button\b[^>]*class="toggle(?: active)?"[^>]*>/g)].map((match) => match[0]);
  assert.equal(toggles.length, 21);
  for (const toggle of toggles) {
    assert.match(toggle, /role="switch"/);
    assert.match(toggle, /aria-checked="(?:true|false)"/);
    const labelIds = toggle.match(/aria-labelledby="([^"]+)"/)?.[1].split(/\s+/);
    assert.ok(labelIds?.length, `Switch needs a visible label: ${toggle}`);
    for (const id of labelIds) {
      const labels = [...html.matchAll(new RegExp(`<(?:span|label)\\b[^>]*id="${id}"[^>]*>([\\s\\S]*?)</(?:span|label)>`, 'g'))];
      assert.equal(labels.length, 1, `Label must exist exactly once: ${id}`);
      assert.ok(labels[0][1].replace(/<[^>]+>/g, '').trim(), `Label must contain text: ${id}`);
    }
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
  assert.equal(dialogs.length, 9);
  for (const dialog of dialogs) {
    assert.match(dialog, /aria-modal="true"/);
    assert.match(dialog, /aria-label="[^"]+"/);
    assert.match(dialog, /tabindex="-1"/);
  }

  for (const closeAction of [
    'closePalette()',
    'closeShortcuts()',
    '_cancelStartWarning()',
    'dismissWelcome()',
    'closeChangelog()',
    'closeAnnouncement()',
    'closeBugReport()',
    'closeBugConfirm()',
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

test('startup window mode is exclusive and resolution confirm remains optional', () => {
  assert.match(html, /id="startupWindowMode"/);
  assert.match(html, /value="unchanged"/);
  assert.match(main, /startup_window_mode: 'startupWindowMode'/);
  for (const key of ['display_confirm_changes']) {
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

/* The UI wires most controls with inline onclick= attributes. Those are governed by
   CSP's script-src-attr, which falls back to script-src — and script-src carries a
   Tauri-injected nonce, which makes its 'unsafe-inline' be ignored under CSP3. A
   WebView2 update started enforcing that and killed every button in bundled builds
   while addEventListener-wired controls kept working. script-src-attr must therefore
   stay declared explicitly, and it must not be given a nonce. */
test('CSP allows the inline event handlers the UI is built on', () => {
  const csp = JSON.parse(tauriConfText).app.security.csp;
  assert.match(csp, /script-src-attr 'unsafe-inline'/);
  assert.doesNotMatch(csp, /script-src-attr[^;]*nonce/);
});

test('inline onclick handlers still exist, so the CSP guard above stays relevant', () => {
  assert.ok(html.split('onclick=').length - 1 > 20);
});

/* The CSP guard above only holds if the config stays right. This is the runtime
   backstop: whatever the next engine update decides to enforce, the app finds out
   at startup and says so, rather than presenting a UI where nothing works. */
test('startup proves inline handlers actually fire', () => {
  const probe = main.slice(main.indexOf('function checkInlineHandlers'));
  assert.ok(probe, 'the inline-handler self-check is gone');
  const body = probe.slice(0, probe.indexOf('})();'));
  // It has to click a real element with a real inline attribute — reading the CSP
  // string or trusting a flag would pass in exactly the build that is broken.
  assert.match(body, /setAttribute\('onclick'/);
  assert.match(body, /\.click\(\)/);
  assert.match(body, /securitypolicyviolation/);
  assert.match(body, /csp-dead-banner/);
  assert.match(styles, /\.csp-dead-banner/);
  // The banner must wire its own button with addEventListener — an inline handler
  // there would be dead in exactly the situation the banner exists for.
  assert.match(body, /copy\.addEventListener\('click'/);
});

test('window controls surface failures instead of swallowing them', () => {
  // A silently failing minimize is indistinguishable from a dead UI. Scoped to
  // _wcSend: the splash reveal and drag handler are deliberately fire-and-forget.
  const body = main.slice(main.indexOf('function _wcSend('));
  const wcSend = body.slice(0, body.indexOf('window.wcMinimize'));
  assert.doesNotMatch(wcSend, /catch\(\(\)\s*=>\s*\{\}\)/);
  assert.match(wcSend, /_reportUiError\('Window control failed'/);
});

test('the vestigial drag region that the ACL forbids is gone', () => {
  // data-tauri-drag-region calls plugin:window|start_dragging, which core:default
  // does not grant; main.js drags via the wc command instead.
  assert.doesNotMatch(html, /data-tauri-drag-region/);
});

/* Posting to GitHub is public and permanent. It must be unreachable by accident:
   a read-the-warning cooldown first, then three clicks spaced a second apart, and
   the primary button is never the initially focused control. */
test('posting publicly is gated against accidental clicks', () => {
  assert.match(main, /const BUG_CONFIRM_COOLDOWN_SEC = 5/);
  assert.match(main, /const BUG_CONFIRM_CLICKS = 3/);
  assert.match(main, /const BUG_CONFIRM_CLICK_GAP_MS = 1000/);
  // The countdown only enables the button; it must never post on its own.
  assert.doesNotMatch(main, /setTimeout\(\(\) => \{[^}]*bug_report_open/);
  // Focus lands on Cancel, not on the destructive action.
  assert.match(main, /_focusModal\(overlay, cancel\)/);
  // Opening is reached only through the confirmation.
  assert.match(main, /getElementById\('bugOpen'\)\?\.addEventListener\('click', openBugConfirm\)/);
  // Not the `disabled` attribute: a disabled button passes clicks through to the
  // dialog behind it, which dismissed the warning and altered the report underneath.
  assert.match(main, /if \(submitting \|\| Date\.now\(\) < lockedUntil\) return;/);
  assert.match(main, /go\.classList\.add\('is-locked'\)/);
  assert.doesNotMatch(main, /go\.disabled = true/);
});

test('the confirm buttons never move between states', () => {
  // The label swings from "click 3 more times" to "Wait…". Without a fixed width the
  // footer reflows and Cancel lands under a cursor mid-click.
  assert.match(styles, /#bugConfirmGo \{ min-width: \d+px/);
});

test('the report carries frontend errors, which never reach the sidecar log', () => {
  assert.match(main, /const UI_ERROR_RING = \[\]/);
  assert.match(main, /UI_ERROR_RING\.push/);
  assert.match(main, /ui_errors: UI_ERROR_RING\.slice\(-10\)/);
});

test('the app never tells users to send reports privately to the author', () => {
  for (const source of [html, main]) {
    assert.doesNotMatch(source, /DM|direct message/i);
  }
});

/* Capture is a screen grab, not a window grab: anything drawn over Roblox is read
   as part of Roblox. The region outlines land on the exact boxes they describe, and
   the Ki check scans the whole client, so there is no safe corner for the panel
   either. The HUD must therefore leave the game window alone while a run is on. */
test('the HUD refuses to sit over the game while the macro runs', () => {
  assert.match(hudMain, /if \(running && !wasRunning && docked\)/);
  assert.match(hudMain, /await setDocked\(false, spot\)/);
  // The Dock button is the other way back in, and it has to be shut too.
  const dockButton = hudMain.slice(hudMain.indexOf("el('btnDock')"));
  assert.match(dockButton.slice(0, dockButton.indexOf('});')), /if \(wasRunning\) return/);
  // Popped out it can still be dragged back over the game, so it says so.
  assert.match(hudMain, /action: 'rect'/);
  assert.match(hudMain, /hudOverlapWarning/);
  assert.match(hudHtml, /id="hudOverlapWarning"/);
  assert.match(hudHtml, /\.overlap-warning\{/);
});

/* The crash notice fires during startup, competing with the splash. A .notif-toast
   auto-hides after a few seconds, which meant it was reliably missed — so it uses
   the persistent .update-toast card, which waits to be acted on. */
test('the crash notice waits to be seen instead of fading', () => {
  assert.match(html, /id="crashToast"[^>]*class="update-toast"|class="update-toast" id="crashToast"/);
  assert.match(main, /document\.getElementById\('crashToast'\)\?\.classList\.add\('show'\)/);
  assert.doesNotMatch(main, /showToast\('Last session ended unexpectedly/);
  // Dismiss and Report both have to close it, or it becomes permanent furniture.
  for (const id of ['crashDismiss', 'crashReport']) {
    assert.ok(main.includes(`document.getElementById('${id}')?.addEventListener('click'`), id);
  }
  assert.match(main, /window\.openBugReport\?\.\(\)/);
});

test('state recovery clears only its own backend-lost bar', async () => {
  const source = await readFile(new URL('main.js', import.meta.url), 'utf8');
  const start = source.indexOf('  let _pollBusy = false;');
  const end = source.indexOf('  let _frontendRefreshTask = null;', start);
  assert.ok(start >= 0 && end > start);

  const ids = ['statusText', 'headerStatusText', 'currentState', 'hudState', 'sfState',
    'elapsedTime', 'hudTime', 'sfTime', 'statusDot', 'hudStatusDot', 'sfDot'];
  const elements = Object.fromEntries(ids.map(id => [id, {
    textContent: 'Active', classList: { remove() {} },
  }]));
  let removedBar = false;
  let errorBar = null;
  const events = [];
  const cleared = [];
  const applied = [];
  const footer = [];
  const responses = [null, null, null, null, { running: false }];
  const context = {
    document: {
      getElementById(id) {
        if (id === 'backendErrorBar') return errorBar;
        return elements[id] || null;
      },
      querySelector() { return { style: {} }; },
      documentElement: { classList: { remove() {} } },
    },
    window: { dispatchEvent(event) {
      events.push(event);
      if (event.detail?.backendLost) {
        errorBar = { dataset: { backendLost: 'true' }, remove() { removedBar = true; } };
      }
    } },
    CustomEvent: class { constructor(type, options) { this.type = type; this.detail = options.detail; } },
    clearInterval(id) { cleared.push(id); },
    getState: async () => responses.shift(),
    applyState(state) { applied.push(state); },
    _updateFooter(state) { footer.push(state); },
    _renderMacroControls() {},
  };
  const setup = `let _failedStatePolls = 0, _elapsedTimer = 123, _startedAt = 100;
    let _macroRunning = true, _lastBackendRunning = null, _macroUiAction = '';
    let _gameWindowFound = true, _gameWindowMinimized = false, _lastStateHash = 'old';
    ${source.slice(start, end)}
    globalThis.pollUnderTest = poll;
    globalThis.markBackendSeen = () => { _lastBackendRunning = true; };
    globalThis.pollState = () => ({ failures: _failedStatePolls, timer: _elapsedTimer,
      startedAt: _startedAt, running: _macroRunning });`;
  runInNewContext(setup, context);

  await context.pollUnderTest();
  assert.equal(context.pollState().failures, 0);
  assert.equal(events.length, 0);
  context.markBackendSeen();
  await context.pollUnderTest();
  assert.equal(events.length, 0);
  assert.equal(context.pollState().running, true);
  await context.pollUnderTest();
  assert.equal(events.length, 0);
  await context.pollUnderTest();
  assert.equal(events.length, 1);
  assert.equal(events[0].type, 'backend-error');
  assert.match(events[0].detail.message, /stopped responding/);
  assert.deepEqual(cleared, [123]);
  assert.equal(context.pollState().timer, null);
  assert.equal(context.pollState().startedAt, 0);
  assert.equal(context.pollState().running, false);
  assert.equal(elements.currentState.textContent, "Lost connection to XynMacro's engine");
  assert.equal(elements.elapsedTime.textContent, '—');
  await context.pollUnderTest();
  assert.equal(context.pollState().failures, 0);
  assert.equal(applied.length, 1);
  assert.equal(footer.length, 1);
  assert.equal(removedBar, true);
});

test('a successful state poll preserves an unrelated startup error bar', async () => {
  const source = await readFile(new URL('main.js', import.meta.url), 'utf8');
  const start = source.indexOf('  let _pollBusy = false;');
  const end = source.indexOf('  let _frontendRefreshTask = null;', start);
  let removed = false;
  const context = {
    document: { getElementById() { return { dataset: {}, remove() { removed = true; } }; } },
    getState: async () => ({ running: false }),
    applyState() {},
    _updateFooter() {},
  };
  runInNewContext(`let _failedStatePolls = 0;
    ${source.slice(start, end)}
    globalThis.pollUnderTest = poll;`, context);
  await context.pollUnderTest();
  assert.equal(removed, false);
});

function keybindFixture() {
  const start = main.indexOf('  function _enterKeybindCapture(btn)');
  const end = main.indexOf('  // Init wired after DOMContentLoaded', start);
  assert.ok(start >= 0 && end > start);
  const classes = new Set();
  const listeners = new Set();
  const button = {
    textContent: 'F7', dataset: { settingKey: 'start_stop_hotkey', value: 'f7' }, disabled: false,
    focus() {},
    classList: { contains: name => classes.has(name), add: name => classes.add(name), remove: name => classes.delete(name) },
  };
  const calls = [], undo = [], toasts = [];
  const context = {
    document: {
      addEventListener: (name, handler) => listeners.add(handler),
      removeEventListener: (name, handler) => listeners.delete(handler),
    },
    sendCommand: (...args) => new Promise(resolve => calls.push({ args, resolve })),
    _pushUndo: (...args) => undo.push(args),
    showToast: (...args) => toasts.push(args),
  };
  runInNewContext(main.slice(start, end), context);
  const press = (key, repeat = false) => {
    for (const handler of [...listeners]) handler({ key, repeat, preventDefault() {}, stopPropagation() {} });
  };
  return { context, button, classes, listeners, calls, undo, toasts, press };
}

test('Space is saved as a named key and renders the backend value', async () => {
  const f = keybindFixture();
  f.context._enterKeybindCapture(f.button);
  f.press(' ');
  assert.equal(f.calls[0].args[1].value, 'space');
  f.calls[0].resolve({ ok: true, value: 'space' });
  await new Promise(setImmediate);
  assert.equal(f.button.dataset.value, 'space');
  assert.equal(f.button.textContent, 'SPACE');
  assert.deepEqual(f.undo, [['start_stop_hotkey', 'f7', 'space']]);
});

test('key capture ends before the save, ignoring repeat and a second capture attempt', async () => {
  const f = keybindFixture();
  f.context._enterKeybindCapture(f.button);
  f.press('K', true);
  assert.equal(f.calls.length, 0);
  f.press('K');
  assert.equal(f.listeners.size, 0);
  assert.equal(f.classes.has('capturing'), false);
  assert.equal(f.button.disabled, true);
  f.context._enterKeybindCapture(f.button);
  f.press('Y'); f.press('K', true);
  assert.equal(f.calls.length, 1);
  f.calls[0].resolve({ ok: true, value: 'k' });
  await new Promise(setImmediate);
  assert.equal(f.button.disabled, false);
  assert.deepEqual(f.undo, [['start_stop_hotkey', 'f7', 'k']]);
});

test('failed key save leaves the old binding and releases capture for a retry', async () => {
  const f = keybindFixture();
  f.context._enterKeybindCapture(f.button);
  f.press('W');
  f.calls[0].resolve({ ok: false, msg: 'Choose another key.' });
  await new Promise(setImmediate);
  assert.equal(f.button.dataset.value, 'f7');
  assert.equal(f.button.textContent, 'F7');
  assert.equal(f.button.disabled, false);
  assert.equal(f.listeners.size, 0);
  assert.deepEqual(f.undo, []);
  assert.deepEqual(f.toasts, [['Choose another key.', 'err']]);
  f.context._enterKeybindCapture(f.button);
  assert.equal(f.listeners.size, 1);
});

test('folder selection disables the button and suppresses duplicate invokes until settled', async () => {
  const start = main.indexOf('  let _saveDirPickerBusy = false;');
  const end = main.indexOf('  window.openSaveDir =', start);
  assert.ok(start >= 0 && end > start);
  const button = { disabled: false };
  const calls = [], toasts = [];
  const context = {
    window: {}, document: { getElementById: id => { assert.equal(id, 'pickSaveDir'); return button; } },
    invoke: (...args) => new Promise((resolve, reject) => calls.push({ args, resolve, reject })),
    showToast: (...args) => toasts.push(args),
  };
  runInNewContext(main.slice(start, end), context);
  const first = context.window.pickSaveDir();
  assert.equal(button.disabled, true);
  await context.window.pickSaveDir();
  assert.equal(calls.length, 1);
  assert.equal(calls[0].args[0], 'proxy_post');
  assert.equal(calls[0].args[1].path, '/pick_save_dir');
  calls[0].resolve({ ok: true }); await first;
  assert.equal(button.disabled, false);
  assert.deepEqual(toasts, [['Save folder updated', 'ok']]);
  const retry = context.window.pickSaveDir();
  calls[1].reject(new Error('synthetic connection error')); await retry;
  assert.equal(button.disabled, false);
  assert.deepEqual(toasts[1], ['Could not open folder picker', 'err']);
  const cancel = context.window.pickSaveDir();
  calls[2].resolve({ ok: false, msg: 'cancelled' }); await cancel;
  assert.equal(button.disabled, false);
  assert.equal(toasts.length, 2);
  assert.match(html, /id="pickSaveDir"[^>]*onclick="pickSaveDir\(\)"/);
});

test('a scale change during DPI confirmation requires acceptance of the new scale', async () => {
  await import('./screen-state.js');
  const normalize = globalThis.XynMacroScreenState.normalizeScreen;
  const screen = scale => normalize({ device: 'DISPLAY1', width: 1920, height: 1080, scale });
  const start = main.indexOf('      while (XynMacroScreenState.needsDpiWarning');
  const end = main.indexOf("      const delayEl = document.getElementById('startDelay');", start);
  assert.ok(start >= 0 && end > start);
  const warnings = [];
  const context = {
    XynMacroScreenState: globalThis.XynMacroScreenState, initial: screen(1.25),
    _macroActionSeq: 1,
    _confirmStartWarning: async message => {
      warnings.push(message);
      if (warnings.length === 1) context.setScreen(screen(1.5));
      return true;
    },
  };
  runInNewContext(`let _screenRes = initial, _acceptedDpiSignature = null;
    globalThis.setScreen = screen => { _screenRes = screen; };
    globalThis.confirmDpi = async () => { const actionSeq = 1; ${main.slice(start, end)} };
    globalThis.acceptedDpi = () => _acceptedDpiSignature;`, context);
  await context.confirmDpi();
  assert.equal(warnings.length, 2);
  assert.match(warnings[0], /125%/);
  assert.match(warnings[1], /150%/);
  assert.equal(context.acceptedDpi(), screen(1.5).dpiSignature);
});
