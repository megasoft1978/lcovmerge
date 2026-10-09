param(
    [string]$Binary = (Join-Path $PSScriptRoot '..\bin\lcovmerge.exe'),
    [switch]$NoLcov
)

$ErrorActionPreference = 'Stop'
$runner = Join-Path $PSScriptRoot 'run_tests.py'
$arguments = @($runner, '--binary', $Binary)
if ($NoLcov) { $arguments += '--no-lcov' }
& python @arguments
exit $LASTEXITCODE
