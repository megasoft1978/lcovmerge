param(
    [string]$Binary = (Join-Path $PSScriptRoot '..\bin\lcovmerge.exe'),
    [switch]$NoLcov
)

$ErrorActionPreference = 'Stop'
$runner = Join-Path $PSScriptRoot 'run_tests.py'
$python3 = Get-Command python3 -ErrorAction SilentlyContinue
$python = if ($python3) { $python3 } else { Get-Command python -ErrorAction Stop }
$arguments = @($runner, '--binary', $Binary)
if ($NoLcov) { $arguments += '--no-lcov' }
& $python.Source @arguments
exit $LASTEXITCODE
