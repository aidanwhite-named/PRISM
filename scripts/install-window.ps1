[CmdletBinding()]
param([string]$PreviewImage, [switch]$VerifyAutomation, [switch]$Managed)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
[System.Windows.Forms.Application]::EnableVisualStyles()
$root = Split-Path -Parent $PSScriptRoot
$script:worker = $null
$script:resultCode = 1
$script:started = $false
$script:lockOwned = $false
$hash = [BitConverter]::ToString([Security.Cryptography.SHA256]::Create().ComputeHash([Text.Encoding]::UTF8.GetBytes($root.ToLowerInvariant()))).Replace('-', '')
$mutex = New-Object Threading.Mutex($false, "Local\PRISM-Setup-$hash")
try { $script:lockOwned = $mutex.WaitOne(0) } catch [Threading.AbandonedMutexException] { $script:lockOwned = $true }
if (-not $script:lockOwned) {
    [System.Windows.Forms.MessageBox]::Show('설치가 이미 진행 중입니다. 열려 있는 설치 창을 확인하세요.', 'PRISM 설치') | Out-Null
    $mutex.Dispose()
    exit 1
}
$form = New-Object System.Windows.Forms.Form
$form.Text = 'PRISM 설치 / 업데이트'
$form.ClientSize = New-Object Drawing.Size(640, 420)
$form.StartPosition = 'CenterScreen'
$form.FormBorderStyle = 'FixedDialog'
$form.MaximizeBox = $false
$form.ControlBox = $false
$form.BackColor = [Drawing.Color]::FromArgb(246, 248, 252)
$form.Font = New-Object Drawing.Font('Malgun Gothic', 10)

$title = New-Object Windows.Forms.Label
$title.Text = 'PRISM을 사용할 준비를 하고 있어요'
$title.Font = New-Object Drawing.Font('Malgun Gothic', 17, [Drawing.FontStyle]::Bold)
$title.SetBounds(28, 24, 590, 42)
$form.Controls.Add($title)
$description = New-Object Windows.Forms.Label
$description.Text = "Python · 라이브러리 · Node.js · Claude Code · Codex · agy를 자동으로 준비합니다.`n이미 설치된 항목은 재사용합니다. Windows 승인 창이 나오면 허용해 주세요."
$description.SetBounds(30, 78, 580, 55)
$form.Controls.Add($description)
$status = New-Object Windows.Forms.Label
$status.Text = '설치를 시작합니다. 잠시 기다려 주세요.'
$status.SetBounds(30, 143, 580, 28)
$form.Controls.Add($status)
$progress = New-Object Windows.Forms.ProgressBar
$progress.Style = 'Marquee'
$progress.SetBounds(30, 178, 580, 12)
$form.Controls.Add($progress)
$details = New-Object Windows.Forms.TextBox
$details.Multiline = $true
$details.ReadOnly = $true
$details.TabStop = $false
$details.ScrollBars = 'Vertical'
$details.Font = New-Object Drawing.Font('Malgun Gothic', 9)
$details.SetBounds(30, 209, 580, 128)
$details.Text = '첫 설치는 인터넷 속도에 따라 몇 분 걸릴 수 있습니다.'
$form.Controls.Add($details)
$close = New-Object Windows.Forms.Button
$close.Text = '닫기'
$close.Enabled = $false
$close.SetBounds(500, 359, 110, 36)
$close.Add_Click({ $form.Close() })
$form.Controls.Add($close)

$timer = New-Object Windows.Forms.Timer
$timer.Interval = 500
$timer.Add_Tick({
    try {
        if (-not $script:started) {
            $script:started = $true
            $logDir = Join-Path $root '.setup'
            New-Item -ItemType Directory -Path $logDir -Force | Out-Null
            $script:stdout = Join-Path $logDir 'install-output.log'
            $script:stderr = Join-Path $logDir 'install-error.log'
            $workerPath = Join-Path $root 'setup.ps1'
            $workerArgs = @('-NoLogo', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', "`"$workerPath`"")
            if ($Managed) { $workerArgs += @('-OwnershipFile', ('"' + (Join-Path $root '.setup\dependencies.json') + '"')) }
            $script:worker = Start-Process -FilePath "$PSHOME\powershell.exe" -ArgumentList $workerArgs -WindowStyle Hidden -RedirectStandardOutput $script:stdout -RedirectStandardError $script:stderr -PassThru
            # Keep the process handle open so Windows PowerShell retains ExitCode.
            $script:workerHandle = $script:worker.Handle
        }
        $lines = @()
        if (Test-Path -LiteralPath $script:stdout) { $lines += @(Get-Content -LiteralPath $script:stdout -Encoding UTF8 -Tail 35) }
        if (Test-Path -LiteralPath $script:stderr) { $lines += @(Get-Content -LiteralPath $script:stderr -Encoding UTF8 -Tail 10) }
        $details.Text = $lines -join "`r`n"
        $details.SelectionStart = $details.TextLength
        $details.ScrollToCaret()
        $step = @($lines | Where-Object { $_ -match '^\[[1-6]/6\]' })
        if ($step.Count) { $status.Text = $step[-1] }
        if ($script:worker.HasExited) {
            $script:worker.WaitForExit()
            $script:resultCode = $script:worker.ExitCode
            if ($null -eq $script:resultCode) { $script:resultCode = 1 }
            $timer.Stop()
            $progress.Style = 'Continuous'
            if ($script:resultCode -eq 0) {
                $progress.Value = 100
                $title.Text = '설치가 완료되었습니다'
                $status.Text = '이 창을 닫고 실행 파일을 더블클릭하세요.'
                $description.Text = 'PRISM이 열리면 Settings에서 사용할 계정에 로그인하세요.'
            } else {
                $title.Text = '설치를 완료하지 못했습니다'
                $status.Text = '아래 오류를 확인한 뒤 설치 파일을 다시 실행하세요.'
                $description.Text = '설치된 항목은 유지됩니다. 오류 기록은 app 폴더의 .setup에 있습니다.'
            }
            $form.ControlBox = $true
            $close.Enabled = $true
            if ($VerifyAutomation) { $form.Close() }
        }
    } catch {
        $timer.Stop()
        $title.Text = '설치를 시작하지 못했습니다'
        $status.Text = '압축을 쓰기 가능한 폴더에 풀었는지 확인하세요.'
        $details.Text = $_.Exception.Message
        $form.ControlBox = $true
        $close.Enabled = $true
        if ($VerifyAutomation) { $form.Close() }
    }
})
try {
    if ($PreviewImage) {
        $form.Show()
        [Windows.Forms.Application]::DoEvents()
        $bitmap = New-Object Drawing.Bitmap($form.Width, $form.Height)
        $form.DrawToBitmap($bitmap, (New-Object Drawing.Rectangle(0, 0, $form.Width, $form.Height)))
        $bitmap.Save($PreviewImage)
        $bitmap.Dispose()
        $form.Close()
        $script:resultCode = 0
    } else {
        $timer.Start()
        [void]$form.ShowDialog()
    }
} finally {
    $timer.Dispose()
    $form.Dispose()
    if ($script:worker) { $script:worker.Dispose() }
    if ($script:lockOwned) { $mutex.ReleaseMutex() }
    $mutex.Dispose()
}
exit $script:resultCode
