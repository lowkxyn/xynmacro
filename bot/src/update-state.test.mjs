import assert from 'node:assert/strict';
import test from 'node:test';

await import('./update-state.js');

const { reminderDecision } = globalThis.XynMacroUpdateState;

test('automatic checks suppress only the ignored exact version', () => {
  assert.deepEqual(reminderDecision('1.0.6', '1.0.6', true), {
    skip: true,
    clearIgnored: false,
  });
});

test('manual checks override the ignored exact version', () => {
  assert.deepEqual(reminderDecision('1.0.6', '1.0.6', false), {
    skip: false,
    clearIgnored: true,
  });
});

test('a future version clears the old ignore and notifies normally', () => {
  assert.deepEqual(reminderDecision('1.0.7', '1.0.6', true), {
    skip: false,
    clearIgnored: true,
  });
});
