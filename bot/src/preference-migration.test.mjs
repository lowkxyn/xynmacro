import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import vm from 'node:vm';

const source = await readFile(new URL('./preference-migration.js', import.meta.url), 'utf8');
const context = {};
vm.createContext(context);
vm.runInContext(source, context);
const { migratePreferences } = context.XynMacroPreferenceMigration;

function memoryStorage(entries = {}, failSet = false) {
  const values = new Map(Object.entries(entries));
  return {
    getItem: (key) => values.has(key) ? values.get(key) : null,
    setItem: (key, value) => {
      if (failSet) throw new Error('quota');
      values.set(key, value);
    },
    removeItem: (key) => values.delete(key),
    values,
  };
}

test('missing current preference is copied and legacy key is removed', () => {
  const storage = memoryStorage({ 'legacy-ui-style': 'aero' });
  migratePreferences(storage, 'legacy-', 'xynmacro-', ['ui-style']);
  assert.equal(storage.getItem('xynmacro-ui-style'), 'aero');
  assert.equal(storage.getItem('legacy-ui-style'), null);
});

test('existing current preference wins and legacy key is removed', () => {
  const storage = memoryStorage({
    'legacy-ui-style': 'aero',
    'xynmacro-ui-style': 'classic',
  });
  migratePreferences(storage, 'legacy-', 'xynmacro-', ['ui-style']);
  assert.equal(storage.getItem('xynmacro-ui-style'), 'classic');
  assert.equal(storage.getItem('legacy-ui-style'), null);
});

test('failed copy preserves legacy preference and does not abort', () => {
  const storage = memoryStorage({ 'legacy-ui-style': 'aero' }, true);
  assert.doesNotThrow(() => {
    migratePreferences(storage, 'legacy-', 'xynmacro-', ['ui-style']);
  });
  assert.equal(storage.getItem('legacy-ui-style'), 'aero');
  assert.equal(storage.getItem('xynmacro-ui-style'), null);
});
