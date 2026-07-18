(function (root) {
  function normalizeScreen(screen) {
    const width = Number(screen?.width || 0);
    const height = Number(screen?.height || 0);
    const device = String(screen?.device || '');
    if (screen?.source === 'unavailable' || width <= 0 || height <= 0 || !device) {
      return null;
    }
    return {
      w: width,
      h: height,
      hz: Number(screen?.hz || 0),
      device,
      signature: `${device}|${width}x${height}`,
    };
  }

  function needsResolutionWarning(screen, acceptedSignature) {
    return !!screen
      && !(screen.w === 1920 && screen.h === 1080)
      && screen.signature !== acceptedSignature;
  }

  root.XMacroScreenState = { normalizeScreen, needsResolutionWarning };
})(globalThis);
