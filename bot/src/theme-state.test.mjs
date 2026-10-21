import test from 'node:test';
import assert from 'node:assert/strict';
import './theme-state.js';
const {safeOverrides, normalizeTheme} = globalThis.XynMacroTheme;

test('theme overrides accept only exposed colour tokens and plain colours', () => {
  assert.deepEqual(safeOverrides({'--bg':'#123456', '--sidebar-w':'0px', position:'fixed', '--accent':'url(https://example.com)', '--text':'red;display:none'}), {'--bg':'#123456'});
});
test('malformed theme shapes fail without applying partial state', () => {
  for(const value of [null, [], true, {color:'invalid'}, {color:34}]) assert.throws(()=>normalizeTheme(value));
});
test('valid themes preserve colours and limit display names', () => {
  const theme = normalizeTheme({color:'#AbCdEf',name:'X'.repeat(100),overrides:{'--accent':'#123456'}});
  assert.equal(theme.name.length,22);
  assert.equal(theme.color,'#AbCdEf');
  assert.deepEqual(theme.overrides,{'--accent':'#123456'});
});
