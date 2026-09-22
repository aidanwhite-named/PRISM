"""Compile the allowlisted source release into a per-user Windows installer."""
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import re
import subprocess
import tempfile
from zipfile import ZipFile, ZIP_DEFLATED

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--compiler', required=True, type=Path)
    args = parser.parse_args()
    version = re.search(r'__version__ = "([\d.]+)"', (ROOT / 'backend/app/__init__.py').read_text(encoding='utf-8')).group(1)
    release = ROOT / 'release'
    source_zip = release / f'PRISM-{version}-windows-x64.zip'
    with tempfile.TemporaryDirectory(prefix='installer-', dir=release) as temporary:
        staging = Path(temporary)
        with ZipFile(source_zip) as archive:
            for name in archive.namelist():
                if not (staging / name).resolve().is_relative_to(staging.resolve()):
                    raise ValueError('Archive path escaped staging directory')
            archive.extractall(staging)
        subprocess.run([str(args.compiler.resolve()), f'/DSourceDir={staging / "PRISM"}',
                        f'/DAppVersion={version}', str(ROOT / 'scripts/prism-installer.iss')], check=True)
    installer = release / f'PRISM-{version}-Setup-x64.exe'
    installer.with_suffix('.exe.sha256').write_text(
        f'{hashlib.sha256(installer.read_bytes()).hexdigest()}  {installer.name}\n', encoding='ascii')
    # Published ZIP has the SAME installer, not a second portable installation flow.
    download = release / f'PRISM-{version}-installer-windows-x64.zip'
    with ZipFile(download, 'w', ZIP_DEFLATED) as archive:
        archive.write(installer, installer.name)
        archive.write(ROOT / '사용안내.txt', '사용안내.txt')
    download.with_suffix('.zip.sha256').write_text(
        f'{hashlib.sha256(download.read_bytes()).hexdigest()}  {download.name}\n', encoding='ascii')
    print(f'Installer: {installer}')


if __name__ == '__main__':
    main()
