param(
    [string]$ExePath = (Join-Path $env:LOCALAPPDATA 'XynMacro\XynMacro.exe'),
    [string]$DataDir = (Join-Path $env:APPDATA 'com.htcgc.xyn'),
    [int]$MaxCleanupMs = 1000
)

$ErrorActionPreference = 'Stop'

if (-not (Test-Path -LiteralPath $ExePath)) {
    throw "XynMacro executable not found: $ExePath"
}
if (Get-Process -Name 'XynMacro', 'XynMacro-core' -ErrorAction SilentlyContinue) {
    throw 'Close the existing XynMacro and XynMacro-core processes before this isolated smoke check.'
}

$app = Start-Process -FilePath $ExePath -PassThru
$portFile = Join-Path $DataDir "port_$($app.Id).json"
$sidecarPid = $null

try {
    $startupDeadline = [DateTime]::UtcNow.AddSeconds(20)
    while (-not (Test-Path -LiteralPath $portFile)) {
        if ([DateTime]::UtcNow -ge $startupDeadline) {
            throw "Timed out waiting for $portFile"
        }
        Start-Sleep -Milliseconds 50
    }

    $portInfo = Get-Content -Raw -LiteralPath $portFile | ConvertFrom-Json
    $sidecarPid = [int]$portInfo.pid
    if (-not (Get-Process -Id $sidecarPid -ErrorAction SilentlyContinue)) {
        throw "Sidecar PID $sidecarPid from the port file is not running"
    }
    $health = Invoke-WebRequest -Uri "http://127.0.0.1:$($portInfo.port)/health" -SkipHttpErrorCheck
    if ($health.StatusCode -ne 401) {
        throw "Unauthenticated backend health request returned HTTP $($health.StatusCode), expected 401"
    }

    $app.Refresh()
    $timer = [Diagnostics.Stopwatch]::StartNew()
    if (-not $app.CloseMainWindow()) {
        $timer.Stop()
        throw 'The app did not accept WM_CLOSE'
    }

    do {
        $shellAlive = [bool](Get-Process -Id $app.Id -ErrorAction SilentlyContinue)
        $sidecarAlive = [bool](Get-Process -Id $sidecarPid -ErrorAction SilentlyContinue)
        $portExists = Test-Path -LiteralPath $portFile
        if (-not $shellAlive -and -not $sidecarAlive -and -not $portExists) {
            break
        }
        Start-Sleep -Milliseconds 10
    } while ($timer.ElapsedMilliseconds -le $MaxCleanupMs)
    $timer.Stop()

    if ($shellAlive -or $sidecarAlive -or $portExists) {
        throw "Close cleanup exceeded ${MaxCleanupMs}ms (shell=$shellAlive sidecar=$sidecarAlive port=$portExists)"
    }
    if (Get-Process -Name 'XynMacro-core' -ErrorAction SilentlyContinue) {
        throw 'An untracked XynMacro-core process remains after close'
    }

    [pscustomobject]@{
        Passed = $true
        CleanupMs = $timer.ElapsedMilliseconds
        ShellPid = $app.Id
        SidecarPid = $sidecarPid
        BackendAuthEnforced = $true
        PortFileRemoved = $true
    }
}
finally {
    Get-Process -Id $app.Id -ErrorAction SilentlyContinue | Stop-Process -Force
    if ($sidecarPid) {
        Get-Process -Id $sidecarPid -ErrorAction SilentlyContinue | Stop-Process -Force
    }
    Remove-Item -LiteralPath $portFile -Force -ErrorAction SilentlyContinue
}
