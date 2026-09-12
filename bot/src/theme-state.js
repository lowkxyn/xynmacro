// @ts-check
(function (root) {
  const colour = /^#[0-9a-f]{6}$/i;
  const allowed = new Set(['--accent', '--accent2', '--bg', '--text', '--text2', '--grad-from', '--grad-to']);

  /** @param {unknown} value @returns {Record<string, string>} */
  function safeOverrides(value) {
    const result = {};
    if (!value || typeof value !== 'object' || Array.isArray(value)) return result;
    for (const [key, colourValue] of Object.entries(value)) {
      if (allowed.has(key) && typeof colourValue === 'string' && colour.test(colourValue)) {
        result[key] = colourValue;
      }
    }
    return result;
  }

  /** @param {unknown} value */
  function normalizeTheme(value) {
    if (!value || typeof value !== 'object' || !('color' in value)
        || typeof value.color !== 'string' || !colour.test(value.color)) {
      throw new Error('Choose a theme with a six-digit hex colour');
    }
    return {
      color: value.color,
      name: 'name' in value && typeof value.name === 'string' ? value.name.slice(0, 22) : 'Custom',
      overrides: safeOverrides('overrides' in value ? value.overrides : null),
    };
  }

  root.XynMacroTheme = { safeOverrides, normalizeTheme };
})(globalThis);
