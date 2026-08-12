param(
    [string]$PythonExe = "",
    [string]$Version = ""
)

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $RepoRoot

$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

if (!$PythonExe) {
    $PythonExe = (Get-Command python -ErrorAction Stop).Source
}
if (!$Version) {
    $Version = if ($env:MS_EVENT_STUDIO_VERSION) { $env:MS_EVENT_STUDIO_VERSION } else { "dev-candidate" }
}
if ($Version -notmatch '^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$') {
    throw "Version may contain only letters, digits, period, underscore, and hyphen (maximum 64 characters)."
}

$Python = (Resolve-Path -LiteralPath $PythonExe).Path
Write-Host "Using Python: $Python"

& $Python -m pip install --upgrade pip wheel setuptools
if ($LASTEXITCODE -ne 0) { throw "Failed to update the Windows build toolchain." }
& $Python -m pip install -e ".[packaging]"
if ($LASTEXITCODE -ne 0) { throw "Failed to install MS Event Studio build dependencies." }

$env:PYTHONPATH = "src;tests;."
& $Python -m unittest discover -s tests -q
if ($LASTEXITCODE -ne 0) { throw "Tests failed; refusing to create a Windows candidate." }
& $Python -m desktop_bundle.build_desktop
if ($LASTEXITCODE -ne 0) { throw "Windows desktop packaging failed." }

$DistRoot = Join-Path $RepoRoot "dist\windows"
$Executable = Join-Path $DistRoot "MS-Event-Studio\MS-Event-Studio.exe"
if (!(Test-Path -LiteralPath $Executable -PathType Leaf)) {
    throw "The packaged executable is missing: $Executable"
}

$ReleaseRoot = Join-Path $RepoRoot "release"
$Archive = Join-Path $ReleaseRoot "MS-Event-Studio-$Version-windows-x64.zip"
$Checksum = "$Archive.sha256"
$ArchiveInputs = @(
    (Join-Path $DistRoot "MS-Event-Studio"),
    (Join-Path $DistRoot "build_manifest.json"),
    (Join-Path $DistRoot "smoke_test.json")
)
$Staging = Join-Path $RepoRoot ("build\release-staging\windows-" + [Guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $Staging -Force | Out-Null
$StagedArchive = Join-Path $Staging (Split-Path $Archive -Leaf)
$StagedChecksum = "$StagedArchive.sha256"
try {
    Compress-Archive -LiteralPath $ArchiveInputs -DestinationPath $StagedArchive -CompressionLevel Optimal
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $Zip = [IO.Compression.ZipFile]::OpenRead($StagedArchive)
    try {
        $Entries = @($Zip.Entries | ForEach-Object { $_.FullName.Replace("\", "/") })
        foreach ($RequiredEntry in @(
            "MS-Event-Studio/MS-Event-Studio.exe",
            "build_manifest.json",
            "smoke_test.json"
        )) {
            if ($Entries -notcontains $RequiredEntry) {
                throw "The staged Windows archive is missing $RequiredEntry."
            }
        }
    }
    finally {
        $Zip.Dispose()
    }
    $Hash = (Get-FileHash -LiteralPath $StagedArchive -Algorithm SHA256).Hash.ToLowerInvariant()
    "$Hash  $(Split-Path $Archive -Leaf)" | Set-Content -LiteralPath $StagedChecksum -Encoding ascii

    New-Item -ItemType Directory -Path $ReleaseRoot -Force | Out-Null
    Remove-Item -LiteralPath $Archive, $Checksum -Force -ErrorAction SilentlyContinue
    Move-Item -LiteralPath $StagedArchive -Destination $Archive
    Move-Item -LiteralPath $StagedChecksum -Destination $Checksum
}
finally {
    if (Test-Path -LiteralPath $Staging) {
        Remove-Item -LiteralPath $Staging -Recurse -Force
    }
}

Write-Host "Build complete: $Archive"
