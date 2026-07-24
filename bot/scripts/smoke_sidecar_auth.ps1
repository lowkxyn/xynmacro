# Verify the sidecar takes its auth token over stdin and then enforces it.
#
# The packaged sidecar is a --noconsole build, so sys.stdin exists only because the
# launcher hands it a real pipe. Nothing in the unit tests can catch a regression
# there, and the symptom would be an app whose backend never starts. Release CI runs
# this against the frozen exe; -Python runs it against the source for local checks.
#
#   ./scripts/smoke_sidecar_auth.ps1 -Exe src-tauri/binaries/XynMacro-core-x86_64-pc-windows-msvc.exe
#   ./scripts/smoke_sidecar_auth.ps1 -Python py -Script python/xynmacro_core.py
[CmdletBinding()]
param(
    [string]$Exe,
    [string]$Python,
    [string]$Script
)
$ErrorActionPreference = "Stop"

if (-not $Exe -and -not ($Python -and $Script)) {
    throw "Pass -Exe <path>, or -Python <exe> -Script <xynmacro_core.py>."
}

$dataDir = Join-Path ([IO.Path]::GetTempPath()) ("sidecar-smoke-" + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Force -Path $dataDir | Out-Null
$token = "smoke-" + [guid]::NewGuid().ToString('N')
# Must be a live PID: the sidecar's parent watchdog exits cleanly as soon as its
# launcher is gone, which would otherwise look exactly like a token failure.
$launcherPid = $PID

$psi = [Diagnostics.ProcessStartInfo]::new()
if ($Exe) {
    $psi.FileName = (Resolve-Path $Exe).Path
} else {
    $psi.FileName = $Python
    $psi.ArgumentList.Add((Resolve-Path $Script).Path)
}
foreach ($a in @('--sidecar', '--pid', "$launcherPid", '--data-dir', $dataDir,
                 '--app-version', '0.0.0-smoke', '--auth-token-stdin')) {
    $psi.ArgumentList.Add($a)
}
$psi.RedirectStandardInput = $true
$psi.UseShellExecute = $false

$proc = [Diagnostics.Process]::Start($psi)
$proc.StandardInput.WriteLine($token)
$proc.StandardInput.Close()

try {
    $portFile = Join-Path $dataDir "port_$launcherPid.json"
    $port = $null
    foreach ($i in 1..60) {
        if (Test-Path $portFile) {
            $port = (Get-Content $portFile -Raw | ConvertFrom-Json).port
            if ($port) { break }
        }
        if ($proc.HasExited) {
            throw "Sidecar exited early (code $($proc.ExitCode)); exit code 2 means it rejected the token it was handed on stdin."
        }
        Start-Sleep -Milliseconds 500
    }
    if (-not $port) { throw "Sidecar never published a port file; the stdin token handover likely failed." }

    $ok = Invoke-RestMethod -Uri "http://127.0.0.1:$port/health" -Headers @{ 'X-XynMacro-Token' = $token }
    if (-not $ok.ok) { throw "Authenticated /health did not report ok." }

    # The token must actually be enforced, not merely accepted.
    try {
        Invoke-RestMethod -Uri "http://127.0.0.1:$port/health" -ErrorAction Stop | Out-Null
        throw "Unauthenticated /health succeeded — the sidecar is not enforcing its token."
    } catch [Microsoft.PowerShell.Commands.HttpResponseException] {
        $status = $_.Exception.Response.StatusCode.value__
        if ($status -ne 401) { throw "Expected 401 without a token, got $status." }
    }

    Write-Output "Sidecar stdin auth verified on port $port."
} finally {
    if (-not $proc.HasExited) { $proc.Kill(); $proc.WaitForExit(5000) | Out-Null }
    Remove-Item -Recurse -Force $dataDir -ErrorAction SilentlyContinue
}
