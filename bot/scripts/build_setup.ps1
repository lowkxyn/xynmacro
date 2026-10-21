# Wrap the NSIS installer from `tauri build` in the branded XynMacro Setup window.
# Run after the main build; writes XynMacro-Setup-<version>.exe next to the NSIS installer.
#
#   cd bot
#   powershell -ExecutionPolicy Bypass -File scripts/build_setup.ps1
#
# The NSIS installer stays the one the auto-updater uses; this exe is only the download people run.
param([string]$Installer = "")
$ErrorActionPreference = "Stop"
$botDir = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$tauriDir = Join-Path $botDir "src-tauri"
$setupDir = Join-Path (Split-Path $botDir -Parent) "setup"
$nsisDir = Join-Path $tauriDir "target\release\bundle\nsis"

$version = (Get-Content (Join-Path $tauriDir "tauri.conf.json") -Raw | ConvertFrom-Json).version
if (-not $Installer) {
    $Installer = Join-Path $nsisDir "XynMacro_${version}_x64-setup.exe"
}
if (-not (Test-Path $Installer)) { throw "NSIS installer not found: $Installer" }

# Files in the order the NSIS template writes them (main exe, resources, sidecar), so the
# progress bar can name the file being copied.
$files = @()
$appExe = Join-Path $tauriDir "target\release\XynMacroApp.exe"
$files += "XynMacroApp.exe|$((Get-Item $appExe).Length)"
Get-ChildItem (Join-Path $tauriDir "resources") -Recurse -File | Sort-Object FullName | ForEach-Object {
    $relative = $_.FullName.Substring($tauriDir.Length + 1)
    $files += "$relative|$($_.Length)"
}
$sidecar = Get-ChildItem (Join-Path $tauriDir "binaries") -Filter "XynMacro-core*.exe" | Select-Object -First 1
$files += "XynMacro-core.exe|$($sidecar.Length)"

$env:XYN_PAYLOAD = (Resolve-Path $Installer).Path
$env:XYN_INSTALL_FILES = $files -join ";"
Push-Location $setupDir
try {
    cargo build --release --locked
    if ($LASTEXITCODE -ne 0) { throw "setup build failed with exit code $LASTEXITCODE" }
} finally {
    Pop-Location
    Remove-Item Env:XYN_PAYLOAD, Env:XYN_INSTALL_FILES -ErrorAction SilentlyContinue
}

$output = Join-Path $nsisDir "XynMacro-Setup-$version.exe"
Copy-Item (Join-Path $setupDir "target\release\XynMacroSetup.exe") $output -Force
Write-Output "Setup -> $output"
