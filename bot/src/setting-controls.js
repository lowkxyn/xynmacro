(function (root) {
  const beanNames = {
    full_only: 'Full only',
    full_then_half: 'Full, then Half',
    half_only: 'Half only',
    half_then_full: 'Half, then Full',
  };
  const healingActions = {
    senzu: 'Using Senzu',
    majin_regen: 'Majin Regeneration',
    majin_cooldown: 'Majin Cooldown',
    reserve_senzu: 'Senzu Fallback',
  };

  function create({ document }) {
    const get = id => document.getElementById(id);
    const enabled = id => !!get(id)?.classList.contains('active');

    function syncGroup(id, active, reason) {
      const group = get(id);
      if (!group) return;
      group.classList.toggle('is-disabled', !active);
      group.setAttribute('aria-disabled', active ? 'false' : 'true');
      group.querySelectorAll('input, select, button').forEach(control => {
        control.disabled = !active;
        control.title = active ? '' : reason;
      });
    }

    function sync() {
      const auto = enabled('toggleMajinRegenAuto');
      const majin = get('healMethod')?.value === 'majin_regen' || auto;
      syncGroup('majinRegenRows', majin, 'Choose Majin Super Regeneration or turn on Auto-Detect to use this setting.');
      const slotInput = get('majinRegenSlot');
      if (slotInput && auto) {
        // Auto-Detect reads the slot from the G panel itself.
        slotInput.disabled = true;
        slotInput.title = 'Auto-Detect finds the slot while holding G.';
      }
      syncGroup('autoRetryWalkRows', enabled('toggleAutoRetryWalkOut'), 'Turn on Return to GC Training Area to use this setting.');
      syncGroup('colourFallbackRows', enabled('toggleNoYellowFallback'), 'Turn on Colour Fallback to use this timeout.');
      syncGroup('stuckCounterRows', enabled('toggleProgressionStall'), 'Turn on Stuck Counter Fallback to use this timeout.');

      const senzu = enabled('toggleSenzu');
      const reserve = enabled('toggleMajinRegenReserve');
      const unavailable = majin && reserve && !senzu;
      const warning = get('majinReserveWarning');
      if (warning) warning.hidden = !unavailable;
      get('toggleMajinRegenReserve')?.setAttribute('aria-describedby', unavailable
        ? 'majinReserveHint majinReserveWarning' : 'majinReserveHint');

      const summary = get('healingSummary');
      if (summary) {
        const beans = beanNames[get('senzuPreferenceMode')?.value] || beanNames.full_only;
        const fallback = reserve
          ? senzu ? `Senzu fallback (${beans})` : 'Senzu fallback unavailable'
          : 'Senzu fallback off';
        const chosen = get('healMethod')?.value === 'majin_regen'
          ? `Majin slot ${get('majinRegenSlot')?.value || '3'}`
          : `Senzu (${senzu ? beans : 'off'})`;
        const text = auto
          ? `Method: Majin Regeneration if found (any slot), otherwise ${chosen}; ${fallback}`
          : majin
            ? `Method: Majin slot ${get('majinRegenSlot')?.value || '3'}; ${fallback}`
            : `Method: Senzu (${senzu ? beans : 'off'})`;
        if (summary.textContent !== text) summary.textContent = text;
      }
    }

    function renderHealingTelemetry(state) {
      const tel = state.telemetry || {};
      const majin = state.config?.heal_method === 'majin_regen'
        || (state.config?.majin_regen_auto && (tel.majin_regens || tel.majin_regen_failures));
      const label = get('telemHealingLabel');
      const value = get('telemSenzuStock');
      const detail = get('telemSenzuBreakdown');
      if (label) label.textContent = majin ? 'Majin Regens' : 'Senzu Stock';
      if (value) value.textContent = majin
        ? String(tel.majin_regens || 0)
        : state.senzu_remaining == null ? '\u2014' : String(state.senzu_remaining);
      if (detail) {
        const type = state.senzu_active_type === 'full' ? 'Full'
          : state.senzu_active_type === 'half' ? 'Half' : 'No bean';
        detail.textContent = majin
          ? `Regens \u00b7 ${tel.majin_regens || 0} done \u00b7 ${tel.majin_regen_failures || 0} failed`
          : `${type} \u00b7 ${state.senzu_status || 'idle'} \u00b7 ${tel.senzu_eaten || 0} eaten \u00b7 ${tel.senzu_refills || 0} refills`;
      }
    }

    function healingActivity(state) {
      return Object.hasOwn(healingActions, state.healing_action)
        ? healingActions[state.healing_action] : 'Healing';
    }

    return { sync, renderHealingTelemetry, healingActivity };
  }

  root.XynMacroSettingControls = { create };
})(globalThis);
