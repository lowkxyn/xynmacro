#requires -Version 7.0
param(
    [string]$ExePath = (Join-Path $env:LOCALAPPDATA 'XynMacro\XynMacroApp.exe'),
    [string]$DataDir = (Join-Path $env:APPDATA 'com.htcgc.xyn'),
    [int]$MaxCleanupMs = 1000
)

$ErrorActionPreference = 'Stop'

function Test-OwnedSidecar {
    param([hashtable]$ProcessRecords, [int]$SidecarId, [int]$AppOwnerPid,
          [datetime]$ShellStartedAt, [string]$ExpectedPath)
    $candidate = $ProcessRecords[$SidecarId]
    if (-not $candidate -or -not $candidate.ExecutablePath -or
        -not [string]::Equals([IO.Path]::GetFullPath($candidate.ExecutablePath),
                             [IO.Path]::GetFullPath($ExpectedPath), [StringComparison]::OrdinalIgnoreCase)) {
        return $false
    }
    $seen = [Collections.Generic.HashSet[int]]::new()
    $cursor = $SidecarId
    while ($cursor -ne $AppOwnerPid) {
        if ($cursor -le 0 -or -not $seen.Add($cursor) -or $seen.Count -gt 8) { return $false }
        $record = $ProcessRecords[$cursor]
        if (-not $record -or -not $record.CreationDate -or
            $record.CreationDate.ToUniversalTime() -lt $ShellStartedAt.ToUniversalTime()) { return $false }
        $cursor = [int]$record.ParentProcessId
    }
    return $true
}

if (-not (Test-Path -LiteralPath $ExePath)) {
    throw "XynMacro executable not found: $ExePath"
}
$ExePath = (Resolve-Path -LiteralPath $ExePath).Path
$appNames = @('XynMacro', 'XynMacroApp', 'XynMacroBeta', 'XynMacro-core',
              [IO.Path]::GetFileNameWithoutExtension($ExePath)) | Select-Object -Unique
if (Get-Process -Name $appNames -ErrorAction SilentlyContinue) {
    throw 'Close the existing XynMacro and XynMacro-core processes before this isolated smoke check.'
}

$app = Start-Process -FilePath $ExePath -PassThru -WindowStyle Hidden
$portFile = Join-Path $DataDir "port_$($app.Id).json"
$sidecarPid = $null
$sidecar = $null

try {
    $startupDeadline = [DateTime]::UtcNow.AddSeconds(20)
    while (-not (Test-Path -LiteralPath $portFile)) {
        if ($app.HasExited) { throw 'The test app exited before publishing a backend.' }
        if ([DateTime]::UtcNow -ge $startupDeadline) {
            throw "Timed out waiting for $portFile"
        }
        Start-Sleep -Milliseconds 50
    }

    $portInfo = Get-Content -Raw -LiteralPath $portFile | ConvertFrom-Json
    $sidecarPid = [int]$portInfo.pid
    # A stale port file can name an unrelated, reused PID. Prove ancestry and
    # executable identity before retaining any handle used by the final cleanup.
    $records = @{}
    $cursor = $sidecarPid
    while ($cursor -gt 0 -and $cursor -ne $app.Id -and -not $records.ContainsKey($cursor) -and $records.Count -lt 8) {
        $record = Get-CimInstance Win32_Process -Filter "ProcessId = $cursor"
        if (-not $record) { break }
        $records[$cursor] = $record
        $cursor = [int]$record.ParentProcessId
    }
    $expectedSidecar = Join-Path (Split-Path -Parent $ExePath) 'XynMacro-core.exe'
    if (-not (Test-OwnedSidecar $records $sidecarPid $app.Id $app.StartTime $expectedSidecar)) {
        throw 'Port file does not identify a newly started sidecar owned by this test app.'
    }
    $candidate = Get-Process -Id $sidecarPid -ErrorAction Stop
    $null = $candidate.Handle
    if (-not [string]::Equals($candidate.Path, $expectedSidecar, [StringComparison]::OrdinalIgnoreCase) -or
        [math]::Abs(($candidate.StartTime.ToUniversalTime() - $records[$sidecarPid].CreationDate.ToUniversalTime()).TotalMilliseconds) -gt 1) {
        $candidate.Dispose()
        throw 'Sidecar identity changed while acquiring its process handle.'
    }
    $sidecar = $candidate
    $port = [uint16]$portInfo.port
    if ($port -eq 0) { throw 'Invalid sidecar port.' }
    $health = Invoke-WebRequest -Uri "http://127.0.0.1:$port/health" -SkipHttpErrorCheck -TimeoutSec 5 -NoProxy -MaximumRedirection 0
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
        $shellAlive = -not $app.HasExited
        $sidecarAlive = -not $sidecar.HasExited
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
    # Use retained, verified process handles, never look a stale numeric PID up
    # again for termination. Tree cleanup also covers the PyInstaller bootloader.
    foreach ($owned in @($sidecar, $app)) {
        if ($owned) {
            if (-not $owned.HasExited) { $owned.Kill($true); $owned.WaitForExit(5000) | Out-Null }
            $owned.Dispose()
        }
    }
    if ($sidecar) { Remove-Item -LiteralPath $portFile -Force -ErrorAction SilentlyContinue }
}
