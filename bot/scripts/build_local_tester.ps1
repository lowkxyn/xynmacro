# Build an unsigned tester installer on this PC (no updater artifacts, no auto-updates).
# Release installers come from the GitHub workflow instead; never publish this one.
#
#   cd bot
#   powershell -ExecutionPolicy Bypass -File scripts/build_local_tester.ps1
#
# Rust embeds the absolute source path of every crate it compiles (panic locations),
# and a local build compiles them from <home>\.cargo, so the installer would carry
# this PC's user folder name. Remapping those prefixes keeps it out of the binary.
param([string]$Python = "py")
$ErrorActionPreference = "Stop"
$botDir = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$cargoHome = if ($env:CARGO_HOME) { $env:CARGO_HOME } else { Join-Path $env:USERPROFILE ".cargo" }
$rustupHome = if ($env:RUSTUP_HOME) { $env:RUSTUP_HOME } else { Join-Path $env:USERPROFILE ".rustup" }
$repoDir = (Resolve-Path (Join-Path $botDir "..")).Path

$remaps = @(
    "--remap-path-prefix=$cargoHome=cargo",
    "--remap-path-prefix=$rustupHome=rustup",
    "--remap-path-prefix=$repoDir=xynmacro"
)
$previousRustFlags = $env:RUSTFLAGS
$env:RUSTFLAGS = (@($previousRustFlags) + $remaps | Where-Object { $_ }) -join " "
try {
    & (Join-Path $PSScriptRoot "build_sidecar.ps1") -Python $Python
    Push-Location $botDir
    try {
        npx tauri build --bundles nsis --config src-tauri/tauri.local.conf.json
        if ($LASTEXITCODE -ne 0) { throw "tauri build failed with exit code $LASTEXITCODE" }
        # Branded setup window around the NSIS installer (same path remaps apply).
        & (Join-Path $PSScriptRoot "build_setup.ps1")
    } finally {
        Pop-Location
    }
} finally {
    $env:RUSTFLAGS = $previousRustFlags
}

$profileName = Split-Path $env:USERPROFILE -Leaf
$installer = Get-ChildItem (Join-Path $botDir "src-tauri\target\release\bundle\nsis") -Filter *-setup.exe |
    Sort-Object LastWriteTime -Descending | Select-Object -First 1
$appExe = Join-Path $botDir "src-tauri\target\release\XynMacroApp.exe"
$leaks = Select-String -Path $appExe -Pattern ('[\\/]Users[\\/]' + [regex]::Escape($profileName) + '[\\/]') -Encoding ascii -List
if ($leaks) { throw "XynMacroApp.exe still contains this PC's user folder; do not share $($installer.Name)." }
$setupExe = Get-ChildItem (Join-Path $botDir "src-tauri\target\release\bundle\nsis") -Filter "XynMacro-Setup-*.exe" |
    Sort-Object LastWriteTime -Descending | Select-Object -First 1
$setupLeaks = Select-String -Path $setupExe.FullName -Pattern ('[\\/]Users[\\/]' + [regex]::Escape($profileName) + '[\\/]') -Encoding ascii -List
if ($setupLeaks) { throw "$($setupExe.Name) still contains this PC's user folder; do not share it." }
Write-Output "Tester installer -> $($installer.FullName)"
Write-Output "Branded setup -> $($setupExe.FullName)"
