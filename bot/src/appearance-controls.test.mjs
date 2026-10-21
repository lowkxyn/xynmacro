import assert from 'node:assert/strict';
import test from 'node:test';
import { readFile } from 'node:fs/promises';
import './theme-state.js';
import './appearance-controls.js';

// Minimal DOM/window doubles: the controller only needs element state, listeners and
// the handful of canvas calls one animation frame makes.
function element(id) {
  const el = {
    id, value: '', checked: false, textContent: '', className: '', tabIndex: 0, hidden: false,
    attributes: {}, style: {}, children: [], listeners: {}, files: null,
    width: 0, height: 0, clientWidth: 800, clientHeight: 600,
    addEventListener(event, listener) { (this.listeners[event] ||= []).push(listener); },
    removeEventListener(event, listener) {
      this.listeners[event] = (this.listeners[event] || []).filter(l => l !== listener);
    },
    fire(event, payload = {}) {
      for (const listener of this.listeners[event] || []) listener({ target: el, preventDefault() {}, stopPropagation() {}, ...payload });
    },
    setAttribute(name, value) { this.attributes[name] = value; },
    removeAttribute(name) { delete this.attributes[name]; },
    appendChild(child) { this.children.push(child); return child; },
    append(...nodes) { this.children.push(...nodes); },
    getContext() { return ctx; },
  };
  el.style.setProperty = (name, value) => { el.style[name] = String(value); };
  el.style.removeProperty = (name) => { delete el.style[name]; };
  Object.defineProperty(el, 'innerHTML', { get: () => '', set: () => { el.children.length = 0; } });
  return el;
}

const ctx = {
  fillStyle: '', strokeStyle: '', lineWidth: 0, globalCompositeOperation: '',
  clearRect() {}, beginPath() {}, arc() {}, fill() {}, fillRect() {}, moveTo() {}, lineTo() {}, stroke() {},
  createRadialGradient() { return { addColorStop() {} }; },
};

function fixture({ stored = {}, reducedMotion = false } = {}) {
  const items = new Map(Object.entries(stored));
  const storage = {
    getItem: (key) => (items.has(key) ? items.get(key) : null),
    setItem: (key, value) => items.set(key, String(value)),
  };
  const elements = new Map();
  const created = [];
  const root = element('root');
  root.classList = { contains: () => false, toggle() {} };
  const document = {
    documentElement: root,
    hidden: false,
    listeners: {},
    getElementById(id) {
      if (!elements.has(id)) elements.set(id, element(id));
      return elements.get(id);
    },
    createElement(tag) { const el = element(tag); created.push(el); return el; },
    addEventListener(event, listener) { (this.listeners[event] ||= []).push(listener); },
  };
  const frames = [];
  const window = {
    innerWidth: 800, innerHeight: 600,
    listeners: {},
    matchMedia: () => ({ matches: reducedMotion }),
    getComputedStyle: () => ({ getPropertyValue: () => '#818cf8' }),
    requestAnimationFrame: (fn) => { frames.push(fn); return frames.length; },
    cancelAnimationFrame: (id) => cancelled.push(id),
    addEventListener(event, listener) { (this.listeners[event] ||= []).push(listener); },
    removeEventListener(event, listener) {
      this.listeners[event] = (this.listeners[event] || []).filter(l => l !== listener);
    },
  };
  const cancelled = [];
  const toasts = [];
  const deps = { document, window, storage, showToast: (...args) => toasts.push(args) };
  return { deps, storage, items, elements, created, frames, cancelled, toasts, root, document, window };
}

const preset = (over = {}) => JSON.stringify([{ id: 'p1', name: 'Mine', color: '#3366ff', overrides: {}, ...over }]);
const rgb = (hex) => [1, 3, 5].map(i => parseInt(hex.slice(i, i + 2), 16));

test('a saved preset is re-applied on load so the generated palette exists before use', () => {
  const f = fixture({ stored: { 'dbog-presets': preset(), 'dbog-theme': 'p:p1' } });
  const ui = XynMacroAppearanceControls.create(f.deps);
  // create() alone must not repaint the palette; main.js controls when that happens.
  assert.equal(f.root.style['--accent'], undefined);

  ui.applySavedPreset();
  assert.equal(f.root.attributes['data-theme'], undefined);
  assert.match(f.root.style['--accent'], /^#[0-9a-f]{6}$/);
  assert.match(f.root.style['--bg'], /^#[0-9a-f]{6}$/);
  assert.equal(f.storage.getItem('dbog-theme'), 'p:p1');

  // The editor is seeded from the active preset rather than the default colour.
  assert.equal(f.elements.get('ctColor').value, '#3366ff');
});

test('a built-in theme key sets data-theme and clears generated custom properties', () => {
  const f = fixture({ stored: { 'dbog-presets': preset(), 'dbog-theme': 'p:p1' } });
  const ui = XynMacroAppearanceControls.create(f.deps);
  ui.applySavedPreset();
  ui.applyTheme('rose');
  assert.equal(f.root.attributes['data-theme'], 'rose');
  assert.equal(f.root.style['--accent'], undefined);
  ui.applyTheme('graphite');
  assert.equal(f.root.attributes['data-theme'], undefined);
});

test('a near-grey custom colour stays neutral instead of snapping to red', () => {
  const f = fixture({ stored: { 'dbog-presets': preset({ color: '#808080' }), 'dbog-theme': 'p:p1' } });
  XynMacroAppearanceControls.create(f.deps).applySavedPreset();
  const [r, g, b] = rgb(f.root.style['--accent']);
  assert.ok(Math.max(r, g, b) - Math.min(r, g, b) <= 12, `expected a neutral accent, got ${f.root.style['--accent']}`);

  const vivid = fixture({ stored: { 'dbog-presets': preset({ color: '#ff0000' }), 'dbog-theme': 'p:p1' } });
  XynMacroAppearanceControls.create(vivid.deps).applySavedPreset();
  const [vr, , vb] = rgb(vivid.root.style['--accent']);
  assert.ok(vr - vb > 40, 'a saturated pick must stay saturated');
});

test('deleting the active preset removes it and falls back to graphite', () => {
  const f = fixture({ stored: { 'dbog-presets': preset(), 'dbog-theme': 'p:p1' } });
  XynMacroAppearanceControls.create(f.deps);
  const grid = f.elements.get('themeGrid');
  assert.equal(grid.children.length, 9);                       // 8 built-ins + 1 preset
  const del = f.created.find(el => el.className === 'theme-del');
  del.fire('click');

  assert.equal(f.storage.getItem('dbog-presets'), '[]');
  assert.equal(f.storage.getItem('dbog-theme'), 'graphite');
  assert.equal(f.root.style['--accent'], undefined);
  assert.deepEqual(f.toasts, [['Preset deleted']]);
  assert.equal(f.elements.get('themeGrid').children.length, 8);
});

test('a missing preset id falls back to graphite instead of leaving a dead key', () => {
  const f = fixture({ stored: { 'dbog-theme': 'p:gone' } });
  const ui = XynMacroAppearanceControls.create(f.deps);
  ui.applyTheme('p:gone');
  assert.equal(f.storage.getItem('dbog-theme'), 'graphite');
  assert.equal(f.root.attributes['data-theme'], undefined);
});

test('the canvas background starts, honours the saved speed and stops on "none"', () => {
  const f = fixture({ stored: { 'dbog-bg': 'flow', 'dbog-bg-speed': '2' } });
  XynMacroAppearanceControls.create(f.deps);
  assert.equal(f.root.attributes['data-bg'], 'flow');
  assert.equal(f.root.style['--bg-speed'], '2');
  assert.equal(f.frames.length, 1);
  assert.equal(f.window.listeners.resize.length, 1);

  const sel = f.elements.get('bgEffect');
  sel.value = 'none';
  sel.fire('change');
  assert.equal(f.storage.getItem('dbog-bg'), 'none');
  assert.equal(f.root.attributes['data-bg'], undefined);
  assert.equal(f.cancelled.length, 1);
  assert.equal(f.window.listeners.resize.length, 0);           // the resize listener is released
});

test('reduced motion keeps the canvas effects off while still setting the CSS background', () => {
  const f = fixture({ stored: { 'dbog-bg': 'particles' }, reducedMotion: true });
  XynMacroAppearanceControls.create(f.deps);
  assert.equal(f.root.attributes['data-bg'], 'particles');
  assert.equal(f.frames.length, 0);
});

test('theme import enforces the 64 KB cap and the validated colour tokens', async () => {
  const f = fixture();
  XynMacroAppearanceControls.create(f.deps);
  const file = f.elements.get('ctFile');
  const settle = () => new Promise(resolve => setImmediate(resolve));

  file.files = [{ size: 70000, text: async () => '{"color":"#112233"}' }];
  file.fire('change');
  await settle();
  assert.deepEqual(f.toasts.at(-1), ['Invalid theme file', 'err']);

  const payload = JSON.stringify({ name: 'Ok', color: '#112233', overrides: { '--accent': '#445566', '--evil': 'url(x)' } });
  file.files = [{ size: payload.length, text: async () => payload }];
  file.fire('change');
  await settle();
  assert.deepEqual(f.toasts.at(-1), ['Imported — name it and hit Save', 'ok']);
  assert.equal(f.elements.get('ctColor').value, '#112233');
  assert.equal(f.root.style['--accent'], '#445566');           // allowed override wins
  assert.equal(f.root.style['--evil'], undefined);             // unknown token is dropped
});

test('main.js delegates the appearance surface to the controller loaded before it', async () => {
  const html = await readFile(new URL('index.html', import.meta.url), 'utf8');
  const main = await readFile(new URL('main.js', import.meta.url), 'utf8');
  assert.ok(html.includes('src="appearance-controls.js"'));
  assert.ok(html.indexOf('appearance-controls.js') < html.indexOf('main.js'));
  assert.equal(main.split('XynMacroAppearanceControls.create(').length - 1, 1);
  assert.ok(main.includes('appearance.applySavedPreset();'));
  assert.ok(main.includes('Object.entries(appearance.THEMES)'));
  assert.ok(main.includes('appearance.applyTheme(key)'));
  // showToast is defined after the controller is created, so it must be wrapped.
  assert.ok(main.indexOf('showToast: (message, type) => window.showToast(message, type)') < main.indexOf('window.showToast = '));
  for (const moved of ['function _genTheme', 'function _loadPresets', 'function _setupBackground', 'const _flow =']) {
    assert.ok(!main.includes(moved), `${moved} should now live in appearance-controls.js`);
  }
});
