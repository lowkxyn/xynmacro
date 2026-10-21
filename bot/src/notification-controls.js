// Create once after the page is ready. sendCommand resolves failures as {ok: false, msg}.
(function (root) {
  function create({ document, sendCommand, showToast }) {
    let notificationDraft = false;
    let notificationBusy = false;
    let saveQueued = false;
    let notificationState = {};
    const controls = ['notificationUrl', 'notificationEnabled', 'notificationInterval', 'notificationScreenshots'];
    function renderNotifications(state) {
      if (!state) return;
      notificationState = state;
      document.getElementById('notificationStatus').textContent = state.configured
        ? `Webhook saved · ${state.status}` : state.status || 'Not configured';
      if (!notificationDraft) {
        document.getElementById('notificationEnabled').checked = !!state.enabled;
        document.getElementById('notificationInterval').value = String(state.progress_minutes || 0);
        document.getElementById('notificationScreenshots').checked = !!state.screenshots;
      }
      // The URL never reaches the UI; bullets in the placeholder show one is saved.
      document.getElementById('notificationUrl').placeholder = state.configured
        ? '•••••••••••• (saved)' : 'Paste Discord webhook URL';
      document.getElementById('notificationTest').disabled = notificationBusy || !state.configured;
      document.getElementById('notificationClear').disabled = notificationBusy || !state.configured;
      for (const id of controls) document.getElementById(id).disabled = notificationBusy;
    }
    async function notificationCommand(action, value) {
      if (notificationBusy) return;
      notificationBusy = true;
      renderNotifications(notificationState);
      const response = await sendCommand(action, value);
      notificationBusy = false;
      if (response.ok && action !== 'notifications_test') {
        document.getElementById('notificationUrl').value = '';
      }
      // A change queued during this save is still unsaved; don't let this response overwrite it.
      if ((action === 'notifications_save' && !saveQueued) || (response.ok && action === 'notifications_clear')) {
        notificationDraft = false;
      }
      renderNotifications(response.notifications || notificationState);
      showToast(response.msg || (response.ok ? 'Notification settings saved' : 'Notification request failed'), response.ok ? 'ok' : 'err');
      if (saveQueued) {
        saveQueued = false;
        saveNotifications();
      }
    }
    // No Save button: every change saves. A change made while a save is in flight
    // is saved right after it, so a quick second toggle isn't lost.
    function saveNotifications() {
      // The controls hold the user's edit until the backend answers.
      notificationDraft = true;
      if (notificationBusy) {
        saveQueued = true;
        return;
      }
      const value = { enabled: document.getElementById('notificationEnabled').checked,
        progress_minutes: Number(document.getElementById('notificationInterval').value),
        screenshots: document.getElementById('notificationScreenshots').checked };
      const url = document.getElementById('notificationUrl').value.trim();
      if (url) value.url = url;
      notificationCommand('notifications_save', value);
    }
    for (const id of controls) {
      document.getElementById(id).addEventListener('input', () => { notificationDraft = true; });
    }
    for (const id of ['notificationEnabled', 'notificationInterval', 'notificationScreenshots']) {
      document.getElementById(id).addEventListener('change', saveNotifications);
    }
    const urlInput = document.getElementById('notificationUrl');
    // Pasting a link saves straight away; typing saves on Enter or when the box loses focus.
    urlInput.addEventListener('paste', () => setTimeout(() => {
      if (urlInput.value.trim()) saveNotifications();
    }, 0));
    urlInput.addEventListener('keydown', (event) => {
      if (event && event.key === 'Enter' && urlInput.value.trim()) saveNotifications();
    });
    urlInput.addEventListener('change', () => {
      if (urlInput.value.trim()) saveNotifications();
    });
    document.getElementById('notificationTest').addEventListener('click', () => notificationCommand('notifications_test'));
    document.getElementById('notificationClear').addEventListener('click', () => notificationCommand('notifications_clear'));

    return { render: renderNotifications };
  }

  root.XynMacroNotificationControls = { create };
})(globalThis);
