Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Invoke-Checked {
    param([string]$File, [string[]]$Arguments)
    & $File @Arguments | Out-Host
    if ($LASTEXITCODE -ne 0) { throw "$File failed (exit $LASTEXITCODE)." }
}

function Update-PrismPath {
    $parts = @(
        [Environment]::GetEnvironmentVariable('Path', 'Machine'),
        [Environment]::GetEnvironmentVariable('Path', 'User'),
        $env:Path,
        (Join-Path $env:APPDATA 'npm'),
        (Join-Path $env:USERPROFILE '.local\bin'),
        (Join-Path $env:LOCALAPPDATA 'Microsoft\WinGet\Links')
        (Join-Path $env:LOCALAPPDATA 'agy\bin')
    )
    $env:Path = ($parts | Where-Object { $_ }) -join ';'
}

function Test-PrismPython {
    param([string]$File)
    if (-not (Test-Path -LiteralPath $File -PathType Leaf)) { return $false }
    if ($File -like '*\Microsoft\WindowsApps\*') { return $false }
    try {
        & $File -c 'import sys,struct; sys.exit(0 if (3,11) <= sys.version_info[:2] < (3,13) and struct.calcsize(chr(80))==8 else 1)' 2>$null | Out-Null
        return $LASTEXITCODE -eq 0
    } catch { return $false }
}

function Find-PrismPython {
    $candidates = @()
    $launcher = Get-Command py.exe -ErrorAction SilentlyContinue
    if ($launcher) {
        foreach ($version in @('-3.11', '-3.12')) {
            try {
                $found = & $launcher.Source $version -c 'import sys; print(sys.executable)' 2>$null
                if ($LASTEXITCODE -eq 0 -and $found) { $candidates += [string]$found }
            } catch { }
        }
    }
    $command = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($command) { $candidates += $command.Source }
    foreach ($version in @('311', '312')) {
        $candidates += Join-Path $env:LOCALAPPDATA "Programs\Python\Python$version\python.exe"
        $candidates += Join-Path $env:ProgramFiles "Python$version\python.exe"
    }
    foreach ($candidate in $candidates) {
        if (Test-PrismPython $candidate) { return $candidate }
    }
    return $null
}

function Install-PrismPackage {
    param([string]$Id, [string]$HelpUrl, [switch]$UserScope)
    $winget = Get-Command winget.exe -ErrorAction SilentlyContinue
    if (-not $winget) { throw "winget is unavailable. Install from $HelpUrl and run setup again." }
    $arguments = @('install', '--id', $Id, '--exact', '--source', 'winget', '--accept-source-agreements', '--accept-package-agreements', '--disable-interactivity', '--silent')
    if ($UserScope) { $arguments += @('--scope', 'user') }
    Invoke-Checked $winget.Source $arguments
    Update-PrismPath
}

function Find-PrismCli {
    param([string]$Name)
    foreach ($extension in @('.exe', '.cmd')) {
        $command = Get-Command "$Name$extension" -ErrorAction SilentlyContinue
        if ($command) { return $command.Source }
    }
    if ($Name -eq 'agy') {
        foreach ($directory in @((Join-Path $env:LOCALAPPDATA 'agy\bin'), (Join-Path $env:USERPROFILE '.agy\bin'))) {
            $candidate = Join-Path $directory 'agy.exe'
            if (Test-Path -LiteralPath $candidate -PathType Leaf) { return $candidate }
        }
    }
    return $null
}

function Install-PrismAgy {
    # Official installer validates the downloaded executable against SHA-512.
    # PowerShell HTTPS uses Windows trust roots; do not bypass TLS validation.
    $installerPath = Join-Path ([IO.Path]::GetTempPath()) ('prism-agy-' + [Guid]::NewGuid().ToString('N') + '.ps1')
    try {
        Invoke-WebRequest -Uri 'https://antigravity.google/cli/install.ps1' -UseBasicParsing -OutFile $installerPath
        $quotedPath = $installerPath.Replace("'", "''")
        # Avoid IE parsing/security prompts in Windows PowerShell 5.1, including
        # the official installer's own executable download.
        $command = "`$PSDefaultParameterValues = @{'Invoke-WebRequest:UseBasicParsing' = `$true}; & '$quotedPath' --skip-aliases"
        Invoke-Checked (Join-Path $env:SYSTEMROOT 'System32\WindowsPowerShell\v1.0\powershell.exe') @('-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-Command', $command)
    } finally {
        if (Test-Path -LiteralPath $installerPath) { Remove-Item -LiteralPath $installerPath -Force }
    }
    Update-PrismPath
}
