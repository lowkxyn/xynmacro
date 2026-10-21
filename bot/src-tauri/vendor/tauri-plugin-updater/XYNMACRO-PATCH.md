# XynMacro updater patch

This is the public `tauri-plugin-updater` **2.10.1** crate with a few safety
changes. Everything else is the upstream package, unchanged.

- Archive: <https://static.crates.io/crates/tauri-plugin-updater/tauri-plugin-updater-2.10.1.crate>
- Archive SHA-256: `806d9dac662c2e4594ff03c647a552f2c9bd544e7d0f683ec58f872f952ce4af`
- Upstream repository: <https://github.com/tauri-apps/plugins-workspace>
- Packaged VCS revision: `d6a3898001a4bcc659e045f9501498751b77dbe6`,
  path `plugins/updater` (see `.cargo_vcs_info.json`).
- The original licence files (`LICENSE_APACHE-2.0`, `LICENSE_MIT`, `LICENSE.spdx`)
  and copyright/SPDX notices are kept as they were.

Upstream 2.13.0 and the `v2` branch already check the `ShellExecuteW` result,
but still read update downloads without a size limit, so this patch stays on
2.10.1 rather than moving to a newer release.

## What changed

- `src/safety.rs` (new, private module declared in `src/lib.rs`) holds the size
  guards and a bounded metadata reader, with tests. No new dependency, public
  API or cryptography.
- Update metadata (`latest.json`) is capped at **1 MiB**. The declared length is
  checked before reading, and every chunk is added with overflow-checked
  arithmetic, so a missing or wrong Content-Length can't get around the cap.
  JSON is parsed with the original decoder after the body is collected, and the
  existing endpoint fallback and release parsing are unchanged.
- Installer downloads are capped at **512 MiB** in `src/updater.rs`, checked the
  same way: declared length first, then each chunk before it is buffered.
  Exactly the limit is allowed.
- On Windows, a `ShellExecuteW` result of 32 or less (including zero) is now
  returned as `Error::Io` with the shell code, instead of the app exiting as if
  the installer had started. Only a value above 32 counts as success, per
  [Microsoft's docs](https://learn.microsoft.com/en-us/windows/win32/api/shellapi/nf-shellapi-shellexecutew#return-value).
  The app's existing install-error handler recovers from it.
- After the normal signature check, the download is also required to match the
  signature's trusted comment, which minisign authenticates. The Tauri signer
  records it as `timestamp:<n>\tfile:<name>`, and the file name must be
  `<productName>_<announced version>_...`. Without this check a tampered feed
  could serve an older signed installer, or the other channel's installer, as a
  newer update. The release workflow checks the same rule before a release goes
  live. Never rename an installer before signing it.
- Signature verification still happens in the original download path before any
  bytes reach the app. Installer extraction, arguments, hooks and exit on
  success are unchanged.
- XynMacro builds the plugin without its `zip` feature (updates are an NSIS
  installer), so `std::io::Cursor` is named where it's used instead of imported.

The app pins `=2.10.1` and uses a `[patch.crates-io]` path override, so a later
resolve can't quietly swap in the unpatched crate. The package manifest,
features and bundled Cargo.lock are unmodified. Before replacing this patch,
check that the new version covers all of the above and rerun the tests below.

## Tests and limits

From `src-tauri`:

```text
cargo test --locked --offline -p tauri-plugin-updater -p xyn_macro --lib
```

These are unit tests with synthetic responses: declared and streamed limits,
overflow, both release formats, malformed JSON, body failures and the shell
result. They don't do a live download or install.

The caps limit the buffered body, not total memory: HTTP chunks, allocator
slack, parsed JSON and signature checking still use some on top. A successful
launch doesn't prove the installer finishes; the existing before-exit hook
still runs first, and the app recovers from install errors by restarting.
