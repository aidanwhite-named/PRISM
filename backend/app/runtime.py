"""Paths and child commands shared by source and frozen Windows builds."""
from __future__ import annotations

import os
import sys
from pathlib import Path


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def resource_root() -> Path:
    if is_frozen():
        return Path(sys._MEIPASS)
    return Path(__file__).resolve().parents[2]


def search_server_command() -> dict:
    if is_frozen():
        return {"command": sys.executable, "args": ["--search-mcp"], "env": {}}
    return {
        "command": sys.executable,
        "args": ["-m", "app.search_mcp_server"],
        "env": {"PYTHONPATH": str(Path(__file__).resolve().parents[1])},
    }


_dll_handles = []


def prepare_frozen_runtime() -> None:
    """Keep Python DLL lookup local without passing SetDllDirectory to CLIs."""
    if not is_frozen() or sys.platform != "win32":
        return
    import ctypes

    root = resource_root()
    # add_dll_directory affects this process only, unlike SetDllDirectoryW.
    for folder in {root, *(p.parent for p in root.rglob("*.dll"))}:
        _dll_handles.append(os.add_dll_directory(str(folder)))
    if not ctypes.windll.kernel32.SetDllDirectoryW(None):
        raise ctypes.WinError()


def seed_prompts(destination: Path) -> None:
    """Seed only bundled defaults; never overwrite a user's edited template."""
    destination.mkdir(parents=True, exist_ok=True)
    for name in ("patent-analysis-master-prompt.md", "search_prompt.md"):
        target = destination / name
        if target.exists():
            continue
        content = (resource_root() / "prompt" / name).read_bytes()
        try:
            with target.open("xb") as stream:
                stream.write(content)
        except FileExistsError:
            pass
