"""Build a source-runtime ZIP from an explicit allowlist, never a working-tree copy."""
from __future__ import annotations

import hashlib
import re
import subprocess
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

ROOT = Path(__file__).resolve().parents[1]
FILES = (
    '처음설치.cmd', 'PRISM실행.cmd', 'setup.ps1', 'start-prism.ps1',
    'scripts/windows-common.ps1', '사용안내.txt',
    'backend/requirements.txt',
)
PROMPTS = ('patent-analysis-master-prompt.md', 'search_prompt.md')


def release_entries(root: Path) -> dict[str, bytes]:
    entries = {name: (root / name).read_bytes() for name in FILES}
    # Only tracked application code: no venv, tests, uploaded documents, DBs or credentials.
    tracked = subprocess.check_output(
        ['git', 'ls-files', '-z', '--', 'backend/app'], cwd=root,
    ).decode('utf-8').split('\0')
    for name in tracked:
        if not name:
            continue
        if not name.endswith('.py'):
            raise ValueError(f'Review new runtime resource before packaging: {name}')
        path = root / name
        if path.is_symlink():
            raise ValueError(f'Symlink is not allowed: {name}')
        entries[name] = path.read_bytes()
    if 'backend/app/main.py' not in entries:
        raise ValueError('Tracked backend code is missing.')
    dist = root / 'frontend/dist'
    if not (dist / 'index.html').is_file():
        raise ValueError('Build the frontend before creating a release.')
    for path in sorted(dist.rglob('*')):
        if path.is_symlink():
            raise ValueError(f'Symlink is not allowed: {path}')
        if path.is_file():
            if path.suffix not in {'.html', '.js', '.css', '.svg', '.png', '.ico', '.webp', '.woff', '.woff2', '.txt'}:
                raise ValueError(f'Review unexpected frontend asset: {path}')
            entries[path.relative_to(root).as_posix()] = path.read_bytes()
    # Prompt edits are user data. Ship committed defaults, not local edited templates.
    for name in PROMPTS:
        relative = f'prompt/{name}'
        entries[relative] = subprocess.check_output(['git', 'show', f'HEAD:{relative}'], cwd=root)
    return entries


def main() -> None:
    entries = release_entries(ROOT)
    version = re.search(r'__version__\s*=\s*"([0-9]+\.[0-9]+\.[0-9]+)"',
                        entries['backend/app/__init__.py'].decode('utf-8')).group(1)
    output = ROOT / 'release'
    output.mkdir(exist_ok=True)
    archive = output / f'PRISM-{version}-windows-x64.zip'
    temporary = archive.with_suffix('.zip.tmp')
    with ZipFile(temporary, 'w', ZIP_DEFLATED) as zipped:
        for name, content in sorted(entries.items()):
            zipped.writestr(f'PRISM/{name}', content)
    temporary.replace(archive)
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    archive.with_suffix('.zip.sha256').write_text(f'{digest}  {archive.name}\n', encoding='ascii')
    print(f'Created {archive} ({archive.stat().st_size:,} bytes, {len(entries)} files)')


if __name__ == '__main__':
    main()
