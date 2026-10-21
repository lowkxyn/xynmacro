import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import { runInNewContext } from 'node:vm';

const main = await readFile(new URL('./main.js', import.meta.url), 'utf8');
const settled = () => new Promise(resolve => setImmediate(resolve));

function fixture(active = false) {
  const classes = new Set(active ? ['active'] : []);
  const attributes = { 'aria-checked': String(active) };
  const toggle = {
    disabled: false,
    classList: {
      contains: name => classes.has(name),
      toggle(name, on) { on ? classes.add(name) : classes.delete(name); },
    },
    setAttribute: (name, value) => { attributes[name] = value; },
    closest: () => ({ querySelector: () => ({ textContent: 'Stuck Counter Fallback' }) }),
  };
  const requests = [], toasts = [];
  const undoButton = {};
  let syncs = 0;
  const context = {
    window: {},
    document: {
      getElementById: id => id === 'toggleProgressionStall' ? toggle : id === 'undoButton' ? undoButton : null,
      querySelector: () => null,
    },
    entryMap: {}, toggleMap: { progression_stall_enabled: 'toggleProgressionStall' },
    settingControls: { sync() { syncs += 1; } },
    _syncAgilityModeSeg() {}, _syncHealthModeSeg() {}, _syncKiV8ModeSeg() {},
    showToast: (...args) => toasts.push(args),
    sendCommand: (action, payload) => new Promise(resolve => requests.push({ action, ...payload, resolve })),
  };
  const queue = main.slice(main.indexOf('  function _queueFieldSave('), main.indexOf('  function _setupAutoSave('));
  const handlers = main.slice(main.indexOf('  // === Undo stack'), main.indexOf('  function _readInputValue('));
  const pollBegin = main.indexOf('    for (const [key, elId] of Object.entries(toggleMap))');
  const poll = main.slice(pollBegin, main.indexOf('    _syncAgilityModeSeg(', pollBegin));
  runInNewContext(queue + handlers + `
    globalThis.undoEntries = _undoStack;
    globalThis.pollToggles = cfg => { ${poll} };
  `, context);
  return {
    context, toggle, requests, attributes, toasts, undoButton,
    syncs: () => syncs,
    click: () => context.window.toggleSetting('progression_stall_enabled', toggle),
    checked: () => classes.has('active'),
    history: () => Array.from(context.undoEntries, entry => [entry.oldValue, entry.newValue]),
  };
}

test('two rapid clicks save both intended states in order and retain accurate Undo history', async () => {
  const f = fixture();
  const first = f.click();
  assert.equal(f.checked(), true);
  assert.equal(f.attributes['aria-checked'], 'true');
  const second = f.click();
  assert.equal(f.checked(), false);
  assert.equal(f.attributes['aria-checked'], 'false');
  await settled();
  assert.equal(f.requests.length, 1);
  assert.equal(f.requests[0].value, true);
  f.requests[0].resolve({ ok: true, value: true });
  await first;
  await settled();
  assert.equal(f.requests.length, 2);
  assert.equal(f.requests[1].value, false);
  assert.equal(f.checked(), false);
  f.requests[1].resolve({ ok: true, value: false });
  await second;
  assert.equal(f.checked(), false);
  assert.equal(f.toggle._prevValue, false);
  assert.deepEqual(f.history(), [[false, true], [true, false]]);
  assert.equal(f.toggle._saveTask, null);
  assert.ok(f.syncs() >= 3);
  assert.deepEqual(f.toasts.map(([text]) => text), ['Stuck Counter Fallback on', 'Stuck Counter Fallback off']);
});

test('polling preserves the latest pending toggle choice, then resumes normal synchronization', async () => {
  const f = fixture(true);
  const save = f.click();
  await settled();
  f.context.pollToggles({ progression_stall_enabled: true });
  assert.equal(f.checked(), false);
  assert.equal(f.attributes['aria-checked'], 'false');
  f.requests[0].resolve({ ok: true, value: false });
  await save;
  f.context.pollToggles({ progression_stall_enabled: true });
  assert.equal(f.checked(), true);
  assert.equal(f.toggle._prevValue, true);
  assert.equal(f.attributes['aria-checked'], 'true');
});

test('a rejected latest toggle restores the last saved state and leaves only successful Undo entries', async () => {
  const f = fixture();
  const first = f.click();
  const second = f.click();
  await settled();
  f.requests[0].resolve({ ok: true });
  await first;
  await settled();
  f.requests[1].resolve({ ok: false, msg: 'Save rejected' });
  await second;
  assert.equal(f.checked(), true);
  assert.equal(f.attributes['aria-checked'], 'true');
  assert.equal(f.toggle._prevValue, true);
  assert.deepEqual(f.history(), [[false, true]]);
  assert.deepEqual(f.toasts.at(-1), ['Save rejected', 'err']);
  const retry = f.click();
  await settled();
  f.requests[2].resolve({ ok: true });
  await retry;
  assert.equal(f.checked(), false);
});

test('an earlier rejected write cannot roll back a later click', async () => {
  const f = fixture();
  const first = f.click();
  const second = f.click();
  await settled();
  f.requests[0].resolve({ ok: false, msg: 'Save rejected' });
  await first;
  await settled();
  assert.equal(f.checked(), false);
  assert.equal(f.requests[1].value, false);
  f.requests[1].resolve({ ok: true, value: false });
  await second;
  assert.equal(f.checked(), false);
  assert.deepEqual(f.history(), []);
});

test('Undo immediately restores toggle appearance and accessible state as well as the backend', async () => {
  const f = fixture();
  for (const value of [true, false]) {
    const save = f.click();
    await settled();
    f.requests.at(-1).resolve({ ok: true, value });
    await save;
  }
  const undo = f.context.window.undoLastChange();
  await settled();
  assert.equal(f.requests[2].action, 'set');
  assert.equal(f.requests[2].value, true);
  f.requests[2].resolve({ ok: true, value: true });
  await undo;
  assert.equal(f.checked(), true);
  assert.equal(f.attributes['aria-checked'], 'true');
  assert.equal(f.toggle._prevValue, true);
  assert.deepEqual(f.history(), [[false, true]]);
});

test('Undo queues behind an earlier toggle save and reverts its newest completed change', async () => {
  const f = fixture();
  const first = f.click();
  await settled();
  f.requests[0].resolve({ ok: true, value: true });
  await first;
  const second = f.click();
  await settled();
  const undo = f.context.window.undoLastChange();
  assert.equal(f.requests.length, 2);
  f.requests[1].resolve({ ok: true, value: false });
  await second;
  await settled();
  assert.equal(f.requests[2].value, true);
  f.requests[2].resolve({ ok: true, value: true });
  await undo;
  assert.equal(f.checked(), true);
  assert.deepEqual(f.history(), [[false, true]]);
});

test('a disabled dependent toggle ignores clicks and sends no setting request', async () => {
  const f = fixture();
  f.toggle.disabled = true;
  await f.click();
  assert.equal(f.requests.length, 0);
  assert.equal(f.checked(), false);
  assert.deepEqual(f.history(), []);
});

test('backend-normalized values and missing responses keep the toggle and history truthful', async () => {
  const f = fixture();
  let save = f.click();
  await settled();
  f.requests[0].resolve({ ok: true, value: false });
  await save;
  assert.equal(f.checked(), false);
  assert.deepEqual(f.history(), []);
  save = f.click();
  await settled();
  f.requests[1].resolve(null);
  await save;
  assert.equal(f.checked(), false);
  assert.deepEqual(f.history(), []);
  assert.deepEqual(f.toasts.at(-1), ['Could not change Stuck Counter Fallback', 'err']);
});
