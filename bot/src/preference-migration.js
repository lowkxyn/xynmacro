(function (root) {
  function migratePreferences(storage, legacyPrefix, currentPrefix, suffixes) {
    for (const suffix of suffixes) {
      const legacyKey = legacyPrefix + suffix;
      const currentKey = currentPrefix + suffix;
      const legacyValue = storage.getItem(legacyKey);
      if (legacyValue === null) continue;

      if (storage.getItem(currentKey) !== null) {
        try { storage.removeItem(legacyKey); } catch (_) {}
        continue;
      }

      try {
        storage.setItem(currentKey, legacyValue);
        storage.removeItem(legacyKey);
      } catch (_) {
        // Preserve the legacy value and continue startup. A later launch can retry.
      }
    }
  }

  root.XynMacroPreferenceMigration = { migratePreferences };
})(globalThis);
