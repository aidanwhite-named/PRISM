"""Exercise installer control flow without changing system Python, Node or CLIs."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
POWERSHELL = str(Path(os.environ['SYSTEMROOT']) / 'System32/WindowsPowerShell/v1.0/powershell.exe')
STUB = r'''
function Update-PrismPath {}
function Save-PrismDependency { param($File, $Name) }
function Test-PrismPython { param($File); return $true }
function Find-PrismPython { return 'python.exe' }
function Find-PrismCli {
    param($Name)
    if ($env:PRISM_TEST_EXISTING -eq '1') { return "$Name.exe" }
    if ($Name -eq 'codex' -and $script:codexInstalled) { return 'codex.exe' }
    if ($Name -eq 'agy' -and $script:agyInstalled) { return 'agy.exe' }
    return $null
}
function Install-PrismPackage { param($Id, $HelpUrl); throw "INSTALL-BLOCKED:$Id" }
function Install-PrismAgy {
    Add-Content -LiteralPath $env:PRISM_TEST_LOG -Value 'INSTALL-AGY'
    if ($env:PRISM_TEST_AGY_FAIL -eq '1') { throw 'AGY-INSTALL-FAILED' }
    $script:agyInstalled = $true
}
function Invoke-Checked {
    param($File, $Arguments)
    Add-Content -LiteralPath $env:PRISM_TEST_LOG -Value "$File $Arguments"
    if ($Arguments -contains '@openai/codex') {
        Add-Content -LiteralPath $env:PRISM_TEST_LOG -Value "NPM_NODE_OPTIONS=$env:NODE_OPTIONS"
        $script:codexInstalled = $true
    }
    if ($Arguments -contains '--version') {
        Add-Content -LiteralPath $env:PRISM_TEST_LOG -Value "CLI_NODE_OPTIONS=$env:NODE_OPTIONS"
    }
    if ($env:PRISM_TEST_FAIL -eq '1' -and $Arguments -contains 'install') { throw 'PIP-FAILED' }
}
'''


class SetupTests(unittest.TestCase):
    def test_winget_bootstrap_and_package_continuation(self):
        for scenario in ('existing', 'missing', 'download-failed', 'blocked', 'no-dependencies', 'not-starting'):
            with self.subTest(scenario=scenario), tempfile.TemporaryDirectory(prefix='prism-winget-test-') as temporary:
                root = Path(temporary) / "한글 공백 '"
                root.mkdir()
                log = root / 'calls.txt'
                env = dict(os.environ, TEMP=str(root), TMP=str(root),
                           PRISM_TEST_SCENARIO=scenario, PRISM_TEST_LOG=str(log))
                common = str(ROOT / 'scripts/windows-common.ps1').replace("'", "''")
                script = ". '%s'\n" % common + r'''
$script:installed = $false
function Find-PrismWinget {
    if ($env:PRISM_TEST_SCENARIO -eq 'existing' -or
        ($script:installed -and $env:PRISM_TEST_SCENARIO -ne 'not-starting')) { return 'winget.exe' }
    return $null
}
function Invoke-RestMethod {
    param($Uri, [switch]$UseBasicParsing)
    if ($Uri -ne 'https://api.github.com/repos/microsoft/winget-cli/releases/latest') { throw 'Wrong release source' }
    Add-Content -LiteralPath $env:PRISM_TEST_LOG -Value 'RELEASE'
    return @{assets = @(
        @{name='Microsoft.DesktopAppInstaller_8wekyb3d8bbwe.msixbundle'; browser_download_url='https://github.com/microsoft/winget-cli/releases/download/test/bundle'},
        @{name='DesktopAppInstaller_Dependencies.zip'; browser_download_url='https://github.com/microsoft/winget-cli/releases/download/test/dependencies'}
    )}
}
function Invoke-WebRequest {
    param($Uri, [switch]$UseBasicParsing, $OutFile)
    if (-not $UseBasicParsing) { throw 'Interactive parsing is forbidden' }
    if ($env:PRISM_TEST_SCENARIO -eq 'download-failed') { throw 'DOWNLOAD-FAILED' }
    Set-Content -LiteralPath $OutFile -Value 'fixture'
}
function Expand-Archive {
    param($LiteralPath, $DestinationPath)
    foreach ($architecture in @('x64', 'x86', 'arm64')) {
        $directory = Join-Path $DestinationPath $architecture
        New-Item -ItemType Directory -Path $directory -Force | Out-Null
        if ($env:PRISM_TEST_SCENARIO -ne 'no-dependencies') {
            Set-Content -LiteralPath (Join-Path $directory 'framework.appx') -Value 'fixture'
        }
    }
}
function Add-AppxPackage {
    param($Path, $DependencyPath, $ErrorAction)
    if (-not (Test-Path -LiteralPath $Path)) { throw 'Bundle missing' }
    if (@($DependencyPath).Count -ne 1 -or $DependencyPath[0] -notlike '*\x64\framework.appx') { throw 'Wrong architecture' }
    Add-Content -LiteralPath $env:PRISM_TEST_LOG -Value 'APPX'
    if ($env:PRISM_TEST_SCENARIO -eq 'blocked') { throw 'POLICY-BLOCKED' }
    $script:installed = $true
}
function Invoke-Checked {
    param($File, $Arguments)
    Add-Content -LiteralPath $env:PRISM_TEST_LOG -Value "$File $Arguments"
}
$oldProgress = $ProgressPreference
$oldTls = [Net.ServicePointManager]::SecurityProtocol
$code = 0
try {
    Install-PrismPackage 'Python.Python.3.11' 'https://www.python.org/' -UserScope
    Install-PrismPackage 'OpenJS.NodeJS.LTS' 'https://nodejs.org/'
} catch {
    Write-Output $_.Exception.Message
    $code = 1
}
if ($ProgressPreference -ne $oldProgress -or [Net.ServicePointManager]::SecurityProtocol -ne $oldTls) { throw 'Settings not restored' }
exit $code
'''
                result = subprocess.run([POWERSHELL, '-NoProfile', '-ExecutionPolicy', 'Bypass', '-Command', script],
                                        env=env, capture_output=True, timeout=30)
                output = result.stdout.decode(errors='replace') + result.stderr.decode(errors='replace')
                calls = log.read_text() if log.exists() else ''
                self.assertEqual(list(root.glob('prism-winget-*')), [], output)
                if scenario in ('existing', 'missing'):
                    self.assertEqual(result.returncode, 0, output)
                    self.assertIn('winget.exe install --id Python.Python.3.11', calls)
                    self.assertIn('--scope user', calls)
                    self.assertIn('winget.exe install --id OpenJS.NodeJS.LTS', calls)
                    self.assertEqual(calls.count('APPX'), int(scenario == 'missing'))
                    self.assertEqual(calls.count('RELEASE'), int(scenario == 'missing'))
                else:
                    self.assertEqual(result.returncode, 1, output)
                    self.assertIn('WinGet automatic installation failed', output)
                    self.assertNotIn('winget.exe install', calls)
                    expected = {'download-failed': 'DOWNLOAD-FAILED', 'blocked': 'POLICY-BLOCKED',
                                'no-dependencies': 'dependency packages are missing',
                                'not-starting': 'could not be started'}[scenario]
                    self.assertIn(expected, output)

    def run_setup(self, cli, *, existing=False, fail=False, agy_fail=False):
        with tempfile.TemporaryDirectory(prefix='prism-setup-test-') as temporary:
            root = Path(temporary) / '한글 공백'
            (root / 'scripts').mkdir(parents=True)
            (root / 'frontend/dist').mkdir(parents=True)
            (root / 'frontend/dist/index.html').write_text('<html></html>')
            shutil.copyfile(ROOT / 'setup.ps1', root / 'setup.ps1')
            (root / 'scripts/windows-common.ps1').write_text(STUB, encoding='utf-8-sig')
            log = root / 'calls.txt'
            env = dict(os.environ, PRISM_TEST_EXISTING=str(int(existing)),
                       PRISM_TEST_FAIL=str(int(fail)), PRISM_TEST_LOG=str(log),
                       PRISM_TEST_AGY_FAIL=str(int(agy_fail)),
                       NODE_OPTIONS='--max-old-space-size=1024')
            result = subprocess.run([POWERSHELL, '-NoProfile', '-ExecutionPolicy', 'Bypass',
                                     '-File', str(root / 'setup.ps1'), '-Cli', cli],
                                    env=env, capture_output=True, timeout=30)
            return result.returncode, result.stdout.decode(errors='replace'), log.read_text() if log.exists() else ''

    def test_skip_installs_backend_and_reports_pending_cli(self):
        code, output, calls = self.run_setup('skip')
        self.assertEqual(code, 0, output)
        self.assertIn('requirements.txt', calls)
        self.assertIn('pip check', calls)
        self.assertIn('still required', output)
        self.assertNotIn('--version', calls)

    def test_bootstrap_uses_system_certificates_and_utf8(self):
        code, output, calls = self.run_setup('skip')
        self.assertEqual(code, 0, output)
        installs = [line for line in calls.splitlines() if 'pip install' in line]
        self.assertEqual(len(installs), 2)
        self.assertIn('--use-feature=truststore', installs[0])
        self.assertIn('pip>=24.2', installs[0])
        for line in installs:
            self.assertIn('-X utf8 -m pip install', line)
            self.assertNotIn('--trusted-host', line)

    def test_requirements_decode_with_korean_windows_locale(self):
        # Exercise the bootstrap pip shipped with Python, not the upgraded pip
        # in the developer venv. No package download or installation is needed.
        script = r'''
import ensurepip
from pathlib import Path
import sys
from unittest.mock import patch
wheel = next((Path(ensurepip.__file__).parent / '_bundled').glob('pip-*.whl'))
sys.path.insert(0, str(wheel))
from pip._internal.req.req_file import get_file_content
with patch('locale.getpreferredencoding', return_value='cp949'):
    for path in Path(sys.argv[1]).glob('requirements*.txt'):
        _, content = get_file_content(str(path), session=None)
        assert content == path.read_bytes().decode('utf-8-sig'), str(path)
'''
        result = subprocess.run([sys.executable, '-X', 'utf8=0', '-c', script,
                                 str(ROOT / 'backend')], capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_dependency_failure_does_not_report_success(self):
        code, output, calls = self.run_setup('both', fail=True)
        self.assertEqual(code, 1)
        self.assertIn('PIP-FAILED', output)
        self.assertNotIn('setup complete', output)
        self.assertNotIn('--version', calls)

    def test_existing_clis_are_reused(self):
        code, output, calls = self.run_setup('both', existing=True)
        self.assertEqual(code, 0, output)
        self.assertIn('claude.exe --version', calls)
        self.assertIn('codex.exe --version', calls)

    def test_npm_uses_system_ca_and_restores_node_options(self):
        code, output, calls = self.run_setup('codex')
        self.assertEqual(code, 0, output)
        self.assertIn('NPM_NODE_OPTIONS=--max-old-space-size=1024 --use-system-ca', calls)
        self.assertIn('CLI_NODE_OPTIONS=--max-old-space-size=1024\n', calls)
        self.assertNotIn('strict-ssl', calls)

    def test_installer_failure_is_propagated(self):
        code, output, _ = self.run_setup('claude')
        self.assertEqual(code, 1)
        self.assertIn('INSTALL-BLOCKED:Anthropic.ClaudeCode', output)
        self.assertNotIn('setup complete', output)

    def test_missing_agy_is_installed(self):
        code, output, calls = self.run_setup('agy')
        self.assertEqual(code, 0, output)
        self.assertIn('INSTALL-AGY', calls)
        self.assertIn('agy.exe --version', calls)

    def test_agy_install_failure_is_propagated(self):
        code, output, calls = self.run_setup('agy', agy_fail=True)
        self.assertEqual(code, 1, output)
        self.assertIn('AGY-INSTALL-FAILED', output)
        self.assertNotIn('setup complete', output)
        self.assertNotIn('agy.exe --version', calls)

    def test_all_reuses_three_existing_clis(self):
        code, output, calls = self.run_setup('all', existing=True)
        self.assertEqual(code, 0, output)
        for name in ('claude', 'codex', 'agy'):
            self.assertIn(f'{name}.exe --version', calls)
        self.assertNotIn('INSTALL-AGY', calls)

    def test_external_exit_code_is_checked(self):
        script = ". '%s'; Invoke-Checked '%s' @('-c', 'exit(7)')" % (
            str(ROOT / 'scripts/windows-common.ps1').replace("'", "''"),
            __import__('sys').executable.replace("'", "''"))
        result = subprocess.run([POWERSHELL, '-NoProfile', '-Command', script], capture_output=True)
        self.assertNotEqual(result.returncode, 0)

    def test_default_setup_never_prompts_and_prepares_all_clis(self):
        source = (ROOT / 'setup.ps1').read_text(encoding='utf-8-sig')
        self.assertNotIn('Read-Host', source)
        self.assertIn("[string]$Cli = 'all'", source)

    def test_agy_official_installer_exit_status_and_cleanup(self):
        for code in (0, 7):
            with self.subTest(code=code), tempfile.TemporaryDirectory(prefix='prism-agy-test-') as temporary:
                root = Path(temporary) / "한글 공백 '"
                root.mkdir()
                env = dict(os.environ, TEMP=str(root), TMP=str(root), PRISM_TEST_EXIT=str(code))
                common = str(ROOT / 'scripts/windows-common.ps1').replace("'", "''")
                script = ". '%s'; " % common + r'''
function Invoke-WebRequest {
    param($Uri, [switch]$UseBasicParsing, $OutFile)
    if ($Uri -ne 'https://antigravity.google/cli/install.ps1' -or -not $UseBasicParsing) { throw 'Wrong installer source' }
    Set-Content -LiteralPath $OutFile -Encoding UTF8 -Value ('exit ' + $env:PRISM_TEST_EXIT)
}
function Update-PrismPath {}
Install-PrismAgy
'''
                result = subprocess.run([POWERSHELL, '-NoProfile', '-Command', script],
                                        env=env, capture_output=True, timeout=30)
                self.assertEqual(result.returncode == 0, code == 0, result.stderr)
                self.assertEqual(list(root.glob('prism-agy-*.ps1')), [])

    def test_install_window_waits_for_worker_and_preserves_result(self):
        for code in (0, 7):
            with self.subTest(code=code), tempfile.TemporaryDirectory(prefix='prism-window-') as temporary:
                root = Path(temporary) / '한글 공백'
                (root / 'scripts').mkdir(parents=True)
                shutil.copyfile(ROOT / 'scripts/install-window.ps1', root / 'scripts/install-window.ps1')
                (root / 'setup.ps1').write_text(f"Write-Host '[1/5] testing'; Start-Sleep -Milliseconds 600; exit {code}", encoding='utf-8-sig')
                result = subprocess.run([POWERSHELL, '-NoProfile', '-STA', '-WindowStyle', 'Hidden',
                                         '-ExecutionPolicy', 'Bypass', '-File', str(root / 'scripts/install-window.ps1'),
                                         '-VerifyAutomation'], capture_output=True, timeout=20)
                self.assertEqual(result.returncode, code, result.stderr)
                self.assertIn('testing', (root / '.setup/install-output.log').read_text())


if __name__ == '__main__':
    unittest.main()
