// Button and region calibration, including live previews.
(function (root) {
  function create({ document, invoke, sendCommand, showToast }) {
    const calibBtn = async (statName) => {
      const r = await sendCommand('calibrate_button_begin', { stat: statName });
      showToast(r.ok ? `Click the ${statName} trait button in Roblox` : (r.msg || 'Failed'),
                r.ok ? 'ok' : 'err');
    };

    const calibRegion = async (region) => {
      const r = await sendCommand('calibrate_region_begin', { region });
      const label = region === 'health_box' ? 'Health Box'
                  : region === 'agility_box' ? 'Agility Box'
                  : region;
      showToast(r.ok ? `Drag a rectangle over the ${label}` : (r.msg || 'Failed'),
                r.ok ? 'ok' : 'err');
    };

    /* Live region preview. One auto-refresh loop per open preview. Stops on toggle-off. */
    const _previewState = {}; // {region: {timer, busy, rowEl, imgEl, metaEl}}

    const PREVIEW_TARGETS = {
      health_box:  { rowId: 'previewRowHealth',  imgId: 'previewImgHealth',  metaId: 'previewMetaHealth',  btnId: 'previewBtnHealth'  },
      agility_box: { rowId: 'previewRowAgility', imgId: 'previewImgAgility', metaId: 'previewMetaAgility', btnId: 'previewBtnAgility' },
      diagnostics: { rowId: 'previewRowDiagnostics', imgId: 'previewImgDiagnostics', metaId: 'previewMetaDiagnostics', btnId: 'previewBtnDiagnostics' },
    };

    async function _fetchPreview(region) {
      const state = _previewState[region];
      if (!state || state.busy) return; // a screen capture can outlast the 1s tick
      state.busy = true;
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
      } catch (_) {
      } finally {
        state.busy = false;
      }
    }

    // Preview loops are per-region and only stopped by their own toggle, so leaving a
    // calibration view with one open kept polling heavy /preview captures forever.
    function _stopAllPreviews() {
      for (const region of Object.keys(_previewState)) {
        clearInterval(_previewState[region].timer);
        delete _previewState[region];
        const map = PREVIEW_TARGETS[region];
        if (!map) continue;
        const row = document.getElementById(map.rowId);
        if (row) row.style.display = 'none';
        const btn = document.getElementById(map.btnId);
        if (btn) btn.classList.remove('active');
      }
    }

    const togglePreview = (region) => {
      const map = PREVIEW_TARGETS[region];
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
        busy: false,
        rowEl: row,
        imgEl: document.getElementById(map.imgId),
        metaEl: document.getElementById(map.metaId),
      };
      _fetchPreview(region);
    };

    return { calibBtn, calibRegion, togglePreview, stopAllPreviews: _stopAllPreviews };
  }

  root.XynMacroCalibrationControls = { create };
})(globalThis);
