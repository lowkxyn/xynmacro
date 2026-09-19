// Theme presets, the custom-colour editor and the animated background.
// Create once after the page is ready. showToast is taken as a dependency because
// main.js defines the real toast after this runs — pass a wrapper, not the function.

(function (globalScope) {
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

  // A custom theme is a single colour; the whole palette is generated from it the
  // same way the presets are built (neutral-dark surfaces + a vivid accent), so it
  // recolours the interface instead of looking like a flat overlay.
  function hexToRgb(h) {
    h = (h || '').replace('#', '');
    if (h.length === 3) h = [...h].map(x => x + x).join('');
    const n = parseInt(h || '0', 16);
    return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
  }
  function hexToHsl(hex) {
    let [r, g, b] = hexToRgb(hex); r /= 255; g /= 255; b /= 255;
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
  function hslHex(h, s, l) {
    s = Math.max(0, Math.min(100, s)) / 100; l = Math.max(0, Math.min(100, l)) / 100;
    const k = n => (n + h / 30) % 12, a = s * Math.min(l, 1 - l);
    const f = n => l - a * Math.max(-1, Math.min(k(n) - 3, 9 - k(n), 1));
    const to = x => Math.round(255 * x).toString(16).padStart(2, '0');
    return '#' + to(f(0)) + to(f(8)) + to(f(4));
  }
  function hslRgba(h, s, l, a) {
    const [r, g, b] = hexToRgb(hslHex(h, s, l));
    return `rgba(${r},${g},${b},${a})`;
  }
  const GEN_KEYS = ['--bg','--bg-alt','--bg-card','--bg-input','--bg-hover','--bg-nav','--border','--border-light','--border-input','--accent','--accent2','--accent-dim','--text','--text2','--text3','--grad-from','--grad-to','--grad-shadow','--glow1','--glow2','--glow3'];
  function genTheme(accentHex) {
    const [h, s] = hexToHsl(accentHex);
    const neutral = s < 8;                       // near-grey pick: stay neutral, don't snap to red (hue 0)
    const aS = neutral ? 7 : Math.max(35, Math.min(95, s));   // accent saturation
    const bS = neutral ? 4 : Math.min(aS, 40) * 0.7;          // surfaces: subtle tint, kept dark/neutral
    return {
      '--bg':       hslHex(h, bS, 5),
      '--bg-alt':   hslHex(h, bS, 9),
      '--bg-card':  hslRgba(h, bS * 0.85, 11, 0.88),
      '--bg-input': hslHex(h, bS, 13),
      '--bg-hover': hslHex(h, bS, 18),
      '--bg-nav':   hslRgba(h, bS, 4, 0.96),
      '--border':       hslRgba(h, aS, 62, 0.16),
      '--border-light': hslRgba(h, aS, 62, 0.08),
      '--border-input': hslRgba(h, aS, 62, 0.20),
      '--accent':     hslHex(h, aS, 72),
      '--accent2':    hslHex(h, aS, 62),
      '--accent-dim': hslRgba(h, aS, 72, 0.12),
      '--text':  hslHex(h, Math.min(aS, 45), 92),
      '--text2': hslHex(h, Math.min(aS, 35), 73),
      '--text3': hslHex(h, Math.min(aS, 22), 48),
      '--grad-from':   hslHex(h, Math.min(aS + 8, 95), 50),
      '--grad-to':     hslHex((h + 20) % 360, Math.min(aS, 85), 56),
      '--grad-shadow': hslRgba(h, aS, 50, 0.28),
      '--glow1': hslRgba(h, aS, 55, 0.14),
      '--glow2': hslRgba((h + 30) % 360, aS, 55, 0.10),
      '--glow3': hslRgba(h, aS, 55, 0.05),
    };
  }

  // Tokens exposed in the Advanced section, layered on top of the generated palette.
  const ADV_VARS = {
    '--accent': 'advAccent', '--accent2': 'advAccent2', '--bg': 'advBg',
    '--text': 'advText', '--text2': 'advText2', '--grad-from': 'advGradFrom', '--grad-to': 'advGradTo',
  };

  function create({ document, window, storage, showToast }) {
    const root = document.documentElement;

    // Canvas background effects (Flow blobs and Particles), coloured from the active
    // theme. Only runs while one of those effects is selected.
    const flow = (() => {
      let canvas, ctx, raf = 0, items = [], w = 0, h = 0, running = false, mode = 'flow', speed = 1;
      const cvar = (n) => window.getComputedStyle(root).getPropertyValue(n).trim();
      const rgba = (c, a) => {
        c = (c || '').trim();
        if (c[0] === '#') { const [r, g, b] = hexToRgb(c); return `rgba(${r},${g},${b},${a})`; }
        const m = c.match(/\d+/g);
        return m ? `rgba(${m[0]},${m[1]},${m[2]},${a})` : `rgba(129,140,248,${a})`;
      };
      const cols = () => [cvar('--accent'), cvar('--grad-to'), cvar('--accent2')].filter(Boolean);
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
            ctx.fillStyle = rgba(p.c, p.a);
            ctx.beginPath(); ctx.arc(p.x, p.y, p.r, 0, Math.PI * 2); ctx.fill();
          }
        } else if (mode === 'starfield') {
          ctx.globalCompositeOperation = 'source-over';
          for (const s of items) {
            s.y += s.z * 0.7 * speed; s.x += s.z * 0.25 * speed;
            if (s.y > h + 2) { s.y = -2; s.x = Math.random() * w; }
            if (s.x > w + 2) s.x = -2;
            ctx.fillStyle = rgba(s.c, s.a);
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
                ctx.strokeStyle = rgba(items[i].c, (1 - d / D) * 0.35);
                ctx.lineWidth = 1;
                ctx.beginPath(); ctx.moveTo(items[i].x, items[i].y); ctx.lineTo(items[j].x, items[j].y); ctx.stroke();
              }
            }
          }
          for (const n of items) {
            ctx.fillStyle = rgba(n.c, 0.7);
            ctx.beginPath(); ctx.arc(n.x, n.y, 1.8, 0, Math.PI * 2); ctx.fill();
          }
        } else {
          ctx.globalCompositeOperation = 'lighter';
          for (const b of items) {
            b.x += b.vx * speed; b.y += b.vy * speed;
            if (b.x < -b.r) b.x = w + b.r; else if (b.x > w + b.r) b.x = -b.r;
            if (b.y < -b.r) b.y = h + b.r; else if (b.y > h + b.r) b.y = -b.r;
            const g = ctx.createRadialGradient(b.x, b.y, 0, b.x, b.y, b.r);
            g.addColorStop(0, rgba(b.c, 0.42));
            g.addColorStop(1, rgba(b.c, 0));
            ctx.fillStyle = g;
            ctx.fillRect(b.x - b.r, b.y - b.r, b.r * 2, b.r * 2);
          }
        }
        raf = window.requestAnimationFrame(frame);
      }
      return {
        start(m) {
          if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
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
          if (raf) { window.cancelAnimationFrame(raf); raf = 0; }
          window.removeEventListener('resize', resize);
          if (ctx && canvas) ctx.clearRect(0, 0, canvas.width, canvas.height);
        },
        recolor() { if (running) { resize(); seed(); } },
        setSpeed(s) { speed = s || 1; },
      };
    })();

    function clearCustomVars() {
      GEN_KEYS.forEach(k => root.style.removeProperty(k));
    }
    // Custom themes are a list of named presets. Migrate any old single custom theme.
    function loadPresets() {
      try {
        const p = JSON.parse(storage.getItem('dbog-presets') || 'null');
        if (Array.isArray(p)) return p;
      } catch (e) {}
      try {
        const old = JSON.parse(storage.getItem('dbog-custom-theme') || 'null');
        if (old && old.color) {
          const list = [{ id: 'c' + Date.now(), name: 'Custom', color: old.color, overrides: old.overrides || {} }];
          storage.setItem('dbog-presets', JSON.stringify(list));
          return list;
        }
      } catch (e) {}
      return [];
    }
    function savePresets(list) { storage.setItem('dbog-presets', JSON.stringify(list)); }
    function presetById(id) { return loadPresets().find(p => p.id === id) || null; }

    function applyTheme(key) {
      clearCustomVars();
      if (key === 'custom' || (key && key.indexOf('p:') === 0)) {
        const p = key === 'custom' ? loadPresets()[0] : presetById(key.slice(2));
        if (p && p.color) {
          root.removeAttribute('data-theme');
          const vars = Object.assign(genTheme(p.color), XynMacroTheme.safeOverrides(p.overrides));
          for (const [k, v] of Object.entries(vars)) root.style.setProperty(k, v);
          key = 'p:' + p.id;
        } else {
          key = 'graphite';
          root.removeAttribute('data-theme');
        }
      } else if (key === 'graphite') {
        root.removeAttribute('data-theme');
      } else {
        root.setAttribute('data-theme', key);
      }
      storage.setItem('dbog-theme', key);
      renderThemes();
      flow.recolor();
    }

    function themeCard(key, name, accent, from, to, current, deletable) {
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
          savePresets(loadPresets().filter(p => p.id !== id));
          if ((storage.getItem('dbog-theme') || '') === key) applyTheme('graphite');
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
      const current = storage.getItem('dbog-theme') || 'graphite';
      grid.innerHTML = '';
      for (const [key, t] of Object.entries(THEMES)) {
        grid.appendChild(themeCard(key, t.name, t.accent, t.from, t.to, current, false));
      }
      for (const p of loadPresets()) {
        const v = Object.assign(genTheme(p.color), XynMacroTheme.safeOverrides(p.overrides));
        grid.appendChild(themeCard('p:' + p.id, p.name, v['--accent'], v['--grad-from'], v['--grad-to'], current, true));
      }
    }

    function setupCustomTheme() {
      const colorEl = document.getElementById('ctColor');
      if (!colorEl) return;
      let overrides = {};

      function preview() {
        clearCustomVars();
        root.removeAttribute('data-theme');
        const vars = Object.assign(genTheme(colorEl.value), XynMacroTheme.safeOverrides(overrides));
        for (const [k, v] of Object.entries(vars)) root.style.setProperty(k, v);
      }
      // Sync the advanced pickers to the current effective palette (generated + overrides).
      function seedAdv() {
        const vars = Object.assign(genTheme(colorEl.value), XynMacroTheme.safeOverrides(overrides));
        for (const [varName, id] of Object.entries(ADV_VARS)) {
          const el = document.getElementById(id);
          if (el && vars[varName] && vars[varName][0] === '#') el.value = vars[varName];
        }
      }

      // Changing the base colour regenerates everything and drops the fine-tune overrides.
      colorEl.addEventListener('input', () => { overrides = {}; preview(); seedAdv(); });
      for (const [varName, id] of Object.entries(ADV_VARS)) {
        document.getElementById(id)?.addEventListener('input', (e) => {
          overrides[varName] = e.target.value;
          preview();
        });
      }

      const nameEl = document.getElementById('ctName');
      document.getElementById('ctSave')?.addEventListener('click', () => {
        const list = loadPresets();
        const name = (nameEl && nameEl.value.trim()) || ('Custom ' + (list.length + 1));
        const id = 'c' + Date.now();
        list.push({ id, name, color: colorEl.value, overrides: { ...overrides } });
        savePresets(list);
        applyTheme('p:' + id);
        if (nameEl) nameEl.value = '';
        showToast(`Saved "${name}"`, 'ok');
      });
      document.getElementById('ctExport')?.addEventListener('click', () => {
        const data = { name: (nameEl && nameEl.value.trim()) || 'Custom', color: colorEl.value, overrides };
        const blob = new window.Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
        const a = document.createElement('a');
        a.href = window.URL.createObjectURL(blob);
        a.download = data.name.replace(/[^\w-]+/g, '-').toLowerCase() + '-theme.json';
        a.click();
        window.URL.revokeObjectURL(a.href);
        showToast('Theme exported', 'ok');
      });
      const fileEl = document.getElementById('ctFile');
      document.getElementById('ctImport')?.addEventListener('click', () => fileEl?.click());
      fileEl?.addEventListener('change', async () => {
        const f = fileEl.files && fileEl.files[0];
        if (!f) return;
        try {
          if (f.size > 65536) throw new Error('Theme file exceeds 64 KB');
          const ct = XynMacroTheme.normalizeTheme(JSON.parse(await f.text()));
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
      const activeKey = storage.getItem('dbog-theme') || '';
      const activePreset = activeKey.indexOf('p:') === 0 ? presetById(activeKey.slice(2)) : null;
      if (activePreset) {
        colorEl.value = activePreset.color;
        overrides = activePreset.overrides || {};
      }
      seedAdv();
    }

    function setupBackground() {
      const sel = document.getElementById('bgEffect');
      const speedEl = document.getElementById('bgSpeed');
      const CANVAS_MODES = ['flow', 'particles', 'starfield', 'constellation'];
      const apply = (v) => {
        const reduce = storage.getItem('xynmacro-reduce-run-effects') !== 'false';
        const lightweight = reduce && root.classList.contains('macro-active');
        root.classList.toggle('macro-lightweight', lightweight);
        if (document.hidden || lightweight) v = 'none';
        if (!v || v === 'none') root.removeAttribute('data-bg');
        else root.setAttribute('data-bg', v);
        if (CANVAS_MODES.includes(v)) flow.start(v); else flow.stop();
      };
      const reduceToggle = document.getElementById('reduceRunEffects');
      if (reduceToggle) {
        reduceToggle.checked = storage.getItem('xynmacro-reduce-run-effects') !== 'false';
        reduceToggle.addEventListener('change', () => {
          storage.setItem('xynmacro-reduce-run-effects', String(reduceToggle.checked));
          apply(sel.value);
        });
      }
      document.addEventListener('visibilitychange', () => apply(sel.value));
      window.addEventListener('xyn-run-state', () => apply(sel.value));
      // Speed slider scales CSS animations (via --bg-speed) and the canvas effects together.
      const savedSpeed = parseFloat(storage.getItem('dbog-bg-speed') || '1.6') || 1.6;
      root.style.setProperty('--bg-speed', savedSpeed);
      flow.setSpeed(savedSpeed);
      if (speedEl) {
        speedEl.value = savedSpeed;
        speedEl.addEventListener('input', () => {
          const s = parseFloat(speedEl.value) || 1;
          storage.setItem('dbog-bg-speed', s);
          root.style.setProperty('--bg-speed', s);
          flow.setSpeed(s);
        });
      }
      const cur = storage.getItem('dbog-bg') || 'flow';
      apply(cur);
      if (sel) {
        sel.value = cur;
        sel.addEventListener('change', () => {
          storage.setItem('dbog-bg', sel.value);
          apply(sel.value);
        });
      }
    }

    renderThemes();
    setupCustomTheme();
    setupBackground();

    return {
      THEMES,
      applyTheme,
      renderThemes,
      // Re-apply a saved preset/custom theme so the generated palette is computed on load.
      applySavedPreset() {
        const saved = storage.getItem('dbog-theme') || 'graphite';
        if (saved === 'custom' || saved.indexOf('p:') === 0) applyTheme(saved);
      },
    };
  }

  globalScope.XynMacroAppearanceControls = { create, THEMES };
})(globalThis);
