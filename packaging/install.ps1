[CmdletBinding()]
param(
    [string]$InstallDir
)

$ErrorActionPreference = 'Stop'

$version = if ($env:LCOVMERGE_VERSION) { $env:LCOVMERGE_VERSION.TrimStart('v') } else { '1.0.1' }
if ($version -notmatch '^[A-Za-z0-9.+-]+$') {
    throw "Invalid LCOVMERGE_VERSION: $version"
}
$baseUrl = if ($env:LCOVMERGE_BASE_URL) {
    $env:LCOVMERGE_BASE_URL.TrimEnd('/')
} else {
    "https://github.com/megasoft1978/lcovmerge/releases/download/v$version"
}

if ([System.Runtime.InteropServices.RuntimeInformation]::OSArchitecture -ne [System.Runtime.InteropServices.Architecture]::X64) {
    throw 'The published Windows installer currently supports x86_64 only.'
}

$asset = "lcovmerge-$version-windows-x86_64.zip"
$workDir = Join-Path ([System.IO.Path]::GetTempPath()) ([System.Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $workDir | Out-Null
try {
    $archivePath = Join-Path $workDir $asset
    $sumsPath = Join-Path $workDir 'SHA256SUMS'
    Invoke-WebRequest -Uri "$baseUrl/$asset" -OutFile $archivePath
    Invoke-WebRequest -Uri "$baseUrl/SHA256SUMS" -OutFile $sumsPath

    $escapedAsset = [Regex]::Escape($asset)
    $sumLine = Get-Content -LiteralPath $sumsPath | Where-Object { $_ -match "^([0-9a-fA-F]{64})\s+\*?$escapedAsset$" } | Select-Object -First 1
    if (-not $sumLine) {
        throw "No valid SHA-256 entry for $asset in SHA256SUMS."
    }
    $expected = [Regex]::Match($sumLine, '^([0-9a-fA-F]{64})').Groups[1].Value.ToLowerInvariant()
    $actual = (Get-FileHash -LiteralPath $archivePath -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actual -ne $expected) {
        throw "SHA-256 verification failed for $asset."
    }

    $unpackDir = Join-Path $workDir 'unpacked'
    Expand-Archive -LiteralPath $archivePath -DestinationPath $unpackDir
    $binaryPath = Join-Path $unpackDir 'lcovmerge.exe'
    if (-not (Test-Path -LiteralPath $binaryPath -PathType Leaf)) {
        throw 'The release archive does not contain lcovmerge.exe at its root.'
    }

    if (-not $InstallDir) {
        if ($env:PREFIX) {
            $InstallDir = Join-Path $env:PREFIX 'bin'
        } else {
            $InstallDir = Join-Path $env:USERPROFILE '.local\bin'
        }
    }
    New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
    Copy-Item -LiteralPath $binaryPath -Destination (Join-Path $InstallDir 'lcovmerge.exe') -Force
    Write-Output "Installed lcovmerge $version to $(Join-Path $InstallDir 'lcovmerge.exe')"
} finally {
    Remove-Item -LiteralPath $workDir -Recurse -Force -ErrorAction SilentlyContinue
}
