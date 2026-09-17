"""Exercise installer control flow without changing system Python, Node or CLIs."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
POWERSHELL = str(Path(os.environ['SYSTEMROOT']) / 'System32/WindowsPowerShell/v1.0/powershell.exe')
STUB = r'''
function Update-PrismPath {}
function Test-PrismPython { param($File); return $true }
function Find-PrismPython { return 'python.exe' }
function Find-PrismCli {
    param($Name)
    if ($env:PRISM_TEST_EXISTING -eq '1') { return "$Name.exe" }
    return $null
}
function Install-PrismPackage { param($Id, $HelpUrl); throw "INSTALL-BLOCKED:$Id" }
function Invoke-Checked {
    param($File, $Arguments)
    Add-Content -LiteralPath $env:PRISM_TEST_LOG -Value "$File $Arguments"
    if ($env:PRISM_TEST_FAIL -eq '1' -and $Arguments -contains 'install') { throw 'PIP-FAILED' }
}
'''


class SetupTests(unittest.TestCase):
    def run_setup(self, cli, *, existing=False, fail=False):
        with tempfile.TemporaryDirectory(prefix='prism-setup-test-') as temporary:
            root = Path(temporary) / '한글 공백'
            (root / 'scripts').mkdir(parents=True)
            (root / 'frontend/dist').mkdir(parents=True)
            (root / 'frontend/dist/index.html').write_text('<html></html>')
            shutil.copyfile(ROOT / 'setup.ps1', root / 'setup.ps1')
            (root / 'scripts/windows-common.ps1').write_text(STUB, encoding='utf-8-sig')
            log = root / 'calls.txt'
            env = dict(os.environ, PRISM_TEST_EXISTING=str(int(existing)),
                       PRISM_TEST_FAIL=str(int(fail)), PRISM_TEST_LOG=str(log))
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

    def test_installer_failure_is_propagated(self):
        code, output, _ = self.run_setup('claude')
        self.assertEqual(code, 1)
        self.assertIn('INSTALL-BLOCKED:Anthropic.ClaudeCode', output)
        self.assertNotIn('setup complete', output)

    def test_missing_agy_is_not_replaced(self):
        code, output, _ = self.run_setup('agy')
        self.assertEqual(code, 1)
        self.assertIn('not a substitute', output)

    def test_external_exit_code_is_checked(self):
        script = ". '%s'; Invoke-Checked '%s' @('-c', 'exit(7)')" % (
            str(ROOT / 'scripts/windows-common.ps1').replace("'", "''"),
            __import__('sys').executable.replace("'", "''"))
        result = subprocess.run([POWERSHELL, '-NoProfile', '-Command', script], capture_output=True)
        self.assertNotEqual(result.returncode, 0)

    def test_default_setup_never_prompts_and_prepares_both_clis(self):
        source = (ROOT / 'setup.ps1').read_text(encoding='utf-8-sig')
        self.assertNotIn('Read-Host', source)
        self.assertIn("[string]$Cli = 'both'", source)

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
