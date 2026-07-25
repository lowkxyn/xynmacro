(function (root) {
  function normalizeScreen(screen) {
    const width = Number(screen?.width || 0);
    const height = Number(screen?.height || 0);
    const device = String(screen?.device || '');
    if (screen?.source === 'unavailable' || width <= 0 || height <= 0 || !device) {
      return null;
    }
    const scale = Number(screen?.scale) > 0 ? Number(screen.scale) : 1;
    return {
      w: width,
      h: height,
      hz: Number(screen?.hz || 0),
      scale,
      device,
      signature: `${device}|${width}x${height}`,
    };
  }

  function needsResolutionWarning(screen, acceptedSignature) {
    return !!screen
      && !(screen.w === 1920 && screen.h === 1080)
      && screen.signature !== acceptedSignature;
  }

  // Windows display scaling above 100%. The sidecar is DPI-unaware, so every
  // coordinate it computes is off by this factor — a separate problem from the
  // resolution being wrong, and invisible in the resolution readout. Rounded
  // because the ratio is derived from pixel counts and lands a hair off 1.0.
  function needsDpiWarning(screen, acceptedSignature) {
    return !!screen
      && Math.abs(screen.scale - 1) > 0.01
      && screen.signature !== acceptedSignature;
  }

  root.XynMacroScreenState = { normalizeScreen, needsResolutionWarning, needsDpiWarning };
})(globalThis);
