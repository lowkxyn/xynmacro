(function (root) {
  function reminderDecision(latestVersion, ignoredVersion, automatic) {
    const ignoredExactVersion = !!ignoredVersion && ignoredVersion === latestVersion;
    return {
      skip: !!automatic && ignoredExactVersion,
      clearIgnored: !!ignoredVersion && (!ignoredExactVersion || !automatic),
    };
  }

  root.XynMacroUpdateState = { reminderDecision };
})(globalThis);
