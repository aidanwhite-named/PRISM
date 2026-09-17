"""Collect shipped dependency versions and available license texts."""
import importlib.metadata as metadata
import json
from pathlib import Path
import shutil
import sys

root = Path(__file__).resolve().parents[1]
out = root / 'dist' / 'PRISM' / 'licenses'
out.mkdir(parents=True, exist_ok=True)
inventory = []
build_only = {'pip', 'setuptools', 'pyinstaller', 'pyinstaller-hooks-contrib', 'altgraph', 'pefile', 'pywin32-ctypes', 'packaging'}
for dist in sorted(metadata.distributions(), key=lambda d: d.metadata['Name'].lower()):
    name = dist.metadata['Name']
    if name.lower() in build_only:
        continue
    inventory.append({'name': name, 'version': dist.version, 'ecosystem': 'python',
                      'license': dist.metadata.get('License-Expression') or dist.metadata.get('License', '')})
    for item in dist.files or []:
        if any(part.lower().startswith(('license', 'copying', 'notice', 'authors')) for part in item.parts):
            source = Path(dist.locate_file(item))
            if source.is_file():
                target = out / 'python' / name / str(item)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)

lock = json.loads((root / 'frontend' / 'package-lock.json').read_text(encoding='utf-8'))
for location, item in lock['packages'].items():
    if not location or item.get('dev'):
        continue
    package = root / 'frontend' / location
    details = json.loads((package / 'package.json').read_text(encoding='utf-8'))
    inventory.append({'name': details['name'], 'version': details['version'], 'ecosystem': 'npm',
                      'license': details.get('license', '')})
    for source in package.iterdir():
        if source.is_file() and source.name.lower().startswith(('license', 'copying', 'notice')):
            target = out / 'npm' / details['name'] / source.name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)

# CPython's full license includes notices for bundled third-party libraries.
python_license = Path(sys.base_prefix) / 'LICENSE.txt'
if not python_license.is_file():
    raise RuntimeError('CPython LICENSE.txt missing from build interpreter')
shutil.copy2(python_license, out / 'Python-LICENSE.txt')
(out / 'dependencies.json').write_text(json.dumps(inventory, ensure_ascii=False, indent=2), encoding='utf-8')
(out.parent / 'THIRD-PARTY-NOTICES.txt').write_text(
    'PRISM includes CPython and third-party Python/JavaScript libraries.\n'
    'See licenses/dependencies.json for versions and licenses/ for license texts.\n'
    'AI CLIs and their credentials are not included.\n', encoding='utf-8')
