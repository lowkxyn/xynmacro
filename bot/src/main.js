/* Apply saved appearance before first paint. */
(function(){var u=localStorage.getItem('xmacro-ui-style')||'classic';if(u==='aero')document.documentElement.setAttribute('data-ui','aero');var t=localStorage.getItem('dbog-theme');if(t&&t!=='graphite'&&t!=='custom'&&t.indexOf('p:')!==0)document.documentElement.setAttribute('data-theme',t);var b=localStorage.getItem('dbog-bg')||'flow';if(b!=='none')document.documentElement.setAttribute('data-bg',b)})();

/* Splash: stays until Python sidecar reports healthy (Rust fires `backend-ready`)
   OR a hard cap (8s) — whichever comes first. Minimum hold of 900ms so the splash
   doesn't blink past on a hot reload. */
window.addEventListener('DOMContentLoaded', () => {
  const splash = document.getElementById('splash');
  if (!splash) return;
  // Step the caption through the load stages while the bundled engine unpacks and the
  // backend starts. Real granular progress isn't available (the frozen engine doesn't
  // report it), so the stages advance on a timer and stop the moment the backend is ready.
  const hint = document.getElementById('splashHint');
  const stages = ['Starting up…', 'Unpacking engine…', 'Starting backend…', 'Loading settings…'];
  let si = 0;
  if (hint) { hint.textContent = stages[0]; hint.style.opacity = '1'; }
  const stageTimer = setInterval(() => {
    si = Math.min(si + 1, stages.length - 1);
    if (hint) hint.textContent = stages[si];
  }, 1400);
  const startedAt = Date.now();
  let hidden = false;
  function hide() {
    if (hidden) return;
    hidden = true;
    clearInterval(stageTimer);
    const held = Date.now() - startedAt;
    const wait = Math.max(0, 900 - held);
    setTimeout(() => {
      splash.classList.add('hide');
      setTimeout(() => { splash.remove(); }, 500);
      celebrateWSpain();
    }, wait);
  }
  window.addEventListener('backend-ready', hide, { once: true });
  // Hard cap so a stuck sidecar doesn't lock the UI forever.
  setTimeout(hide, 8000);
});

/* One-time "W spain" moment: a centred card plus an emoji shower as the splash
   fades. Fires once per install — the flag lives in ACTIVE_PREFERENCE_KEYS, so a
   config reset replays it. Everything is pointer-events:none and self-removing. */
const WSPAIN_SEEN_KEY = 'xmacro-wspain-seen';
function celebrateWSpain() {
  const frame = document.querySelector('.window-frame');
  if (!frame || localStorage.getItem(WSPAIN_SEEN_KEY)) return;
  localStorage.setItem(WSPAIN_SEEN_KEY, '1');
  if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;

  const pop = document.createElement('div');
  pop.className = 'wspain-pop';
  pop.setAttribute('aria-hidden', 'true');
  pop.innerHTML = 'W spain<span class="flag-es"></span>';
  frame.appendChild(pop);
  setTimeout(() => pop.remove(), 2600);

  spawnLaunchConfetti();
}

/* One emoji shower. Purely decorative: the layer is pointer-events:none and
   tears itself down, so nothing lingers in the DOM. */
// No flag here — it would fall as the literal letters "ES" on Windows.
const CONFETTI_EMOJI = ['🎉', '🎊', '✨', '🥳', '🎈'];
function spawnLaunchConfetti(count = 26) {
  const frame = document.querySelector('.window-frame');
  if (!frame || window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;

  const layer = document.createElement('div');
  layer.className = 'confetti-layer';
  layer.setAttribute('aria-hidden', 'true');

  let longest = 0;
  for (let i = 0; i < count; i++) {
    const bit = document.createElement('span');
    bit.className = 'confetti-bit';
    bit.textContent = CONFETTI_EMOJI[Math.floor(Math.random() * CONFETTI_EMOJI.length)];
    const dur = 2.2 + Math.random() * 1.8;
    const delay = Math.random() * 1.2;
    bit.style.left = (Math.random() * 100).toFixed(2) + '%';
    bit.style.setProperty('--bit-size', (13 + Math.random() * 12).toFixed(1) + 'px');
    bit.style.setProperty('--bit-dur', dur.toFixed(2) + 's');
    bit.style.setProperty('--bit-delay', delay.toFixed(2) + 's');
    bit.style.setProperty('--bit-spin', (Math.random() < 0.5 ? -1 : 1) * (180 + Math.random() * 540) + 'deg');
    longest = Math.max(longest, dur + delay);
    layer.appendChild(bit);
  }

  frame.appendChild(layer);
  setTimeout(() => layer.remove(), longest * 1000 + 200);
}

/* Hard failure from the Rust shell: the sidecar never came up. Replace the silent
   "loaded but dead" state (every poll fails quietly) with a visible, explained bar. */
window.addEventListener('backend-error', (e) => {
  const msg = (e.detail && e.detail.message) || 'The macro backend failed to start.';
  const splash = document.getElementById('splash');
  if (splash) { splash.classList.add('hide'); setTimeout(() => splash.remove(), 500); }
  if (document.getElementById('backendErrorBar')) return;
  const bar = document.createElement('div');
  bar.id = 'backendErrorBar';
  bar.style.cssText = 'position:fixed;left:0;right:0;bottom:0;z-index:9999;padding:10px 16px;' +
    'background:#3a0d12;border-top:1px solid #b9344a;color:#ffd9df;font-size:12.5px;' +
    'font-family:Inter,system-ui,sans-serif;line-height:1.5';
  bar.innerHTML = '<strong>Backend not running.</strong> ' + msg +
    ' &nbsp;Confirm the install finished, then restart the app. If Windows still blocks hotkeys or game input, try Run as administrator.';
  document.body.appendChild(bar);
});

const { invoke } = window.__TAURI__.core;
const ACTIVE_PREFERENCE_KEYS = [
  'dbog-theme',
  'dbog-bg',
  'dbog-bg-speed',
  'xmacro-ui-style',
  'dbog-sidebar-width',
  'xmacro-auto-update',
  'xmacro-update-ignored-version',
  'xmacro-changelog-seen',
  'xmacro-announcement-seen',
  'xmacro-welcome-seen',
  'xmacro-wspain-seen',
];

// Deleting the visible macro_config.json is a complete reset on next launch.
// Rust checks for it before the sidecar recreates the file and exposes this
// one-shot flag so WebView preferences cannot survive a manual config reset.
invoke('take_config_reset_flag').then((reset) => {
  if (!reset) return;
  ACTIVE_PREFERENCE_KEYS.forEach((key) => localStorage.removeItem(key));
  window.location.reload();
}).catch(() => {});

/* Reveal the window only once the first frames have painted (the splash is up by
   then), so there's no blank/transparent frame on open. Rust shows it after 1.2s
   as a fallback if this never runs. */
requestAnimationFrame(() => requestAnimationFrame(() => { invoke('wc', { action: 'show' }).catch(() => {}); }));

/* When maximized, drop the gutter/shadow/rounding so it's full-bleed like a normal
   maximized window. Toggles a `maximized` class on <html> on every resize. */
(function () {
  try {
    const win = window.__TAURI__.window.getCurrentWindow();
    const sync = async () => {
      try { document.documentElement.classList.toggle('maximized', await win.isMaximized()); } catch (e) {}
    };
    win.onResized(sync);
    sync();
  } catch (e) {}
})();

/* Custom drag handler — data-tauri-drag-region wasn't catching, and
   window.__TAURI__.window may not be loaded with withGlobalTauri. Use invoke
   to call the Rust `wc` command which calls start_dragging() directly. */
window.addEventListener('DOMContentLoaded', () => {
  const dragZone = document.querySelector('.titlebar-drag');
  if (!dragZone) return;
  dragZone.addEventListener('mousedown', (e) => {
    if (e.button !== 0) return;
    if (e.target.closest('button, input, select, textarea, a')) return;
    invoke('wc', { action: 'drag' }).catch(() => {});
  });
});

/* Window control buttons (custom title bar) */
function _wcSend(action, value) {
  invoke('wc', value !== undefined ? { action, value } : { action }).catch(() => {});
}
window.wcMinimize = () => _wcSend('minimize');
window.wcMaximize = () => _wcSend('maximize');
window.wcClose    = () => _wcSend('close');

let _isOnTop = false;
window.wcOnTop = () => {
  _isOnTop = !_isOnTop;
  _wcSend('ontop', _isOnTop);
  document.getElementById('btnOnTop')?.classList.toggle('active', _isOnTop);
};

let _isCompact = false;
window.wcCompact = () => {
  const frame = document.querySelector('.window-frame');
  const compactButton = document.getElementById('btnCompact');
  _isCompact = !_isCompact;
  if (_isCompact) {
    frame.classList.add('compact');
    compactButton?.classList.add('active');
    if (compactButton) {
      compactButton.title = 'Expand';
      compactButton.setAttribute('aria-label', 'Expand');
    }
    _wcSend('compact');
  } else {
    frame.classList.remove('compact');
    compactButton?.classList.remove('active');
    if (compactButton) {
      compactButton.title = 'Roll up';
      compactButton.setAttribute('aria-label', 'Roll up');
    }
    _wcSend('uncompact');
  }
};

(() => {
  let pollTimer = null;

  function _classifyLog(msg) {
    const m = msg.toLowerCase();
    if (/\berror\b|\bfail(?:ed)?\b|\bexception\b|\bcould not\b/.test(m)) return 'log-err';
    if (/\btimeout\b|\bskip(?:ping)?\b|\bwarn(?:ing)?\b|\bnot found\b|\bmissing\b|\bempty\b|\bunconfirmed\b/.test(m)) return 'log-warn';
    if (/\bperfect\b|\bfound\b|\bdetected\b|\bpressed\b|\bclick(?:ed)?\b/.test(m)) return 'log-ok';
    return '';
  }
  let statOrder = [];
  let availableStats = [];
  let lastOrderSig = '';
  let lastStatsSig = '';
  let previousOrderTops = new Map();

  /* Themes — `graphite` is the default and maps to :root (no data-theme attribute). */
  const THEMES = {
    graphite: { name: 'Graphite',      accent: '#b4bbc8', from: '#3a3a42', to: '#6a6a74', bg: '#0d0d10' },
    indigo:   { name: 'Indigo Night',  accent: '#818cf8', from: '#2563eb', to: '#7c3aed', bg: '#060b16' },
    midnight: { name: 'Midnight',      accent: '#818cf8', from: '#2563eb', to: '#7c3aed', bg: '#010204' },
    cyber:    { name: 'Cyber Teal',    accent: '#22d3ee', from: '#0891b2', to: '#06b6d4', bg: '#040d14' },
    emerald:  { name: 'Emerald',       accent: '#34d399', from: '#059669', to: '#10b981', bg: '#040e0a' },
    rose:     { name: 'Rose',          accent: '#f472b6', from: '#db2777', to: '#ec4899', bg: '#10040a' },
    solar:    { name: 'Solar Flare',   accent: '#fbbf24', from: '#d97706', to: '#f59e0b', bg: '#100a04' },
    arctic:   { name: 'Arctic',        accent: '#7dd3fc', from: '#0284c7', to: '#38bdf8', bg: '#080c14' },
  };

  // Canvas background effects (Flow blobs and Particles), coloured from the active
  // theme. Only runs while one of those effects is selected.
  const _flow = (() => {
    let canvas, ctx, raf = 0, items = [], w = 0, h = 0, running = false, mode = 'flow', speed = 1;
    const _cvar = (n) => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
    const _rgba = (c, a) => {
      c = (c || '').trim();
      if (c[0] === '#') { const [r, g, b] = _hexToRgb(c); return `rgba(${r},${g},${b},${a})`; }
      const m = c.match(/\d+/g);
      return m ? `rgba(${m[0]},${m[1]},${m[2]},${a})` : `rgba(129,140,248,${a})`;
    };
    const cols = () => [_cvar('--accent'), _cvar('--grad-to'), _cvar('--accent2')].filter(Boolean);
    function resize() {
      if (!canvas) return;
      w = canvas.width = canvas.clientWidth || window.innerWidth;
      h = canvas.height = canvas.clientHeight || window.innerHeight;
    }
    function seed() {
      const cs = cols(), big = Math.max(w, h);
      if (mode === 'particles') {
        items = Array.from({ length: 80 }, () => ({
          x: Math.random() * w, y: Math.random() * h,
          vy: 0.24 + Math.random() * 0.66, vx: (Math.random() - 0.5) * 0.3,
          r: 1 + Math.random() * 2.2, a: 0.2 + Math.random() * 0.55,
          c: cs[Math.floor(Math.random() * cs.length)] || '#818cf8',
        }));
      } else if (mode === 'starfield') {
        items = Array.from({ length: 130 }, () => {
          const z = 0.25 + Math.random() * 0.75;
          return { x: Math.random() * w, y: Math.random() * h, z,
            r: 0.6 + z * 1.8, a: 0.25 + z * 0.6,
            c: cs[Math.floor(Math.random() * cs.length)] || '#818cf8' };
        });
      } else if (mode === 'constellation') {
        items = Array.from({ length: 46 }, () => ({
          x: Math.random() * w, y: Math.random() * h,
          vx: (Math.random() - 0.5) * 0.4, vy: (Math.random() - 0.5) * 0.4,
          c: cs[0] || '#818cf8',
        }));
      } else {
        items = Array.from({ length: 6 }, (_, i) => ({
          x: Math.random() * w, y: Math.random() * h,
          vx: (Math.random() - 0.5) * 1.32, vy: (Math.random() - 0.5) * 1.32,
          r: big * (0.3 + Math.random() * 0.22),
          c: cs[i % cs.length] || '#818cf8',
        }));
      }
    }
    function frame() {
      if (!running || !ctx) return;
      ctx.clearRect(0, 0, w, h);
      if (mode === 'particles') {
        ctx.globalCompositeOperation = 'source-over';
        for (const p of items) {
          p.y -= p.vy * speed; p.x += p.vx * speed;
          if (p.y < -4) { p.y = h + 4; p.x = Math.random() * w; }
          if (p.x < -4) p.x = w + 4; else if (p.x > w + 4) p.x = -4;
          ctx.fillStyle = _rgba(p.c, p.a);
          ctx.beginPath(); ctx.arc(p.x, p.y, p.r, 0, Math.PI * 2); ctx.fill();
        }
      } else if (mode === 'starfield') {
        ctx.globalCompositeOperation = 'source-over';
        for (const s of items) {
          s.y += s.z * 0.7 * speed; s.x += s.z * 0.25 * speed;
          if (s.y > h + 2) { s.y = -2; s.x = Math.random() * w; }
          if (s.x > w + 2) s.x = -2;
          ctx.fillStyle = _rgba(s.c, s.a);
          ctx.beginPath(); ctx.arc(s.x, s.y, s.r, 0, Math.PI * 2); ctx.fill();
        }
      } else if (mode === 'constellation') {
        ctx.globalCompositeOperation = 'source-over';
        const D = 150;
        for (const n of items) {
          n.x += n.vx * speed; n.y += n.vy * speed;
          if (n.x < 0 || n.x > w) n.vx *= -1;
          if (n.y < 0 || n.y > h) n.vy *= -1;
        }
        for (let i = 0; i < items.length; i++) {
          for (let j = i + 1; j < items.length; j++) {
            const dx = items[i].x - items[j].x, dy = items[i].y - items[j].y;
            const d = Math.hypot(dx, dy);
            if (d < D) {
              ctx.strokeStyle = _rgba(items[i].c, (1 - d / D) * 0.35);
              ctx.lineWidth = 1;
              ctx.beginPath(); ctx.moveTo(items[i].x, items[i].y); ctx.lineTo(items[j].x, items[j].y); ctx.stroke();
            }
          }
        }
        for (const n of items) {
          ctx.fillStyle = _rgba(n.c, 0.7);
          ctx.beginPath(); ctx.arc(n.x, n.y, 1.8, 0, Math.PI * 2); ctx.fill();
        }
      } else {
        ctx.globalCompositeOperation = 'lighter';
        for (const b of items) {
          b.x += b.vx * speed; b.y += b.vy * speed;
          if (b.x < -b.r) b.x = w + b.r; else if (b.x > w + b.r) b.x = -b.r;
          if (b.y < -b.r) b.y = h + b.r; else if (b.y > h + b.r) b.y = -b.r;
          const g = ctx.createRadialGradient(b.x, b.y, 0, b.x, b.y, b.r);
          g.addColorStop(0, _rgba(b.c, 0.42));
          g.addColorStop(1, _rgba(b.c, 0));
          ctx.fillStyle = g;
          ctx.fillRect(b.x - b.r, b.y - b.r, b.r * 2, b.r * 2);
        }
      }
      raf = requestAnimationFrame(frame);
    }
    return {
      start(m) {
        canvas = document.getElementById('bgCanvas');
        if (!canvas) return;
        mode = m || 'flow';
        if (running) { resize(); seed(); return; }
        ctx = canvas.getContext('2d');
        resize(); seed();
        running = true;
        window.addEventListener('resize', resize);
        frame();
      },
      stop() {
        running = false;
        if (raf) { cancelAnimationFrame(raf); raf = 0; }
        window.removeEventListener('resize', resize);
        if (ctx && canvas) ctx.clearRect(0, 0, canvas.width, canvas.height);
      },
      recolor() { if (running) { resize(); seed(); } },
      setSpeed(s) { speed = s || 1; },
    };
  })();

  // A custom theme is a single colour; the whole palette is generated from it the
  // same way the presets are built (neutral-dark surfaces + a vivid accent), so it
  // recolours the interface instead of looking like a flat overlay.
  function _hexToRgb(h) {
    h = (h || '').replace('#', '');
    if (h.length === 3) h = [...h].map(x => x + x).join('');
    const n = parseInt(h || '0', 16);
    return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
  }
  function _hexToHsl(hex) {
    let [r, g, b] = _hexToRgb(hex); r /= 255; g /= 255; b /= 255;
    const max = Math.max(r, g, b), min = Math.min(r, g, b), d = max - min;
    let h = 0, s = 0; const l = (max + min) / 2;
    if (d) {
      s = l > 0.5 ? d / (2 - max - min) : d / (max + min);
      if (max === r) h = (g - b) / d + (g < b ? 6 : 0);
      else if (max === g) h = (b - r) / d + 2;
      else h = (r - g) / d + 4;
      h *= 60;
    }
    return [h, s * 100, l * 100];
  }
  function _hslHex(h, s, l) {
    s = Math.max(0, Math.min(100, s)) / 100; l = Math.max(0, Math.min(100, l)) / 100;
    const k = n => (n + h / 30) % 12, a = s * Math.min(l, 1 - l);
    const f = n => l - a * Math.max(-1, Math.min(k(n) - 3, 9 - k(n), 1));
    const to = x => Math.round(255 * x).toString(16).padStart(2, '0');
    return '#' + to(f(0)) + to(f(8)) + to(f(4));
  }
  function _hslRgba(h, s, l, a) {
    const [r, g, b] = _hexToRgb(_hslHex(h, s, l));
    return `rgba(${r},${g},${b},${a})`;
  }
  const _GEN_KEYS = ['--bg','--bg-alt','--bg-card','--bg-input','--bg-hover','--bg-nav','--border','--border-light','--border-input','--accent','--accent2','--accent-dim','--text','--text2','--text3','--grad-from','--grad-to','--grad-shadow','--glow1','--glow2','--glow3'];
  function _genTheme(accentHex) {
    const [h, s] = _hexToHsl(accentHex);
    const neutral = s < 8;                       // near-grey pick: stay neutral, don't snap to red (hue 0)
    const aS = neutral ? 7 : Math.max(35, Math.min(95, s));   // accent saturation
    const bS = neutral ? 4 : Math.min(aS, 40) * 0.7;          // surfaces: subtle tint, kept dark/neutral
    return {
      '--bg':       _hslHex(h, bS, 5),
      '--bg-alt':   _hslHex(h, bS, 9),
      '--bg-card':  _hslRgba(h, bS * 0.85, 11, 0.88),
      '--bg-input': _hslHex(h, bS, 13),
      '--bg-hover': _hslHex(h, bS, 18),
      '--bg-nav':   _hslRgba(h, bS, 4, 0.96),
      '--border':       _hslRgba(h, aS, 62, 0.16),
      '--border-light': _hslRgba(h, aS, 62, 0.08),
      '--border-input': _hslRgba(h, aS, 62, 0.20),
      '--accent':     _hslHex(h, aS, 72),
      '--accent2':    _hslHex(h, aS, 62),
      '--accent-dim': _hslRgba(h, aS, 72, 0.12),
      '--text':  _hslHex(h, Math.min(aS, 45), 92),
      '--text2': _hslHex(h, Math.min(aS, 35), 73),
      '--text3': _hslHex(h, Math.min(aS, 22), 48),
      '--grad-from':   _hslHex(h, Math.min(aS + 8, 95), 50),
      '--grad-to':     _hslHex((h + 20) % 360, Math.min(aS, 85), 56),
      '--grad-shadow': _hslRgba(h, aS, 50, 0.28),
      '--glow1': _hslRgba(h, aS, 55, 0.14),
      '--glow2': _hslRgba((h + 30) % 360, aS, 55, 0.10),
      '--glow3': _hslRgba(h, aS, 55, 0.05),
    };
  }
  function _clearCustomVars() {
    _GEN_KEYS.forEach(k => document.documentElement.style.removeProperty(k));
  }
  // Custom themes are a list of named presets. Migrate any old single custom theme.
  function _loadPresets() {
    try {
      const p = JSON.parse(localStorage.getItem('dbog-presets') || 'null');
      if (Array.isArray(p)) return p;
    } catch (e) {}
    try {
      const old = JSON.parse(localStorage.getItem('dbog-custom-theme') || 'null');
      if (old && old.color) {
        const list = [{ id: 'c' + Date.now(), name: 'Custom', color: old.color, overrides: old.overrides || {} }];
        localStorage.setItem('dbog-presets', JSON.stringify(list));
        return list;
      }
    } catch (e) {}
    return [];
  }
  function _savePresets(list) { localStorage.setItem('dbog-presets', JSON.stringify(list)); }
  function _presetById(id) { return _loadPresets().find(p => p.id === id) || null; }

  function applyTheme(key) {
    _clearCustomVars();
    if (key === 'custom' || (key && key.indexOf('p:') === 0)) {
      const p = key === 'custom' ? _loadPresets()[0] : _presetById(key.slice(2));
      if (p && p.color) {
        document.documentElement.removeAttribute('data-theme');
        const vars = Object.assign(_genTheme(p.color), p.overrides || {});
        for (const [k, v] of Object.entries(vars)) document.documentElement.style.setProperty(k, v);
        key = 'p:' + p.id;
      } else {
        key = 'graphite';
        document.documentElement.removeAttribute('data-theme');
      }
    } else if (key === 'graphite') {
      document.documentElement.removeAttribute('data-theme');
    } else {
      document.documentElement.setAttribute('data-theme', key);
    }
    localStorage.setItem('dbog-theme', key);
    renderThemes();
    _flow.recolor();
  }

  function _themeCard(key, name, accent, from, to, current, deletable) {
    const card = document.createElement('div');
    card.className = 'theme-card' + (key === current ? ' active' : '');
    card.tabIndex = 0;
    card.setAttribute('role', 'button');
    card.setAttribute('aria-label', `Theme: ${name}`);
    card.setAttribute('aria-pressed', key === current ? 'true' : 'false');

    const preview = document.createElement('div');
    preview.className = 'theme-preview';
    preview.style.background = `linear-gradient(120deg, ${from}, ${to})`;
    if (deletable) {
      const del = document.createElement('button');
      del.className = 'theme-del';
      del.textContent = '×';
      del.title = 'Delete preset';
      del.setAttribute('aria-label', `Delete ${name} theme`);
      del.addEventListener('click', (e) => {
        e.stopPropagation();
        const id = key.slice(2);
        _savePresets(_loadPresets().filter(p => p.id !== id));
        if ((localStorage.getItem('dbog-theme') || '') === key) applyTheme('graphite');
        else renderThemes();
        showToast('Preset deleted');
      });
      preview.appendChild(del);
    }

    const meta = document.createElement('div');
    meta.className = 'theme-meta';
    const dot = document.createElement('span');
    dot.className = 'theme-accent-dot';
    dot.style.background = accent;
    const nm = document.createElement('span');
    nm.className = 'theme-card-name';
    nm.textContent = name;
    meta.append(dot, nm);

    card.append(preview, meta);
    card.addEventListener('click', () => applyTheme(key));
    card.addEventListener('keydown', (event) => {
      if (event.target !== card) return;
      if (event.key !== 'Enter' && event.key !== ' ') return;
      event.preventDefault();
      applyTheme(key);
    });
    return card;
  }

  function renderThemes() {
    const grid = document.getElementById('themeGrid');
    if (!grid) return;
    const current = localStorage.getItem('dbog-theme') || 'graphite';
    grid.innerHTML = '';
    for (const [key, t] of Object.entries(THEMES)) {
      grid.appendChild(_themeCard(key, t.name, t.accent, t.from, t.to, current, false));
    }
    for (const p of _loadPresets()) {
      const v = Object.assign(_genTheme(p.color), p.overrides || {});
      grid.appendChild(_themeCard('p:' + p.id, p.name, v['--accent'], v['--grad-from'], v['--grad-to'], current, true));
    }
  }

  // Tokens exposed in the Advanced section, layered on top of the generated palette.
  const _ADV_VARS = {
    '--accent': 'advAccent', '--accent2': 'advAccent2', '--bg': 'advBg',
    '--text': 'advText', '--text2': 'advText2', '--grad-from': 'advGradFrom', '--grad-to': 'advGradTo',
  };
  function _setupCustomTheme() {
    const colorEl = document.getElementById('ctColor');
    if (!colorEl) return;
    let overrides = {};

    function preview() {
      _clearCustomVars();
      document.documentElement.removeAttribute('data-theme');
      const vars = Object.assign(_genTheme(colorEl.value), overrides);
      for (const [k, v] of Object.entries(vars)) document.documentElement.style.setProperty(k, v);
    }
    // Sync the advanced pickers to the current effective palette (generated + overrides).
    function seedAdv() {
      const vars = Object.assign(_genTheme(colorEl.value), overrides);
      for (const [varName, id] of Object.entries(_ADV_VARS)) {
        const el = document.getElementById(id);
        if (el && vars[varName] && vars[varName][0] === '#') el.value = vars[varName];
      }
    }

    // Changing the base colour regenerates everything and drops the fine-tune overrides.
    colorEl.addEventListener('input', () => { overrides = {}; preview(); seedAdv(); });
    for (const [varName, id] of Object.entries(_ADV_VARS)) {
      document.getElementById(id)?.addEventListener('input', (e) => {
        overrides[varName] = e.target.value;
        preview();
      });
    }

    const nameEl = document.getElementById('ctName');
    document.getElementById('ctSave')?.addEventListener('click', () => {
      const list = _loadPresets();
      const name = (nameEl && nameEl.value.trim()) || ('Custom ' + (list.length + 1));
      const id = 'c' + Date.now();
      list.push({ id, name, color: colorEl.value, overrides: { ...overrides } });
      _savePresets(list);
      applyTheme('p:' + id);
      if (nameEl) nameEl.value = '';
      showToast(`Saved "${name}"`, 'ok');
    });
    document.getElementById('ctExport')?.addEventListener('click', () => {
      const data = { name: (nameEl && nameEl.value.trim()) || 'Custom', color: colorEl.value, overrides };
      const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
      const a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      a.download = data.name.replace(/[^\w-]+/g, '-').toLowerCase() + '-theme.json';
      a.click();
      URL.revokeObjectURL(a.href);
      showToast('Theme exported', 'ok');
    });
    const fileEl = document.getElementById('ctFile');
    document.getElementById('ctImport')?.addEventListener('click', () => fileEl?.click());
    fileEl?.addEventListener('change', async () => {
      const f = fileEl.files && fileEl.files[0];
      if (!f) return;
      try {
        const ct = JSON.parse(await f.text());
        if (!ct || !ct.color) throw new Error('bad file');
        colorEl.value = ct.color;
        overrides = (ct.overrides && typeof ct.overrides === 'object') ? ct.overrides : {};
        if (nameEl && ct.name) nameEl.value = ct.name;
        preview();
        seedAdv();
        showToast('Imported — name it and hit Save', 'ok');
      } catch (e) { showToast('Invalid theme file', 'err'); }
      fileEl.value = '';
    });
    document.getElementById('ctClear')?.addEventListener('click', () => {
      colorEl.value = '#818cf8';
      overrides = {};
      if (nameEl) nameEl.value = '';
      preview();
      seedAdv();
      showToast('Editor reset');
    });

    // Seed the editor from the active preset, if one is selected.
    const activeKey = localStorage.getItem('dbog-theme') || '';
    const activePreset = activeKey.indexOf('p:') === 0 ? _presetById(activeKey.slice(2)) : null;
    if (activePreset) {
      colorEl.value = activePreset.color;
      overrides = activePreset.overrides || {};
    }
    seedAdv();
  }

  function _setupBackground() {
    const sel = document.getElementById('bgEffect');
    const speedEl = document.getElementById('bgSpeed');
    const CANVAS_MODES = ['flow', 'particles', 'starfield', 'constellation'];
    const apply = (v) => {
      if (!v || v === 'none') document.documentElement.removeAttribute('data-bg');
      else document.documentElement.setAttribute('data-bg', v);
      if (CANVAS_MODES.includes(v)) _flow.start(v); else _flow.stop();
    };
    // Speed slider scales CSS animations (via --bg-speed) and the canvas effects together.
    const savedSpeed = parseFloat(localStorage.getItem('dbog-bg-speed') || '1.6') || 1.6;
    document.documentElement.style.setProperty('--bg-speed', savedSpeed);
    _flow.setSpeed(savedSpeed);
    if (speedEl) {
      speedEl.value = savedSpeed;
      speedEl.addEventListener('input', () => {
        const s = parseFloat(speedEl.value) || 1;
        localStorage.setItem('dbog-bg-speed', s);
        document.documentElement.style.setProperty('--bg-speed', s);
        _flow.setSpeed(s);
      });
    }
    const cur = localStorage.getItem('dbog-bg') || 'flow';
    apply(cur);
    if (sel) {
      sel.value = cur;
      sel.addEventListener('change', () => {
        localStorage.setItem('dbog-bg', sel.value);
        apply(sel.value);
      });
    }
  }

  renderThemes();
  _setupCustomTheme();
  _setupBackground();
  function applyUiStyle(style, resetScroll = true) {
    const selected = style === 'aero' ? 'aero' : 'classic';
    document.documentElement.toggleAttribute('data-ui', selected === 'aero');
    if (selected === 'aero') document.documentElement.setAttribute('data-ui', 'aero');
    localStorage.setItem('xmacro-ui-style', selected);
    document.querySelectorAll('[data-ui-style]').forEach((button) => {
      button.classList.toggle('active', button.dataset.uiStyle === selected);
      button.setAttribute('aria-pressed', button.dataset.uiStyle === selected ? 'true' : 'false');
    });
    if (resetScroll) _resetScroll();
  }
  const uiStyle = localStorage.getItem('xmacro-ui-style') === 'aero' ? 'aero' : 'classic';
  document.querySelectorAll('[data-ui-style]').forEach((button) => {
    button.addEventListener('click', () => applyUiStyle(button.dataset.uiStyle));
  });
  applyUiStyle(uiStyle, false);
  // Re-apply a saved preset/custom theme so the generated palette is computed on load.
  { const _t = localStorage.getItem('dbog-theme') || 'graphite'; if (_t === 'custom' || _t.indexOf('p:') === 0) applyTheme(_t); }

  /* Navigation */
  const navBtns = document.querySelectorAll('.nav-btn[data-view]');
  const views   = document.querySelectorAll('.view');

  function switchView(target) {
    navBtns.forEach(b => {
      b.classList.toggle('active', b.dataset.view === target);
    });
    views.forEach(v => {
      v.classList.toggle('active', v.id === target);
    });
  }

  function openView(target) {
    switchView(target);
    _resetScroll();
  }

  const _modalReturnFocus = new WeakMap();
  const _modalOverlayIds = [
    'paletteBackdrop',
    'shortcutsBackdrop',
    'startWarnOverlay',
    'welcomeOverlay',
    'changelogOverlay',
    'announcementOverlay',
  ];

  function _focusModal(overlay, preferredTarget = null) {
    if (!overlay) return;
    const active = document.activeElement;
    if (active instanceof HTMLElement && !overlay.contains(active)) {
      _modalReturnFocus.set(overlay, active);
    }
    const dialog = overlay.querySelector('[role="dialog"]');
    const preferred = typeof preferredTarget === 'string'
      ? overlay.querySelector(preferredTarget)
      : preferredTarget;
    requestAnimationFrame(() => (preferred || dialog)?.focus());
  }

  function _restoreModalFocus(overlay) {
    const target = overlay && _modalReturnFocus.get(overlay);
    if (overlay) _modalReturnFocus.delete(overlay);
    if (target?.isConnected) requestAnimationFrame(() => target.focus());
  }

  function _topOpenModalOverlay() {
    for (const id of _modalOverlayIds) {
      const overlay = document.getElementById(id);
      if (overlay?.classList.contains('open')) return overlay;
    }
    return null;
  }

  function _trapModalFocus(event) {
    const overlay = _topOpenModalOverlay();
    const dialog = overlay?.querySelector('[role="dialog"]');
    if (!dialog) return false;
    const focusable = [...dialog.querySelectorAll(
      'button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'
    )].filter((element) => !element.hidden && element.getAttribute('aria-hidden') !== 'true');
    if (!focusable.length) {
      event.preventDefault();
      dialog.focus();
      return true;
    }
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (!dialog.contains(document.activeElement)) {
      event.preventDefault();
      (event.shiftKey ? last : first).focus();
    } else if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
    return true;
  }

  navBtns.forEach(btn => {
    btn.addEventListener('click', () => openView(btn.dataset.view));
  });

  /* Smooth scroll via translate3d on .main-inner */
  const _scroller = document.querySelector('.main');
  const _inner = document.querySelector('.main-inner');
  let _scrollTarget = 0, _scrollCur = 0, _scrollRaf = 0;

  function _scrollMax() {
    if (!_scroller || !_inner) return 0;
    return Math.max(0, _inner.scrollHeight - _scroller.clientHeight);
  }
  function _applyTransform(y) {
    _inner.style.transform = 'translate3d(0,' + (-Math.round(y)) + 'px,0)';
  }
  function _scrollStep() {
    const diff = _scrollTarget - _scrollCur;
    if (Math.abs(diff) < 0.5 || Math.round(_scrollCur) === Math.round(_scrollTarget)) {
      _scrollCur = _scrollTarget;
      _applyTransform(_scrollCur);
      _scrollRaf = 0;
      _inner.style.pointerEvents = '';
      return;
    }
    _scrollCur += diff * 0.12;
    _applyTransform(_scrollCur);
    _scrollRaf = requestAnimationFrame(_scrollStep);
  }
  function _resetScroll() {
    _scrollTarget = 0; _scrollCur = 0;
    if (_inner) { _applyTransform(0); _inner.style.pointerEvents = ''; }
    if (_scrollRaf) { cancelAnimationFrame(_scrollRaf); _scrollRaf = 0; }
  }
  if (_scroller && _inner) {
    _scroller.addEventListener('wheel', (e) => {
      if (e.ctrlKey) return;
      // Scrollable children (log viewer) keep their own native wheel scroll.
      if (e.target.closest && e.target.closest('.log-viewer')) return;
      const max = _scrollMax();
      if (max <= 0) return;
      e.preventDefault();
      _scrollTarget = Math.max(0, Math.min(max, _scrollTarget + e.deltaY * 0.55));
      _inner.style.pointerEvents = 'none';
      if (!_scrollRaf) _scrollRaf = requestAnimationFrame(_scrollStep);
    }, { passive: false });
    window.addEventListener('resize', () => {
      const max = _scrollMax();
      if (_scrollTarget > max) _scrollTarget = max;
      if (_scrollCur > max) { _scrollCur = max; _applyTransform(_scrollCur); }
    });
  }

  /* Toast */
  let toastTimeout = null;
  let toastHideTimeout = null;
  let _macroRunning = false;
  let _gameWindowFound = false;
  let _macroUiAction = '';
  let _macroActionSeq = 0;
  let _startTask = null;
  let _stopTask = null;
  let _compactAlert = '';

  function _renderMacroControls() {
    const starting = _macroUiAction === 'starting';
    const stopping = _macroUiAction === 'stopping';
    const start = document.getElementById('btnStart');
    const stop = document.getElementById('btnStop');
    if (start) {
      start.disabled = starting || stopping || _macroRunning || !_gameWindowFound;
      start.textContent = starting ? 'STARTING…' : 'START MACRO';
      start.title = _gameWindowFound ? '' : 'Open Roblox before starting XynMacro';
      start.classList.toggle('running', _macroRunning && !stopping);
    }
    if (stop) {
      stop.disabled = stopping || (!_macroRunning && !starting);
      stop.textContent = stopping ? 'STOPPING…' : (starting ? 'CANCEL START' : 'STOP');
    }
    const hudToggle = document.getElementById('hudMacroToggle');
    if (hudToggle) {
      hudToggle.classList.toggle('error', !!_compactAlert);
      hudToggle.disabled = stopping || (!_macroRunning && !starting && !_gameWindowFound);
      if (!_gameWindowFound && !_macroRunning && !starting) {
        hudToggle.title = 'Open Roblox before starting XynMacro';
        hudToggle.setAttribute('aria-label', hudToggle.title);
      }
    }
  }

  function _setMacroUiAction(action) {
    _macroUiAction = action;
    _renderMacroControls();
    const currentState = document.getElementById('currentState');
    if (currentState) {
      currentState.textContent = action === 'starting' ? 'Starting'
        : action === 'stopping' ? 'Stopping'
        : (_macroRunning ? 'Active' : 'Idle');
    }
    const hudState = document.getElementById('hudState');
    if (hudState && !_compactAlert) {
      hudState.textContent = action === 'starting' ? 'Starting'
        : action === 'stopping' ? 'Stopping'
        : (_macroRunning ? 'Active' : 'Idle');
    }
  }

  function _setCompactAlert(message) {
    _compactAlert = String(message || 'Macro stopped on an error');
    _renderMacroControls();
    if (!_isCompact) return;
    const hudState = document.getElementById('hudState');
    const hudTime = document.getElementById('hudTime');
    const hudStat = document.getElementById('hudStat');
    const hudSep = document.getElementById('hudSep');
    const hudToggle = document.getElementById('hudMacroToggle');
    if (hudState) hudState.textContent = 'Error';
    if (hudTime) hudTime.textContent = 'Click to expand';
    if (hudStat) hudStat.textContent = '';
    if (hudSep) hudSep.style.display = 'none';
    if (hudToggle) {
      hudToggle.title = _compactAlert;
      hudToggle.setAttribute('aria-label', _compactAlert);
    }
  }

  window.showToast = (msg, type = 'ok') => {
    if (_isCompact) {
      if (type === 'err') _setCompactAlert(msg);
      return;
    }
    const el = document.getElementById('toast');
    // Never render an empty pill — an empty message left a blank green box stuck
    // in the corner. Strip whitespace plus Unicode format/control chars (zero-width
    // space, word joiner, soft hyphen, BOM, etc.) that survive .trim() and would
    // otherwise render a textless green pill. If nothing's left, hide instead.
    if (msg == null || String(msg).replace(/[\s\p{Cf}\p{Cc}]+/gu, '') === '') {
      clearTimeout(toastTimeout);
      clearTimeout(toastHideTimeout);
      el.style.display = 'none';
      el.classList.remove('hiding');
      return;
    }
    clearTimeout(toastTimeout);
    clearTimeout(toastHideTimeout);
    el.classList.remove('hiding');
    el.textContent = msg;
    el.className = 'notif-toast ' + type;
    el.style.display = 'block';
    toastTimeout = setTimeout(() => {
      el.classList.add('hiding');
      toastHideTimeout = setTimeout(() => {
        el.style.display = 'none';
        el.classList.remove('hiding');
      }, 260);
    }, 2200);
  };

  /* API helpers — all routed through Tauri to the Python sidecar. */
  async function sendCommand(action, value) {
    try {
      return await invoke('send_to_python', { action, value: value ?? null });
    } catch (e) {
      console.error('command failed:', e);
      return { ok: false, msg: String(e) };
    }
  }

  /* Display resolution switch (Setup checklist) */
  const _btnRes1080 = document.getElementById('btnRes1080');
  if (_btnRes1080) _btnRes1080.addEventListener('click', async () => {
    const r = await sendCommand('display_set_1080');
    showToast(r.msg || 'Display change requested', r.ok ? 'ok' : 'err');
  });
  const _btnResRevert = document.getElementById('btnResRevert');
  if (_btnResRevert) _btnResRevert.addEventListener('click', async () => {
    const r = await sendCommand('display_revert');
    showToast(r.msg || 'Revert requested', r.ok ? 'ok' : 'err');
  });

  async function getState() {
    try {
      return await invoke('proxy_get', { path: '/state' });
    } catch (e) {
      return null;
    }
  }

  function _sig(arr) {
    return (Array.isArray(arr) ? arr : []).join('|');
  }

  function _sanitizeOrder(order) {
    const seen = new Set();
    const valid = [];
    for (const name of (order || [])) {
      const s = String(name);
      if (!availableStats.includes(s)) continue;
      if (seen.has(s)) continue;
      seen.add(s);
      valid.push(s);
    }
    return valid;
  }

  let _liveDrag = null;

  function renderTrainingOrder() {
    const queueEl = document.getElementById('statOrderList');
    const poolEl = document.getElementById('statPoolList');
    if (!queueEl || !poolEl) return;
    if (_liveDrag) return;

    previousOrderTops = new Map();
    queueEl.querySelectorAll('.stat-item[data-name]').forEach((el) => {
      previousOrderTops.set(el.dataset.name, el.getBoundingClientRect().top);
    });

    queueEl.innerHTML = '';
    poolEl.innerHTML = '';

    const queue = _sanitizeOrder(statOrder);
    statOrder = queue;

    const ROW_H = 40;
    const GAP = 6;
    const STEP = ROW_H + GAP;

    function _startDrag(row, idx, startY) {
      if (_liveDrag) return;
      const rows = Array.from(queueEl.querySelectorAll('.stat-item[data-idx]'));
      const rects = rows.map(r => r.getBoundingClientRect());
      const listTop = queueEl.getBoundingClientRect().top;

      _liveDrag = { fromIdx: idx, curIdx: idx, startY, rows, rects, listTop };
      row.classList.add('lifting');
      document.body.style.userSelect = 'none';
      document.body.style.cursor = 'grabbing';
    }

    function _moveDrag(clientY) {
      if (!_liveDrag) return;
      const d = _liveDrag;
      const listRect = queueEl.getBoundingClientRect();
      const rowRect = d.rects[d.fromIdx];
      const OVERFLOW = 28;
      const minY = listRect.top - rowRect.top - OVERFLOW;
      const maxY = listRect.bottom - rowRect.bottom + OVERFLOW;
      const dy = Math.max(minY, Math.min(maxY, clientY - d.startY));
      const draggedRow = d.rows[d.fromIdx];
      if (!draggedRow) return;

      draggedRow.style.transition = 'none';
      draggedRow.style.transform = `translateY(${dy}px) scale(1.03)`;

      const draggedCenter = d.rects[d.fromIdx].top + d.rects[d.fromIdx].height / 2 + dy;
      let newIdx = d.fromIdx;

      for (let i = 0; i < d.rows.length; i++) {
        if (i === d.fromIdx) continue;
        const mid = d.rects[i].top + d.rects[i].height / 2;
        if (d.fromIdx < i && draggedCenter > mid) newIdx = i;
        if (d.fromIdx > i && draggedCenter < mid) newIdx = Math.min(newIdx, i);
      }

      if (newIdx !== d.curIdx) d.curIdx = newIdx;

      for (let i = 0; i < d.rows.length; i++) {
        if (i === d.fromIdx) continue;
        let shift = 0;
        if (d.fromIdx < d.curIdx) {
          if (i > d.fromIdx && i <= d.curIdx) shift = -STEP;
        } else if (d.fromIdx > d.curIdx) {
          if (i >= d.curIdx && i < d.fromIdx) shift = STEP;
        }
        d.rows[i].style.transition = 'transform .22s cubic-bezier(.2,.8,.2,1)';
        d.rows[i].style.transform = shift ? `translateY(${shift}px)` : '';
        d.rows[i].classList.toggle('shifting', !!shift);
      }
    }

    async function _endDrag() {
      if (!_liveDrag) return;
      const d = _liveDrag;
      _liveDrag = null;
      document.body.style.userSelect = '';
      document.body.style.cursor = '';

      d.rows.forEach(r => {
        r.classList.remove('lifting', 'shifting');
        r.style.transition = '';
        r.style.transform = '';
      });

      if (d.fromIdx !== d.curIdx && d.fromIdx >= 0 && d.curIdx >= 0 && d.curIdx < statOrder.length) {
        const next = [...statOrder];
        const [moved] = next.splice(d.fromIdx, 1);
        next.splice(d.curIdx, 0, moved);
        await saveTrainingOrder(next, `${moved} moved`);
      }
    }

    queue.forEach((name, idx) => {
      const row = document.createElement('div');
      row.className = 'stat-item stat-item-draggable';
      row.dataset.idx = String(idx);
      row.dataset.name = name;

      const left = document.createElement('div');
      left.className = 'stat-item-left';

      const handle = document.createElement('span');
      handle.className = 'stat-drag-handle';
      handle.textContent = '⋮⋮';
      handle.title = 'Drag to reorder';
      handle.tabIndex = 0;
      handle.setAttribute('role', 'button');
      handle.setAttribute('aria-label', `Move ${name} in training order`);

      const label = document.createElement('span');
      label.className = 'stat-item-label';
      label.textContent = `${idx + 1}. ${name}`;
      left.append(handle, label);

      const ctrls = document.createElement('div');
      ctrls.className = 'stat-item-controls';

      const rem = document.createElement('button');
      rem.className = 'btn-mini danger';
      rem.textContent = 'Remove';
      rem.addEventListener('click', async () => {
        const next = statOrder.filter(v => v !== name);
        await saveTrainingOrder(next, `${name} removed from order`);
      });

      handle.addEventListener('pointerdown', (e) => {
        e.preventDefault();
        _startDrag(row, idx, e.clientY);
        const onMove = (ev) => _moveDrag(ev.clientY);
        const onUp = () => {
          window.removeEventListener('pointermove', onMove);
          window.removeEventListener('pointerup', onUp);
          _endDrag();
        };
        window.addEventListener('pointermove', onMove);
        window.addEventListener('pointerup', onUp);
      });
      handle.addEventListener('keydown', async (event) => {
        if (!['ArrowUp', 'ArrowDown'].includes(event.key)) return;
        event.preventDefault();
        const direction = event.key === 'ArrowUp' ? -1 : 1;
        const targetIndex = idx + direction;
        if (targetIndex < 0 || targetIndex >= statOrder.length) return;
        const next = [...statOrder];
        const [moved] = next.splice(idx, 1);
        next.splice(targetIndex, 0, moved);
        await saveTrainingOrder(next, `${moved} moved`);
        requestAnimationFrame(() => {
          document.querySelector(`.stat-item[data-name="${CSS.escape(name)}"] .stat-drag-handle`)?.focus();
        });
      });

      ctrls.append(rem);
      row.append(left, ctrls);
      queueEl.appendChild(row);
    });

    queueEl.querySelectorAll('.stat-item[data-name]').forEach((el) => {
      const name = el.dataset.name;
      const oldTop = previousOrderTops.get(name);
      if (oldTop == null) return;
      const newTop = el.getBoundingClientRect().top;
      const dy = oldTop - newTop;
      if (Math.abs(dy) < 1) return;
      el.animate(
        [{ transform: `translateY(${dy}px)` }, { transform: 'translateY(0px)' }],
        { duration: 220, easing: 'cubic-bezier(.2,.8,.2,1)' }
      );
    });

    const inQueue = new Set(queue);
    availableStats.filter(v => !inQueue.has(v)).forEach((name) => {
      const row = document.createElement('div');
      row.className = 'stat-item';
      const label = document.createElement('span');
      label.className = 'stat-item-label';
      label.textContent = name;
      const ctrls = document.createElement('div');
      ctrls.className = 'stat-item-controls';

      const add = document.createElement('button');
      add.className = 'btn-mini';
      add.textContent = 'Add';
      add.addEventListener('click', async () => {
        const next = [...statOrder, name];
        await saveTrainingOrder(next, `${name} added to order`);
      });

      ctrls.append(add);
      row.append(label, ctrls);
      poolEl.appendChild(row);
    });
  }

  async function saveTrainingOrder(nextOrder, okMsg = 'Training order saved') {
    const sanitized = _sanitizeOrder(nextOrder);
    const r = await sendCommand('set', { key: 'training_order', value: sanitized });
    if (r.ok !== false) {
      statOrder = sanitized;
      lastOrderSig = _sig(statOrder);
      renderTrainingOrder();
      showToast(okMsg, 'ok');
    } else {
      showToast(r.msg || 'Failed to save training order', 'err');
    }
  }

  /* Visible countdown overlay tied to start_delay_sec. Mirrors Python's
     safe_sleep(START_DELAY) so the user has a clear "switch to Roblox now" cue. */
  let _countdownTimer = null;
  let _startCountdownRemaining = null;
  function _hideStartCountdown() {
    const ov = document.getElementById('countdownOverlay');
    if (ov) ov.classList.remove('open');
    if (_countdownTimer) { clearTimeout(_countdownTimer); _countdownTimer = null; }
    _startCountdownRemaining = null;
  }
  function _showStartCountdown(secs) {
    const ov = document.getElementById('countdownOverlay');
    const num = document.getElementById('countdownNumber');
    if (!ov || !num) return;
    _hideStartCountdown();
    const seconds = Number(secs);
    const durationMs = Number.isFinite(seconds) ? Math.max(0, seconds * 1000) : 0;
    if (durationMs === 0) return;
    const endsAt = performance.now() + durationMs;
    const showTenths = Math.abs(seconds - Math.round(seconds)) > 0.0001;
    let displayed = null;
    let pulseSecond = null;
    if (!_isCompact) ov.classList.add('open');

    function tick() {
      const remainingMs = endsAt - performance.now();
      if (remainingMs <= 0) {
        _hideStartCountdown();
        return;
      }
      const remaining = showTenths
        ? Math.ceil(remainingMs / 100) / 10
        : Math.ceil(remainingMs / 1000);
      const remainingText = Number.isInteger(remaining) ? String(remaining) : remaining.toFixed(1);
      _startCountdownRemaining = remainingText;
      if (_isCompact) {
        const hudState = document.getElementById('hudState');
        const hudTime = document.getElementById('hudTime');
        if (hudState) hudState.textContent = 'Starting';
        if (hudTime) hudTime.textContent = remainingText;
      }
      if (remaining !== displayed) {
        displayed = remaining;
        num.textContent = remainingText;
        const nextPulseSecond = Math.ceil(remainingMs / 1000);
        if (nextPulseSecond !== pulseSecond) {
          pulseSecond = nextPulseSecond;
          num.classList.remove('pulse');
          void num.offsetWidth;
          num.classList.add('pulse');
        }
      }
      const quantumMs = showTenths ? 100 : 1000;
      const untilNextNumber = remainingMs - ((Math.ceil(remainingMs / quantumMs) - 1) * quantumMs);
      _countdownTimer = setTimeout(
        tick,
        Math.max(16, Math.min(remainingMs, untilNextNumber))
      );
    }
    tick();
  }

  /* Actions */
  let _cancelResolutionWarning = null;
  // First Start of the session on a non-1080p display: a brief warning that
  // auto-confirms after a 5s countdown (or Cancel to back out). The macro still
  // runs at any resolution — this is only a heads-up.
  function _confirmResStart(screen) {
    return new Promise((resolve) => {
      const ov = document.getElementById('startWarnOverlay');
      const msg = document.getElementById('warnResMsg');
      const yes = document.getElementById('warnYes');
      const cancel = document.getElementById('warnCancel');
      if (!ov || !yes || !cancel) { resolve(true); return; }
      if (msg) msg.textContent = `Your display is ${screen.w}×${screen.h}, not 1920×1080. The macro will still run, but clicks and detection may be less accurate.`;
      let remaining = 5;
      yes.disabled = true;
      yes.style.animation = '';          // let the faded entrance animation replay
      yes.textContent = `Continue (${remaining})`;
      ov.classList.add('open');
      _focusModal(ov, cancel);
      let timer = null;
      let settled = false;
      const done = (ok) => {
        if (settled) return;
        settled = true;
        if (timer) clearInterval(timer);
        _cancelResolutionWarning = null;
        yes.onclick = null; cancel.onclick = null;
        // Matched fade-out (entrance played back), then resolve once it's gone.
        yes.style.animation = '';        // clear the inline 'none' so the exit animation runs
        ov.classList.remove('open');
        ov.classList.add('closing');
        _restoreModalFocus(ov);
        setTimeout(() => { ov.classList.remove('closing'); resolve(ok); }, 500);
      };
      _cancelResolutionWarning = () => done(false);
      cancel.onclick = () => done(false);
      yes.onclick = () => { if (!yes.disabled) done(true); };
      timer = setInterval(() => {
        remaining -= 1;
        if (remaining <= 0) {
          clearInterval(timer); timer = null;
          done(true);
        } else {
          yes.textContent = `Continue (${remaining})`;
        }
      }, 1000);
    });
  }

  window.startMacro = () => {
    if (_startTask || _stopTask || _macroRunning) return _startTask || _stopTask;
    if (!_gameWindowFound) {
      showToast('Open Roblox before starting XynMacro', 'err');
      return Promise.resolve();
    }
    const actionSeq = ++_macroActionSeq;
    _compactAlert = '';
    _setMacroUiAction('starting');

    const task = (async () => {
      // Non-1080p heads-up. Continue = accept for this session (don't warn again);
      // Cancel = not accepted, so it reappears on the next Start.
      while (XMacroScreenState.needsResolutionWarning(_screenRes, _acceptedDisplaySignature)) {
        const warnedScreen = _screenRes;
        const ok = await _confirmResStart(warnedScreen);
        if (!ok || actionSeq !== _macroActionSeq) return;
        if (_screenRes?.signature === warnedScreen.signature) {
          _acceptedDisplaySignature = warnedScreen.signature;
        }
      }

      const delayEl = document.getElementById('startDelay');
      const parsedDelay = delayEl ? parseFloat(delayEl.value) : 5;
      const delay = Number.isFinite(parsedDelay) ? Math.max(0, Math.min(30, parsedDelay)) : 5;
      // Persist before Start so typing a decimal and immediately clicking Start
      // cannot race the backend and launch with the previous countdown value.
      const setDelay = await sendCommand('set', { key: 'start_delay_sec', value: delay });
      if (actionSeq !== _macroActionSeq) return;
      if (!setDelay || setDelay.ok === false) {
        showToast(setDelay?.msg || 'Could not save Start Countdown', 'err');
        return;
      }
      if (delayEl) {
        delayEl.value = String(delay);
        delayEl._prevValue = delay;
      }

      _showStartCountdown(delay);
      const r = await sendCommand('start');
      if (actionSeq !== _macroActionSeq) return;
      if (!r || r.ok === false) {
        _hideStartCountdown();
        showToast(r?.msg || 'Macro did not start', 'err');
        return;
      }
      _macroRunning = true;
      showToast(r.msg || 'Macro started', 'ok');
    })().finally(() => {
      if (_startTask === task) _startTask = null;
      if (actionSeq === _macroActionSeq && _macroUiAction === 'starting') {
        _setMacroUiAction('');
      }
    });
    _startTask = task;
    return task;
  };

  window.stopMacro = () => {
    if (_stopTask) return _stopTask;
    _hideStartCountdown();
    if (!_macroRunning && !_startTask) return Promise.resolve();
    const actionSeq = ++_macroActionSeq;
    const pendingStart = _startTask;
    let stopAccepted = false;
    _setMacroUiAction('stopping');

    const task = (async () => {
      // If Start is between its settings save and backend command, invalidate it
      // and wait for that task to settle before Stop reaches the backend.
      if (pendingStart) await pendingStart;
      const r = await sendCommand('stop');
      if (!r || r.ok === false) {
        showToast(r?.msg || 'Macro did not stop', 'err');
        return;
      }
      stopAccepted = true;
      showToast(r.msg || 'Stop requested', 'ok');
      const state = await getState();
      if (state) {
        _lastStateHash = '';
        applyState(state);
        _updateFooter(state);
      }
    })().finally(() => {
      if (_stopTask === task) _stopTask = null;
      // A successful stop remains visibly pending until /state confirms idle.
      // Failed/idle requests clear here so controls cannot get stuck.
      if (actionSeq === _macroActionSeq && (!stopAccepted || !_macroRunning)) _setMacroUiAction('');
    });
    _stopTask = task;
    return task;
  };

  window.toggleMacroFromHud = () => {
    if (_compactAlert) {
      const message = _compactAlert;
      _compactAlert = '';
      _renderMacroControls();
      if (_isCompact) window.wcCompact();
      setTimeout(() => showToast(message, 'err'), 180);
      return;
    }
    if (_macroUiAction === 'stopping') return;
    return (_macroRunning || _macroUiAction === 'starting' || _startCountdownRemaining != null)
      ? window.stopMacro()
      : window.startMacro();
  };

  // === Undo stack (used by auto-save + keybind capture) ===
  const _undoStack = [];
  const _UNDO_MAX = 10;

  function _pushUndo(key, oldValue, newValue) {
    if (oldValue === newValue) return;
    _undoStack.push({ key, oldValue, newValue });
    if (_undoStack.length > _UNDO_MAX) _undoStack.shift();
    _renderUndoButton();
  }

  function _renderUndoButton() {
    const btn = document.getElementById('undoButton');
    if (!btn) return;
    btn.disabled = _undoStack.length === 0;
    const tip = _undoStack.length === 0
      ? 'Nothing to undo'
      : `Undo: ${_undoStack[_undoStack.length - 1].key}`;
    btn.title = tip;
    btn.textContent = _undoStack.length === 0
      ? '↶ Undo'
      : `↶ Undo (${_undoStack.length})`;
  }

  window.undoLastChange = async () => {
    if (_undoStack.length === 0) return;
    const last = _undoStack[_undoStack.length - 1];
    // Send the revert without pushing onto the stack again.
    const el = document.getElementById(entryMap[last.key]);
    if (el) el._suppressUndo = true;
    const response = await sendCommand('set', { key: last.key, value: last.oldValue });
    if (!response || response.ok === false) {
      if (el) el._suppressUndo = false;
      showToast(response?.msg || `Could not undo ${last.key}`, 'err');
      return;
    }
    _undoStack.pop();
    _renderUndoButton();
    if (el) {
      el.value = last.oldValue == null ? '' : last.oldValue;
      el._prevValue = last.oldValue;
      el._suppressUndo = false;
    }
    // Keybind buttons resync on next /state poll. Force a one-shot display update too.
    const kbBtn = document.querySelector(`.keybind-btn[data-setting-key="${last.key}"]`);
    if (kbBtn) {
      kbBtn.textContent = String(last.oldValue || '').toUpperCase() || '?';
      kbBtn.dataset.value = String(last.oldValue || '');
    }
    if (last.key === 'agility_mode') _syncAgilityModeSeg(last.oldValue);
    if (last.key === 'health_mode') _syncHealthModeSeg(last.oldValue);
    if (last.key === 'ki_v8_mode') _syncKiV8ModeSeg(last.oldValue);
    showToast(`Undone ${last.key}`);
  };

  window.toggleSetting = async (key, el) => {
    const isActive = el.classList.contains('active');
    const next = !isActive;
    const r = await sendCommand('set', { key, value: next });
    if (r.ok !== false) {
      el.classList.toggle('active', next);
      el.setAttribute('aria-checked', next ? 'true' : 'false');
      _pushUndo(key, isActive, next);
      if (key === 'shutdown_pc_when_finished' || key === 'after_run_on_failure') {
        _syncAfterRunControls();
      }
    }
    const shutdownLabels = {
      shutdown_pc_when_finished: 'Shutdown when finished',
      after_run_on_failure: 'After-run actions on failure',
    };
    const message = r.ok !== false && shutdownLabels[key]
      ? `${shutdownLabels[key]} ${next ? 'enabled' : 'disabled'}`
      : (r.msg || `${key}: ${next ? 'ON' : 'OFF'}`);
    showToast(message, r.ok !== false ? 'ok' : 'err');
  };

  function _syncAfterRunControls() {
    const failureToggle = document.getElementById('toggleAfterRunFailure');
    const failureRow = document.getElementById('afterRunFailureRow');
    const failureHint = document.getElementById('afterRunFailureHint');
    const gameAction = document.getElementById('afterRunGameAction')?.value || 'none';
    const shutdownEnabled = document.getElementById('toggleShutdownFinished')?.classList.contains('active');
    const disabled = gameAction === 'none' && !shutdownEnabled;
    if (failureToggle) disabled ? failureToggle.setAttribute('disabled', '') : failureToggle.removeAttribute('disabled');
    failureRow?.classList.toggle('is-disabled', disabled);
    failureHint?.classList.toggle('is-disabled', disabled);
  }

  function _readInputValue(el) {
    if (!el) return null;
    if (el.type === 'checkbox') return el.checked;
    if (el.type === 'number' || el.type === 'range') {
      // Preserve null for blank radius-style fields that accept null.
      if (el.value.trim() === '') return null;
      const v = parseFloat(el.value);
      return isNaN(v) ? null : v;
    }
    return el.value;
  }

  function _renderGravityValue(value) {
    const badge = document.getElementById('gcGravityValue');
    if (!badge) return;
    const gravity = Number(value) || 0;
    badge.textContent = gravity === 0 ? 'Off' : `${gravity}G`;
  }

  function _setupGravitySlider() {
    const slider = document.getElementById('gcGravityTarget');
    if (!slider) return;
    slider.addEventListener('input', () => _renderGravityValue(slider.value));
    _renderGravityValue(slider.value);
  }

  function _setupAutoSave() {
    for (const [key, id] of Object.entries(entryMap)) {
      const el = document.getElementById(id);
      if (!el) continue;
      el._prevValue = _readInputValue(el);
      el.addEventListener('change', async () => {
        const nv = _readInputValue(el);
        const ov = el._prevValue;
        if (nv === ov) return;
        const r = await sendCommand('set', { key, value: nv });
        if (r.ok === false) {
          el.value = ov == null ? '' : ov;
          el._prevValue = ov;
          showToast(r.msg || `Failed: ${key}`, 'err');
        } else {
          const savedValue = Object.hasOwn(r, 'value') ? r.value : nv;
          el.value = savedValue == null ? '' : savedValue;
          el._prevValue = savedValue;
          if (!el._suppressUndo && savedValue !== ov) {
            _pushUndo(key, ov, savedValue);
          }
          if (key === 'after_run_game_action') _syncAfterRunControls();
          el.classList.add('saved-flash');
          setTimeout(() => el.classList.remove('saved-flash'), 450);
        }
      });
    }
  }

  // === Keybind capture ===
  function _setupKeybindCapture() {
    document.querySelectorAll('.keybind-btn').forEach(btn => {
      btn.addEventListener('click', () => _enterKeybindCapture(btn));
      btn.addEventListener('blur', () => _exitKeybindCapture(btn, false));
    });
  }

  function _enterKeybindCapture(btn) {
    if (btn.classList.contains('capturing')) return;
    btn._captureOriginalText = btn.textContent;
    btn._captureOriginalValue = btn.dataset.value || '';
    btn.classList.add('capturing');
    btn.textContent = '…';
    btn.focus();

    const handler = (e) => {
      e.preventDefault();
      e.stopPropagation();
      if (e.key === 'Escape') {
        _exitKeybindCapture(btn, false);
        return;
      }
      const k = _normalizeKeyEvent(e);
      if (!k) return;
      _commitKeybind(btn, k);
    };
    btn._captureHandler = handler;
    document.addEventListener('keydown', handler, true);
  }

  function _exitKeybindCapture(btn, committed) {
    if (!btn.classList.contains('capturing')) return;
    btn.classList.remove('capturing');
    if (!committed) {
      btn.textContent = btn._captureOriginalText;
    }
    if (btn._captureHandler) {
      document.removeEventListener('keydown', btn._captureHandler, true);
      btn._captureHandler = null;
    }
  }

  function _normalizeKeyEvent(e) {
    // Skip modifier-only presses; the `keyboard` lib also doesn't bind those alone.
    const skip = ['Shift', 'Control', 'Alt', 'Meta', 'Tab', 'CapsLock', 'OS', 'ContextMenu'];
    if (skip.includes(e.key)) return null;
    if (!e.key) return null;
    return e.key.toLowerCase();
  }

  async function _commitKeybind(btn, keyName) {
    const settingKey = btn.dataset.settingKey;
    const oldValue = btn._captureOriginalValue;
    const r = await sendCommand('set', { key: settingKey, value: keyName });
    if (r.ok === false) {
      showToast(r.msg || `Failed to bind ${settingKey}`, 'err');
      _exitKeybindCapture(btn, false);
      return;
    }
    _pushUndo(settingKey, oldValue, keyName);
    btn.dataset.value = keyName;
    btn.textContent = keyName.toUpperCase();
    _exitKeybindCapture(btn, true);
    showToast(`${settingKey.replace(/_/g, ' ')} → ${keyName.toUpperCase()}`);
  }

  // Init wired after DOMContentLoaded handler at the bottom of this IIFE.

  window.resetMacroSettings = async () => {
    if (_macroRunning || _macroUiAction) {
      showToast('Stop the macro before resetting settings', 'err');
      return;
    }
    if (!window.confirm('Reset macro controls and tuning to the shipped defaults? Calibration, appearance, window size, save location, presets, and logs will be kept.')) return;
    const r = await sendCommand('reset_defaults');
    if (r?.ok) {
      _undoStack.length = 0;
      _renderUndoButton();
      _lastStateHash = '';
      const state = await getState();
      if (state) applyState(state);
    }
    showToast(r?.msg || 'Macro settings restored', r?.ok ? 'ok' : 'err');
  };

  // Backward-compatible alias for the command palette until every caller is migrated.
  window.resetDefaults = window.resetMacroSettings;

  window.factoryResetApp = async () => {
    if (_macroRunning || _macroUiAction) {
      showToast('Stop the macro before factory reset', 'err');
      return;
    }
    const confirmed = window.confirm(
      'Factory reset XynMacro? This restores macro settings, calibration, save location, appearance, update preferences, sidebar width, and window position/size. Saved logs and custom theme presets are kept.'
    );
    if (!confirmed) return;
    const backend = await sendCommand('factory_reset');
    if (!backend?.ok) {
      showToast(backend?.msg || 'Factory reset failed', 'err');
      return;
    }
    ACTIVE_PREFERENCE_KEYS.forEach((key) => localStorage.removeItem(key));
    try {
      await invoke('factory_reset_app_prefs');
    } catch (error) {
      showToast(`Macro reset, but window reset failed: ${error}`, 'err');
      return;
    }
    window.location.reload();
  };

  window.calibBtn = async (statName) => {
    const r = await sendCommand('calibrate_button_begin', { stat: statName });
    showToast(r.ok ? `Click the ${statName} trait button in Roblox` : (r.msg || 'Failed'),
              r.ok ? 'ok' : 'err');
  };

  window.calibRegion = async (region) => {
    const r = await sendCommand('calibrate_region_begin', { region });
    const label = region === 'health_box' ? 'Health Box'
                : region === 'agility_box' ? 'Agility Box'
                : region;
    showToast(r.ok ? `Drag a rectangle over the ${label}` : (r.msg || 'Failed'),
              r.ok ? 'ok' : 'err');
  };

  /* Live region preview. One auto-refresh loop per open preview. Stops on toggle-off. */
  const _previewState = {}; // {region: {timer, rowEl, imgEl, metaEl}}

  async function _fetchPreview(region) {
    try {
      const r = await invoke('proxy_get', { path: '/preview?region=' + encodeURIComponent(region) });
      if (!r || r.ok === false) {
        const ps = _previewState[region];
        if (ps && ps.metaEl) ps.metaEl.textContent = (r && r.msg) || 'preview unavailable';
        return;
      }
      const ps = _previewState[region];
      if (!ps) return;
      ps.imgEl.src = r.image;
      ps.metaEl.textContent = `(${r.left}, ${r.top}) ${r.width}×${r.height}`;
    } catch (_) {}
  }

  window.togglePreview = (region) => {
    const map = {
      health_box:  { rowId: 'previewRowHealth',  imgId: 'previewImgHealth',  metaId: 'previewMetaHealth',  btnId: 'previewBtnHealth'  },
      agility_box: { rowId: 'previewRowAgility', imgId: 'previewImgAgility', metaId: 'previewMetaAgility', btnId: 'previewBtnAgility' },
      diagnostics: { rowId: 'previewRowDiagnostics', imgId: 'previewImgDiagnostics', metaId: 'previewMetaDiagnostics', btnId: 'previewBtnDiagnostics' },
    }[region];
    if (!map) return;
    const row = document.getElementById(map.rowId);
    const btn = document.getElementById(map.btnId);
    if (!row) return;

    if (_previewState[region]) {
      clearInterval(_previewState[region].timer);
      delete _previewState[region];
      row.style.display = 'none';
      if (btn) btn.classList.remove('active');
      return;
    }
    row.style.display = '';
    if (btn) btn.classList.add('active');
    _previewState[region] = {
      timer: setInterval(() => _fetchPreview(region), 1000),
      rowEl: row,
      imgEl: document.getElementById(map.imgId),
      metaEl: document.getElementById(map.metaId),
    };
    _fetchPreview(region);
  };

  window.copyDiagnostics = async () => {
    try {
      const report = await invoke('proxy_get', { path: '/diagnostics' });
      if (!report || report.ok === false) {
        showToast(report?.summary || 'Diagnostics unavailable', 'err');
        return;
      }
      const lines = [
        `XynMacro ${document.getElementById('appVer')?.textContent || ''}`.trim(),
        report.summary,
        `Monitor: ${JSON.stringify(report.monitor || {})}`,
        `Mode: ${report.window_mode}; DPI: ${report.dpi}; foreground: ${report.foreground}; minimized: ${report.minimized}`,
        `Template scores: ${JSON.stringify(report.template_scores || {})}`,
        `Settings: ${JSON.stringify(report.settings || {})}`,
        `Calibration: ${JSON.stringify(report.calibration || {})}`,
        report.capture_note || '',
        ...(report.issues || []).map(issue => `WARNING: ${issue}`),
      ].filter(Boolean);
      await navigator.clipboard.writeText(lines.join('\n'));
      showToast('Diagnostic report copied', 'ok');
    } catch (error) {
      showToast(`Could not copy diagnostics: ${error}`, 'err');
    }
  };

  function _selectedSegmentValue(segmentId) {
    return document.querySelector(`#${segmentId} .seg-btn.active`)?.dataset.val || null;
  }

  window.setAgilityMode = async (mode) => {
    const previous = _selectedSegmentValue('agilityModeSeg');
    const r = await sendCommand('set', { key: 'agility_mode', value: mode });
    if (r.ok !== false) {
      _syncAgilityModeSeg(mode);
      if (previous !== null) _pushUndo('agility_mode', previous, mode);
    }
  };

  function _syncAgilityModeSeg(mode) {
    const seg = document.getElementById('agilityModeSeg');
    if (!seg) return;
    seg.querySelectorAll('.seg-btn').forEach(b => {
      const selected = b.dataset.val === mode;
      b.classList.toggle('active', selected);
      b.setAttribute('aria-pressed', selected ? 'true' : 'false');
    });
  }

  window.setHealthMode = async (mode) => {
    const previous = _selectedSegmentValue('healthModeSeg');
    const r = await sendCommand('set', { key: 'health_mode', value: mode });
    if (r.ok !== false) {
      _syncHealthModeSeg(mode);
      if (previous !== null) _pushUndo('health_mode', previous, mode);
    }
  };

  function _syncHealthModeSeg(mode) {
    const seg = document.getElementById('healthModeSeg');
    if (!seg) return;
    seg.querySelectorAll('.seg-btn').forEach(b => {
      const selected = b.dataset.val === mode;
      b.classList.toggle('active', selected);
      b.setAttribute('aria-pressed', selected ? 'true' : 'false');
    });
    const legacyOnly = mode !== 'v1_legacy';
    document.querySelectorAll('[data-health-legacy-control]').forEach((control) => {
      control.disabled = legacyOnly;
      control.title = legacyOnly
        ? 'Only used by Legacy v1 health detection.'
        : '';
    });
  }

  window.setKiV8Mode = async (mode) => {
    const previous = _selectedSegmentValue('kiV8ModeSeg');
    const r = await sendCommand('set', { key: 'ki_v8_mode', value: mode });
    if (r.ok !== false) {
      _syncKiV8ModeSeg(mode);
      if (previous !== null) _pushUndo('ki_v8_mode', previous, mode);
    }
  };

  function _syncKiV8ModeSeg(mode) {
    const seg = document.getElementById('kiV8ModeSeg');
    if (!seg) return;
    seg.querySelectorAll('.seg-btn').forEach(b => {
      const selected = b.dataset.val === mode;
      b.classList.toggle('active', selected);
      b.setAttribute('aria-pressed', selected ? 'true' : 'false');
    });
  }

  /* Polling */
  const toggleMap = {
    senzu_enabled: 'toggleSenzu',
    senzu_zero_gravity_on_empty: 'toggleSenzuZeroGravity',
    no_yellow_fallback_enabled: 'toggleNoYellowFallback',
    prevent_sleep_while_running: 'togglePreventSleep',
    diagnostic_mode: 'toggleDiagnosticMode',
    shutdown_pc_when_finished: 'toggleShutdownFinished',
    after_run_on_failure: 'toggleAfterRunFailure',
    auto_retry_on_failure: 'toggleAutoRetry',
    auto_retry_walk_out: 'toggleAutoRetryWalkOut',
  };

  const entryMap = {
    start_delay_sec:                 'startDelay',
    after_run_game_action:           'afterRunGameAction',
    auto_retry_max_attempts:         'autoRetryMaxAttempts',
    auto_retry_recovery_mode:        'autoRetryRecoveryMode',
    auto_retry_walk_seconds:         'autoRetryWalkSeconds',
    gc_gravity_target_g:             'gcGravityTarget',
    no_yellow_timeout_sec:           'noYellowTimeout',
    after_switch_wait_sec:           'afterSwitchWait',
    wasd_key_press_delay_sec:        'wasdKeyPress',
    wasd_stabilize_delay_sec:        'wasdStabilize',
    wasd_post_burst_delay_sec:       'wasdPostBurst',
    agility_green_observe_sec:       'wasdGreenObserve',
    agility_after_green_settle_sec:  'wasdAfterGreenSettle',
    agility_inter_string_wait_sec:   'wasdInterStringWait',
    health_hit_cooldown_sec:         'healthHitCooldown',
    ki_v8_click_delay_sec:           'kiV8ClickDelay',
    ki_v8_v2_target_r_factor:        'kiV8V2TargetRFactor',
    ki_v8_v2_brightness_threshold:   'kiV8V2BrightnessThreshold',
    ki_v8_v2_bright_count_threshold: 'kiV8V2BrightCountThreshold',
    ki_latency_comp_ms:              'kiLatencyComp',
    senzu_slot:                      'senzuSlot',
    senzu_delay_sec:                 'senzuDelay',
    senzu_recovery_timeout_sec:      'senzuRecoveryTimeout',
    senzu_preference_mode:           'senzuPreferenceMode',
  };

  // Keybind settings mapped to .keybind-btn elements by data-setting-key.
  const keybindSettings = ['manual_next_key', 'start_stop_hotkey', 'pause_hotkey'];

  let _lastStateHash = '';
  let _screenRes = null;
  let _lastErrCount = null;   // tracks state.error_count so we toast once per new error
  let _acceptedDisplaySignature = null;
  let _elapsedTimer = null;
  let _startedAt = 0;
  let _logSince = 0;
  let _logTimer = null;
  let _logsRefreshing = false;
  let _logGeneration = 0;
  let _lastBackendRunning = null;
  let _lastOperationalStop = '';
  const _shortcutHotkeys = { startStop: 'F6', skip: 'L', pause: 'U' };

  function _fmtElapsed(secs) {
    if (secs < 0) secs = 0;
    const h = Math.floor(secs / 3600);
    const m = Math.floor((secs % 3600) / 60);
    const s = secs % 60;
    if (h > 0) return `${h}h ${String(m).padStart(2,'0')}m ${String(s).padStart(2,'0')}s`;
    return `${m}m ${String(s).padStart(2,'0')}s`;
  }

  function _tickElapsed() {
    if (!_startedAt) return;
    const formatted = _fmtElapsed(Math.floor(Date.now() / 1000 - _startedAt));
    const el = document.getElementById('elapsedTime');
    if (el) el.textContent = formatted;
    const hudTime = document.getElementById('hudTime');
    if (hudTime) {
      hudTime.textContent = _startCountdownRemaining != null
        ? String(_startCountdownRemaining)
        : formatted;
    }
  }

  let _backendPort = 0;
  async function _refreshBackendPort(attempt = 0) {
    try {
      _backendPort = Number(await invoke('get_backend_port')) || 0;
      const badge = document.getElementById('backendPort');
      if (badge) badge.textContent = _backendPort || '—';
    } catch (_) {}
    // The WebView normally loads before the frozen sidecar writes its port
    // file. Retry only during startup so Settings cannot stay stuck at “—”.
    if (!_backendPort && attempt < 20) {
      setTimeout(() => _refreshBackendPort(attempt + 1), 250);
    }
  }
  _refreshBackendPort();
  window.addEventListener('backend-ready', () => _refreshBackendPort());

  function _renderRunSummary(run) {
    const el = document.getElementById('runSummary');
    if (!el) return;
    const tel = run?.telemetry || {};
    const outcomeLabels = {
      completed: 'Completed',
      incomplete: 'Incomplete',
      stopped: 'Stopped',
      error: 'Error',
    };
    const parts = [
      outcomeLabels[run?.outcome] || 'Ended',
      `${tel.switches || 0} switches`,
      `${tel.health_hits || 0} health hits`,
      `${tel.ki_clicks || 0} Ki clicks`,
      `${tel.wasd_greens || 0} WASD confirmed`,
    ];
    if (tel.wasd_unconfirmed || tel.wasd_reds) {
      parts.push(`${tel.wasd_unconfirmed || 0} uncertain`, `${tel.wasd_reds || 0} failed`);
    }
    if (tel.senzu_eaten || tel.senzu_refills) {
      parts.push(`${tel.senzu_eaten || 0} Senzu eaten`, `${tel.senzu_refills || 0} refills`);
    }
    el.textContent = `Last run · ${parts.join(' · ')}`;
    el.hidden = false;
  }

  function applyState(state) {
    if (!state) return;

    const hash = JSON.stringify(state);
    const changed = hash !== _lastStateHash;
    _lastStateHash = hash;

    const cfg = state.config || {};
    const running = !!state.running;
    _gameWindowFound = !!state.game_window?.found;
    const backendActivity = state.stop_requested ? 'Stopping'
      : state.controller_paused_for_senzu ? 'Auto-Senzu'
      : state.controller_paused ? 'Paused'
      : state.training_menu_visible ? 'Training Menu'
      : (running && !state.current_state) ? 'Starting'
      : (running ? 'Active' : 'Idle');
    const runStarted = _lastBackendRunning === false && running;
    const runEnded = _lastBackendRunning === true && !running;
    _lastBackendRunning = running;
    _macroRunning = running;
    if (!running && _startCountdownRemaining != null
        && !_startTask && _macroUiAction !== 'starting') {
      _hideStartCountdown();
    }
    if (runStarted) {
      _compactAlert = '';
      _lastOperationalStop = '';
    }
    if (_macroUiAction === 'stopping' && !running) _setMacroUiAction('');
    _renderMacroControls();

    // Toast once when a run ends on an error (the sidecar bumps error_count on a crash).
    if (typeof state.error_count === 'number') {
      if (_lastErrCount !== null && state.error_count > _lastErrCount) {
        showToast(state.last_error ? `Macro stopped — ${state.last_error}. Check Logs.`
                                   : 'Macro stopped on an error — check Logs.', 'err');
      }
      _lastErrCount = state.error_count;
    }
    const operationalStop = ['error', 'empty'].includes(state.senzu_status)
      ? state.senzu_status
      : '';
    if (operationalStop && operationalStop !== _lastOperationalStop) {
      _lastOperationalStop = operationalStop;
      showToast(
        operationalStop === 'empty'
          ? 'No allowed Senzu Bean stock left — Auto-Senzu is off for this run. Training continues.'
          : 'Macro stopped during Senzu recovery. Check Logs.',
        operationalStop === 'empty' ? 'warn' : 'err'
      );
    }
    const stats = Array.isArray(state.available_stats) ? state.available_stats : ['Health', 'Agility', 'Ki Control', 'Physical Damage', 'Ki Damage'];

    if (running && state.started_at) {
      _startedAt = state.started_at;
      if (!_elapsedTimer) {
        _elapsedTimer = setInterval(_tickElapsed, 1000);
        _tickElapsed();
      }
    } else {
      if (_elapsedTimer) { clearInterval(_elapsedTimer); _elapsedTimer = null; }
      const el = document.getElementById('elapsedTime');
      if (el) el.textContent = _startedAt ? _fmtElapsed(Math.floor(Date.now() / 1000 - _startedAt)) : '—';
      const hudTime = document.getElementById('hudTime');
      if (hudTime) hudTime.textContent = '';
      _startedAt = 0;
    }

    if (_compactAlert) _setCompactAlert(_compactAlert);
    if (!changed) return;

    const dot  = document.getElementById('statusDot');
    const text = document.getElementById('statusText');
    const hDot = document.querySelector('#headerStatus .dot');
    const hTxt = document.getElementById('headerStatusText');

    dot.classList.toggle('active', running);
    text.textContent = running ? 'Active' : 'Idle';
    if (hDot) hDot.style.background = running ? 'var(--green)' : 'var(--text3)';
    if (hTxt) hTxt.textContent = running ? 'Active' : 'Idle';

    const curState = state.current_state || 'Ready';
    document.getElementById('currentState').textContent = _macroUiAction === 'starting' ? 'Starting'
      : _macroUiAction === 'stopping' ? 'Stopping'
      : backendActivity;
    const curStat = document.getElementById('currentStat');
    if (curStat) {
      const prog = state.progression;
      const progTxt = (running && prog) ? ` · ${prog.x}/${prog.y}`
                    : (running && state.progression_status === 'complete') ? ' · complete'
                    : (running && state.progression_status === 'tracking') ? ' · tracking'
                    : '';
      const menuTxt = state.training_menu_visible ? ' · menu open' : '';
      curStat.textContent = (running && state.current_state) ? state.current_state + progTxt + menuTxt : '—';
    }
    // Compact HUD: show current stat (and separator) only when actually running.
    const hudStat = document.getElementById('hudStat');
    const hudSep = document.getElementById('hudSep');
    if (hudStat) hudStat.textContent = (running && state.current_state) ? state.current_state : '';
    if (hudSep) hudSep.style.display = (running && state.current_state) ? '' : 'none';
    const hudDot = document.getElementById('hudStatusDot');
    const hudState = document.getElementById('hudState');
    const hudToggle = document.getElementById('hudMacroToggle');
    if (hudDot) hudDot.classList.toggle('active', running);
    if (hudState && !_compactAlert) {
      hudState.textContent = _macroUiAction === 'stopping' ? 'Stopping'
        : _startCountdownRemaining != null ? 'Starting'
        : backendActivity;
    }
    if (_startCountdownRemaining != null) {
      if (hudStat) hudStat.textContent = '';
      if (hudSep) hudSep.style.display = 'none';
      const hudTime = document.getElementById('hudTime');
      if (hudTime) hudTime.textContent = String(_startCountdownRemaining);
    }
    if (hudToggle && !_compactAlert) {
      hudToggle.title = (running || _macroUiAction === 'starting' || _startCountdownRemaining != null)
        ? 'Stop macro'
        : 'Start macro';
      hudToggle.setAttribute('aria-label', hudToggle.title);
    }
    if (_compactAlert) _setCompactAlert(_compactAlert);

    const formatHotkey = (value, fallback) => String(value || fallback).toUpperCase();
    const hintStartStop = document.getElementById('hintStartStop');
    const hintManualNext = document.getElementById('hintManualNext');
    const hintPause = document.getElementById('hintPause');
    if (hintStartStop) hintStartStop.textContent = formatHotkey(cfg.start_stop_hotkey, 'F6');
    if (hintManualNext) hintManualNext.textContent = formatHotkey(cfg.manual_next_key, 'L');
    if (hintPause) hintPause.textContent = formatHotkey(cfg.pause_hotkey, 'U');
    _shortcutHotkeys.startStop = formatHotkey(cfg.start_stop_hotkey, 'F6');
    _shortcutHotkeys.skip = formatHotkey(cfg.manual_next_key, 'L');
    _shortcutHotkeys.pause = formatHotkey(cfg.pause_hotkey, 'U');

    for (const [key, elId] of Object.entries(toggleMap)) {
      const el = document.getElementById(elId);
      if (el) {
        const enabled = !!cfg[key];
        el.classList.toggle('active', enabled);
        el.setAttribute('aria-checked', enabled ? 'true' : 'false');
      }
    }
    _syncAgilityModeSeg(cfg.agility_mode || 'v2');
    _syncHealthModeSeg(cfg.health_mode || 'v2_track');
    if (cfg.ki_v8_mode) _syncKiV8ModeSeg(cfg.ki_v8_mode);

    for (const [key, elId] of Object.entries(entryMap)) {
      const el = document.getElementById(elId);
      if (el && document.activeElement !== el) {
        const v = cfg[key];
        el.value = v == null ? '' : v;
        // Keep auto-save's reference value in sync so post-state edits diff correctly.
        el._prevValue = v == null ? null
          : ((el.type === 'number' || el.type === 'range') ? Number(v) : v);
      }
    }
    _syncAfterRunControls();
    _renderGravityValue(cfg.gc_gravity_target_g);

    // Keybind buttons: sync display only when not actively capturing.
    for (const setting of keybindSettings) {
      const btn = document.querySelector(`.keybind-btn[data-setting-key="${setting}"]`);
      if (!btn || btn.classList.contains('capturing')) continue;
      const v = cfg[setting];
      if (v == null) continue;
      btn.dataset.value = String(v);
      btn.textContent = String(v).toUpperCase();
    }

    const ver = state.version || '';
    document.getElementById('brandVer').textContent = ver;
    document.getElementById('appVer').textContent = ver;
    if (ver) window._maybeShowChangelog(ver);
    document.getElementById('backendPort').textContent = _backendPort || '—';
    const saveDirEl = document.getElementById('saveDir');
    if (saveDirEl && state.save_dir) saveDirEl.textContent = state.save_dir;
    const configDirEl = document.getElementById('configDir');
    if (configDirEl && state.config_dir) configDirEl.textContent = state.config_dir;

    const incomingOrder = Array.isArray(cfg.training_order) ? cfg.training_order : [];
    const incomingOrderSig = _sig(incomingOrder);
    const incomingStatsSig = _sig(stats);
    if (incomingOrderSig !== lastOrderSig || incomingStatsSig !== lastStatsSig) {
      availableStats = [...stats];
      statOrder = incomingOrder.filter(v => availableStats.includes(v));
      lastOrderSig = _sig(statOrder);
      lastStatsSig = incomingStatsSig;
      renderTrainingOrder();
    }

    const scr = state.screen || {};
    const resIcon = document.getElementById('resIcon');
    const resText = document.getElementById('resText');
    const resPill = document.getElementById('resPill');
    const setResolutionButton = document.getElementById('btnRes1080');
    const revertResolutionButton = document.getElementById('btnResRevert');
    if (resIcon && resText) {
      const nextScreen = XMacroScreenState.normalizeScreen(scr);
      if (setResolutionButton) setResolutionButton.disabled = running || !nextScreen;
      // Revert can still restore a display changed earlier when Roblox has
      // temporarily disappeared, so only an active macro blocks it here.
      if (revertResolutionButton) {
        revertResolutionButton.disabled = running
          || (!nextScreen && !state.display_restore_pending);
        revertResolutionButton.title = state.display_restore_pending
          ? 'Restore the display mode saved before Set 1080p'
          : 'Restore the current Roblox display to its Windows setting';
      }
      if (!nextScreen) {
        _screenRes = null;
        _acceptedDisplaySignature = null;
        resIcon.className = 'res-icon res-warn';
        resIcon.innerHTML = '&#10007;';
        resText.textContent = 'Unavailable';
        if (resPill) resPill.title = 'Open Roblox to detect its display';
      } else {
        const { w, h, hz } = nextScreen;
        const ok = (w === 1920 && h === 1080);
        if (_screenRes?.signature !== nextScreen.signature) {
          _acceptedDisplaySignature = null;
        }
        resIcon.className = 'res-icon ' + (ok ? 'res-ok' : 'res-warn');
        resIcon.innerHTML = ok ? '&#10003;' : '&#10007;';
        resText.textContent = ok ? '1080p' : `${w}×${h}`;
        _screenRes = nextScreen;
        if (resPill) {
          let tip = `Resolution ${w}×${h}`;
          if (hz) tip += ` @ ${hz}Hz`;
          if (!ok) tip += ' — macro designed for 1920×1080';
          resPill.title = tip;
        }
      }
    }

    const gw = state.game_window || {};
    const gameIcon = document.getElementById('gameIcon');
    const gameText = document.getElementById('gameText');
    if (gameIcon && gameText) {
      if (gw.found) {
        gameIcon.className = 'res-icon res-ok';
        gameIcon.innerHTML = '&#10003;';
        gameText.textContent = `${gw.width}×${gw.height} @ (${gw.x}, ${gw.y})`;
      } else {
        gameIcon.className = 'res-icon res-warn';
        gameIcon.innerHTML = '&#10007;';
        gameText.textContent = 'Not found — open Roblox first';
      }
    }

    const bc = state.button_calibration || {};
    const cur = bc.current || {};
    const waiting = bc.waiting || '';
    [
      ['Health',          'btnPosHealth'],
      ['Agility',         'btnPosAgility'],
      ['Ki Control',      'btnPosKiControl'],
      ['Physical Damage', 'btnPosPhysicalDamage'],
      ['Ki Damage',       'btnPosKiDamage'],
    ].forEach(([statName, elId]) => {
      const el = document.getElementById(elId);
      if (!el) return;
      if (waiting === statName) {
        el.textContent = 'Waiting for click…';
      } else {
        const v = cur[statName];
        el.textContent = (Array.isArray(v) && v.length === 2 && (v[0] || v[1])) ? `(${v[0]}, ${v[1]})` : '—';
      }
    });

    const rc = state.region_calibration || {};
    const rcCur = rc.current || {};
    const rcWaiting = rc.waiting || '';
    [['health_box','regionPosHealth'],['agility_box','regionPosAgility']].forEach(([key, elId]) => {
      const el = document.getElementById(elId);
      if (!el) return;
      if (rcWaiting === key) {
        el.textContent = 'Drag rectangle…';
      } else {
        const v = rcCur[key];
        el.textContent = (v && v.width && v.height)
          ? `(${v.left}, ${v.top}) ${v.width}×${v.height}`
          : '—';
      }
    });

    // Telemetry strip on Dashboard.
    const tel = state.telemetry || {};
    const wasdAttempts = (tel.wasd_greens || 0) + (tel.wasd_unconfirmed || 0) + (tel.wasd_reds || 0);
    const adaptiveWasd = cfg.agility_mode !== 'v1';
    const wasdRateEl = document.getElementById('telemWasdRate');
    if (wasdRateEl) {
      if (!adaptiveWasd || wasdAttempts === 0) {
        wasdRateEl.textContent = '—';
      } else {
        const pct = (tel.wasd_greens / wasdAttempts) * 100;
        wasdRateEl.textContent = pct.toFixed(0) + '%';
      }
    }
    const wasdSubEl = document.getElementById('telemWasdBreakdown');
    if (wasdSubEl) {
      wasdSubEl.textContent = !adaptiveWasd
        ? 'unavailable in Burst mode'
        : wasdAttempts === 0
        ? 'no data yet'
        : `${tel.wasd_greens || 0}✓  ${tel.wasd_unconfirmed || 0}? ${tel.wasd_reds || 0}✗ (${tel.wasd_sequences || 0} seq)`;
    }
    const kiClicksEl = document.getElementById('telemKiClicks');
    if (kiClicksEl) kiClicksEl.textContent = String(tel.ki_clicks || 0);
    const kiSubEl = document.getElementById('telemKiBreakdown');
    if (kiSubEl) {
      kiSubEl.textContent = (tel.ki_dots_found || 0) === 0
        ? 'no dots found'
        : `${tel.ki_dots_found || 0} dots · ${tel.ki_timeouts || 0} timeouts`;
    }
    const healthHitsEl = document.getElementById('telemHealthHits');
    if (healthHitsEl) healthHitsEl.textContent = String(tel.health_hits || 0);
    const healthSubEl = document.getElementById('telemHealthBreakdown');
    if (healthSubEl) healthSubEl.textContent = (tel.health_hits || 0) === 0 ? 'no hits yet' : 'F presses';
    const senzuStockEl = document.getElementById('telemSenzuStock');
    if (senzuStockEl) {
      senzuStockEl.textContent = state.senzu_remaining == null ? '—' : String(state.senzu_remaining);
    }
    const senzuSubEl = document.getElementById('telemSenzuBreakdown');
    if (senzuSubEl) {
      const activeType = state.senzu_active_type === 'full' ? 'Full'
        : state.senzu_active_type === 'half' ? 'Half'
        : 'No bean';
      senzuSubEl.textContent = `${activeType} · ${state.senzu_status || 'idle'} · ${tel.senzu_eaten || 0} eaten · ${tel.senzu_refills || 0} refills`;
    }
    const switchesEl = document.getElementById('telemSwitches');
    if (switchesEl) switchesEl.textContent = String(tel.switches || 0);
    const runSummary = document.getElementById('runSummary');
    if (runStarted && runSummary) runSummary.hidden = true;
    if (runEnded) {
      _renderRunSummary(state.last_run);
      if (state.last_run?.outcome === 'incomplete') {
        showToast(state.last_run.reason || 'Training order ended with skipped stats', 'warn');
      }
    }

  }

  /* Log viewer */
  function _fmtLogTime(epoch) {
    const d = new Date(epoch * 1000);
    return d.toLocaleTimeString([], { hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' });
  }

  async function pollLogs(replace = false) {
    if (_logsRefreshing && !replace) return;
    const generation = replace ? ++_logGeneration : _logGeneration;
    if (replace) _logsRefreshing = true;
    try {
      const since = replace ? 0 : _logSince;
      const entries = await invoke('proxy_get', { path: '/logs?since=' + since });
      if (generation !== _logGeneration || !Array.isArray(entries)) return;
      // Write into every viewer tagged .log-feed (Logs tab + any dashboard monitor variant).
      const viewers = document.querySelectorAll('.log-feed');
      if (!viewers.length) return;

      const autoScroll = document.getElementById('logAutoScroll');
      const shouldScroll = !autoScroll || autoScroll.checked;
      const MAX_LINES = 600;
      let latest = replace ? 0 : _logSince;

      viewers.forEach((viewer) => {
        if (replace) viewer.innerHTML = '';
        const empty = viewer.querySelector('.log-empty');
        if (empty) empty.remove();
        const frag = document.createDocumentFragment();
        for (const e of entries) {
          const line = document.createElement('div');
          const logType = _classifyLog(e.msg);
          line.className = logType ? 'log-line ' + logType : 'log-line';
          const ts = document.createElement('span');
          ts.className = 'log-ts';
          ts.textContent = _fmtLogTime(e.t);
          const msg = document.createElement('span');
          msg.className = 'log-msg';
          msg.textContent = e.msg;
          line.append(ts, msg);
          frag.appendChild(line);
          if (e.t > latest) latest = e.t;
        }
        viewer.appendChild(frag);
        if (replace && !entries.length) {
          viewer.innerHTML = '<div class="log-empty">No session log entries</div>';
        }
        while (viewer.children.length > MAX_LINES) viewer.removeChild(viewer.firstChild);
        if (shouldScroll) viewer.scrollTop = viewer.scrollHeight;
      });

      _logSince = latest;
    } catch (_) {
      if (replace) showToast('Could not refresh session log', 'err');
    } finally {
      if (replace && generation === _logGeneration) _logsRefreshing = false;
    }
  }

  window.clearLogs = () => {
    document.querySelectorAll('.log-feed').forEach((viewer) => {
      viewer.innerHTML = '<div class="log-empty">View cleared; session log retained</div>';
    });
  };

  window.copyLog = async () => {
    const viewer = document.getElementById('logViewer');
    if (!viewer) return;
    const text = Array.from(viewer.querySelectorAll('.log-line'))
      .map((line) => line.innerText.trim())
      .filter(Boolean)
      .join('\n');
    if (!text) {
      showToast('Log is empty', 'err');
      return;
    }
    try {
      await navigator.clipboard.writeText(text);
      showToast(`Copied ${text.split('\n').length} lines`, 'ok');
    } catch (e) {
      // Fallback for older browsers — should not be needed in WebView2 but safe.
      const ta = document.createElement('textarea');
      ta.value = text;
      document.body.appendChild(ta);
      ta.select();
      try { document.execCommand('copy'); showToast('Copied (fallback)', 'ok'); }
      catch { showToast('Copy failed: ' + e, 'err'); }
      finally { document.body.removeChild(ta); }
    }
  };

  window.saveLog = async () => {
    try {
      const r = await invoke('proxy_post', { path: '/save_log', body: null });
      if (r && r.ok) {
        showToast(`Saved ${r.count} lines → ${r.path}`, 'ok');
      } else {
        showToast((r && r.msg) || 'Save failed', 'err');
      }
    } catch (e) {
      showToast('Save failed: ' + e, 'err');
    }
  };

  window.pickSaveDir = async () => {
    try {
      const r = await invoke('proxy_post', { path: '/pick_save_dir', body: null });
      if (r && r.ok) showToast('Save folder updated', 'ok');
      else if (r && r.msg !== 'cancelled') showToast((r && r.msg) || 'Could not set folder', 'err');
    } catch (e) { showToast('Could not open folder picker', 'err'); }
  };
  window.openSaveDir = async () => {
    try {
      const r = await invoke('proxy_post', { path: '/open_save_dir', body: null });
      if (!r || r.ok === false) showToast((r && r.msg) || 'Could not open folder', 'err');
    } catch (e) { showToast('Could not open folder', 'err'); }
  };
  window.openConfigDir = async () => {
    try {
      const r = await invoke('proxy_post', { path: '/open_config_dir', body: null });
      if (!r || r.ok === false) showToast((r && r.msg) || 'Could not open folder', 'err');
    } catch (e) { showToast('Could not open folder', 'err'); }
  };

  async function poll() {
    const state = await getState();
    applyState(state);
    _updateFooter(state); // footer shares this poll — it used to run its own /state interval
  }

  let _frontendRefreshTask = null;
  window.refreshFrontend = () => {
    if (_frontendRefreshTask) return _frontendRefreshTask;
    const task = (async () => {
      // Refresh rendered frontend data only. The sidecar and an active macro run
      // stay untouched, and replacing the log DOM avoids duplicate lines.
      _lastStateHash = '';
      lastOrderSig = '';
      lastStatsSig = '';
      const [state] = await Promise.all([getState(), pollLogs(true)]);
      if (!state) {
        showToast('Could not refresh backend state', 'err');
        return;
      }
      applyState(state);
      _updateFooter(state);
      showToast('Frontend state and session log refreshed', 'ok');
    })().finally(() => {
      if (_frontendRefreshTask === task) _frontendRefreshTask = null;
    });
    _frontendRefreshTask = task;
    return task;
  };

  _setupGravitySlider();
  _setupAutoSave();
  _setupKeybindCapture();
  _renderUndoButton();

  poll();
  pollTimer = setInterval(poll, 800);
  _logTimer = setInterval(pollLogs, 400);
  pollLogs();

  /* Command palette */
  const COMMANDS = [
    { id: 'start',       label: 'Start macro',                group: 'Actions',     hint: () => _shortcutHotkeys.startStop, run: () => window.startMacro() },
    { id: 'stop',        label: 'Stop macro',                  group: 'Actions',     hint: () => _shortcutHotkeys.startStop, run: () => window.stopMacro() },
    { id: 'compact',     label: 'Toggle compact mode',         group: 'Actions',     hint: 'Ctrl+M', run: () => window.wcCompact() },
    { id: 'ontop',       label: 'Toggle always on top',        group: 'Actions',                  run: () => window.wcOnTop() },
    { id: 'savelog',     label: 'Save session log',            group: 'Actions',                  run: () => window.saveLog() },
    { id: 'refresh',     label: 'Refresh frontend data',       group: 'Actions',     hint: 'Ctrl+R', run: () => window.refreshFrontend() },
    { id: 'reset',       label: 'Reset macro settings',        group: 'Actions',                  run: () => window.resetMacroSettings() },
    { id: 'nav-dash',    label: 'Open Dashboard',              group: 'Navigation', run: () => openView('dashboard') },
    { id: 'nav-ctrl',    label: 'Open Controls',               group: 'Navigation', run: () => openView('controls') },
    { id: 'nav-tune',    label: 'Open Tuning',                 group: 'Navigation', run: () => openView('tuning') },
    { id: 'nav-calib',   label: 'Open Calibration',            group: 'Navigation', run: () => openView('ki') },
    { id: 'nav-logs',    label: 'Open Logs',                   group: 'Navigation', run: () => openView('logs') },
    { id: 'nav-set',     label: 'Open Settings',               group: 'Navigation', run: () => openView('settings') },
  ];
  for (const [key, t] of Object.entries(THEMES)) {
    COMMANDS.push({ id: 'theme-' + key, label: 'Theme: ' + t.name, group: 'Themes', run: () => applyTheme(key) });
  }

  const paletteBackdrop = document.getElementById('paletteBackdrop');
  const paletteInput    = document.getElementById('paletteInput');
  const paletteResults  = document.getElementById('paletteResults');
  let paletteOpen = false;
  let paletteFiltered = [];
  let paletteActiveIdx = 0;

  function _matchScore(q, label) {
    if (!q) return 1;
    q = q.toLowerCase();
    label = label.toLowerCase();
    if (label.startsWith(q)) return 3;
    if (label.includes(q)) return 2;
    let i = 0;
    for (const ch of label) { if (ch === q[i]) i++; if (i === q.length) return 1; }
    return 0;
  }

  function renderPalette() {
    const q = paletteInput.value.trim();
    paletteFiltered = COMMANDS
      .map(c => ({ c, s: _matchScore(q, c.label) }))
      .filter(x => x.s > 0)
      .sort((a, b) => b.s - a.s)
      .map(x => x.c);
    if (paletteActiveIdx >= paletteFiltered.length) paletteActiveIdx = 0;

    paletteResults.innerHTML = '';
    if (paletteFiltered.length === 0) {
      const empty = document.createElement('div');
      empty.className = 'palette-empty';
      empty.textContent = 'No matches';
      paletteResults.appendChild(empty);
      return;
    }
    let lastGroup = null;
    paletteFiltered.forEach((cmd, i) => {
      if (cmd.group !== lastGroup) {
        const g = document.createElement('div');
        g.className = 'palette-group';
        g.textContent = cmd.group;
        paletteResults.appendChild(g);
        lastGroup = cmd.group;
      }
      const item = document.createElement('div');
      item.className = 'palette-item' + (i === paletteActiveIdx ? ' active' : '');
      item.dataset.idx = i;
      const lbl = document.createElement('span');
      lbl.className = 'palette-item-label';
      lbl.textContent = cmd.label;
      item.appendChild(lbl);
      const hint = typeof cmd.hint === 'function' ? cmd.hint() : cmd.hint;
      if (hint) {
        const k = document.createElement('span');
        k.className = 'palette-kbd';
        k.textContent = hint;
        item.appendChild(k);
      }
      item.addEventListener('click', () => { paletteActiveIdx = i; runActivePalette(); });
      item.addEventListener('mouseenter', () => {
        if (paletteActiveIdx === i) return;
        paletteActiveIdx = i;
        paletteResults.querySelectorAll('.palette-item').forEach((el, idx) => {
          el.classList.toggle('active', idx === paletteActiveIdx);
        });
      });
      paletteResults.appendChild(item);
    });
  }

  function openPalette() {
    if (_isCompact) {
      window.wcCompact();
      setTimeout(openPalette, 260);
      return;
    }
    paletteOpen = true;
    paletteInput.value = '';
    paletteActiveIdx = 0;
    paletteBackdrop.classList.add('open');
    renderPalette();
    _focusModal(paletteBackdrop, paletteInput);
  }
  function closePalette() {
    paletteOpen = false;
    paletteBackdrop.classList.remove('open');
    _restoreModalFocus(paletteBackdrop);
  }
  function runActivePalette() {
    const cmd = paletteFiltered[paletteActiveIdx];
    if (!cmd) return;
    closePalette();
    setTimeout(() => cmd.run(), 40);
  }

  paletteInput.addEventListener('input', renderPalette);
  paletteInput.addEventListener('keydown', (e) => {
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      paletteActiveIdx = Math.min(paletteFiltered.length - 1, paletteActiveIdx + 1);
      renderPalette();
      const el = paletteResults.querySelector('.palette-item.active');
      if (el) el.scrollIntoView({ block: 'nearest' });
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      paletteActiveIdx = Math.max(0, paletteActiveIdx - 1);
      renderPalette();
      const el = paletteResults.querySelector('.palette-item.active');
      if (el) el.scrollIntoView({ block: 'nearest' });
    } else if (e.key === 'Enter') {
      e.preventDefault();
      runActivePalette();
    } else if (e.key === 'Escape') {
      e.preventDefault();
      closePalette();
    }
  });
  paletteBackdrop.addEventListener('click', (e) => {
    if (e.target === paletteBackdrop) closePalette();
  });

  /* Shortcuts overlay */
  const shortcutSections = () => [
    { group: 'Global',  items: [
      [_shortcutHotkeys.startStop, 'Start / stop macro (configurable)'],
      [_shortcutHotkeys.skip,      'Skip current stat (configurable)'],
      [_shortcutHotkeys.pause,     'Pause / resume (configurable)'],
    ]},
    { group: 'App',     items: [
      ['Ctrl+K', 'Command palette'],
      ['Ctrl+R', 'Refresh frontend state, stats, and session log'],
      ['Ctrl+M', 'Toggle compact mode'],
      ['?',      'This menu'],
      ['Esc',    'Close overlays'],
    ]},
    { group: 'Window',  items: [
      ['Drag title bar',  'Move window'],
      ['Drag any edge',  'Resize window'],
      ['Drag sidebar',   'Resize sidebar'],
    ]},
  ];
  const shortcutsBackdrop = document.getElementById('shortcutsBackdrop');
  const shortcutsContent  = document.getElementById('shortcutsContent');
  let shortcutsOpen = false;
  function renderShortcuts() {
    shortcutsContent.innerHTML = '';
    for (const sec of shortcutSections()) {
      const g = document.createElement('div');
      g.className = 'palette-group';
      g.textContent = sec.group;
      shortcutsContent.appendChild(g);
      for (const [k, lbl] of sec.items) {
        const it = document.createElement('div');
        it.className = 'palette-item';
        const l = document.createElement('span');
        l.className = 'palette-item-label';
        l.textContent = lbl;
        const kb = document.createElement('span');
        kb.className = 'palette-kbd';
        kb.textContent = k;
        it.append(l, kb);
        shortcutsContent.appendChild(it);
      }
    }
  }
  function openShortcuts() {
    if (_isCompact) {
      window.wcCompact();
      setTimeout(openShortcuts, 260);
      return;
    }
    shortcutsOpen = true;
    shortcutsBackdrop.classList.add('open');
    renderShortcuts();
    _focusModal(shortcutsBackdrop);
  }
  function closeShortcuts() {
    shortcutsOpen = false;
    shortcutsBackdrop.classList.remove('open');
    _restoreModalFocus(shortcutsBackdrop);
  }
  shortcutsBackdrop.addEventListener('click', (e) => {
    if (e.target === shortcutsBackdrop) closeShortcuts();
  });

  document.addEventListener('keydown', (e) => {
    if (e.key === 'Tab' && _trapModalFocus(e)) return;
    const inInput = ['INPUT','TEXTAREA'].includes(document.activeElement?.tagName);
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'r') {
      e.preventDefault();
      window.refreshFrontend();
      return;
    }
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') {
      e.preventDefault();
      if (paletteOpen) closePalette(); else openPalette();
      return;
    }
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'm') {
      e.preventDefault();
      window.wcCompact();
      return;
    }
    if (e.key === '?' && !inInput) {
      e.preventDefault();
      if (shortcutsOpen) closeShortcuts(); else openShortcuts();
      return;
    }
    if (e.key === 'Escape') {
      if (paletteOpen) { e.preventDefault(); closePalette(); return; }
      if (shortcutsOpen) { e.preventDefault(); closeShortcuts(); return; }
      if (_cancelResolutionWarning) { e.preventDefault(); _cancelResolutionWarning(); return; }
      if (document.getElementById('welcomeOverlay')?.classList.contains('open')) {
        e.preventDefault(); dismissWelcome(); return;
      }
      if (document.getElementById('changelogOverlay')?.classList.contains('open')) {
        e.preventDefault(); closeChangelog(); return;
      }
      if (document.getElementById('announcementOverlay')?.classList.contains('open')) {
        e.preventDefault(); closeAnnouncement();
      }
    }
  });

  /* Status footer updates */
  function _updateFooter(state) {
    if (!state) return;
    const running = !!state.running;
    const sfDot = document.getElementById('sfDot');
    const sfState = document.getElementById('sfState');
    const sfTime = document.getElementById('sfTime');
    if (sfDot) sfDot.classList.toggle('active', running);
    if (sfState) sfState.textContent = running ? (state.current_state || 'Running') : 'Idle';
    if (sfTime) {
      sfTime.textContent = _startedAt
        ? _fmtElapsed(Math.floor(Date.now() / 1000 - _startedAt))
        : '—';
    }
  }

  /* ── Signed GitHub updates + changelog overlay ── */
  const AUTO_UPDATE_KEY = 'xmacro-auto-update';
  const IGNORED_UPDATE_KEY = 'xmacro-update-ignored-version';
  const SEEN_VERSION_KEY = 'xmacro-changelog-seen';
  const ANNOUNCEMENT_SEEN_KEY = 'xmacro-announcement-seen';
  const WELCOME_SEEN_KEY = 'xmacro-welcome-seen';
  const ANNOUNCEMENT_URL = 'https://raw.githubusercontent.com/lowkxyn/xynmacro/main/announcements.json';

  // What's-new content, newest first. Each entry: {version, notes:[{h, items[]}]}.
  const CHANGELOG = [
    { version: '1.1.0', notes: [
      { h: 'Error Recovery', items: [
        'Added bounded retry-after-error controls with a configurable retry limit, recovery method, and walk duration.',
        'GC death is detected directly, the Respawn dialog is confirmed before clicking, and completed stats are rechecked after recovery.',
        'Starting the macro while already on GC\'s death screen is detected before any menu input is sent.',
      ]},
      { h: 'Safety', items: [
        'Manual Stop never retries, stale monitor input is stopped before recovery, and after-run failure actions wait until retries are exhausted.',
      ]},
    ]},
    { version: '1.0.5', notes: [
      { h: 'Interface', items: [
        'Added After Run choices for Main Menu, closing Roblox, staying in GC at 0G, and optional PC shutdown.',
        'Added Support Diagnostics with a live labelled vision preview and copyable environment report.',
      ]},
      { h: 'Fixes', items: [
        'Training Mode is detected during a run, so minigame input stops and an unfinished stat resumes safely.',
        'Manually skipped stats now mark the order incomplete and never trigger successful after-run actions.',
      ]},
    ]},
    { version: '1.0.4', notes: [
      { h: 'Interface', items: [
        'Added the W spain titlebar tag and one-time launch celebration.',
      ]},
    ]},
    { version: '1.0.3', notes: [
      { h: 'Fixes', items: [
        'Removed a stray empty notification pill that could briefly appear in the top-right corner.',
      ]},
    ]},
    { version: '1.0.2', notes: [
      { h: 'Fixes', items: [
        'Auto-Senzu no longer misfires on startup (the stray Tab press right after a category begins).',
        'In-game clicks now land immediately without needing a mouse wiggle first.',
        'Aero style: hover tooltips no longer render behind panels.',
      ]},
    ]},
    { version: '1.0.1', notes: [
      { h: 'Fixes', items: [
        'Notifications no longer overlap the window buttons in the top-right corner.',
        'Fixed the 1080p monitor switch failing on secondary monitors (display error -2).',
      ]},
    ]},
    { version: '1.0.0', notes: [
      { h: 'Training automation', items: [
        'Automates Health, Agility, Physical Damage, Ki Control, and Ki Damage in HTC and GC.',
        'Tracks each stat’s progression and advances through your chosen training order.',
        'Starts safely from gameplay, the Game Menu, the Training menu, or an active minigame.',
      ]},
      { h: 'Auto-Senzu and gravity', items: [
        'Detects red HP, consumes and refills Senzu Beans, and resumes the interrupted stat.',
        'Supports full beans, half beans, and configurable preference order.',
        'Can raise GC gravity automatically and return it to 0G when beans run out.',
      ]},
      { h: 'Desktop app', items: [
        'Classic and Aero interface styles, eight colour themes, animated backgrounds, and compact pill mode.',
        'Live telemetry, session logs, calibration tools, configurable hotkeys, and monitor-aware 1080p switching.',
        'Signed automatic updates, release notes, and owner announcements through the title-bar bell.',
      ]},
    ]},
  ];

  function autoUpdateOn() {
    return localStorage.getItem(AUTO_UPDATE_KEY) !== 'off';
  }

  window.toggleAutoUpdate = (el) => {
    const next = !autoUpdateOn();
    localStorage.setItem(AUTO_UPDATE_KEY, next ? 'on' : 'off');
    el.classList.toggle('active', next);
    el.setAttribute('aria-checked', next ? 'true' : 'false');
    const status = document.getElementById('updateStatus');
    if (status) status.textContent = next
      ? 'Automatic update checks are on.'
      : 'Automatic checks are off. Check now still works.';
  };

  let _updateCheckTask = null;
  let _autoUpdateChecked = false;

  function _setUpdateStatus(message) {
    const status = document.getElementById('updateStatus');
    if (status) status.textContent = message;
  }

  function _friendlyUpdateError(error) {
    const message = String(error || 'Unknown update error');
    if (/pubkey|public key|endpoint|not configured|configuration|release json/i.test(message)) {
      return 'This workspace build is not release-configured. Installed releases use signed GitHub updates.';
    }
    return `Update check failed: ${message}`;
  }

  // If installation failed after the app had already hidden and stopped its
  // backend, Rust relaunches cleanly and leaves one short-lived error marker.
  // Read it once so the failure is visible instead of looking like a random
  // restart; the next check can safely offer the update again.
  setTimeout(async () => {
    try {
      const error = await invoke('take_update_install_error');
      if (!error) return;
      _setUpdateStatus(`Update was not installed: ${error}`);
      showToast('Update installation failed. It will be offered again.', 'err');
    } catch (error) {}
  }, 500);

  // Update-on-restart: the update downloads in the background (safe even while
  // the macro is running) and installs when the app closes or the user clicks
  // "Restart & update" — never mid-session.
  let _pendingUpdateVersion = null;

  // Transient titlebar text (e.g. "Downloading update… 42%"). Passing a falsy
  // value fades it away; the element collapses to zero width so nothing lingers.
  function _setTicker(text) {
    const t = document.getElementById('updateTicker');
    if (!t) return;
    if (text) { t.textContent = text; t.classList.add('show'); }
    else { t.classList.remove('show'); }
  }

  // Green dot on the bell while an update is downloaded and waiting to install.
  function _syncUpdateBell() {
    const bell = document.getElementById('announcementBell');
    if (!bell) return;
    const pending = !!_pendingUpdateVersion;
    bell.classList.toggle('update-ready', pending);
    if (pending) {
      bell.title = `Update ready — v${_pendingUpdateVersion} (click to view)`;
      bell.setAttribute('aria-label', bell.title);
    }
    else _syncAnnouncementBell();
  }

  function _showUpdateToast(version) {
    const toast = document.getElementById('updateToast');
    const body = document.getElementById('utBody');
    if (body) body.textContent = `XynMacro v${version} is downloaded. It installs automatically when you close or restart the app.`;
    toast?.classList.add('show');
  }

  async function _deferPendingUpdate({ ignoreVersion = false } = {}) {
    const version = _pendingUpdateVersion;
    if (!version) return;
    try {
      await invoke('discard_pending_update');
    } catch (error) {
      _setUpdateStatus(_friendlyUpdateError(error));
      return;
    }
    if (ignoreVersion) {
      localStorage.setItem(IGNORED_UPDATE_KEY, version);
      _setUpdateStatus(`XynMacro v${version} reminders are hidden. Use Check now to update manually.`);
    } else {
      _setUpdateStatus(`XynMacro v${version} was postponed. It will appear again on the next launch or check.`);
    }
    _pendingUpdateVersion = null;
    document.getElementById('updateToast')?.classList.remove('show');
    _syncUpdateBell();
  }

  async function _downloadUpdateAndNotify(info) {
    if (_pendingUpdateVersion === info.version) {
      _showUpdateToast(info.version);
      _syncUpdateBell();
      return true;
    }
    _setUpdateStatus(`Downloading XynMacro v${info.version}…`);
    _setTicker('Downloading update…');
    try {
      const version = await invoke('download_update');
      if (!version) {
        _setUpdateStatus('The update was no longer available. Check again.');
        return false;
      }
      _pendingUpdateVersion = version;
      _setUpdateStatus(`XynMacro v${version} is ready — it installs when the app restarts.`);
      _showUpdateToast(version);
      _syncUpdateBell();
      return true;
    } catch (error) {
      _setUpdateStatus(_friendlyUpdateError(error));
      return false;
    } finally {
      _setTicker('');
    }
  }

  document.getElementById('utLater')?.addEventListener('click', () => {
    void _deferPendingUpdate();
  });
  document.getElementById('utIgnore')?.addEventListener('click', () => {
    void _deferPendingUpdate({ ignoreVersion: true });
  });
  document.getElementById('utRestart')?.addEventListener('click', async () => {
    if (_macroRunning || _macroUiAction) {
      _setUpdateStatus('Macro is running — the update installs when you stop it and close the app.');
      return;
    }
    const installing = document.getElementById('updateInstalling');
    const hint = document.getElementById('updateInstallingHint');
    if (hint) hint.textContent = _pendingUpdateVersion
      ? `Updating to v${_pendingUpdateVersion} — XynMacro will restart in a moment…`
      : 'XynMacro will restart in a moment…';
    installing?.classList.add('open');
    try {
      const started = await invoke('install_pending_update');
      if (!started) {
        installing?.classList.remove('open');
        _setUpdateStatus('The downloaded update is no longer pending. Check again.');
      }
      // On success the process exits into the installer; this line rarely runs.
    } catch (error) {
      installing?.classList.remove('open');
      _setUpdateStatus(_friendlyUpdateError(error));
    }
  });

  async function _runUpdateCheck({ automatic = false } = {}) {
    if (_updateCheckTask) return _updateCheckTask;
    _setUpdateStatus('Checking GitHub for updates…');
    const task = (async () => {
      try {
        const info = await invoke('check_update');
        if (!info) {
          _setUpdateStatus(`XynMacro ${_appVersion || ''} is up to date.`.trim());
          return null;
        }
        const ignoredVersion = localStorage.getItem(IGNORED_UPDATE_KEY);
        const reminder = XMacroUpdateState.reminderDecision(
          info.version,
          ignoredVersion,
          automatic,
        );
        if (reminder.skip) {
          _setUpdateStatus(`XynMacro v${info.version} is available. Its reminder is hidden; use Check now to update.`);
          return info;
        }
        if (reminder.clearIgnored) {
          localStorage.removeItem(IGNORED_UPDATE_KEY);
        }
        _setUpdateStatus(`XynMacro v${info.version} is available.`);
        await _downloadUpdateAndNotify(info);
        return info;
      } catch (error) {
        _setUpdateStatus(_friendlyUpdateError(error));
        return null;
      }
    })().finally(() => {
      if (_updateCheckTask === task) _updateCheckTask = null;
    });
    _updateCheckTask = task;
    return task;
  }

  try {
    window.__TAURI__.event?.listen('update-download-progress', ({ payload }) => {
      const downloaded = Number(payload?.downloaded || 0);
      const total = Number(payload?.total || 0);
      const label = total > 0
        ? `Downloading update… ${Math.min(100, Math.round(downloaded * 100 / total))}%`
        : `Downloading update… ${Math.round(downloaded / 1024)} KB`;
      _setUpdateStatus(label);
      _setTicker(label);
    });
  } catch (error) {}

  // Inbox of every version, newest first. The one matching `version` (the
  // just-updated release) opens automatically; the rest are collapsed headers
  // the user can pop down. Clicking a header toggles its panel.
  function renderChangelog(version) {
    const verEl = document.getElementById('clVersion');
    const body = document.getElementById('clBody');
    const known = CHANGELOG.some((c) => c.version === version);
    const openVer = known ? version : (CHANGELOG[0] && CHANGELOG[0].version);
    if (verEl) verEl.textContent = 'v' + (version || openVer || '');
    if (!body) return;
    if (!CHANGELOG.length) {
      body.innerHTML = '<p style="padding:8px 2px;color:var(--text3)">You’re up to date.</p>';
      return;
    }
    const chevron = '<svg class="cl-chevron" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="9 6 15 12 9 18"/></svg>';
    body.innerHTML = CHANGELOG.map((entry, i) => {
      const open = entry.version === openVer;
      const panelId = `changelog-${entry.version.replace(/[^a-z0-9_-]/gi, '-')}`;
      const notes = entry.notes.map((group) =>
        `<h4>${group.h}</h4><ul>${group.items.map((it) => `<li>${it}</li>`).join('')}</ul>`
      ).join('');
      const summary = entry.notes.map((g) => g.h).join(' · ');
      const tag = i === 0 ? '<span class="cl-entry-tag">New</span>' : '';
      return `<div class="cl-entry${open ? ' open' : ''}" data-ver="${entry.version}">`
        + `<button class="cl-entry-head" type="button" aria-expanded="${open}" aria-controls="${panelId}">${chevron}`
        + `<span class="cl-entry-ver">v${entry.version}</span>${tag}`
        + `<span class="cl-entry-summary">${summary}</span></button>`
        + `<div class="cl-entry-panel" id="${panelId}" role="region" aria-hidden="${!open}"><div class="cl-entry-panel-inner">${notes}</div></div>`
        + `</div>`;
    }).join('');
    body.querySelectorAll('.cl-entry-head').forEach((head) => {
      head.addEventListener('click', () => {
        const entry = head.closest('.cl-entry');
        const open = entry?.classList.toggle('open') || false;
        head.setAttribute('aria-expanded', open ? 'true' : 'false');
        entry?.querySelector('.cl-entry-panel')?.setAttribute('aria-hidden', open ? 'false' : 'true');
      });
    });
  }

  // Re-query at call time rather than caching a possibly-null reference from
  // init (guards against the element not existing yet when the module set up).
  function openChangelog(version) {
    const o = document.getElementById('changelogOverlay');
    if (!o) return;
    renderChangelog(version);
    o.classList.remove('closing');
    o.classList.add('open');
    _focusModal(o, '#clContinue');
  }

  function closeChangelog() {
    const o = document.getElementById('changelogOverlay');
    if (!o) return;
    o.classList.remove('open');
    o.classList.add('closing');
    _restoreModalFocus(o);
    setTimeout(() => o.classList.remove('closing'), 760);
  }

  const clOverlay = document.getElementById('changelogOverlay');

  document.getElementById('clContinue')?.addEventListener('click', closeChangelog);
  clOverlay?.addEventListener('click', (e) => { if (e.target === clOverlay) closeChangelog(); });

  // Subtle 3D parallax: the card tilts toward the cursor so the text reads as
  // floating. Skipped when the user prefers reduced motion. Shared by the
  // changelog and welcome cards so both feel identical.
  const _reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  function attachCardParallax(overlay) {
    const card = overlay?.querySelector('.changelog-card');
    if (!card || _reduceMotion) return;
    overlay.addEventListener('mousemove', (e) => {
      const r = card.getBoundingClientRect();
      const dx = (e.clientX - (r.left + r.width / 2)) / r.width;
      const dy = (e.clientY - (r.top + r.height / 2)) / r.height;
      card.style.transform = `perspective(900px) rotateY(${dx * 1.35}deg) rotateX(${-dy * 1.35}deg)`;
    });
    overlay.addEventListener('mouseleave', () => { card.style.transform = ''; });
  }
  attachCardParallax(clOverlay);
  /* First-run welcome — same overlay mechanics as the changelog card. */
  function openWelcome() {
    const o = document.getElementById('welcomeOverlay');
    if (!o) return;
    o.classList.remove('closing');
    o.classList.add('open');
    _focusModal(o, '#welcomeStart');
  }

  function dismissWelcome() {
    localStorage.setItem(WELCOME_SEEN_KEY, '1');
    const o = document.getElementById('welcomeOverlay');
    if (!o) return;
    o.classList.remove('open');
    o.classList.add('closing');
    _restoreModalFocus(o);
    setTimeout(() => o.classList.remove('closing'), 760);
  }

  const welcomeOverlay = document.getElementById('welcomeOverlay');
  document.getElementById('welcomeStart')?.addEventListener('click', dismissWelcome);
  welcomeOverlay?.addEventListener('click', (e) => { if (e.target === welcomeOverlay) dismissWelcome(); });
  attachCardParallax(welcomeOverlay);

  document.getElementById('btnViewChangelog')?.addEventListener('click', () => openChangelog(_appVersion));
  document.getElementById('btnCheckUpdate')?.addEventListener('click', () => _runUpdateCheck());

  let _announcement = null;
  let _announcementLoaded = false;
  let _announcementError = false;

  function _normaliseAnnouncement(value) {
    const item = value?.announcement;
    if (!item || typeof item !== 'object') return null;
    const id = String(item.id || '').trim().slice(0, 100);
    const title = String(item.title || '').trim().slice(0, 120);
    const body = String(item.body || '').trim().slice(0, 4000);
    if (!id || !title || !body) return null;
    return {
      id,
      title,
      body,
      publishedAt: String(item.publishedAt || '').trim().slice(0, 40),
    };
  }

  function _renderAnnouncement() {
    const title = document.getElementById('announcementTitle');
    const body = document.getElementById('announcementBody');
    const date = document.getElementById('announcementDate');
    if (!title || !body || !date) return;
    if (_announcement) {
      title.textContent = _announcement.title;
      body.textContent = _announcement.body;
      const parsed = Date.parse(_announcement.publishedAt);
      date.textContent = Number.isFinite(parsed)
        ? new Date(parsed).toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' })
        : '';
      return;
    }
    title.textContent = _announcementError
      ? 'Messages unavailable'
      : (_announcementLoaded ? 'No announcements' : 'Checking for messages…');
    body.textContent = _announcementError
      ? 'XynMacro could not check GitHub right now. Check your connection and try again later.'
      : (_announcementLoaded ? 'There is no owner message right now.' : 'XynMacro is checking GitHub.');
    date.textContent = '';
  }

  function _syncAnnouncementBell() {
    const bell = document.getElementById('announcementBell');
    if (!bell) return;
    const unread = !!_announcement
      && localStorage.getItem(ANNOUNCEMENT_SEEN_KEY) !== _announcement.id;
    bell.classList.toggle('unread', unread);
    bell.title = _announcementError
      ? 'Announcements unavailable'
      : _announcement
      ? (unread ? 'New announcement' : 'Announcements')
      : 'Announcements';
    bell.setAttribute('aria-label', bell.title);
  }

  async function _loadAnnouncement() {
    try {
      _announcementError = false;
      const response = await fetch(ANNOUNCEMENT_URL, { cache: 'no-store' });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      _announcement = _normaliseAnnouncement(await response.json());
    } catch (error) {
      _announcement = null;
      _announcementError = true;
    } finally {
      _announcementLoaded = true;
      _renderAnnouncement();
      _syncAnnouncementBell();
    }
  }

  function closeAnnouncement() {
    const overlay = document.getElementById('announcementOverlay');
    if (!overlay) return;
    overlay.classList.remove('open');
    overlay.classList.add('closing');
    _restoreModalFocus(overlay);
    setTimeout(() => overlay.classList.remove('closing'), 760);
  }

  window.toggleAnnouncement = () => {
    // A waiting update takes over the bell: clicking it opens the update inbox
    // and its install/postpone actions.
    if (_pendingUpdateVersion) {
      _showUpdateToast(_pendingUpdateVersion);
      return;
    }
    const overlay = document.getElementById('announcementOverlay');
    if (!overlay) return;
    _renderAnnouncement();
    if (_announcement) {
      localStorage.setItem(ANNOUNCEMENT_SEEN_KEY, _announcement.id);
      _syncAnnouncementBell();
    }
    overlay.classList.remove('closing');
    overlay.classList.add('open');
    _focusModal(overlay, '#announcementClose');
  };

  const announcementOverlay = document.getElementById('announcementOverlay');
  document.getElementById('announcementClose')?.addEventListener('click', closeAnnouncement);
  announcementOverlay?.addEventListener('click', (event) => {
    if (event.target === announcementOverlay) closeAnnouncement();
  });
  setTimeout(_loadAnnouncement, 1500);

  // Initialise the toggle from storage and, once we know the version, show the
  // changelog the first time we see a new one (that's "after an update").
  let _appVersion = '';
  let _changelogChecked = false;
  const autoUpdateToggle = document.getElementById('toggleAutoUpdate');
  autoUpdateToggle?.classList.toggle('active', autoUpdateOn());
  autoUpdateToggle?.setAttribute('aria-checked', autoUpdateOn() ? 'true' : 'false');

  window._maybeShowChangelog = (version) => {
    _appVersion = version || _appVersion;
    if (!version) return;
    if (!_autoUpdateChecked && autoUpdateOn()) {
      _autoUpdateChecked = true;
      setTimeout(() => _runUpdateCheck({ automatic: true }), 1200);
    }
    if (_changelogChecked) return;
    _changelogChecked = true;
    const seen = localStorage.getItem(SEEN_VERSION_KEY);
    if (localStorage.getItem(WELCOME_SEEN_KEY) === null) {
      // One-time welcome takes priority over the changelog: fresh installs
      // and installs that predate the welcome card both see it exactly once.
      localStorage.setItem(SEEN_VERSION_KEY, version);
      setTimeout(openWelcome, 650);
    } else if (seen === null) {
      localStorage.setItem(SEEN_VERSION_KEY, version);
    } else if (seen !== version) {
      localStorage.setItem(SEEN_VERSION_KEY, version);
      setTimeout(() => openChangelog(version), 650);
    }
  };

  /* Resizable sidebar */
  const sidebarHandle = document.getElementById('sidebarResize');
  const SIDEBAR_MIN = 180;
  const SIDEBAR_MAX = 340;
  const SIDEBAR_KEY = 'dbog-sidebar-width';
  const savedW = parseInt(localStorage.getItem(SIDEBAR_KEY) || '0', 10);
  const setSidebarWidth = (width, save = false) => {
    const value = Math.max(SIDEBAR_MIN, Math.min(SIDEBAR_MAX, width));
    document.documentElement.style.setProperty('--sidebar-w', value + 'px');
    sidebarHandle?.setAttribute('aria-valuenow', String(value));
    if (save) localStorage.setItem(SIDEBAR_KEY, String(value));
  };
  if (savedW >= SIDEBAR_MIN && savedW <= SIDEBAR_MAX) {
    setSidebarWidth(savedW);
  }
  if (sidebarHandle) {
    if (!sidebarHandle.hasAttribute('aria-valuenow')) {
      const initialWidth = parseInt(getComputedStyle(document.documentElement).getPropertyValue('--sidebar-w'), 10);
      const accessibleWidth = Math.max(SIDEBAR_MIN, Math.min(SIDEBAR_MAX, initialWidth || SIDEBAR_MIN));
      sidebarHandle.setAttribute('aria-valuenow', String(accessibleWidth));
    }
    sidebarHandle.addEventListener('mousedown', (e) => {
      if (e.button !== 0) return;
      e.preventDefault();
      sidebarHandle.classList.add('dragging');
      document.body.classList.add('sidebar-dragging');
      const onMove = (ev) => {
        setSidebarWidth(ev.clientX);
      };
      const onUp = () => {
        window.removeEventListener('mousemove', onMove);
        window.removeEventListener('mouseup', onUp);
        sidebarHandle.classList.remove('dragging');
        document.body.classList.remove('sidebar-dragging');
        const cur = parseInt(getComputedStyle(document.documentElement).getPropertyValue('--sidebar-w'), 10);
        if (cur) setSidebarWidth(cur, true);
      };
      window.addEventListener('mousemove', onMove);
      window.addEventListener('mouseup', onUp);
    });
    sidebarHandle.addEventListener('keydown', (event) => {
      if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
      event.preventDefault();
      const current = parseInt(getComputedStyle(document.documentElement).getPropertyValue('--sidebar-w'), 10) || SIDEBAR_MIN;
      const next = event.key === 'Home' ? SIDEBAR_MIN
        : event.key === 'End' ? SIDEBAR_MAX
        : current + (event.key === 'ArrowLeft' ? -10 : 10);
      setSidebarWidth(next, true);
    });
  }
})();
