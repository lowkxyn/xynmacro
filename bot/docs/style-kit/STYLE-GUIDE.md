# Xyn's interface style

Copy the prompt below into another project. Use `xyn-style.css` for the portable layout and tokens, and `index.html` as a small working reference. These files contain no Tauri, Python, game automation or account integrations. The CSS is scoped beneath `.xyn-app`.

## Copy-and-paste design brief

Keep this project visually consistent with my XynMacro app. Build a compact desktop-tool interface: a dark rounded shell, slim title bar, left navigation, a scrollable content area, and a quiet status footer. Use Inter for normal text and DM Mono sparingly for timings, keyboard shortcuts and numeric readouts. Use system-font fallbacks when those fonts aren't bundled.

Use layered charcoal surfaces, fine low-contrast borders, clear near-white text and one restrained accent colour. My neutral palette starts at #0d0d10 for the background, #15151a for raised surfaces, #191920 for inputs, #e2e3e7 for primary text and #9a9da4 for secondary text. Use #858893 for readable tertiary text. The optional Aero treatment adds a narrow floating icon dock, indigo accents and restrained translucent panels. Keep a solid-surface fallback for reduced transparency and slower devices.

Arrange controls by what I want to do. Put the main workflow first, commonly changed options beside their effects, and diagnostics and advanced tuning out of the main path. Prefer setting rows inside grouped panels to a separate card for every switch. Use short section titles, sentence-case labels and one useful explanation beneath a setting. Explain what happens now versus next run. Keep destructive actions visually separate.

Use 4/8 px spacing increments, about 10 px control/panel radii, 12–14 px interface text, and 24–28 px page titles. Keep row labels left-aligned and their controls right-aligned. Labels may wrap; buttons and keyboard targets must keep their size. Lists should keep names readable next to fixed-width actions, and drag reordering must also support the keyboard. At narrow sizes, reduce or stack navigation and controls without losing information or introducing horizontal scrolling.

The compact HUD is a horizontal pill with a status dot, short activity label, elapsed time, and essential controls. Long activity names truncate with a tooltip; Stop and Expand stay visible. Don't cram the full configuration UI into the HUD.

Use motion to explain an interaction, such as changing a view or reordering a row. Keep hover motion subtle, respect reduced-motion preferences, and allow decorative backgrounds to be disabled. Avoid continuous effects in performance-sensitive projects. Preserve visible focus rings, readable contrast, accessible control names and clear disabled states. Always include useful empty, loading, saved and error states.

Keep this visual identity, but adapt the content and navigation to this project's actual job. Don't copy game labels, Windows-only actions, updater code, credentials, or the macro's technical internals into other projects.

## Using the starter

1. Copy `xyn-style.css` and the structure from `index.html` into the target project.
2. Wrap the UI in `.xyn-app`. The optional `.xyn-aero` class selects the indigo variant.
3. Replace the placeholder navigation and controls with real project content and actions.
4. Bundle licensed Inter/DM Mono font assets if exact typography is needed. The starter uses fallbacks without downloading anything.
5. Check keyboard focus, labels, smaller widths, contrast and reduced motion in the target product. The static starter does not implement application behaviour.
