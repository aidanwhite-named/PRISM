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
        (Join-Path $env:LOCALAPPDATA 'Microsoft\WindowsApps'),
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

function Find-PrismWinget {
    Update-PrismPath
    $candidates = @()
    $command = Get-Command winget.exe -ErrorAction SilentlyContinue
    if ($command) { $candidates += $command.Source }
    # The execution alias may not be available in this session yet.
    if (Get-Command Get-AppxPackage -ErrorAction SilentlyContinue) {
        $packages = @(Get-AppxPackage -Name Microsoft.DesktopAppInstaller -ErrorAction SilentlyContinue)
        foreach ($package in $packages) {
            if ($package.InstallLocation) { $candidates += Join-Path $package.InstallLocation 'winget.exe' }
        }
    }
    foreach ($candidate in $candidates) {
        try {
            & $candidate --version 2>$null | Out-Null
            if ($LASTEXITCODE -eq 0) { return $candidate }
        } catch { }
    }
    return $null
}

function Ensure-PrismWinget {
    $winget = Find-PrismWinget
    if ($winget) { return $winget }
    Write-Host 'WinGet is missing. Downloading and installing it from Microsoft (no Store required)...' -ForegroundColor Cyan
    $temporaryRoot = [IO.Path]::GetFullPath([IO.Path]::GetTempPath())
    $downloadDirectory = Join-Path $temporaryRoot ('prism-winget-' + [Guid]::NewGuid().ToString('N'))
    $previousProgress = $ProgressPreference
    $previousTls = [Net.ServicePointManager]::SecurityProtocol
    try {
        if (-not (Get-Command Add-AppxPackage -ErrorAction SilentlyContinue)) {
            throw 'Windows AppX installation support is unavailable.'
        }
        New-Item -ItemType Directory -Path $downloadDirectory | Out-Null
        $ProgressPreference = 'SilentlyContinue'
        [Net.ServicePointManager]::SecurityProtocol = $previousTls -bor [Net.SecurityProtocolType]::Tls12
        # Resolve once so the bundle and dependencies always come from the same stable release.
        $release = Invoke-RestMethod -Uri 'https://api.github.com/repos/microsoft/winget-cli/releases/latest' -UseBasicParsing
        foreach ($name in @('Microsoft.DesktopAppInstaller_8wekyb3d8bbwe.msixbundle', 'DesktopAppInstaller_Dependencies.zip')) {
            $assets = @($release.assets | Where-Object { $_.name -eq $name })
            if ($assets.Count -ne 1) { throw "Official WinGet release is missing $name." }
            $url = [string]$assets[0].browser_download_url
            if (-not $url.StartsWith('https://github.com/microsoft/winget-cli/releases/download/', [StringComparison]::Ordinal)) {
                throw 'Unexpected WinGet download source.'
            }
            Invoke-WebRequest -Uri $url -UseBasicParsing -OutFile (Join-Path $downloadDirectory $name)
        }
        $dependenciesDirectory = Join-Path $downloadDirectory 'dependencies'
        Expand-Archive -LiteralPath (Join-Path $downloadDirectory 'DesktopAppInstaller_Dependencies.zip') -DestinationPath $dependenciesDirectory
        $dependencies = @(Get-ChildItem -LiteralPath (Join-Path $dependenciesDirectory 'x64') -Recurse -File |
            Where-Object { $_.Extension -in @('.appx', '.msix') } | ForEach-Object { $_.FullName })
        if ($dependencies.Count -eq 0) { throw 'WinGet x64 dependency packages are missing.' }
        # Windows validates package signatures; use normal per-user installation.
        Add-AppxPackage -Path (Join-Path $downloadDirectory 'Microsoft.DesktopAppInstaller_8wekyb3d8bbwe.msixbundle') -DependencyPath $dependencies -ErrorAction Stop
        $winget = Find-PrismWinget
        if (-not $winget) { throw 'WinGet was installed but could not be started.' }
        Write-Host 'WinGet is ready. Continuing PRISM setup.' -ForegroundColor Green
        return $winget
    } catch {
        throw "WinGet automatic installation failed: $($_.Exception.Message) If company policy or network access blocks installation, contact IT. Official installer: https://aka.ms/getwinget . Run PRISM setup again after resolving the error."
    } finally {
        $ProgressPreference = $previousProgress
        [Net.ServicePointManager]::SecurityProtocol = $previousTls
        # Only remove the unique directory created by this invocation, under TEMP.
        $resolvedDownload = [IO.Path]::GetFullPath($downloadDirectory)
        $tempPrefix = $temporaryRoot.TrimEnd('\', '/') + [IO.Path]::DirectorySeparatorChar
        if ($resolvedDownload.StartsWith($tempPrefix, [StringComparison]::OrdinalIgnoreCase) -and
            (Test-Path -LiteralPath $resolvedDownload)) {
            Remove-Item -LiteralPath $resolvedDownload -Recurse -Force -ErrorAction SilentlyContinue
        }
    }
}

function Install-PrismPackage {
    param([string]$Id, [string]$HelpUrl, [switch]$UserScope)
    $winget = Ensure-PrismWinget
    $arguments = @('install', '--id', $Id, '--exact', '--source', 'winget', '--accept-source-agreements', '--accept-package-agreements', '--disable-interactivity', '--silent')
    if ($UserScope) { $arguments += @('--scope', 'user') }
    Invoke-Checked $winget $arguments
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
