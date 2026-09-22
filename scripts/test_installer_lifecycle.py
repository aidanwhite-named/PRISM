"""Compile real Inno install/update/uninstall fixtures; never touch real PRISM data."""
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import uuid
import winreg

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--compiler', required=True)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='prism-lifecycle-') as temporary:
        base = Path(temporary)
        source = base / 'stage/PRISM'
        app = source / 'app'
        for folder in ('scripts', 'prompt', 'backend'):
            (app / folder).mkdir(parents=True)
        for name in ('windows-common.ps1', 'uninstall-cleanup.ps1'):
            shutil.copyfile(ROOT / 'scripts' / name, app / 'scripts' / name)
        # Only dependency installation is mocked; copying, upgrades, registry and
        # uninstall cleanup all run through the actual compiled installer.
        (app / 'setup.ps1').write_text(
            "param($OwnershipFile, $Cli)\nif ($env:PRISM_FIXTURE_FAIL -eq '1') { exit 1 }; exit 0\n")
        for name in ('설치.cmd', '실행.cmd', '제거.cmd'):
            shutil.copyfile(ROOT / name, source / name)
        for name in ('search_prompt.md', 'patent-analysis-master-prompt.md'):
            (app / 'prompt' / name).write_text('default prompt')
        install = base / '한글 설치 경로'
        local = base / 'local'
        data = local / 'PRISM'
        data.mkdir(parents=True)
        (data / 'prism.db').write_text('history sentinel')
        profile = base / 'profile'
        (profile / '.codex').mkdir(parents=True)
        (profile / '.codex/session.txt').write_text('unrelated session')
        env = dict(os.environ, LOCALAPPDATA=str(local), USERPROFILE=str(profile))
        for name in ('PRISM_DATA_DIR', 'CODEX_HOME', 'CLAUDE_CONFIG_DIR'):
            env.pop(name, None)
        guid = str(uuid.uuid4()).upper()
        common = ['/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/NOICONS', '/CLI=skip']

        def build(version):
            (app / 'version.txt').write_text(version)
            result = subprocess.run([str(Path(args.compiler).resolve()), f'/DSourceDir={source}',
                                     f'/DAppVersion={version}', f'/DAppGuid={guid}',
                                     str(ROOT / 'scripts/prism-installer.iss')], capture_output=True, timeout=45)
            assert result.returncode == 0, result.stdout + result.stderr
            return base / f'PRISM-{version}-Setup-x64.exe'

        def run(executable, arguments, expected=0, extra_env=None):
            result = subprocess.run([str(executable), *arguments], env=extra_env or env, timeout=60)
            logs = '\n'.join(p.read_text(encoding='utf-8-sig', errors='replace') for p in base.glob('*.log'))
            assert result.returncode == expected, (executable, result.returncode, expected, logs)

        first = build('2.0.5')
        run(first, [*common, f'/DIR={install}', f'/LOG={base / "first.log"}'])
        assert (install / 'unins000.exe').exists()
        (install / 'app/prompt/search_prompt.md').write_text('my edited prompt')
        venv = install / 'app/backend/.venv'
        venv.mkdir()
        (venv / 'keep.txt').write_text('reuse environment')
        second = build('2.0.6')
        # No /DIR: the same AppId must discover and reuse the prior installation.
        run(second, [*common, f'/LOG={base / "update.log"}'])
        assert (install / 'app/version.txt').read_text() == '2.0.6'
        assert (install / 'app/prompt/search_prompt.md').read_text() == 'my edited prompt'
        assert (venv / 'keep.txt').exists()
        assert (data / 'prism.db').read_text() == 'history sentinel'
        run(first, common, expected=1)
        assert (install / 'app/version.txt').read_text() == '2.0.6'
        run(second, common, expected=1, extra_env=dict(env, PRISM_FIXTURE_FAIL='1'))
        print('PASS: install, same-location update, prompt/history/venv preservation, downgrade block, setup failure exit', flush=True)
        run(install / 'unins000.exe', ['/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', f'/LOG={base / "uninstall.log"}'])
        assert not data.exists()
        assert not venv.exists()
        assert not (install / 'app/prompt').exists()
        assert (profile / '.codex/session.txt').exists()
        key = f'Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\{{{guid}}}_is1'
        try:
            winreg.OpenKey(winreg.HKEY_CURRENT_USER, key)
        except FileNotFoundError:
            pass
        else:
            raise AssertionError('Uninstall registry key remains')
        for _ in range(30):
            if not install.exists():
                break
            time.sleep(.1)
        assert not install.exists(), list(install.rglob('*'))
        print('PASS: actual uninstaller removes installed files, generated environment, data and registry; shared profiles survive', flush=True)


if __name__ == '__main__':
    main()
