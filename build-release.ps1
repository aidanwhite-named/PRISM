[CmdletBinding()]
param([string]$InnoCompiler = '')
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'scripts\windows-common.ps1')
try {
    $python = Find-PrismPython
    if (-not $python) { throw 'Python 3.11/3.12 x64 is required to create a release.' }
    if (-not (Get-Command npm.cmd -ErrorAction SilentlyContinue)) { throw 'Install Node.js LTS to build the UI.' }
    Push-Location (Join-Path $PSScriptRoot 'frontend')
    try {
        Invoke-Checked 'npm.cmd' @('ci')
        Invoke-Checked 'npm.cmd' @('run', 'build')
    } finally { Pop-Location }
    Invoke-Checked $python @((Join-Path $PSScriptRoot 'scripts\create_release.py'))
    if (-not $InnoCompiler) {
        $InnoCompiler = Join-Path $PSScriptRoot 'release\tools\inno\ISCC.exe'
    }
    if (-not (Test-Path -LiteralPath $InnoCompiler)) { throw 'Inno Setup compiler is required. Pass -InnoCompiler with the path to ISCC.exe.' }
    Invoke-Checked $python @((Join-Path $PSScriptRoot 'scripts\create_installer.py'), '--compiler', $InnoCompiler)
    exit 0
} catch {
    Write-Host "Release failed: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}
