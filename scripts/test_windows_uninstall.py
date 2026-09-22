"""Destructive cleanup is exercised only under an isolated fake Windows profile."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
PS = str(Path(os.environ['SYSTEMROOT']) / 'System32/WindowsPowerShell/v1.0/powershell.exe')


class CleanupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='prism-uninstall-test-')
        self.base = Path(self.tmp.name)
        self.app = self.base / 'installed/app'
        for name in ('scripts', '.setup', 'backend/.venv', 'prompt'):
            (self.app / name).mkdir(parents=True)
        for name in ('uninstall-cleanup.ps1', 'windows-common.ps1'):
            shutil.copyfile(ROOT / 'scripts' / name, self.app / 'scripts' / name)
        self.profile = self.base / 'profile'
        self.local = self.base / 'local'
        self.data = self.local / 'PRISM'
        self.data.mkdir(parents=True)
        (self.data / 'prism.db').write_text('history')
        for name in ('.codex', '.claude', '.gemini/antigravity-cli', '.gemini/config'):
            folder = self.profile / name
            folder.mkdir(parents=True)
            (folder / 'session.txt').write_text('shared history')
        self.env = dict(os.environ, LOCALAPPDATA=str(self.local), USERPROFILE=str(self.profile))
        for name in ('PRISM_DATA_DIR', 'CODEX_HOME', 'CLAUDE_CONFIG_DIR'):
            self.env.pop(name, None)

    def tearDown(self):
        self.tmp.cleanup()

    def run_cleanup(self, *args):
        return subprocess.run([PS, '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
                               str(self.app / 'scripts/uninstall-cleanup.ps1'), '-NonInteractive', *args],
                              env=self.env, capture_output=True, timeout=30)

    def test_default_removes_prism_data_but_preserves_shared_profiles(self):
        result = self.run_cleanup()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.data.exists())
        self.assertFalse((self.app / 'backend/.venv').exists())
        self.assertFalse((self.app / 'prompt').exists())
        self.assertTrue((self.profile / '.codex/session.txt').exists())
        self.assertTrue((self.profile / '.gemini/config/session.txt').exists())

    def test_shared_profile_cleanup_requires_explicit_option(self):
        result = self.run_cleanup('-RemoveSharedProfiles')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((self.profile / '.codex').exists())
        self.assertFalse((self.profile / '.claude').exists())
        self.assertFalse((self.profile / '.gemini/antigravity-cli').exists())
        self.assertFalse((self.profile / '.gemini/config').exists())

    def test_preview_only_includes_owned_cli_and_optional_owned_runtimes(self):
        (self.app / '.setup/dependencies.json').write_text('["codex", "python"]')
        result = self.run_cleanup('-Preview')
        self.assertEqual(result.returncode, 0, result.stderr)
        plan = json.loads(result.stdout)
        self.assertEqual(plan['dependencies'], ['codex'])
        result = self.run_cleanup('-Preview', '-RemoveOwnedRuntimes')
        self.assertEqual(json.loads(result.stdout)['dependencies'], ['codex', 'python'])
        self.assertTrue((self.data / 'prism.db').exists())

    def test_custom_data_path_is_not_silently_deleted_or_ignored(self):
        self.env['PRISM_DATA_DIR'] = str(self.base)
        result = self.run_cleanup()
        self.assertEqual(result.returncode, 1)
        self.assertTrue((self.data / 'prism.db').exists())

    def test_default_unregisters_only_prism_mcp(self):
        config = self.profile / '.gemini/config/mcp_config.json'
        config.write_text(json.dumps({'mcpServers': {'prism-search': {'args': ['-m', 'app.search_mcp_server']}, 'other': {'command': 'keep'}}}))
        settings = self.profile / '.gemini/antigravity-cli/settings.json'
        settings.write_text(json.dumps({'permissions': {'allow': ['mcp(prism-search/*)', 'read_url(example.com)']}}))
        result = self.run_cleanup()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(config.read_text(encoding='utf-8-sig'))['mcpServers'], {'other': {'command': 'keep'}})
        self.assertEqual(json.loads(settings.read_text(encoding='utf-8-sig'))['permissions']['allow'], ['read_url(example.com)'])

    def test_junction_aborts_before_any_deletion(self):
        outside = self.base / 'outside'
        outside.mkdir()
        (outside / 'keep.txt').write_text('unrelated')
        link = self.data / 'linked'
        script = "New-Item -ItemType Junction -Path $env:PRISM_TEST_LINK -Target $env:PRISM_TEST_TARGET | Out-Null"
        subprocess.run([PS, '-NoProfile', '-Command', script], check=True,
                       env=dict(self.env, PRISM_TEST_LINK=str(link), PRISM_TEST_TARGET=str(outside)), capture_output=True)
        try:
            result = self.run_cleanup()
            self.assertEqual(result.returncode, 1, result.stdout)
            self.assertTrue((outside / 'keep.txt').exists())
            self.assertTrue((self.data / 'prism.db').exists())
        finally:
            # Remove the link itself, never recurse into its target.
            os.rmdir(link)

    def test_ownership_record_survives_repeated_setup(self):
        ownership = self.app / '.setup/dependencies.json'
        script = ". $env:PRISM_TEST_COMMON; Save-PrismDependency $env:PRISM_TEST_OWNED 'codex'; Save-PrismDependency $env:PRISM_TEST_OWNED 'agy'; Save-PrismDependency $env:PRISM_TEST_OWNED 'codex'"
        result = subprocess.run([PS, '-NoProfile', '-Command', script],
            env=dict(self.env, PRISM_TEST_COMMON=str(self.app / 'scripts/windows-common.ps1'), PRISM_TEST_OWNED=str(ownership)), capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(ownership.read_text(encoding='utf-8-sig')), ['codex', 'agy'])


if __name__ == '__main__':
    unittest.main()
