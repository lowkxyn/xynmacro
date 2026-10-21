import assert from 'node:assert/strict';
import test from 'node:test';
import { readFile } from 'node:fs/promises';
import './shutdown-controls.js';
import './notification-controls.js';

const ids = ['shutdownBanner', 'shutdownMessage', 'cancelShutdownButton', 'hudCancelShutdown',
  'notificationStatus', 'notificationEnabled', 'notificationInterval',
  'notificationTest', 'notificationClear', 'notificationUrl', 'notificationScreenshots'];

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

test('failed save retains the URL but restores backend controls and their polling sync', async () => {
  const f = fixture(); const ui = XynMacroNotificationControls.create(f.deps); const e = f.elements;
  ui.render(configured);
  e.notificationUrl.value = '  synthetic-url  '; e.notificationUrl.fire('input');
  e.notificationInterval.value = '10';
  e.notificationUrl.fire('change');
  assert.equal(f.calls.length, 1);
  assert.deepEqual(f.calls[0].args, ['notifications_save', { enabled: true, progress_minutes: 10, screenshots: false, url: 'synthetic-url' }]);
  for (const id of ['notificationUrl', 'notificationEnabled', 'notificationInterval', 'notificationScreenshots', 'notificationTest', 'notificationClear']) assert.equal(e[id].disabled, true);
  f.calls[0].resolve({ ok: false }); await settled();
  assert.equal(e.notificationUrl.value, '  synthetic-url  ');
  assert.equal(e.notificationInterval.value, '15');
  ui.render({ ...configured, enabled: false, progress_minutes: 30 });
  assert.equal(e.notificationEnabled.checked, false);
  assert.equal(e.notificationInterval.value, '30');
  assert.deepEqual(f.toasts[0], ['Notification request failed', 'err']);
  e.notificationInterval.value = '10'; e.notificationInterval.fire('input');
  e.notificationInterval.fire('change');
  f.calls[1].resolve({ ok: true, notifications: { ...configured, progress_minutes: 10 } }); await settled();
  assert.equal(e.notificationUrl.value, '');
  ui.render(configured);
  assert.equal(e.notificationInterval.value, '15');
  assert.equal(e.notificationInterval.disabled, false);
});

test('a rejected enable restores the response snapshot and keeps following state polls', async () => {
  const f = fixture(); const ui = XynMacroNotificationControls.create(f.deps); const e = f.elements;
  const disabled = { configured: false, status: 'Not configured', enabled: false, progress_minutes: 0 };
  ui.render(disabled);
  e.notificationEnabled.checked = true; e.notificationEnabled.fire('input');
  e.notificationInterval.value = '5';
  e.notificationEnabled.fire('change');
  f.calls[0].resolve({ ok: false, msg: 'Save a webhook before enabling notifications.', notifications: disabled });
  await settled();
  assert.equal(e.notificationEnabled.checked, false);
  assert.equal(e.notificationInterval.value, '0');
  ui.render(configured);
  assert.equal(e.notificationEnabled.checked, true);
  assert.equal(e.notificationInterval.value, '15');
});

test('blank URL is omitted and a response without state uses the latest poll', async () => {
  const f = fixture(); const ui = XynMacroNotificationControls.create(f.deps); const e = f.elements;
  ui.render(configured);
  e.notificationUrl.value = '  '; e.notificationUrl.fire('input');
  e.notificationUrl.fire('change');
  assert.equal(f.calls.length, 0);   // a blank box never saves by itself
  e.notificationInterval.fire('change');
  assert.deepEqual(f.calls[0].args[1], { enabled: true, progress_minutes: 15, screenshots: false });
  ui.render({ ...configured, progress_minutes: 30 });
  f.calls[0].resolve({ ok: true }); await settled();
  assert.equal(e.notificationInterval.value, '30');
  assert.equal(e.notificationTest.disabled, false);
  assert.equal(e.notificationClear.disabled, false);
  assert.deepEqual(f.toasts[0], ['Notification settings saved', 'ok']);
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

test('pasting or pressing Enter saves the webhook; a change during a save is saved after it', async () => {
  const f = fixture(); const ui = XynMacroNotificationControls.create(f.deps); const e = f.elements;
  ui.render(configured);
  e.notificationUrl.value = 'pasted-url'; e.notificationUrl.fire('paste');
  assert.equal(f.calls.length, 0);   // waits a tick so the pasted text is in the box
  await new Promise(resolve => setTimeout(resolve, 0));
  assert.deepEqual(f.calls[0].args[1], { enabled: true, progress_minutes: 15, screenshots: false, url: 'pasted-url' });
  e.notificationScreenshots.checked = true; e.notificationScreenshots.fire('change');
  assert.equal(f.calls.length, 1);
  f.calls[0].resolve({ ok: true, notifications: configured }); await settled();
  assert.equal(f.calls.length, 2);
  assert.equal(f.calls[1].args[1].screenshots, true);
  f.calls[1].resolve({ ok: true, notifications: { ...configured, screenshots: true } }); await settled();
  e.notificationUrl.value = 'typed-url';
  e.notificationUrl.listeners.keydown({ key: 'a' });
  assert.equal(f.calls.length, 2);
  e.notificationUrl.listeners.keydown({ key: 'Enter' });
  assert.equal(f.calls[2].args[1].url, 'typed-url');
});
