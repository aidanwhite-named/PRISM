[CmdletBinding()]
param(
    [switch]$NonInteractive,
    [switch]$Preview,
    [switch]$RemoveSharedProfiles,
    [switch]$RemoveSharedCli,
    [switch]$RemoveOwnedRuntimes
)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'windows-common.ps1')
$appRoot = [IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot))
$dataRoot = Join-Path $env:LOCALAPPDATA 'PRISM'
$ownedFile = Join-Path $appRoot '.setup\dependencies.json'

function Assert-PrismRemovalPath {
    param([string]$Path, [string]$Boundary)
    $resolved = [IO.Path]::GetFullPath($Path).TrimEnd('\')
    $allowed = [IO.Path]::GetFullPath($Boundary).TrimEnd('\')
    if (-not $resolved.Equals($allowed, [StringComparison]::OrdinalIgnoreCase) -and
        -not $resolved.StartsWith($allowed + '\', [StringComparison]::OrdinalIgnoreCase)) {
        throw "Removal path escaped its allowed directory: $resolved"
    }
    # Reject junctions/symlinks both above and below a deletion target.
    $cursor = $resolved
    while ($cursor) {
        if (Test-Path -LiteralPath $cursor) {
            if ((Get-Item -LiteralPath $cursor -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) {
                throw "Linked directory cannot be removed automatically: $cursor"
            }
        }
        $cursor = Split-Path -Parent $cursor
    }
    $pending = New-Object 'System.Collections.Generic.Stack[string]'
    if (Test-Path -LiteralPath $resolved -PathType Container) { $pending.Push($resolved) }
    while ($pending.Count) {
        foreach ($child in Get-ChildItem -LiteralPath $pending.Pop() -Force) {
            if ($child.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw "Linked path cannot be removed automatically: $($child.FullName)" }
            if ($child.PSIsContainer) { $pending.Push($child.FullName) }
        }
    }
}

try {
    if (-not $Preview -and -not $NonInteractive) {
        Add-Type -AssemblyName System.Windows.Forms
        Add-Type -AssemblyName System.Drawing
        [Windows.Forms.Application]::EnableVisualStyles()
        $form = New-Object Windows.Forms.Form
        $form.Text = 'PRISM 완전 제거'
        $form.ClientSize = New-Object Drawing.Size(680, 345)
        $form.StartPosition = 'CenterScreen'
        $form.Font = New-Object Drawing.Font('Malgun Gothic', 10)
        $label = New-Object Windows.Forms.Label
        $label.SetBounds(22, 20, 635, 88)
        $label.Text = "PRISM 프로그램, 히스토리, 설정, 첨부파일, 실행 기록, 편집 프롬프트와`nPRISM이 새로 설치한 CLI를 삭제합니다. 이 작업은 되돌릴 수 없습니다.`n데이터: $dataRoot"
        $form.Controls.Add($label)
        $profiles = New-Object Windows.Forms.CheckBox
        $profiles.SetBounds(22, 116, 635, 52)
        $profiles.Text = "공유 CLI 기록도 삭제 (.codex, .claude, Gemini/agy 설정·세션)`n다른 프로젝트와 Codex 앱의 로컬 계정·설정·세션에도 영향을 줍니다."
        $form.Controls.Add($profiles)
        $cli = New-Object Windows.Forms.CheckBox
        $cli.SetBounds(22, 177, 635, 45)
        $cli.Text = "기존 CLI도 제거 (이전 ZIP 설치 등 설치 주체를 모르는 경우)`nCodex npm / Claude WinGet / agy 기본 설치만 지원합니다."
        $form.Controls.Add($cli)
        $runtimes = New-Object Windows.Forms.CheckBox
        $runtimes.SetBounds(22, 230, 635, 28)
        $runtimes.Text = 'PRISM이 새로 설치한 Python·Node.js도 제거 (다른 앱에 영향 가능)'
        $form.Controls.Add($runtimes)
        $remove = New-Object Windows.Forms.Button
        $remove.Text = '선택 항목 제거'
        $remove.SetBounds(390, 288, 140, 35)
        $remove.DialogResult = [Windows.Forms.DialogResult]::OK
        $form.Controls.Add($remove)
        $cancel = New-Object Windows.Forms.Button
        $cancel.Text = '취소'
        $cancel.SetBounds(542, 288, 110, 35)
        $cancel.DialogResult = [Windows.Forms.DialogResult]::Cancel
        $form.Controls.Add($cancel)
        $form.CancelButton = $cancel
        if ($form.ShowDialog() -ne [Windows.Forms.DialogResult]::OK) { $form.Dispose(); exit 2 }
        $RemoveSharedProfiles = $profiles.Checked
        $RemoveSharedCli = $cli.Checked
        $RemoveOwnedRuntimes = $runtimes.Checked
        $form.Dispose()
    }
    if ($env:PRISM_DATA_DIR -and [IO.Path]::GetFullPath($env:PRISM_DATA_DIR).TrimEnd('\') -ne $dataRoot) {
        throw "Custom PRISM_DATA_DIR is configured. Back up/remove that directory explicitly before clearing this override and retrying: $env:PRISM_DATA_DIR"
    }
    $owned = @()
    if (Test-Path -LiteralPath $ownedFile) { $owned = @(Get-Content -LiteralPath $ownedFile -Raw | ConvertFrom-Json | ForEach-Object { $_ }) }
    $dependencies = @($owned | Where-Object { $_ -in @('codex', 'claude', 'agy') })
    if ($RemoveSharedCli) { $dependencies = @('codex', 'claude', 'agy') }
    if ($RemoveOwnedRuntimes) { $dependencies += @($owned | Where-Object { $_ -in @('node', 'python') }) }
    $targets = @(
        @{path=$dataRoot; boundary=$dataRoot},
        @{path=(Join-Path $appRoot 'backend\.venv'); boundary=$appRoot},
        @{path=(Join-Path $appRoot 'prompt'); boundary=$appRoot}
    )
    if ($RemoveSharedProfiles) {
        foreach ($relative in @('.codex', '.claude', '.claude.json', '.gemini\antigravity-cli', '.gemini\config')) {
            $target = Join-Path $env:USERPROFILE $relative
            $targets += @{path=$target; boundary=$target}
        }
        if ($env:CODEX_HOME -and [IO.Path]::GetFullPath($env:CODEX_HOME).TrimEnd('\') -ne (Join-Path $env:USERPROFILE '.codex')) {
            throw 'Custom CODEX_HOME is set. Remove its records explicitly; automatic shared-profile cleanup is disabled.'
        }
        if ($env:CLAUDE_CONFIG_DIR -and [IO.Path]::GetFullPath($env:CLAUDE_CONFIG_DIR).TrimEnd('\') -ne (Join-Path $env:USERPROFILE '.claude')) {
            throw 'Custom CLAUDE_CONFIG_DIR is set. Remove its records explicitly; automatic shared-profile cleanup is disabled.'
        }
    }
    if ('agy' -in $dependencies) {
        $target = Join-Path $env:LOCALAPPDATA 'agy'
        $targets += @{path=$target; boundary=$target}
    }
    $configEdits = @()
    if (-not $RemoveSharedProfiles) {
        # Remove only PRISM's MCP registration, preserving other servers/rules.
        foreach ($relative in @('.gemini\config\mcp_config.json', '.gemini\antigravity-cli\settings.json')) {
            $configPath = Join-Path $env:USERPROFILE $relative
            Assert-PrismRemovalPath $configPath $configPath
            if (Test-Path -LiteralPath $configPath) {
                $raw = Get-Content -LiteralPath $configPath -Raw
                if ($raw -and $raw.Trim()) {
                    $document = $raw | ConvertFrom-Json
                    $changed = $false
                    if ($document.PSObject.Properties['mcpServers']) {
                        $entry = $document.mcpServers.PSObject.Properties['prism-search']
                        if ($entry -and $entry.Value.args -contains 'app.search_mcp_server') {
                            $document.mcpServers.PSObject.Properties.Remove('prism-search')
                            $changed = $true
                        }
                    }
                    if ($document.PSObject.Properties['permissions'] -and $document.permissions.PSObject.Properties['allow']) {
                        if ($document.permissions.allow -contains 'mcp(prism-search/*)') {
                            $document.permissions.allow = @($document.permissions.allow | Where-Object { $_ -ne 'mcp(prism-search/*)' })
                            $changed = $true
                        }
                    }
                    if ($changed) { $configEdits += @{path=$configPath; content=($document | ConvertTo-Json -Depth 100)} }
                }
            }
        }
        $cache = Join-Path $env:USERPROFILE '.gemini\antigravity-cli\mcp\prism-search'
        $targets += @{path=$cache; boundary=$cache}
    }
    # Inspect every target before removing anything. Inno owns installed source files.
    foreach ($target in $targets) { Assert-PrismRemovalPath $target.path $target.boundary }
    Assert-PrismRemovalPath (Join-Path $appRoot 'backend') $appRoot
    if ($Preview) {
        @{paths=@($targets | ForEach-Object { $_.path }); dependencies=$dependencies; edited_configs=@($configEdits | ForEach-Object { $_.path })} | ConvertTo-Json -Depth 5
        exit 0
    }
    Update-PrismPath
    # npm is needed to remove Codex before optionally removing Node.
    $dependencies = @($dependencies | Sort-Object @{Expression={ if ($_ -eq 'codex') { 0 } elseif ($_ -in @('node','python')) { 2 } else { 1 } }})
    foreach ($dependency in $dependencies) {
        switch ($dependency) {
            'codex' {
                $npm = Get-Command npm.cmd -ErrorAction SilentlyContinue
                if ($npm) { Invoke-Checked $npm.Source @('uninstall', '-g', '@openai/codex') }
                elseif (Find-PrismCli 'codex') { throw 'npm is missing; Codex could not be uninstalled. Restore npm and retry.' }
            }
            { $_ -in @('claude', 'node', 'python') } {
                $id = @{claude='Anthropic.ClaudeCode'; node='OpenJS.NodeJS.LTS'; python='Python.Python.3.11'}[$dependency]
                $winget = Find-PrismWinget
                if (-not $winget) { throw "WinGet is unavailable; could not remove $id." }
                & $winget uninstall --id $id --exact --source winget --silent --disable-interactivity | Out-Host
                # APPINSTALLER_CLI_ERROR_NO_APPLICATIONS_FOUND means already removed.
                if ($LASTEXITCODE -ne 0 -and $LASTEXITCODE -ne -1978335212) { throw "$id uninstall failed ($LASTEXITCODE)." }
            }
        }
    }
    foreach ($edit in $configEdits) {
        $temporary = $edit.path + '.prism-uninstall.tmp'
        Assert-PrismRemovalPath $temporary (Split-Path -Parent $edit.path)
        $edit.content | Set-Content -LiteralPath $temporary -Encoding UTF8
        Move-Item -LiteralPath $temporary -Destination $edit.path -Force
    }
    foreach ($target in $targets) {
        Assert-PrismRemovalPath $target.path $target.boundary
        if (Test-Path -LiteralPath $target.path) { Remove-Item -LiteralPath $target.path -Recurse -Force }
    }
    if ('agy' -in $dependencies) {
        $agyBin = Join-Path $env:LOCALAPPDATA 'agy\bin'
        $userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
        $parts = @($userPath -split ';' | Where-Object { $_.TrimEnd('\') -ne $agyBin })
        [Environment]::SetEnvironmentVariable('Path', ($parts -join ';'), 'User')
    }
    # Generated caches/logs live only inside the verified installed application.
    foreach ($target in @((Join-Path $appRoot '.setup')) + @(Get-ChildItem -LiteralPath (Join-Path $appRoot 'backend') -Directory -Filter '__pycache__' -Recurse | ForEach-Object { $_.FullName })) {
        Assert-PrismRemovalPath $target $appRoot
        if (Test-Path -LiteralPath $target) { Remove-Item -LiteralPath $target -Recurse -Force }
    }
    exit 0
} catch {
    Write-Error $_ -ErrorAction Continue
    if (-not $NonInteractive -and -not $Preview) {
        [Windows.Forms.MessageBox]::Show("제거를 완료하지 못했습니다. 오류를 해결한 뒤 다시 실행하세요.`n$($_.Exception.Message)", 'PRISM 제거 실패') | Out-Null
    }
    exit 1
}
