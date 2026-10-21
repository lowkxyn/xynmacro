// One-time launch celebration, created by main.js before the splash can finish.
(function (root) {
  function create({ document, window, storage, preferencePrefix }) {
    /* One-time "W spain" moment: a centred card plus an emoji shower as the splash
       fades. Fires once per install — the flag lives in ACTIVE_PREFERENCE_KEYS, so a
       config reset replays it. Everything is pointer-events:none and self-removing. */
    const WSPAIN_SEEN_KEY = preferencePrefix + 'wspain-seen';
    function celebrateWSpain() {
      const frame = document.querySelector('.window-frame');
      if (!frame || storage.getItem(WSPAIN_SEEN_KEY)) return;
      // Reduce Effects While Running: skip the pop and confetti, and keep the one-time flag unset so it replays later.
      if (document.documentElement.classList.contains('macro-lightweight')) return;
      storage.setItem(WSPAIN_SEEN_KEY, '1');
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
    // No flag here, it would fall as the literal letters "ES" on Windows.
    const CONFETTI_EMOJI = ['🎉', '🎊', '✨', '🥳', '🎈'];
    function spawnLaunchConfetti(count = 26) {
      const frame = document.querySelector('.window-frame');
      if (!frame || window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
      if (document.documentElement.classList.contains('macro-lightweight')) return;

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
    return { celebrateWSpain, spawnLaunchConfetti };
  }

  root.XynMacroLaunchCelebration = { create };
})(globalThis);
