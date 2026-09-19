// Create once after the page is ready. sendCommand resolves failures as {ok: false, msg}.
(function (root) {
  function create({ document, sendCommand, showToast }) {
    let notificationDraft = false;
    let notificationBusy = false;
    let notificationState = {};
    function renderNotifications(state) {
      if (!state) return;
      notificationState = state;
      document.getElementById('notificationStatus').textContent = state.configured
        ? `Webhook saved · ${state.status}` : state.status || 'Not configured';
      if (!notificationDraft) {
        document.getElementById('notificationEnabled').checked = !!state.enabled;
        document.getElementById('notificationInterval').value = String(state.progress_minutes || 0);
      }
      document.getElementById('notificationSave').disabled = notificationBusy;
      document.getElementById('notificationTest').disabled = notificationBusy || !state.configured;
      document.getElementById('notificationClear').disabled = notificationBusy || !state.configured;
      for (const id of ['notificationUrl', 'notificationEnabled', 'notificationInterval']) {
        document.getElementById(id).disabled = notificationBusy;
      }
    }
    for (const id of ['notificationUrl', 'notificationEnabled', 'notificationInterval']) {
      document.getElementById(id).addEventListener('input', () => { notificationDraft = true; });
    }
    async function notificationCommand(action, value) {
      if (notificationBusy) return;
      notificationBusy = true;
      renderNotifications(notificationState);
      const response = await sendCommand(action, value);
      notificationBusy = false;
      if (response.ok && action !== 'notifications_test') {
        document.getElementById('notificationUrl').value = '';
        notificationDraft = false;
      }
      renderNotifications(response.notifications || notificationState);
      showToast(response.msg || (response.ok ? 'Notification settings updated' : 'Notification request failed'), response.ok ? 'ok' : 'err');
    }
    document.getElementById('notificationSave').addEventListener('click', () => {
      const value = { enabled: document.getElementById('notificationEnabled').checked,
        progress_minutes: Number(document.getElementById('notificationInterval').value) };
      const url = document.getElementById('notificationUrl').value.trim();
      if (url) value.url = url;
      notificationCommand('notifications_save', value);
    });
    document.getElementById('notificationTest').addEventListener('click', () => notificationCommand('notifications_test'));
    document.getElementById('notificationClear').addEventListener('click', () => notificationCommand('notifications_clear'));

    return { render: renderNotifications };
  }

  root.XynMacroNotificationControls = { create };
})(globalThis);
