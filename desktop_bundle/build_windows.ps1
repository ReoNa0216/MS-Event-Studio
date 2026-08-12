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

$Executable = Join-Path $RepoRoot "release\windows\MS-Event-Studio\MS-Event-Studio.exe"
if (!(Test-Path -LiteralPath $Executable -PathType Leaf)) {
    throw "The packaged executable is missing: $Executable"
}

$Archive = Join-Path $RepoRoot "release\MS-Event-Studio-$Version-windows-x64.zip"
$Checksum = "$Archive.sha256"
Remove-Item -LiteralPath $Archive, $Checksum -Force -ErrorAction SilentlyContinue
$ArchiveInputs = @(
    (Join-Path $RepoRoot "release\windows\MS-Event-Studio"),
    (Join-Path $RepoRoot "release\windows\build_manifest.json"),
    (Join-Path $RepoRoot "release\windows\smoke_test.json")
)
Compress-Archive -LiteralPath $ArchiveInputs -DestinationPath $Archive -CompressionLevel Optimal
$Hash = (Get-FileHash -LiteralPath $Archive -Algorithm SHA256).Hash.ToLowerInvariant()
"$Hash  $(Split-Path $Archive -Leaf)" | Set-Content -LiteralPath $Checksum -Encoding ascii

Write-Host "Build complete: $Archive"
