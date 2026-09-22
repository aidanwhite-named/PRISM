"""Install and launch a ZIP in an isolated directory; no CLI install or paid AI calls."""
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from zipfile import ZipFile


def main():
    archive = Path(sys.argv[1]).resolve()
    powershell = str(Path(os.environ['SYSTEMROOT']) / 'System32/WindowsPowerShell/v1.0/powershell.exe')
    with tempfile.TemporaryDirectory(prefix='prism-source-smoke-') as temporary:
        base = Path(temporary) / '한글 배포 경로'
        base.mkdir()
        with ZipFile(archive) as zipped:
            names = zipped.namelist()
            assert not any(any(part in name.split('/') for part in ('.venv', 'node_modules', '.git', '__pycache__')) for name in names)
            assert not any(name.endswith(('.db', '.pyc', '.exe', '.env')) for name in names)
            zipped.extractall(base)
        assert {name.split('/')[1] for name in names} == {'설치.cmd', '실행.cmd', '제거.cmd', 'app'}
        root = base / 'PRISM/app'
        env = dict(os.environ, PRISM_DATA_DIR=str(base / 'user data'),
                   PRISM_PROMPT_DIR=str(root / 'prompt'), PYTHONUTF8='1')
        log_path = base / 'setup.log'
        with log_path.open('wb') as log:
            result = subprocess.run([powershell, '-NoProfile', '-ExecutionPolicy', 'Bypass',
                                     '-File', str(root / 'setup.ps1'), '-Cli', 'skip'],
                                    cwd=base, env=env, stdout=log, stderr=log, timeout=300)
        if result.returncode:
            raise AssertionError(log_path.read_text(encoding='utf-8', errors='replace'))
        print('PASS: fresh ZIP setup, isolated venv and dependency imports', flush=True)
        # No global Python/Node/npm on PATH. The launcher must use its own venv.
        env['PATH'] = str(Path(os.environ['SYSTEMROOT']) / 'System32')
        # Update-PrismPath normally recovers installed CLI paths; replace only that
        # function for this test to keep the no-PATH constraint throughout startup.
        common = root / 'scripts/windows-common.ps1'
        with common.open('a', encoding='utf-8') as stream:
            stream.write('\nfunction Update-PrismPath {}\n')
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            port = listener.getsockname()[1]
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with (base / 'server.log').open('wb') as log:
            process = subprocess.Popen([powershell, '-NoProfile', '-ExecutionPolicy', 'Bypass',
                                        '-File', str(root / 'start-prism.ps1'), '-NoBrowser', '-Port', str(port)],
                                       cwd=base, env=env, stdout=log, stderr=log)
            try:
                deadline = time.monotonic() + 45
                url = f'http://127.0.0.1:{port}'
                while True:
                    try:
                        with opener.open(url + '/api/health', timeout=2) as response:
                            assert json.load(response)['status'] == 'ok'
                        break
                    except OSError:
                        if process.poll() is not None or time.monotonic() > deadline:
                            raise AssertionError((base / 'server.log').read_text(errors='replace'))
                        time.sleep(0.2)
                with opener.open(url) as response:
                    html = response.read().decode()
                script = re.search(r'src="(/assets/[^\"]+\.js)"', html).group(1)
                with opener.open(url + script) as response:
                    assert len(response.read()) > 1000
                with opener.open(url + '/api/prompts') as response:
                    assert json.load(response)
                print('PASS: source launcher, UI assets and prompts without global Python/Node PATH', flush=True)
            finally:
                if process.poll() is None:
                    subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'],
                                   check=True, capture_output=True)
                process.wait(timeout=15)
    print('Source release smoke checks passed.', flush=True)


if __name__ == '__main__':
    main()
