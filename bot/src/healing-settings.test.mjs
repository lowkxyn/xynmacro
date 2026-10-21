import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import { runInNewContext } from 'node:vm';

const main = await readFile(new URL('./main.js', import.meta.url), 'utf8');
const html = await readFile(new URL('./index.html', import.meta.url), 'utf8');

function fixture() {
  const elements = new Map();
  const calls = [], undo = [];
  for (const [id, type, value] of [['healMethod', 'select-one', 'senzu'], ['majinRegenSlot', 'number', '3']]) {
    elements.set(id, { type, value, listeners: {}, classList: { add() {}, remove() {} },
      addEventListener(name, callback) { this.listeners[name] = callback; } });
  }
  const context = {
    window: {}, document: { getElementById: id => elements.get(id) },
    _syncAfterRunControls() {}, settingControls: { sync() {} },
    setTimeout() {}, showToast() {}, _pushUndo: (...args) => undo.push(args),
    sendCommand: async (action, payload) => {
      calls.push({ action, ...payload });
      return { ok: true, value: payload.value };
    },
  };
  const maps = main.slice(main.indexOf('  const toggleMap ='), main.indexOf('  // Keybind settings mapped'));
  const inputs = main.slice(main.indexOf('  function _readInputValue('), main.indexOf('  // === Keybind capture'));
  const toggle = main.slice(main.indexOf('  window.toggleSetting ='), main.indexOf('  function _syncAfterRunControls('));
  runInNewContext(maps + inputs + toggle + '\n_setupAutoSave(); globalThis.toggles = toggleMap;', context);
  return { context, elements, calls, undo };
}

test('healing controls expose both methods and the full transformation slot range', () => {
  assert.match(html, /id="healMethod"[\s\S]*?value="senzu"[\s\S]*?value="majin_regen"/);
  assert.match(html, /id="majinRegenSlot"[^>]*value="3"[^>]*min="1"[^>]*max="8"/);
  assert.match(html, /id="toggleMajinRegenReserve"[^>]*toggleSetting\('majin_regen_senzu_reserve',this\)/);
  assert.equal(fixture().context.toggles.majin_regen_senzu_reserve, 'toggleMajinRegenReserve');
});

test('method and slot edits use the existing set command and undo path', async () => {
  const f = fixture();
  for (const [id, value] of [['healMethod', 'majin_regen'], ['majinRegenSlot', '8']]) {
    const el = f.elements.get(id);
    el.value = value;
    await el.listeners.change();
  }
  assert.deepEqual(f.calls, [
    { action: 'set', key: 'heal_method', value: 'majin_regen' },
    { action: 'set', key: 'majin_regen_slot', value: 8 },
  ]);
  assert.deepEqual(f.undo, [['heal_method', 'senzu', 'majin_regen'], ['majin_regen_slot', 3, 8]]);
});

test('rejected healing edits restore the last saved value', async () => {
  const f = fixture();
  f.context.sendCommand = async () => ({ ok: false, msg: 'Rejected' });
  const el = f.elements.get('healMethod');
  el.value = 'invalid';
  await el.listeners.change();
  assert.equal(el.value, 'senzu');
  assert.equal(f.undo.length, 0);
});

test('reserve toggle persists a boolean and updates its accessible state', async () => {
  const f = fixture();
  let checked = true;
  const attributes = {};
  const toggle = { classList: { contains: () => checked, toggle: (_class, value) => { checked = value; } },
    setAttribute: (name, value) => { attributes[name] = value; } };
  await f.context.window.toggleSetting('majin_regen_senzu_reserve', toggle);
  assert.deepEqual(f.calls, [{ action: 'set', key: 'majin_regen_senzu_reserve', value: false }]);
  assert.equal(attributes['aria-checked'], 'false');
  assert.equal(checked, false);
});
