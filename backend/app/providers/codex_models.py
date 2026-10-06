"""Discover picker models without starting a thread or a billable model turn."""

from __future__ import annotations

import asyncio
import contextlib
import json
import subprocess
import sys
from pathlib import Path

from .resolver import ResolvedExecutable

# Keep the last successful catalogue when a later probe cannot reach Codex.
# Key by executable and home so a different installation/account home is isolated.
_last_catalogs: dict[tuple[str, str], dict] = {}


def capabilities(rows: list, *, cached: bool = False) -> dict:
    models: list[str] = []
    by_model: dict[str, list[str]] = {}
    defaults: dict[str, str] = {}
    default_model = ""
    for row in rows:
        if not isinstance(row, dict):
            continue
        if cached:
            if row.get("visibility") != "list":
                continue
            model = row.get("slug")
            levels = row.get("supported_reasoning_levels", [])
            default = row.get("default_reasoning_level")
            effort_key = "effort"
        else:
            if row.get("hidden") is True:
                continue
            model = row.get("model")
            levels = row.get("supportedReasoningEfforts", [])
            default = row.get("defaultReasoningEffort")
            effort_key = "reasoningEffort"
        if not isinstance(model, str) or not model.strip() or model in models:
            continue
        models.append(model)
        if not cached and row.get("isDefault") is True:
            default_model = model
        efforts: list[str] = []
        for level in levels if isinstance(levels, list) else []:
            effort = level.get(effort_key) if isinstance(level, dict) else None
            if isinstance(effort, str) and effort and effort not in efforts:
                efforts.append(effort)
        by_model[model] = efforts
        if isinstance(default, str) and default in efforts:
            defaults[model] = default
    return {
        "models": models,
        "default_model": default_model,
        "reasoning_efforts_by_model": by_model,
        "reasoning_defaults_by_model": defaults,
        "reasoning_efforts": list(dict.fromkeys(
            effort for model in models for effort in by_model[model]
        )),
    }


async def list_models(
    resolved: ResolvedExecutable, env: dict[str, str], *, timeout: float = 12.0,
) -> list:
    """Bounded stdio JSON-RPC session; only initialize and model/list are sent."""
    process = None
    try:
        async with asyncio.timeout(timeout):
            process = await asyncio.create_subprocess_exec(
                *resolved.command(["app-server", "-c", 'model_provider="openai"']),
                env=env,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                limit=4 * 1024 * 1024,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
            )

            async def send(message: dict) -> None:
                process.stdin.write((json.dumps(message) + "\n").encode("utf-8"))
                await process.stdin.drain()

            async def response(request_id: int) -> dict:
                while True:
                    line = await process.stdout.readline()
                    if not line:
                        raise ValueError("Codex closed model discovery")
                    message = json.loads(line)
                    if not isinstance(message, dict) or message.get("id") != request_id:
                        continue
                    result = message.get("result")
                    if "error" in message or not isinstance(result, dict):
                        raise ValueError("Codex rejected model discovery")
                    return result

            await send({"id": 1, "method": "initialize", "params": {
                "clientInfo": {"name": "prism", "title": "PRISM", "version": "1.0"},
            }})
            await response(1)
            await send({"method": "initialized", "params": {}})
            rows: list = []
            cursor = None
            seen: set[str] = set()
            # Pagination is bounded even if a malformed server repeats cursors.
            for request_id in range(2, 22):
                params = {"limit": 100, "includeHidden": False}
                if cursor:
                    params["cursor"] = cursor
                await send({"id": request_id, "method": "model/list", "params": params})
                page = await response(request_id)
                data = page.get("data")
                if not isinstance(data, list):
                    raise ValueError("Invalid model list")
                rows.extend(data)
                cursor = page.get("nextCursor")
                if cursor is None:
                    return rows
                if not isinstance(cursor, str) or not cursor or cursor in seen:
                    raise ValueError("Invalid model cursor")
                seen.add(cursor)
            raise ValueError("Too many model pages")
    finally:
        if process is not None:
            if process.stdin is not None:
                process.stdin.close()
            if process.returncode is None:
                with contextlib.suppress(ProcessLookupError):
                    process.terminate()
            try:
                await asyncio.wait_for(process.wait(), 2.0)
            except TimeoutError:
                with contextlib.suppress(ProcessLookupError):
                    process.kill()
                await process.wait()


async def discover(resolved: ResolvedExecutable, env: dict[str, str]) -> tuple[dict, str]:
    # This is the same home passed to the execution CLI. build_child_env removes
    # inherited CODEX_HOME, so never read a different parent's catalogue here.
    user_home = env.get("USERPROFILE") if sys.platform == "win32" else env.get("HOME")
    home = Path(env["CODEX_HOME"]) if env.get("CODEX_HOME") else (
        Path(user_home or Path.home()) / ".codex"
    )
    key = (resolved.path, str(home))
    try:
        catalog = capabilities(await list_models(resolved, env))
        if catalog["models"]:
            _last_catalogs[key] = catalog
            return {**catalog, "model_catalog_source": "codex"}, ""
    except (OSError, ValueError, TypeError, TimeoutError, NotImplementedError):
        pass
    if key in _last_catalogs:
        return {**_last_catalogs[key], "model_catalog_source": "previous"}, (
            "모델 목록을 갱신하지 못해 마지막으로 확인한 목록을 표시합니다."
        )
    try:
        data = json.loads((home / "models_cache.json").read_text(encoding="utf-8"))
        rows = data.get("models")
        if isinstance(rows, list):
            catalog = capabilities(rows, cached=True)
            if catalog["models"]:
                return {**catalog, "model_catalog_source": "cache"}, (
                    "모델 목록을 갱신하지 못해 Codex에 저장된 목록을 표시합니다."
                )
    except (OSError, ValueError, AttributeError):
        pass
    return {**capabilities([]), "model_catalog_source": "unavailable"}, (
        "Codex 모델 목록을 확인하지 못했습니다. 기존 선택을 유지하며 CLI 기본 모델도 사용할 수 있습니다."
    )
