import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import { runInNewContext } from 'node:vm';
import './setting-controls.js';

const html = await readFile(new URL('./index.html', import.meta.url), 'utf8');
const main = await readFile(new URL('./main.js', import.meta.url), 'utf8');

function element(value = '') {
  const classes = new Set();
  return {
    value, textContent: '', hidden: false, disabled: false, title: '', attributes: {},
    classList: {
      contains: name => classes.has(name),
      toggle(name, on) { on ? classes.add(name) : classes.delete(name); },
    },
    setAttribute(name, value) { this.attributes[name] = value; },
  };
}

function fixture() {
  const elements = new Map();
  for (const [id, value] of [
    ['healMethod', 'senzu'], ['majinRegenSlot', '3'], ['senzuPreferenceMode', 'full_only'],
    ['toggleMajinRegenReserve'], ['toggleMajinRegenAuto'], ['toggleSenzu'], ['majinReserveWarning'], ['healingSummary'],
    ['toggleAutoRetryWalkOut'], ['toggleAutoRetryWalkFallback'], ['respawnSettle', '2'],
    ['autoRetryWalkSeconds', '7'], ['toggleNoYellowFallback'], ['noYellowTimeout', '15'],
    ['toggleProgressionStall'], ['progressionStallTimeout', '120'],
    ['telemHealingLabel'], ['telemSenzuStock'], ['telemSenzuBreakdown'],
  ]) elements.set(id, element(value));
  for (const [id, children] of [
    ['majinRegenRows', ['majinRegenSlot', 'toggleMajinRegenReserve']],
    ['autoRetryWalkRows', ['respawnSettle', 'toggleAutoRetryWalkFallback', 'autoRetryWalkSeconds']],
    ['colourFallbackRows', ['noYellowTimeout']],
    ['stuckCounterRows', ['progressionStallTimeout']],
  ]) {
    const group = element();
    group.querySelectorAll = () => children.map(id => elements.get(id));
    elements.set(id, group);
  }
  const ui = XynMacroSettingControls.create({ document: { getElementById: id => elements.get(id) } });
  const on = (id, value) => elements.get(id).classList.toggle('active', value);
  return { ui, on, get: id => elements.get(id) };
}

test('Majin controls disable and dim with Senzu selected, preserving saved choices', () => {
  const f = fixture();
  f.get('majinRegenSlot').value = '8';
  f.on('toggleMajinRegenReserve', true);
  f.ui.sync();
  for (const id of ['majinRegenSlot', 'toggleMajinRegenReserve']) {
    assert.equal(f.get(id).disabled, true);
    assert.match(f.get(id).title, /Choose Majin/);
  }
  assert.equal(f.get('majinRegenRows').classList.contains('is-disabled'), true);
  assert.equal(f.get('majinRegenRows').attributes['aria-disabled'], 'true');
  f.get('healMethod').value = 'majin_regen';
  f.ui.sync();
  for (const id of ['majinRegenSlot', 'toggleMajinRegenReserve']) {
    assert.equal(f.get(id).disabled, false);
    assert.equal(f.get(id).title, '');
  }
  assert.equal(f.get('majinRegenRows').attributes['aria-disabled'], 'false');
  assert.equal(f.get('majinRegenSlot').value, '8');
  assert.equal(f.get('toggleMajinRegenReserve').classList.contains('active'), true);
});

test('the reserve warning appears only for Majin with fallback on and Senzu off', () => {
  const f = fixture();
  for (const majin of [false, true]) {
    for (const reserve of [false, true]) {
      for (const senzu of [false, true]) {
        f.get('healMethod').value = majin ? 'majin_regen' : 'senzu';
        f.on('toggleMajinRegenReserve', reserve);
        f.on('toggleSenzu', senzu);
        f.ui.sync();
        assert.equal(f.get('majinReserveWarning').hidden, !(majin && reserve && !senzu));
        assert.equal(f.get('toggleMajinRegenReserve').attributes['aria-describedby'], majin && reserve && !senzu
          ? 'majinReserveHint majinReserveWarning' : 'majinReserveHint');
      }
    }
  }
});

test('return settings disable together and keep the walking fallback preference', () => {
  const f = fixture();
  f.on('toggleAutoRetryWalkFallback', true);
  f.ui.sync();
  const ids = ['respawnSettle', 'autoRetryWalkSeconds', 'toggleAutoRetryWalkFallback'];
  for (const id of ids) {
    assert.equal(f.get(id).disabled, true);
    assert.match(f.get(id).title, /Return to GC Training Area/);
  }
  assert.equal(f.get('autoRetryWalkRows').classList.contains('is-disabled'), true);
  f.on('toggleAutoRetryWalkOut', true);
  f.ui.sync();
  for (const id of ids) assert.equal(f.get(id).disabled, false);
  assert.equal(f.get('autoRetryWalkSeconds').value, '7');
  assert.equal(f.get('toggleAutoRetryWalkFallback').classList.contains('active'), true);
});

test('each progression timeout follows its own fallback toggle', () => {
  const f = fixture();
  f.on('toggleProgressionStall', true);
  f.ui.sync();
  assert.equal(f.get('noYellowTimeout').disabled, true);
  assert.equal(f.get('progressionStallTimeout').disabled, false);
  f.on('toggleProgressionStall', false);
  f.on('toggleNoYellowFallback', true);
  f.ui.sync();
  assert.equal(f.get('noYellowTimeout').disabled, false);
  assert.equal(f.get('progressionStallTimeout').disabled, true);
  assert.equal(f.get('progressionStallTimeout').value, '120');
});

test('the healing summary follows method, slot, bean choice and fallback availability', () => {
  const f = fixture();
  f.ui.sync();
  assert.equal(f.get('healingSummary').textContent, 'Method: Senzu (off)');
  f.on('toggleSenzu', true);
  f.get('senzuPreferenceMode').value = 'half_then_full';
  f.ui.sync();
  assert.equal(f.get('healingSummary').textContent, 'Method: Senzu (Half, then Full)');
  f.get('healMethod').value = 'majin_regen';
  f.get('majinRegenSlot').value = '8';
  f.on('toggleMajinRegenReserve', true);
  f.ui.sync();
  assert.equal(f.get('healingSummary').textContent, 'Method: Majin slot 8; Senzu fallback (Half, then Full)');
  f.on('toggleSenzu', false);
  f.ui.sync();
  assert.equal(f.get('healingSummary').textContent, 'Method: Majin slot 8; Senzu fallback unavailable');
  f.on('toggleMajinRegenReserve', false);
  f.ui.sync();
  assert.equal(f.get('healingSummary').textContent, 'Method: Majin slot 8; Senzu fallback off');
});

test('the telemetry card switches between Majin counts and Senzu stock, including zero stock', () => {
  const f = fixture();
  f.ui.renderHealingTelemetry({ config: { heal_method: 'majin_regen' }, telemetry: { majin_regens: 3, majin_regen_failures: 2 } });
  assert.equal(f.get('telemHealingLabel').textContent, 'Majin Regens');
  assert.equal(f.get('telemSenzuStock').textContent, '3');
  assert.equal(f.get('telemSenzuBreakdown').textContent, 'Regens · 3 done · 2 failed');
  f.ui.renderHealingTelemetry({ config: { heal_method: 'senzu' }, senzu_remaining: 0,
    senzu_active_type: 'half', senzu_status: 'empty', telemetry: { senzu_eaten: 4, senzu_refills: 1 } });
  assert.equal(f.get('telemHealingLabel').textContent, 'Senzu Stock');
  assert.equal(f.get('telemSenzuStock').textContent, '0');
  assert.equal(f.get('telemSenzuBreakdown').textContent, 'Half · empty · 4 eaten · 1 refills');
  f.ui.renderHealingTelemetry({ config: { heal_method: 'majin_regen' } });
  assert.equal(f.get('telemSenzuBreakdown').textContent, 'Regens · 0 done · 0 failed');
  f.ui.renderHealingTelemetry({});
  assert.equal(f.get('telemSenzuStock').textContent, '—');
});

test('healing activity names the executing action independently of the selected method', () => {
  const f = fixture();
  const activity = main.slice(main.indexOf('    const backendActivity ='), main.indexOf('    const runStarted ='));
  function render(state) {
    const context = { state, running: true, cfg: { heal_method: 'majin_regen' }, settingControls: f.ui };
    runInNewContext(activity + '\nglobalThis.activity = backendActivity;', context);
    return context.activity;
  }
  for (const [action, label] of [
    ['senzu', 'Using Senzu'], ['majin_regen', 'Majin Regeneration'],
    ['majin_cooldown', 'Majin Cooldown'], ['reserve_senzu', 'Senzu Fallback'],
    [undefined, 'Healing'], ['unknown', 'Healing'], ['toString', 'Healing'],
  ]) {
    assert.equal(render({ controller_paused_for_senzu: true, healing_action: action }), label);
  }
  assert.equal(render({ controller_paused_for_senzu: true, healing_action: 'reserve_senzu', controller_paused: true }), 'Paused');
  assert.equal(render({ controller_paused_for_senzu: true, healing_action: 'majin_regen', stop_requested: true }), 'Stopping');
  assert.equal(render({ controller_paused_for_senzu: false, healing_action: 'majin_regen' }), 'Starting');
});

test('the healing layout puts Senzu settings before Majin and labels both slots', () => {
  const ids = ['healMethod', 'toggleSenzu', 'senzuPreferenceMode', 'senzuSlot', 'senzuDelay',
    'senzuRecoveryTimeout', 'majinRegenRows', 'majinRegenSlot', 'toggleMajinRegenReserve'];
  let previous = -1;
  for (const id of ids) {
    const next = html.indexOf(`id="${id}"`);
    assert.ok(next > previous, `Healing control out of order: ${id}`);
    previous = next;
  }
  assert.match(html, /for="majinRegenSlot">Transformation Slot \(Hold G\)/);
  assert.match(html, /for="senzuSlot">Senzu Hotbar Slot/);
  assert.match(html, /id="majinReserveWarning"[^>]*role="status"[^>]*hidden/);
  assert.doesNotMatch(html, /4\.5 seconds/);
});

test('Charging is beside Startup and explains charging outside minigames', () => {
  const training = html.slice(html.indexOf('id="controls"'), html.indexOf('id="input"'));
  assert.ok(training.indexOf('>Startup<') < training.indexOf('>Charging<'));
  assert.ok(training.indexOf('>Charging<') < training.indexOf('>Error Recovery<'));
  assert.match(training, /id="chargeHoldMode"[^>]*aria-describedby="chargeHoldHint chargeRechargeHint"/);
  assert.match(training, /value="off">Off/);
  assert.match(training, /value="third">To the third bar/);
  assert.match(training, /value="second">To the second bar/);
  assert.match(training, /Holds Shift outside minigames/);
  assert.match(training, /drops a whole bar below the target mid-stat, XynMacro steps out of the minigame, charges, and goes back in/);
  const minigames = html.slice(html.indexOf('id="tuning"'), html.indexOf('<!-- CALIBRATION -->'));
  assert.doesNotMatch(minigames, /id="chargeHoldMode"/);
});

test('fallback hints, names and HTML defaults agree with the visible controls', () => {
  assert.match(html, />Colour Fallback</);
  assert.match(html, />Colour Fallback Timeout</);
  assert.match(html, />Stuck Counter Fallback</);
  assert.match(html, />Stuck Counter Timeout</);
  assert.match(html, /id="progressionStallTimeout"[^>]*value="120"/);
  assert.match(html, /id="toggleAutoRetryWalkFallback"[^>]*aria-describedby="autoRetryWalkFallbackHint"/);
  assert.match(html, /holds W for the remaining Movement Budget/);
  assert.match(html, /Off: stops the return and abandons the retry/);
  const switches = [...html.matchAll(/<button\b[^>]*role="switch"[^>]*>/g)].map(m => m[0]);
  assert.equal(switches.length, 21);
  for (const control of switches) {
    assert.match(control, /class="toggle"/);
    assert.match(control, /aria-checked="false"/);
    assert.match(control, /aria-labelledby="[^"]+"/);
  }
});

test('the palette finds new controls and the welcome copy uses the current navigation', () => {
  const aliases = main.slice(main.indexOf('  const aliases ='), main.indexOf('  for (const control of document.querySelectorAll', main.indexOf('  const aliases =')));
  const context = {};
  runInNewContext(aliases + '\nglobalThis.aliases = aliases;', context);
  for (const [id, words] of [
    ['healMethod', ['majin', 'regen', 'heal', 'senzu']],
    ['chargeHoldMode', ['shift', 'charge', 'ki']],
    ['progressionStallTimeout', ['stuck', 'capped', 'points left']],
    ['toggleAutoRetryWalkFallback', ['hold w', 'walk blind', 'boundary']],
  ]) for (const word of words) assert.ok(context.aliases[id].includes(word));
  assert.doesNotMatch(html, /Tuning and Calibration|Burst in Controls|rebindable in Controls/);
  assert.match(html, /change keys in Input &amp; Display/);
  assert.match(html, /aria-label="Wait after respawn in seconds"/);
  assert.match(html, /aria-label="Wait before reopening training in seconds"/);
});

test('both reset buttons use the same confirmation and cancel sends no command', async () => {
  const resetButtons = [...html.matchAll(/<button\b[^>]*onclick="resetMacroSettings\(\)"[^>]*>/g)];
  assert.equal(resetButtons.length, 2);
  const source = main.slice(main.indexOf('  window.resetMacroSettings ='), main.indexOf('  // Backward-compatible alias for the command palette'));
  const calls = [];
  let confirmed = false;
  const context = {
    window: { confirm: message => { assert.match(message, /shipped defaults/); return confirmed; } },
    _macroRunning: false, _macroUiAction: null, _undoStack: [], _lastStateHash: 'old',
    _renderUndoButton() {}, showToast() {},
    sendCommand: async action => { calls.push(action); return { ok: true }; },
    getState: async () => ({ config: {} }), applyState() {},
  };
  runInNewContext(source, context);
  for (const _button of resetButtons) await context.window.resetMacroSettings();
  assert.deepEqual(calls, []);
  confirmed = true;
  for (const _button of resetButtons) await context.window.resetMacroSettings();
  assert.deepEqual(calls, ['reset_defaults', 'reset_defaults']);
});

test('the settings controller is loaded once and syncs on startup, edits, Undo and polls', () => {
  assert.ok(html.indexOf('src="setting-controls.js"') < html.indexOf('src="main.js"'));
  assert.equal(main.split('XynMacroSettingControls.create(').length - 1, 1);
  for (const [start, end] of [
    ['  window.undoLastChange =', '  window.toggleSetting ='],
    ['  window.toggleSetting =', '  function _syncAfterRunControls('],
    ['  function _setupAutoSave(', '  // === Keybind capture'],
    ['  function applyState(', '  async function poll('],
    ['  _setupAutoSave();', '  /* Command palette */'],
  ]) {
    const begin = main.indexOf(start);
    assert.ok(begin >= 0, `Missing section: ${start}`);
    const finish = main.indexOf(end, begin);
    assert.ok(finish > begin, `Missing section end: ${end}`);
    assert.ok(main.slice(begin, finish).includes('settingControls.sync();'));
  }
  assert.match(main, /settingControls\.renderHealingTelemetry\(state\)/);
  assert.match(main, /state\.controller_paused_for_senzu \? \(state\.controller_paused \? 'Paused' : settingControls\.healingActivity\(state\)\)/);
});


test('Auto-Detect enables the Majin rows, locks the slot and explains the fallback', () => {
  const f = fixture();
  f.on('toggleSenzu', true);
  f.on('toggleMajinRegenReserve', true);
  f.on('toggleMajinRegenAuto', true);
  f.ui.sync();
  assert.equal(f.get('toggleMajinRegenReserve').disabled, false);
  assert.equal(f.get('majinRegenSlot').disabled, true);
  assert.match(f.get('majinRegenSlot').title, /Auto-Detect finds the slot/);
  assert.equal(f.get('healingSummary').textContent,
    'Method: Majin Regeneration if found (any slot), otherwise Senzu (Full only); Senzu fallback (Full only)');
  f.on('toggleMajinRegenAuto', false);
  f.ui.sync();
  assert.equal(f.get('majinRegenSlot').disabled, true);  // Senzu method: whole group off
  assert.equal(f.get('healingSummary').textContent, 'Method: Senzu (Full only)');
});

test('the telemetry card shows Majin counts once Auto-Detect has used regeneration', () => {
  const f = fixture();
  f.ui.renderHealingTelemetry({ config: { heal_method: 'senzu', majin_regen_auto: true },
    telemetry: { majin_regens: 2, majin_regen_failures: 0 } });
  assert.equal(f.get('telemHealingLabel').textContent, 'Majin Regens');
  f.ui.renderHealingTelemetry({ config: { heal_method: 'senzu', majin_regen_auto: true },
    telemetry: {}, senzu_remaining: 91 });
  assert.equal(f.get('telemHealingLabel').textContent, 'Senzu Stock');
});
