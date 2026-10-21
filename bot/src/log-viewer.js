// Session log rendering and actions.
(function (root) {
  function create({ document, invoke, navigator, showToast }) {
    function _classifyLog(msg) {
      const m = msg.toLowerCase();
      if (/\berror\b|\bfail(?:ed)?\b|\bexception\b|\bcould not\b/.test(m)) return 'log-err';
      if (/\btimeout\b|\bskip(?:ping)?\b|\bwarn(?:ing)?\b|\bnot found\b|\bmissing\b|\bempty\b|\bunconfirmed\b/.test(m)) return 'log-warn';
      if (/\bperfect\b|\bfound\b|\bdetected\b|\bpressed\b|\bclick(?:ed)?\b/.test(m)) return 'log-ok';
      return '';
    }
    let _logSince = 0;
    let _logTimer = null;
    let _logsRefreshing = false;
    let _logsPolling = false;
    let _logGeneration = 0;
    /* Log viewer */
    function _fmtLogTime(epoch) {
      const d = new Date(epoch * 1000);
      return d.toLocaleTimeString([], { hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' });
    }

    // Scrolling up switches auto-scroll off; getting back to the bottom switches it on.
    // scroll doesn't bubble, so listen in the capture phase for every .log-feed.
    document.addEventListener('scroll', (event) => {
      const viewer = event.target;
      const autoScroll = document.getElementById('logAutoScroll');
      if (!autoScroll || !viewer.classList || !viewer.classList.contains('log-feed')) return;
      autoScroll.checked = viewer.scrollHeight - viewer.scrollTop - viewer.clientHeight <= 4;
    }, true);
    const autoScrollToggle = document.getElementById('logAutoScroll');
    if (autoScrollToggle) {
      autoScrollToggle.addEventListener('change', () => {
        if (!autoScrollToggle.checked) return;
        document.querySelectorAll('.log-feed').forEach((viewer) => { viewer.scrollTop = viewer.scrollHeight; });
      });
    }

    async function pollLogs(replace = false) {
      // Same overlap guard as poll(): the 400ms tick must not stack a second /logs
      // invoke on top of one that hasn't come back yet.
      if ((_logsRefreshing || _logsPolling) && !replace) return;
      const generation = replace ? ++_logGeneration : _logGeneration;
      if (replace) _logsRefreshing = true;
      else _logsPolling = true;
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
            if (e.seq > latest) latest = e.seq;
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
        if (!replace) _logsPolling = false;
      }
    }

    const clearLogs = () => {
      document.querySelectorAll('.log-feed').forEach((viewer) => {
        viewer.innerHTML = '<div class="log-empty">View cleared; session log retained</div>';
      });
    };

    // The viewer shows raw lines; what leaves the app is redacted by the backend,
    // the same path Save Log and bug reports use.
    const copyLog = async () => {
      let text = '';
      try {
        const r = await invoke('proxy_post', { path: '/copy_log', body: null });
        text = (r && r.ok && r.text) || '';
      } catch (_) {
        showToast('Could not prepare the log for copying', 'err');
        return;
      }
      if (!text) {
        showToast('Log is empty', 'err');
        return;
      }
      try {
        await navigator.clipboard.writeText(text);
        showToast(`Copied ${text.split('\n').length} lines with paths, emails and tokens removed — review before sharing`, 'ok');
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

    const saveLog = async () => {
      try {
        const r = await invoke('proxy_post', { path: '/save_log', body: null });
        if (r && r.ok) {
          showToast(`Saved ${r.count} redacted lines → ${r.path}. Review before sharing`, 'ok');
        } else {
          showToast((r && r.msg) || 'Save failed', 'err');
        }
      } catch (e) {
        showToast('Save failed: ' + e, 'err');
      }
    };

    function start() {
      _logTimer = setInterval(pollLogs, 400);
      pollLogs();
    }

    return { poll: pollLogs, start, clearLogs, copyLog, saveLog };
  }

  root.XynMacroLogViewer = { create };
})(globalThis);
