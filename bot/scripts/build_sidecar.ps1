# Freeze the Python sidecar into one self-contained .exe (Python + all libs + the
# template/calibration assets baked in) and drop it where Tauri's externalBin expects.
# The installed app then needs nothing preinstalled. Re-run after changing xmacro_core.py.
#
#   cd bot
#   powershell -ExecutionPolicy Bypass -File scripts/build_sidecar.ps1
#
# CI uses the runner's interpreter:  ... build_sidecar.ps1 -Python python
#
param([string]$Python = "py")
$ErrorActionPreference = "Stop"
$pyDir = (Resolve-Path (Join-Path $PSScriptRoot "..\python")).Path
$triple = "x86_64-pc-windows-msvc"

Push-Location $pyDir
try {
    & $Python -m PyInstaller --noconfirm --clean --onefile --noconsole --name XynMacro-core `
        --add-data "tpl_w.png;." --add-data "tpl_a.png;." `
        --add-data "tpl_s.png;." --add-data "tpl_d.png;." `
        --add-data "tpl_training_mode.png;." `
        --add-data "tpl_game_menu.png;." --add-data "tpl_inventory_menu.png;." `
        --add-data "tpl_senzu_bean.png;." --add-data "tpl_slot_senzu.png;." `
        --add-data "tpl_inventory_digits.png;." `
        --add-data "gravity;gravity" `
        --add-data "defaults;defaults" `
        --hidden-import win32gui --hidden-import win32process `
        xmacro_core.py
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller failed with exit code $LASTEXITCODE"
    }
} finally {
    Pop-Location
}

$binDir = Join-Path $PSScriptRoot "..\src-tauri\binaries"
New-Item -ItemType Directory -Force -Path $binDir | Out-Null
$src = Join-Path $pyDir "dist\XynMacro-core.exe"
$dst = Join-Path $binDir "XynMacro-core-$triple.exe"
Copy-Item $src $dst -Force
Write-Output "Sidecar exe -> $dst"
