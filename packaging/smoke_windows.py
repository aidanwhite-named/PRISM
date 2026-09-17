"""Exercise the built EXE with isolated data, no CLI/Python on PATH, no model calls."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request


def main():
    original = Path(sys.argv[1] if len(sys.argv) > 1 else 'dist/PRISM').resolve()
    with tempfile.TemporaryDirectory(prefix='prism-release-') as temporary:
        root = Path(temporary)
        folder = root / '한글 설치 경로' / 'PRISM'
        shutil.copytree(original, folder)
        exe = str(folder / 'PRISM.exe')
        data = root / '사용자 data'
        env = {key: value for key, value in os.environ.items()
               if key.upper() in {'SYSTEMROOT', 'WINDIR', 'TEMP', 'TMP', 'COMSPEC', 'PATHEXT'}}
        env.update(PATH=str(Path(os.environ['SYSTEMROOT']) / 'System32'),
                   USERPROFILE=str(root / 'user'), APPDATA=str(root / 'roaming'),
                   LOCALAPPDATA=str(root / 'local'), PRISM_DATA_DIR=str(data),
                   PYTHONUTF8='1', PYTHONIOENCODING='utf-8')
        for key in ('USERPROFILE', 'APPDATA', 'LOCALAPPDATA'):
            Path(env[key]).mkdir(parents=True)
        requests = [
            {'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {}},
            {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list'},
        ]
        def mcp(environment):
            result = subprocess.run([exe, '--search-mcp'], input=''.join(json.dumps(r) + '\n' for r in requests),
                                    text=True, encoding='utf-8', capture_output=True, env=environment, timeout=30)
            assert result.returncode == 0, result.stderr
            rows = [json.loads(line) for line in result.stdout.splitlines()]
            assert rows[0]['result']['serverInfo']['name'] == 'prism-search'
            return rows[1]['result']['tools']
        assert mcp(env) == []
        print('PASS: frozen MCP stdio handshake; no tools outside a search run')
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        def get(url, headers=None, method=None):
            with opener.open(urllib.request.Request(url, headers=headers or {}, method=method), timeout=3) as response:
                return response.read()
        with socket.socket() as occupied:
            occupied.bind(('127.0.0.1', 0))
            occupied.listen()
            port = occupied.getsockname()[1]
            log_path = root / 'server.log'
            with log_path.open('w', encoding='utf-8') as log:
                process = subprocess.Popen([exe, '--no-browser', '--port', str(port)], env=env,
                                           stdout=log, stderr=log, creationflags=subprocess.CREATE_NO_WINDOW)
                try:
                    deadline = time.monotonic() + 60
                    while not (data / 'desktop.json').exists():
                        if process.poll() is not None or time.monotonic() > deadline:
                            raise AssertionError(log_path.read_text(encoding='utf-8'))
                        time.sleep(0.1)
                    state = json.loads((data / 'desktop.json').read_text(encoding='utf-8'))
                    assert state['port'] != port
                    url = f"http://127.0.0.1:{state['port']}"
                    health = json.loads(get(url + '/api/health'))
                    assert health['status'] == 'ok' and health['port'] == state['port']
                    html = get(url).decode()
                    assert '<html' in html and '/assets/' in html
                    import re
                    script = re.search(r'src="(/assets/[^\"]+\.js)"', html).group(1)
                    assert len(get(url + script)) > 100
                    prompts = json.loads(get(url + '/api/prompts'))
                    assert prompts, prompts
                    get(url + '/api/settings')
                    print('PASS: startup, port collision, health, UI assets, prompts and settings without Python/Node on PATH')
                    for method in ('GET', 'POST'):
                        try:
                            get(url + '/api/desktop/instance', {'X-PRISM-Client': '1'}, method)
                        except urllib.error.HTTPError as error:
                            assert error.code == 403
                        else:
                            raise AssertionError('Unauthenticated desktop access was accepted')
                    again = subprocess.run([exe, '--no-browser'], env=env, capture_output=True, timeout=15)
                    assert again.returncode == 0, again.stderr
                    assert process.poll() is None
                    print('PASS: duplicate execution and authenticated shutdown boundary')
                    active = dict(env, PRISM_SEARCH_WORK_DIR=str(data / 'runs' / 'smoke'))
                    Path(active['PRISM_SEARCH_WORK_DIR']).mkdir(parents=True)
                    assert mcp(active), 'Active MCP tools missing'
                    print('PASS: active frozen MCP tool discovery')
                    edited = data / 'prompts' / 'search_prompt.md'
                    edited.write_text('user-edited prompt', encoding='utf-8')
                    stopped = subprocess.run([exe, '--stop'], env=env, capture_output=True, timeout=15)
                    assert stopped.returncode == 0, stopped.stderr
                    assert process.wait(timeout=20) == 0, log_path.read_text(encoding='utf-8')
                    assert not (data / 'desktop.json').exists()
                finally:
                    if process.poll() is None:
                        process.kill()
                        process.wait()
            # Restart checks data preservation and lock release.
            with log_path.open('a', encoding='utf-8') as log:
                process = subprocess.Popen([exe, '--no-browser'], env=env, stdout=log, stderr=log,
                                           creationflags=subprocess.CREATE_NO_WINDOW)
                try:
                    deadline = time.monotonic() + 30
                    while not (data / 'desktop.json').exists():
                        if process.poll() is not None or time.monotonic() > deadline:
                            raise AssertionError(log_path.read_text(encoding='utf-8'))
                        time.sleep(0.1)
                    assert edited.read_text(encoding='utf-8') == 'user-edited prompt'
                    subprocess.run([exe, '--stop'], env=env, check=True, capture_output=True, timeout=15)
                    assert process.wait(timeout=20) == 0
                    print('PASS: restart, persistent edited prompts, graceful exit')
                finally:
                    if process.poll() is None:
                        process.kill()
                        process.wait()
    print('Release smoke checks passed. No paid model calls performed.')


if __name__ == '__main__':
    main()
