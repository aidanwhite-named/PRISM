[CmdletBinding()]
param(
    [string]$Python = 'python',
    [string]$IsccPath = '',
    [switch]$SkipInstaller
)
$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
Set-Location -LiteralPath $root

function Invoke-Checked([string]$Program, [string[]]$Arguments) {
    & $Program @Arguments
    if ($LASTEXITCODE -ne 0) { throw "$Program failed with exit code $LASTEXITCODE" }
}

if (-not $IsWindows -and $PSVersionTable.PSVersion.Major -ge 6) { throw 'Build on Windows x64.' }
Invoke-Checked $Python @('-c', 'import struct; assert struct.calcsize("P") == 8, "64-bit Python required"')
$buildPython = Join-Path $root '.build-venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $buildPython)) {
    Invoke-Checked $Python @('-m', 'venv', (Join-Path $root '.build-venv'))
}
$env:PYTHONUTF8 = '1'
Invoke-Checked $buildPython @('-m', 'pip', 'install', '--upgrade', 'pip')
Invoke-Checked $buildPython @('-m', 'pip', 'install', '-r', 'backend/requirements-build.txt')
Push-Location (Join-Path $root 'frontend')
try {
    Invoke-Checked 'npm.cmd' @('ci')
    Invoke-Checked 'npm.cmd' @('run', 'build')
} finally { Pop-Location }
Invoke-Checked $buildPython @('-m', 'PyInstaller', '--noconfirm', '--clean', 'packaging/prism.spec')
Invoke-Checked $buildPython @('packaging/collect_notices.py')
Copy-Item -LiteralPath 'packaging/READ-ME.txt' -Destination 'dist/PRISM/READ-ME.txt'
$version = (& $buildPython -c 'import sys; sys.path.insert(0, "backend"); from app import __version__; print(__version__)').Trim()
if ($LASTEXITCODE -ne 0) { throw 'Cannot read version' }
New-Item -ItemType Directory -Force -Path 'release' | Out-Null
& $buildPython -m pip freeze | Set-Content -LiteralPath 'release/build-requirements.txt' -Encoding utf8
$zip = "release/PRISM-$version-windows-x64.zip"
Compress-Archive -Path 'dist/PRISM' -DestinationPath $zip -Force

if (-not $SkipInstaller) {
    if (-not $IsccPath) {
        $candidates = @(
            (Join-Path $root '.build-tools\InnoSetup\ISCC.exe'),
            "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
            "$env:ProgramFiles\Inno Setup 7\ISCC.exe"
        )
        $command = Get-Command ISCC.exe -ErrorAction SilentlyContinue
        if ($command) { $IsccPath = $command.Source }
        else { $IsccPath = $candidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1 }
    }
    if (-not $IsccPath) { throw "Portable ZIP built: $zip. Install Inno Setup or pass -IsccPath to also build Setup.exe." }
    Invoke-Checked $IsccPath @("/DAppVersion=$version", 'packaging/prism.iss')
}
Get-ChildItem -LiteralPath 'release' -File | Where-Object { $_.Extension -in '.exe', '.zip' } |
    Get-FileHash -Algorithm SHA256 | ForEach-Object { "$($_.Hash)  $(Split-Path -Leaf $_.Path)" } |
    Set-Content -LiteralPath 'release/SHA256SUMS.txt' -Encoding ascii
Write-Host "Built PRISM $version in $root\release"
