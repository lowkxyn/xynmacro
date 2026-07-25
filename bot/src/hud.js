/* Debug HUD.
 *
 * Runs in its own transparent always-on-top window. Docked, it is stretched over
 * Roblox's client rect and is click-through, so it can draw the scan regions exactly
 * where the scanner looks. Popped out, it is an ordinary draggable panel and the
 * boxes are meaningless, so they are hidden.
 *
 * All numbers come from the backend's /state — the same source the main window reads,
 * so the HUD can never show a second, disagreeing version of the truth. */
(function () {
  const invoke = window.__TAURI__.core.invoke;
  const REFERENCE_WIDTH = 1920;
  const REFERENCE_HEIGHT = 1080;
  const POLL_MS = 500;

  const el = (id) => document.getElementById(id);
  const boxes = el('boxes');
  const labels = el('labels');

  let docked = true;
  let showBoxes = true;
  let placedGeometry = null;
  let scaleFactor = 1;
  let polling = false;
  let consecutiveFailures = 0;

  function set(id, text, cls) {
    const node = el(id);
    if (!node) return;
    node.textContent = text;
    node.className = 'v' + (cls ? ' ' + cls : '');
  }

  /* ---- region boxes ---------------------------------------------------------- */

  let lastRegionKey = '';

  function renderRegions(regions) {
    // Rebuilding the SVG every poll would fight the compositor for no reason; the
    // regions only change when calibration changes them.
    const key = JSON.stringify(regions);
    if (key === lastRegionKey) return;
    lastRegionKey = key;

    boxes.innerHTML = '';
    labels.innerHTML = '';
    el('regionList').innerHTML = '';

    for (const region of regions) {
      const rect = document.createElementNS('http://www.w3.org/2000/svg', 'rect');
      rect.setAttribute('x', region.left);
      rect.setAttribute('y', region.top);
      rect.setAttribute('width', region.width);
      rect.setAttribute('height', region.height);
      rect.setAttribute('stroke', region.colour);
      boxes.appendChild(rect);

      // Labels live in HTML rather than the stretched SVG so the text stays legible
      // even when the client is not 16:9.
      const label = document.createElement('span');
      label.textContent = region.name;
      label.style.color = region.colour;
      label.style.left = (region.left / REFERENCE_WIDTH * 100) + '%';
      label.style.top = (region.top / REFERENCE_HEIGHT * 100) + '%';
      labels.appendChild(label);

      const row = document.createElement('div');
      row.className = 'region';
      const swatch = document.createElement('i');
      swatch.style.background = region.colour;
      const name = document.createElement('span');
      name.className = 'n';
      name.textContent = region.name;
      const coords = document.createElement('span');
      coords.className = 'c';
      coords.textContent =
        `${region.left},${region.top} ${region.width}×${region.height}`;
      row.append(swatch, name, coords);
      el('regionList').appendChild(row);
    }
    el('regionCoordSpace').textContent = '(canonical 1920×1080)';
  }

  /* ---- modules --------------------------------------------------------------- */

  function renderWindow(state) {
    const gw = state.game_window || {};
    const screen = state.screen || {};
    const notes = [];

    if (!gw.found) {
      set('wFound', gw.minimized ? 'minimized' : 'not found', 'bad');
    } else {
      set('wFound', gw.fullscreen === true ? 'fullscreen'
        : gw.fullscreen === false ? 'windowed' : 'open', 'good');
    }

    const width = gw.width || 0;
    const height = gw.height || 0;
    set('wSize', width && height ? `${width} × ${height}` : '—',
      width === REFERENCE_WIDTH && height === REFERENCE_HEIGHT ? 'good' : '');
    set('wOrigin', gw.found ? `${gw.x}, ${gw.y}` : '—');

    if (width && height) {
      const scaleX = width / REFERENCE_WIDTH;
      const scaleY = height / REFERENCE_HEIGHT;
      const skewed = Math.abs(scaleX - scaleY) > 0.005;
      set('wScale', `${scaleX.toFixed(3)} / ${scaleY.toFixed(3)}`,
        skewed ? 'bad' : scaleX === 1 ? 'good' : 'warn');
      const ratio = width / height;
      set('wAspect', ratio.toFixed(3) + (skewed ? ' (not 16:9)' : ''),
        skewed ? 'bad' : 'good');
      if (skewed) {
        notes.push('X and Y scale independently, so this client skews every region. '
          + 'Use Windowed Mode or Set 1080p.');
      }
    } else {
      set('wScale', '—');
      set('wAspect', '—');
    }

    set('wScreen', screen.width && screen.height
      ? `${screen.width} × ${screen.height}` : '—');
    set('wDpi', scaleFactor.toFixed(2) + '×',
      Math.abs(scaleFactor - 1) < 0.001 ? 'good' : 'warn');
    if (Math.abs(scaleFactor - 1) > 0.001) {
      notes.push('Display scaling is not 100%. The backend measures windows without '
        + 'DPI awareness, so the boxes drawn here may sit off the real regions.');
    }

    const note = el('wNote');
    note.textContent = notes.join(' ');
    note.style.display = notes.length ? '' : 'none';
  }

  function renderRun(state) {
    if (state.running) {
      const label = state.stop_requested ? 'stopping'
        : state.controller_paused_for_senzu ? 'auto-senzu'
        : state.controller_paused ? 'paused'
        : 'running';
      set('rState', label, label === 'running' ? 'good' : 'warn');
    } else {
      set('rState', 'idle');
    }
    set('rStat', state.current_state || '—');
    set('rMenu', state.running ? (state.training_menu_visible ? 'yes' : 'no') : '—',
      state.running && !state.training_menu_visible ? 'warn' : '');

    const senzu = state.senzu_status || (state.senzu_remaining != null
      ? `${state.senzu_remaining} left` : '—');
    set('rSenzu', String(senzu));

    const errors = state.error_count || 0;
    set('rErrors', String(errors), errors ? 'bad' : 'good');
    const lastErrorRow = el('rLastErrRow');
    if (state.last_error) {
      lastErrorRow.style.display = '';
      el('rLastErr').textContent = String(state.last_error).slice(0, 160);
    } else {
      lastErrorRow.style.display = 'none';
    }

    set('bVersion', state.version || '—');
  }

  /* ---- placement ------------------------------------------------------------- */

  // The window is created hidden so it is never seen at Tauri's default position —
  // docked, that could be the wrong monitor entirely. Nothing shows it until it has
  // been placed at least once.
  let shown = false;

  async function reveal() {
    if (shown) return;
    shown = true;
    await invoke('hud', { action: 'show' }).catch(() => { shown = false; });
  }

  async function place(state) {
    const gw = state.game_window || {};
    if (!docked) return;
    if (!gw.found || !gw.width || !gw.height) {
      // Roblox closed or minimized. Show the panel anyway, parked on the primary
      // display, so pressing the button never looks like nothing happened — the
      // Window module is what explains why there is nothing to dock to.
      if (!shown) {
        await invoke('hud', {
          action: 'place',
          value: { x: 40, y: 40, width: 360, height: 460 },
        }).catch(() => {});
        await reveal();
      }
      placedGeometry = null;
      return;
    }
    const geometry = `${gw.x},${gw.y},${gw.width},${gw.height}`;
    if (geometry === placedGeometry) {
      await reveal();
      return;
    }
    placedGeometry = geometry;
    try {
      await invoke('hud', {
        action: 'place',
        value: { x: gw.x, y: gw.y, width: gw.width, height: gw.height },
      });
      await reveal();
    } catch (e) {
      placedGeometry = null; // let the next poll retry
      console.error('[hud] place failed', e);
    }
  }

  /* ---- poll ------------------------------------------------------------------ */

  async function poll() {
    if (polling) return; // a slow /state must not stack invokes
    polling = true;
    try {
      const response = await invoke('proxy_get', { path: '/state' });
      const state = response && response.ok === false ? null : response;
      if (!state) throw new Error(response?.msg || 'no state');
      consecutiveFailures = 0;
      set('bPoll', 'ok', 'good');
      renderWindow(state);
      renderRun(state);
      renderRegions(state.scan_regions || []);
      await place(state);
    } catch (e) {
      consecutiveFailures += 1;
      set('bPoll', `unreachable (${consecutiveFailures})`, 'bad');
    } finally {
      polling = false;
    }
  }

  /* ---- controls -------------------------------------------------------------- */

  // Only reachable when popped out; docked is click-through by design.
  el('btnBoxes').addEventListener('click', () => {
    showBoxes = !showBoxes;
    document.body.classList.toggle('no-boxes', !showBoxes);
  });

  el('btnDock').addEventListener('click', () => setDocked(true));

  el('hudHeader').addEventListener('mousedown', (event) => {
    if (docked || event.target.closest('button')) return;
    invoke('hud', { action: 'drag' }).catch(() => {});
  });

  async function setDocked(next) {
    docked = next;
    document.body.classList.toggle('docked', docked);
    document.body.classList.toggle('popped', !docked);
    document.body.classList.toggle('no-boxes', !docked || !showBoxes);
    placedGeometry = null;
    await invoke('hud', { action: 'docked', value: docked });
    if (!docked) {
      await invoke('hud', {
        action: 'place',
        value: { x: 80, y: 80, width: 360, height: 460 },
      }).catch(() => {});
      await reveal();
    }
  }

  // The main window tells the HUD which mode to start in via the URL.
  const params = new URLSearchParams(location.search);
  window.addEventListener('DOMContentLoaded', async () => {
    // devicePixelRatio is the window's scale factor, and unlike
    // window.__TAURI__.window it is always present under withGlobalTauri.
    scaleFactor = window.devicePixelRatio || 1;
    await setDocked(params.get('mode') !== 'window');
    poll();
    setInterval(poll, POLL_MS);
  });
})();
