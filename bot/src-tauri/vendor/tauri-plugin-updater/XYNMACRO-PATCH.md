# XynMacro updater patch

Based on the exact public `tauri-plugin-updater` **2.10.1** crate already installed
in the local Cargo cache. All 29 archive files were compared byte-for-byte with
the installed source before copying. The shared registry source was not edited.

- Archive: <https://static.crates.io/crates/tauri-plugin-updater/tauri-plugin-updater-2.10.1.crate>
- Archive SHA-256, verified against the app's original lockfile:
  `806d9dac662c2e4594ff03c647a552f2c9bd544e7d0f683ec58f872f952ce4af`
- Upstream repository: <https://github.com/tauri-apps/plugins-workspace>
- Packaged VCS revision: `d6a3898001a4bcc659e045f9501498751b77dbe6`,
  path `plugins/updater` (retained `.cargo_vcs_info.json`).
- Original `LICENSE_APACHE-2.0`, `LICENSE_MIT`, `LICENSE.spdx`, copyright/SPDX
  notices and the remaining public package files are retained unchanged.

## Upstream check, 2026-10-02

[Tagged 2.13.0 source](https://github.com/tauri-apps/plugins-workspace/blob/updater-v2.13.0/plugins/updater/src/updater.rs)
and the current `v2` branch check `ShellExecuteW` for results at or below 32,
but still append response chunks without a download byte ceiling. No complete
upstream replacement for both requested fixes was found in these sources.
The installed 2.10.1 package is patched to preserve all other dependency selections.

## Deliberate differences

- `src/safety.rs` adds pure guards and a bounded metadata reader, with pure and
  synthetic-response tests. `src/lib.rs` declares that
  private module. No new dependency, public API or cryptography is introduced.
- Successful metadata responses (except the unchanged 204 return) are limited to
  **1 MiB (1048576 bytes)**. The known body length and declared Content-Length are
  checked before reading. Each chunk passes checked cumulative-size addition
  before buffer extension; JSON decoding starts only after bounded collection.
  Missing, zero or underreported lengths cannot bypass the stream ceiling.
  The bounded in-memory body uses the original reqwest JSON decoder, preserving
  its error variant and source URL. Body/JSON/size failures return immediately;
  the existing endpoint fallback and `raw_json`/`RemoteRelease` parsing are retained.
- `src/updater.rs` rejects a declared length above **512 MiB (536870912 bytes)**
  before reading the body, and checks accumulated bytes plus each chunk with
  checked addition before progress notification or buffer extension. Missing or
  underreported lengths cannot bypass the ceiling. Exactly the limit is allowed.
- Windows checks the signed pointer-sized `ShellExecuteW` result immediately
  after the call and returns an existing `Error::Io` for every value <= 32,
  including zero. The error preserves the returned shell code. Exit occurs only
  for a value > 32, as specified by
  [Microsoft](https://learn.microsoft.com/en-us/windows/win32/api/shellapi/nf-shellapi-shellexecutew#return-value).
  This follows the upstream success predicate, with deterministic error reporting
  that is testable without launching an installer.
- Added 2026-10-17: after the unchanged signature check, the download path requires
  the signature's trusted comment (which minisign authenticates) to name the signed
  file `<productName>_<announced version>_...`, as the Tauri signer records it
  (`timestamp:<n>\tfile:<name>`). Without this, the signature authenticated the
  installer bytes but not the feed's version, so a tampered feed could replay an
  older signed installer, or the other stable/beta profile's installer, as a newer
  update. The release workflow checks the same rule before a release goes live,
  so a mismatch is caught in CI rather than leaving installs unable to update.
  Never rename an installer before signing it.
- Signature verification remains in the original download path before bytes are
  returned to the app. Installer extraction, arguments, hooks and successful exit
  are unchanged. The app's existing install-error handler can now recover from a
  refused launch. Its source does not need changing.

The app uses an exact `=2.10.1` requirement and a `[patch.crates-io]` path override
so a later resolver run cannot silently replace the patched dependency. The
package manifest, features and its bundled upstream Cargo.lock are unmodified.
When replacing this patch, review all guards and rerun the tests below before
removing the pin and override.

## Checks and limits

From `src-tauri`:

```text
cargo test --locked --offline -p tauri-plugin-updater -p xyn_macro --lib
```

These are pure/synthetic-response and shell unit tests, not live download or
installer acceptance. Metadata tests exercise the actual collector and parser,
including declared limits before body polling, exact/cumulative stream limits,
integer overflow, both release formats, malformed JSON and body failures.
The byte ceilings limit buffered body length, not total process memory: HTTP
chunks, allocator capacity, parsed/cloned JSON and signature-verification overhead
still exist. Post-signature archive expansion is unchanged.
An OS launch success cannot prove that the installer subsequently completes.
The existing before-exit hook still runs before attempting the launch; this app
already cleans up before install and recovers errors by restarting.

- XynMacro builds the plugin without its `zip` feature (updates are an NSIS installer), so
  `std::io::Cursor` is named at its use sites instead of imported.
