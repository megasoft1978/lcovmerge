[CmdletBinding()]
param(
    [string]$Binary = (Join-Path $PSScriptRoot '..\bin\lcovmerge.exe')
)

$ErrorActionPreference = 'Stop'
$binaryPath = (Resolve-Path -LiteralPath $Binary).Path
$baseDirectory = if ($env:RUNNER_TEMP) { $env:RUNNER_TEMP } else { [System.IO.Path]::GetTempPath() }
$workDirectory = Join-Path $baseDirectory ('lcovmerge-windows-smoke-' + [guid]::NewGuid().ToString('N'))

function Get-ExtendedPath([string]$Path) {
    $absolute = [System.IO.Path]::GetFullPath($Path)
    if ($absolute.StartsWith('\\?\', [System.StringComparison]::Ordinal)) { return $absolute }
    if ($absolute.Length -lt 248) { return $absolute }
    if ($absolute.StartsWith('\\', [System.StringComparison]::Ordinal)) {
        return '\\?\UNC\' + $absolute.Substring(2)
    }
    return '\\?\' + $absolute
}

function Invoke-Lcovmerge([string]$Executable, [string[]]$Arguments) {
    $startInfo = [System.Diagnostics.ProcessStartInfo]::new()
    $startInfo.FileName = $Executable
    $startInfo.UseShellExecute = $false
    $startInfo.RedirectStandardOutput = $true
    $startInfo.RedirectStandardError = $true
    foreach ($argument in $Arguments) { [void]$startInfo.ArgumentList.Add($argument) }

    $process = [System.Diagnostics.Process]::new()
    $process.StartInfo = $startInfo
    $output = [System.IO.MemoryStream]::new()
    try {
        if (-not $process.Start()) { throw 'Could not start lcovmerge.exe.' }
        $copyOutput = $process.StandardOutput.BaseStream.CopyToAsync($output)
        $readError = $process.StandardError.ReadToEndAsync()
        $process.WaitForExit()
        $copyOutput.GetAwaiter().GetResult()
        [pscustomobject]@{
            ExitCode = $process.ExitCode
            Stdout = $output.ToArray()
            Stderr = $readError.GetAwaiter().GetResult()
        }
    } finally {
        $output.Dispose()
        $process.Dispose()
    }
}

try {
    [void][System.IO.Directory]::CreateDirectory((Get-ExtendedPath $workDirectory))
    $longDirectory = Join-Path $workDirectory 'unicode-café-🙂'
    [void][System.IO.Directory]::CreateDirectory((Get-ExtendedPath $longDirectory))
    $segment = 0
    while ($longDirectory.Length -lt 285) {
        $name = 'segment-{0:D2}-{1}' -f $segment, ('x' * 48)
        $longDirectory = [System.IO.Path]::Combine($longDirectory, $name)
        [void][System.IO.Directory]::CreateDirectory((Get-ExtendedPath $longDirectory))
        $segment++
    }

    $runDirectory = [System.IO.Path]::Combine($longDirectory, 'runs')
    $inputPath = [System.IO.Path]::Combine($longDirectory, 'input.info')
    $outputPath = [System.IO.Path]::Combine($longDirectory, 'merged.info')
    [void][System.IO.Directory]::CreateDirectory((Get-ExtendedPath $runDirectory))
    $utf8 = [System.Text.UTF8Encoding]::new($false)
    [System.IO.File]::WriteAllText(
        (Get-ExtendedPath $inputPath),
        "SF:/windows-smoke.c`nDA:2,4`nDA:1,5`nend_of_record`n",
        $utf8
    )

    $fileResult = Invoke-Lcovmerge $binaryPath @(
        '--jobs', '1', '--tmpdir', $runDirectory, '-v', $inputPath, '-o', $outputPath
    )
    if ($fileResult.ExitCode -ne 0) {
        throw "Long-path merge failed ($($fileResult.ExitCode)): $($fileResult.Stderr)"
    }
    if ($fileResult.Stderr -notmatch 'using 1 job\(s\)') {
        throw "Out-of-order input did not exercise the external-sort path: $($fileResult.Stderr)"
    }
    $mergedBytes = [System.IO.File]::ReadAllBytes((Get-ExtendedPath $outputPath))
    $mergedText = $utf8.GetString($mergedBytes)
    if ($mergedText.Contains("`r") -or
        -not $mergedText.Contains("DA:1,5`nDA:2,4`nend_of_record`n")) {
        throw 'Long-path output was malformed or changed LF bytes.'
    }
    if ([System.IO.Directory]::GetFileSystemEntries((Get-ExtendedPath $runDirectory)).Length -ne 0) {
        throw 'The long temporary-run directory was not cleaned.'
    }

    $stdoutResult = Invoke-Lcovmerge $binaryPath @(
        '--jobs', '1', '--tmpdir', $runDirectory, $inputPath, '-o', '-'
    )
    if ($stdoutResult.ExitCode -ne 0 -or
        [System.Convert]::ToBase64String($stdoutResult.Stdout) -ne
            [System.Convert]::ToBase64String($mergedBytes) -or
        $stdoutResult.Stderr.Length -ne 0) {
        throw "Redirected stdout differed from the file output ($($stdoutResult.ExitCode)): $($stdoutResult.Stderr)"
    }
    if ([System.IO.Directory]::GetFileSystemEntries((Get-ExtendedPath $runDirectory)).Length -ne 0) {
        throw 'The stdout temporary-run directory was not cleaned.'
    }
    Write-Output 'windows_long_path_unicode_stdout_cleanup=PASS'
} finally {
    $cleanupPath = Get-ExtendedPath $workDirectory
    if ([System.IO.Directory]::Exists($cleanupPath)) {
        [System.IO.Directory]::Delete($cleanupPath, $true)
    }
}
