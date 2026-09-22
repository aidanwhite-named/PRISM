[CmdletBinding()]
param(
    [ValidateSet('claude', 'codex', 'both', 'agy', 'all', 'skip')]
    [string]$Cli = 'all',
    [string]$OwnershipFile = ''
)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
. (Join-Path $PSScriptRoot 'scripts\windows-common.ps1')

try {
    if (-not [Environment]::Is64BitOperatingSystem -or $env:PROCESSOR_ARCHITECTURE -eq 'ARM64') {
        throw 'This distribution supports Windows x64.'
    }
    Update-PrismPath
    Write-Host 'PRISM 최초 설치' -ForegroundColor Cyan
    Write-Host '[1/6] Python을 준비합니다.'
    if (-not (Test-Path (Join-Path $PSScriptRoot 'frontend\dist\index.html'))) {
        throw 'Built UI is missing. Use the release ZIP, or build frontend first (see README).'
    }
    $venv = Join-Path $PSScriptRoot 'backend\.venv\Scripts\python.exe'
    if (-not (Test-PrismPython $venv)) {
        if (Test-Path (Join-Path $PSScriptRoot 'backend\.venv')) {
            throw 'Existing backend\.venv is incompatible or damaged. Rename it, then run setup again.'
        }
        $python = Find-PrismPython
        if (-not $python) {
            Write-Host 'Installing Python 3.11 x64...'
            Install-PrismPackage 'Python.Python.3.11' 'https://www.python.org/downloads/windows/' -UserScope
            Save-PrismDependency $OwnershipFile 'python'
            $python = Find-PrismPython
        }
        if (-not $python) { throw 'Python 3.11/3.12 x64 was not found. Reopen setup after installation.' }
        Invoke-Checked $python @('-m', 'venv', (Join-Path $PSScriptRoot 'backend\.venv'))
    }
    Write-Host '[2/6] PRISM 라이브러리를 설치합니다...' -ForegroundColor Cyan
    # Bootstrap with Windows trust roots even when ensurepip supplied pip 24.0.
    # Require a version with system trust enabled by default; a failed upgrade
    # must not silently leave an already-installed older pip in use.
    Invoke-Checked $venv @('-X', 'utf8', '-m', 'pip', 'install', '--use-feature=truststore', '--upgrade', 'pip>=24.2')
    Invoke-Checked $venv @('-X', 'utf8', '-m', 'pip', 'install', '-r', (Join-Path $PSScriptRoot 'backend\requirements.txt'))
    Invoke-Checked $venv @('-X', 'utf8', (Join-Path $PSScriptRoot 'backend\scripts\prepare_tokenizer.py'))
    Invoke-Checked $venv @('-m', 'pip', 'check')
    Invoke-Checked $venv @('-c', 'import fastapi,uvicorn,sqlalchemy,pypdf,arxiv,pyalex,truststore,winpty')

    if ($Cli -in @('codex', 'both', 'all')) {
        Write-Host '[3/6] Node.js를 준비합니다.'
        $node = Get-Command node.exe -ErrorAction SilentlyContinue
        $nodeOk = $false
        if ($node) {
            & $node.Source --use-system-ca -e 'process.exit(Number(process.versions.node.split(String.fromCharCode(46))[0]) >= 22 ? 0 : 1)'
            $nodeOk = $LASTEXITCODE -eq 0
        }
        if (-not $nodeOk) {
            $nodeWasPresent = $null -ne $node
            Install-PrismPackage 'OpenJS.NodeJS.LTS' 'https://nodejs.org/en/download'
            if (-not $nodeWasPresent) { Save-PrismDependency $OwnershipFile 'node' }
            $node = Get-Command node.exe -ErrorAction SilentlyContinue
            if (-not $node) { throw 'Node.js was not found. Reopen setup after installing Node.js LTS.' }
            Invoke-Checked $node.Source @('--use-system-ca', '-e', 'process.exit(0)')
        }
        $npm = Get-Command npm.cmd -ErrorAction SilentlyContinue
        if (-not $npm) { throw 'npm was not found. Reopen setup after installing Node.js LTS.' }
    }

    if ($Cli -in @('claude', 'both', 'all')) {
        Write-Host '[4/6] Claude Code를 준비합니다.'
        $claude = Find-PrismCli 'claude'
        if (-not $claude) {
            Install-PrismPackage 'Anthropic.ClaudeCode' 'https://code.claude.com/docs/en/setup'
            Save-PrismDependency $OwnershipFile 'claude'
            $claude = Find-PrismCli 'claude'
        }
        if (-not $claude) { throw 'Claude CLI was not found. Reopen setup or check the official installation guide.' }
        Invoke-Checked $claude @('--version')
    }
    if ($Cli -in @('codex', 'both', 'all')) {
        Write-Host '[5/6] Codex를 준비합니다.'
        $codex = Find-PrismCli 'codex'
        if (-not $codex) {
            # npm runs in a separate Node process; inherit the Windows trust
            # store without disabling TLS verification or changing user config.
            $previousNodeOptions = $env:NODE_OPTIONS
            try {
                $env:NODE_OPTIONS = "$previousNodeOptions --use-system-ca".Trim()
                Invoke-Checked $npm.Source @('install', '-g', '@openai/codex')
                Save-PrismDependency $OwnershipFile 'codex'
            } finally {
                $env:NODE_OPTIONS = $previousNodeOptions
            }
            Update-PrismPath
            $codex = Find-PrismCli 'codex'
        }
        if (-not $codex) { throw 'Codex CLI was not found. Check npm global PATH and reopen setup.' }
        Invoke-Checked $codex @('--version')
    }
    if ($Cli -in @('agy', 'all')) {
        Write-Host '[6/6] agy (Antigravity CLI)를 준비합니다.'
        $agy = Find-PrismCli 'agy'
        if (-not $agy) {
            Install-PrismAgy
            Save-PrismDependency $OwnershipFile 'agy'
            $agy = Find-PrismCli 'agy'
        }
        if (-not $agy) { throw 'agy was not found after installation. Check the installation log and retry.' }
        $env:AGY_CLI_DISABLE_AUTO_UPDATE = 'true'
        Invoke-Checked $agy @('--version')
    }
    Write-Host 'PRISM setup complete. Open PRISM launcher, then log in through Settings.' -ForegroundColor Green
    Write-Host '설치가 완료되었습니다. 실행.cmd를 열고 Settings에서 로그인하세요.' -ForegroundColor Green
    if ($Cli -eq 'skip') { Write-Host 'AI CLI installation/login is still required before analysis.' -ForegroundColor Yellow }
    exit 0
} catch {
    Write-Host "Setup failed: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host 'Resolve the error above and run setup again. Completed installations can be reused.'
    exit 1
}
