// Create once after the page is ready. sendCommand resolves failures as {ok: false, msg}.
(function (root) {
  function create({ document, sendCommand, showToast }) {
    let shutdownBusy = false;
    function renderShutdown(state) {
      if (!state) return;
      const pending = !!state.pending;
      const labels = {
        requesting: 'Sending shutdown request to Windows…',
        requested: 'Shutdown requested. Windows may be waiting for open apps.',
        failed: 'Windows did not accept the shutdown request.',
        unknown: 'Shutdown request status is unknown. Check Windows before starting again.',
      };
      const message = pending ? `PC shutdown in ${state.seconds_remaining}s` : (labels[state.status] || '');
      document.getElementById('shutdownBanner').hidden = !message;
      document.getElementById('shutdownMessage').textContent = message;
      document.documentElement.classList.toggle('shutdown-notice', !!message);
      for (const id of ['cancelShutdownButton', 'hudCancelShutdown']) {
        const button = document.getElementById(id);
        button.hidden = !pending;
        button.disabled = shutdownBusy;
        button.textContent = shutdownBusy ? 'Cancelling…' : `Cancel shutdown${id === 'hudCancelShutdown' ? ` · ${state.seconds_remaining}s` : ''}`;
      }
    }
    const cancel = async () => {
      if (shutdownBusy) return;
      shutdownBusy = true;
      for (const id of ['cancelShutdownButton', 'hudCancelShutdown']) document.getElementById(id).disabled = true;
      const response = await sendCommand('shutdown_cancel');
      shutdownBusy = false;
      renderShutdown(response.shutdown);
      for (const id of ['cancelShutdownButton', 'hudCancelShutdown']) document.getElementById(id).disabled = false;
      showToast(response.msg || 'Could not confirm cancellation. Try again.', response.ok ? 'ok' : 'err');
    };

    return { render: renderShutdown, cancel };
  }

  root.XynMacroShutdownControls = { create };
})(globalThis);
