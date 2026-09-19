import assert from 'node:assert/strict';
import test from 'node:test';
import { readFile } from 'node:fs/promises';
import './shutdown-controls.js';
import './notification-controls.js';

const ids = ['shutdownBanner', 'shutdownMessage', 'cancelShutdownButton', 'hudCancelShutdown',
  'notificationStatus', 'notificationEnabled', 'notificationInterval', 'notificationSave',
  'notificationTest', 'notificationClear', 'notificationUrl'];

function fixture() {
  const elements = Object.fromEntries(ids.map(id => [id, {
    hidden: false, disabled: false, textContent: '', value: '', checked: false,
    listeners: {},
    addEventListener(event, listener) { this.listeners[event] = listener; },
    fire(event) { return this.listeners[event](); },
  }]));
  const classes = new Set();
  const document = {
    getElementById(id) { assert.ok(elements[id], `Unexpected element: ${id}`); return elements[id]; },
    documentElement: { classList: { toggle(name, enabled) { enabled ? classes.add(name) : classes.delete(name); } } },
  };
  const calls = [];
  const toasts = [];
  const sendCommand = (...args) => new Promise(resolve => calls.push({ args, resolve }));
  const deps = { document, sendCommand, showToast: (...args) => toasts.push(args) };
  return { elements, classes, calls, toasts, deps };
}
const settled = () => new Promise(resolve => setImmediate(resolve));
const pending = { pending: true, seconds_remaining: 42 };
const configured = { configured: true, status: 'Ready', enabled: true, progress_minutes: 15 };

test('page loads both controllers before main and forwards state to their single instances', async () => {
  const html = await readFile(new URL('index.html', import.meta.url), 'utf8');
  const main = await readFile(new URL('main.js', import.meta.url), 'utf8');
  for (const [file, name, state] of [['shutdown-controls', 'Shutdown', 'shutdown'], ['notification-controls', 'Notification', 'notifications']]) {
    assert.ok(html.includes(`src="${file}.js"`));
    assert.ok(html.indexOf(`${file}.js`) < html.indexOf('main.js'));
    assert.equal(main.split(`XynMacro${name}Controls.create(`).length - 1, 1);
    assert.ok(main.includes(`${name.toLowerCase()}Controls.render(state.${state})`));
  }
  assert.ok(main.includes('window.cancelShutdown = shutdownControls.cancel'));
});

test('shutdown countdown and dispatched/uncertain states show truthful controls', () => {
  const f = fixture(); const ui = XynMacroShutdownControls.create(f.deps); const e = f.elements;
  ui.render(pending);
  assert.equal(e.shutdownBanner.hidden, false);
  assert.equal(e.shutdownMessage.textContent, 'PC shutdown in 42s');
  assert.equal(e.hudCancelShutdown.textContent, 'Cancel shutdown · 42s');
  assert.equal(e.cancelShutdownButton.hidden, false);
  assert.equal(e.cancelShutdownButton.disabled, false);
  for (const status of ['requesting', 'requested', 'failed', 'unknown']) {
    ui.render({ pending: false, status });
    assert.equal(e.shutdownBanner.hidden, false);
    assert.equal(e.cancelShutdownButton.hidden, true);
    assert.equal(e.hudCancelShutdown.hidden, true);
    assert.equal(f.classes.has('shutdown-notice'), true);
  }
  ui.render({ pending: false, status: 'idle' });
  assert.equal(e.shutdownBanner.hidden, true);
  assert.equal(f.classes.has('shutdown-notice'), false);
});

test('both cancel controls share one in-flight request and reset after acknowledgement', async () => {
  const f = fixture(); const ui = XynMacroShutdownControls.create(f.deps);
  ui.render(pending);
  const first = ui.cancel(); await ui.cancel();
  assert.deepEqual(f.calls[0].args, ['shutdown_cancel']);
  assert.equal(f.calls.length, 1);
  ui.render({ ...pending, seconds_remaining: 41 });
  for (const id of ['cancelShutdownButton', 'hudCancelShutdown']) {
    assert.equal(f.elements[id].disabled, true);
    assert.equal(f.elements[id].textContent, 'Cancelling…');
  }
  f.calls[0].resolve({ ok: true, msg: 'Cancelled', shutdown: { pending: false, status: 'idle' } });
  await first;
  assert.equal(f.elements.shutdownBanner.hidden, true);
  assert.equal(f.elements.cancelShutdownButton.disabled, false);
  assert.equal(f.elements.hudCancelShutdown.disabled, false);
  assert.deepEqual(f.toasts, [['Cancelled', 'ok']]);
});

test('failed cancellation without a snapshot preserves the warning and permits retry', async () => {
  const f = fixture(); const ui = XynMacroShutdownControls.create(f.deps);
  ui.render(pending);
  const first = ui.cancel(); f.calls[0].resolve({ ok: false, msg: 'Timed out' }); await first;
  assert.equal(f.elements.shutdownBanner.hidden, false);
  assert.equal(f.elements.shutdownMessage.textContent, 'PC shutdown in 42s');
  assert.equal(f.elements.cancelShutdownButton.disabled, false);
  assert.deepEqual(f.toasts[0], ['Timed out', 'err']);
  const retry = ui.cancel();
  assert.equal(f.calls.length, 2);
  f.calls[1].resolve({ ok: false, shutdown: { status: 'unknown' } }); await retry;
  assert.match(f.elements.shutdownMessage.textContent, /unknown/);
  assert.deepEqual(f.toasts[1], ['Could not confirm cancellation. Try again.', 'err']);
});

test('polling refreshes notification status without overwriting unsaved controls', () => {
  const f = fixture(); const ui = XynMacroNotificationControls.create(f.deps); const e = f.elements;
  ui.render(configured);
  assert.equal(e.notificationInterval.value, '15');
  e.notificationInterval.value = '5'; e.notificationInterval.fire('input');
  ui.render({ ...configured, status: 'Sending', enabled: false, progress_minutes: 30 });
  assert.equal(e.notificationEnabled.checked, true);
  assert.equal(e.notificationInterval.value, '5');
  assert.equal(e.notificationStatus.textContent, 'Webhook saved · Sending');
  ui.render({ configured: false, status: 'Removed' });
  assert.equal(e.notificationTest.disabled, true);
  assert.equal(e.notificationClear.disabled, true);
});

test('save trims the URL, suppresses duplicate commands and clears the draft only on success', async () => {
  const f = fixture(); const ui = XynMacroNotificationControls.create(f.deps); const e = f.elements;
  ui.render(configured);
  e.notificationUrl.value = '  synthetic-url  '; e.notificationUrl.fire('input');
  e.notificationInterval.value = '10';
  e.notificationSave.fire('click'); e.notificationSave.fire('click');
  assert.equal(f.calls.length, 1);
  assert.deepEqual(f.calls[0].args, ['notifications_save', { enabled: true, progress_minutes: 10, url: 'synthetic-url' }]);
  for (const id of ['notificationUrl', 'notificationEnabled', 'notificationInterval', 'notificationSave', 'notificationTest', 'notificationClear']) assert.equal(e[id].disabled, true);
  f.calls[0].resolve({ ok: false }); await settled();
  assert.equal(e.notificationUrl.value, '  synthetic-url  ');
  ui.render(configured);
  assert.equal(e.notificationInterval.value, '10');
  assert.deepEqual(f.toasts[0], ['Notification request failed', 'err']);
  e.notificationSave.fire('click');
  f.calls[1].resolve({ ok: true, notifications: { ...configured, progress_minutes: 10 } }); await settled();
  assert.equal(e.notificationUrl.value, '');
  ui.render(configured);
  assert.equal(e.notificationInterval.value, '15');
  assert.equal(e.notificationSave.disabled, false);
});

test('blank URL is omitted and a response without state uses the latest poll', async () => {
  const f = fixture(); const ui = XynMacroNotificationControls.create(f.deps); const e = f.elements;
  ui.render(configured);
  e.notificationUrl.value = '  '; e.notificationUrl.fire('input');
  e.notificationSave.fire('click');
  assert.deepEqual(f.calls[0].args[1], { enabled: true, progress_minutes: 15 });
  ui.render({ ...configured, progress_minutes: 30 });
  f.calls[0].resolve({ ok: true }); await settled();
  assert.equal(e.notificationInterval.value, '30');
  assert.equal(e.notificationTest.disabled, false);
  assert.equal(e.notificationClear.disabled, false);
  assert.deepEqual(f.toasts[0], ['Notification settings updated', 'ok']);
});

test('test-send preserves a draft; failed removal retains it and successful removal clears it', async () => {
  const f = fixture(); const ui = XynMacroNotificationControls.create(f.deps); const e = f.elements;
  ui.render(configured);
  e.notificationUrl.value = 'unsaved-url'; e.notificationUrl.fire('input');
  e.notificationInterval.value = '5';
  e.notificationTest.fire('click');
  assert.equal(f.calls[0].args[0], 'notifications_test');
  f.calls[0].resolve({ ok: true, notifications: configured }); await settled();
  assert.equal(e.notificationUrl.value, 'unsaved-url');
  assert.equal(e.notificationInterval.value, '5');
  e.notificationClear.fire('click');
  assert.equal(f.calls[1].args[0], 'notifications_clear');
  f.calls[1].resolve({ ok: false, notifications: configured }); await settled();
  assert.equal(e.notificationUrl.value, 'unsaved-url');
  e.notificationClear.fire('click');
  f.calls[2].resolve({ ok: true, notifications: { configured: false, enabled: false, progress_minutes: 0 } }); await settled();
  assert.equal(e.notificationUrl.value, '');
  assert.equal(e.notificationInterval.value, '0');
  assert.equal(e.notificationEnabled.checked, false);
  assert.equal(e.notificationTest.disabled, true);
  assert.equal(e.notificationClear.disabled, true);
});
