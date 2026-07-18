import assert from 'node:assert/strict';
import test from 'node:test';

await import('./screen-state.js');

const { normalizeScreen, needsResolutionWarning } = globalThis.XMacroScreenState;

const screen = (device, width, height) => normalizeScreen({
  source: 'game-monitor',
  device,
  width,
  height,
  hz: 60,
});

test('unavailable display clears cached screen before Roblox is found again', () => {
  assert.equal(normalizeScreen({ source: 'unavailable', width: 0, height: 0 }), null);
  assert.equal(screen('DISPLAY1', 1920, 1080).signature, 'DISPLAY1|1920x1080');
});

test('external resolution changes invalidate an accepted display signature', () => {
  const accepted = screen('DISPLAY1', 2560, 1440).signature;
  assert.equal(needsResolutionWarning(screen('DISPLAY1', 2560, 1440), accepted), false);
  assert.equal(needsResolutionWarning(screen('DISPLAY1', 1920, 1200), accepted), true);
});

test('moving Roblox to another monitor requires fresh acceptance', () => {
  const accepted = screen('DISPLAY1', 2560, 1440).signature;
  assert.equal(needsResolutionWarning(screen('DISPLAY2', 2560, 1440), accepted), true);
});

test('disappearance invalidates an accepted signature before the same display returns', () => {
  const accepted = screen('DISPLAY1', 2560, 1440).signature;
  const unavailableAccepted = normalizeScreen({ source: 'unavailable' }) ? accepted : null;
  assert.equal(needsResolutionWarning(screen('DISPLAY1', 2560, 1440), unavailableAccepted), true);
});
