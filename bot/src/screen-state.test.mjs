import assert from 'node:assert/strict';
import test from 'node:test';

await import('./screen-state.js');

const { normalizeScreen, needsResolutionWarning, needsDpiWarning } = globalThis.XynMacroScreenState;

const screen = (device, width, height, scale = 1) => normalizeScreen({
  source: 'game-monitor',
  device,
  width,
  height,
  hz: 60,
  scale,
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

test('a correct resolution at the wrong scaling still warns', () => {
  // The case the resolution check cannot see: 1920x1080 reads as perfect while
  // every click lands 25% short.
  assert.equal(needsResolutionWarning(screen('DISPLAY1', 1920, 1080, 1.25), null), false);
  assert.equal(needsDpiWarning(screen('DISPLAY1', 1920, 1080, 1.25), null), true);
});

test('100% scaling never warns, including the ratio landing a hair off', () => {
  assert.equal(needsDpiWarning(screen('DISPLAY1', 1920, 1080), null), false);
  assert.equal(needsDpiWarning(screen('DISPLAY1', 1920, 1080, 1.004), null), false);
  assert.equal(needsDpiWarning(null, null), false);
});

test('accepting the scaling warning is per display, like the resolution one', () => {
  const accepted = screen('DISPLAY1', 1920, 1080, 1.5).signature;
  assert.equal(needsDpiWarning(screen('DISPLAY1', 1920, 1080, 1.5), accepted), false);
  assert.equal(needsDpiWarning(screen('DISPLAY2', 1920, 1080, 1.5), accepted), true);
});

test('a missing scale from an older backend is treated as 100%', () => {
  const legacy = normalizeScreen({ source: 'game-monitor', device: 'DISPLAY1', width: 1920, height: 1080 });
  assert.equal(legacy.scale, 1);
  assert.equal(needsDpiWarning(legacy, null), false);
});
