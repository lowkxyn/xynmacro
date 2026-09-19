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
$psi.CreateNoWindow = $true

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

    $headers = @{ 'X-XynMacro-Token' = $token }
    $state = Invoke-RestMethod -Uri "http://127.0.0.1:$port/state" -Headers $headers
    if ($state.shutdown.pending -or $state.notifications.enabled -or $state.notifications.configured) {
        throw 'A fresh sidecar must not schedule shutdown or send notifications.'
    }
    # Synthetic URL, disabled configuration: verify the frozen DPAPI dependency
    # without sending any message or touching an actual webhook.
    $syntheticUrl = 'https://discord.com/api/webhooks/123456789012345678/' + ('x' * 60)
    $saveBody = @{ action = 'notifications_save'; value = @{ url = $syntheticUrl; enabled = $false; progress_minutes = 0 } } | ConvertTo-Json -Depth 4
    $saved = Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:$port/command" -Headers $headers -ContentType 'application/json' -Body $saveBody
    if (-not $saved.ok -or -not $saved.notifications.configured -or $saved.notifications.enabled) {
        throw 'Frozen sidecar could not save disabled encrypted notification settings.'
    }
    $stateText = Invoke-WebRequest -Uri "http://127.0.0.1:$port/state" -Headers $headers
    if ($stateText.Content.Contains($syntheticUrl)) { throw 'Webhook URL leaked in state.' }
    $clearBody = @{ action = 'notifications_clear' } | ConvertTo-Json
    $cleared = Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:$port/command" -Headers $headers -ContentType 'application/json' -Body $clearBody
    if (-not $cleared.ok -or (Test-Path -LiteralPath (Join-Path $dataDir 'json/notifications.dpapi'))) {
        throw 'Frozen sidecar did not remove notification storage.'
    }

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
    $resolvedSmokeDir = [IO.Path]::GetFullPath($dataDir)
    $smokePrefix = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\') + '\sidecar-smoke-'
    if (-not $resolvedSmokeDir.StartsWith($smokePrefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw 'Refusing cleanup outside the temporary smoke-test directory.'
    }
    Remove-Item -LiteralPath $resolvedSmokeDir -Recurse -Force -ErrorAction SilentlyContinue
}
