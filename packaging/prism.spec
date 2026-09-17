# Build with the isolated environment created by build-windows.ps1.
from pathlib import Path
from PyInstaller.utils.hooks import collect_submodules, copy_metadata

root = Path(SPECPATH).parent
data = [(str(root / 'frontend' / 'dist'), 'frontend/dist')]
for name in ('patent-analysis-master-prompt.md', 'search_prompt.md'):
    data.append((str(root / 'prompt' / name), 'prompt'))
for package in ('uvicorn', 'pypdf', 'arxiv', 'pyalex', 'pywinpty'):
    data += copy_metadata(package)

a = Analysis(
    [str(root / 'backend' / 'desktop.py')],
    pathex=[str(root / 'backend')],
    datas=data,
    hiddenimports=collect_submodules('uvicorn') + ['sqlalchemy.dialects.sqlite', 'winpty'],
    excludes=['torch', 'sentence_transformers', 'transformers', 'pytest', 'tkinter'],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='PRISM',
          console=True, debug=False, strip=False, upx=False)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='PRISM')
