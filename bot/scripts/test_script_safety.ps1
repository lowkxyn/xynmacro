# This loads only a pure predicate from the parsed script. No app/process/network
# operations from either smoke script are executed.
$ErrorActionPreference = 'Stop'
$tokens = $null
$parseErrors = $null
$source = Join-Path $PSScriptRoot 'check_close_cleanup.ps1'
$ast = [Management.Automation.Language.Parser]::ParseFile($source, [ref]$tokens, [ref]$parseErrors)
if ($parseErrors.Count) { throw ($parseErrors -join "`n") }
$predicate = $ast.Find({ param($node)
    $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq 'Test-OwnedSidecar'
}, $false)
if (-not $predicate) { throw 'Ownership predicate is missing.' }
. ([scriptblock]::Create($predicate.Extent.Text))

$start = [datetime]'2026-10-02T12:00:00Z'
$expected = 'C:\Synthetic\XynMacro-core.exe'
function New-Record([int]$parent, [string]$path = $expected, [datetime]$created = $start.AddSeconds(1)) {
    [pscustomobject]@{ ParentProcessId = $parent; ExecutablePath = $path; CreationDate = $created }
}
function Assert-Ownership([hashtable]$records, [bool]$want, [string]$label) {
    if ((Test-OwnedSidecar $records 102 100 $start $expected) -ne $want) { throw $label }
}
Assert-Ownership @{102 = (New-Record 100)} $true 'Direct child was rejected.'
Assert-Ownership @{102 = (New-Record 101); 101 = (New-Record 100)} $true 'PyInstaller child was rejected.'
Assert-Ownership @{102 = (New-Record 999)} $false 'Unrelated process was accepted.'
Assert-Ownership @{102 = (New-Record 100 'C:\Synthetic\unrelated.exe')} $false 'Wrong executable was accepted.'
Assert-Ownership @{102 = (New-Record 100 $expected $start.AddSeconds(-1))} $false 'Reused parent PID was accepted.'
Assert-Ownership @{102 = (New-Record 101); 101 = (New-Record 102)} $false 'Cyclic ancestry was accepted.'
Assert-Ownership @{} $false 'Missing process was accepted.'
Assert-Ownership @{102 = (New-Record 101); 101 = (New-Record 100 $expected $start.AddSeconds(-1))} $false 'Stale ancestor was accepted.'

foreach ($file in Get-ChildItem -LiteralPath $PSScriptRoot -Filter '*.ps1') {
    $tokens = $null
    $parseErrors = $null
    $null = [Management.Automation.Language.Parser]::ParseFile($file.FullName, [ref]$tokens, [ref]$parseErrors)
    if ($parseErrors.Count) { throw "$($file.Name): $($parseErrors -join '; ')" }
}
Write-Output '8 synthetic ownership cases passed; all packaging/smoke PowerShell scripts parsed. No apps launched.'
