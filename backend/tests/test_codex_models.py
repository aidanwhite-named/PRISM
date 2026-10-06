"""Model discovery uses the configured CLI, never a paid turn or fixed slugs."""

import asyncio
import json
import sys
from pathlib import Path

import pytest

from app.providers import codex_models
from app.providers.codex_cli import CodexCliProvider
from app.providers.resolver import ResolvedExecutable


def row(model="future-sol", **extra):
    return {"model": model, "defaultReasoningEffort": "low",
            "supportedReasoningEfforts": [{"reasoningEffort": "low"},
                                          {"reasoningEffort": "ultra"}], **extra}


@pytest.fixture(autouse=True)
def clear_memory():
    codex_models._last_catalogs.clear()
    yield
    codex_models._last_catalogs.clear()


def test_visible_models_and_efforts_are_not_hardcoded():
    caps = codex_models.capabilities([None, row(), row(), row("hidden", hidden=True),
                                    row("small", supportedReasoningEfforts=[
                                        {"reasoningEffort": "new-effort"}])])
    assert caps["models"] == ["future-sol", "small"]
    assert caps["reasoning_efforts_by_model"]["small"] == ["new-effort"]
    assert "small" not in caps["reasoning_defaults_by_model"]


def test_stdio_handshake_pagination_and_no_turn(tmp_path):
    script = tmp_path / "server.py"
    log = tmp_path / "requests.jsonl"
    script.write_text('''import json,sys
with open(sys.argv[1], 'w') as log:
 for line in sys.stdin:
  m=json.loads(line); log.write(line); log.flush()
  if m['method']=='initialize': result={}
  elif m['method']=='initialized': continue
  elif m['method']=='model/list':
   cursor=m['params'].get('cursor')
   result={'data':[{'model':'second' if cursor else 'first',
                   'supportedReasoningEfforts':[{'reasoningEffort':'low'}],
                   'defaultReasoningEffort':'low'}],
           'nextCursor':None if cursor else 'page2'}
  else: raise RuntimeError('unexpected method')
  print(json.dumps({'method':'notice','params':{}}),flush=True)
  print(json.dumps({'id':m['id'],'result':result}),flush=True)
''', encoding="utf-8")
    executable = ResolvedExecutable(str(log), "test", [sys.executable, str(script)])
    rows = asyncio.run(codex_models.list_models(executable, {}))
    assert [r["model"] for r in rows] == ["first", "second"]
    requests = [json.loads(line) for line in log.read_text().splitlines()]
    assert [r["method"] for r in requests] == [
        "initialize", "initialized", "model/list", "model/list"]
    assert requests[-1]["params"]["cursor"] == "page2"
    assert requests[-1]["params"]["includeHidden"] is False


def test_timeout_ends_discovery_child(tmp_path):
    script = tmp_path / "waiting.py"
    script.write_text("import time; time.sleep(60)", encoding="utf-8")
    executable = ResolvedExecutable(str(script), "test", [sys.executable])
    with pytest.raises(TimeoutError):
        asyncio.run(codex_models.list_models(executable, {}, timeout=0.15))


def test_live_catalog_refresh_then_failure_keeps_previous(tmp_path, monkeypatch):
    executable = ResolvedExecutable("cli.exe", "test")
    env = {"CODEX_HOME": str(tmp_path)}
    responses = [[row("v1")], [row("v2")], OSError("offline")]

    async def listing(*args):
        value = responses.pop(0)
        if isinstance(value, Exception):
            raise value
        return value

    monkeypatch.setattr(codex_models, "list_models", listing)

    async def check():
        assert (await codex_models.discover(executable, env))[0]["models"] == ["v1"]
        assert (await codex_models.discover(executable, env))[0]["models"] == ["v2"]
        caps, note = await codex_models.discover(executable, env)
        assert caps["models"] == ["v2"] and caps["model_catalog_source"] == "previous"
        assert note
        other, _ = await codex_models.discover(ResolvedExecutable("other.exe", "test"), env)
        assert other["models"] == []

    # Allow another installation's lookup to fail independently.
    responses.append(OSError("offline"))
    asyncio.run(check())


@pytest.mark.parametrize("corrupt", [False, True])
def test_cache_fallback_uses_child_home_not_parent_home(tmp_path, monkeypatch, corrupt):
    async def fail(*args):
        raise ValueError("unsupported RPC")

    monkeypatch.setattr(codex_models, "list_models", fail)
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "wrong-parent"))
    home = tmp_path / ".codex"
    home.mkdir()
    (home / "models_cache.json").write_text("bad json" if corrupt else json.dumps({"models": [
        {"slug": "cached-sol", "visibility": "list", "default_reasoning_level": "low",
         "supported_reasoning_levels": [{"effort": "low"}]},
        {"slug": "private", "visibility": "hide"},
    ]}), encoding="utf-8")
    caps, note = asyncio.run(codex_models.discover(ResolvedExecutable("cli.exe", "test"), {
        "USERPROFILE": str(tmp_path), "HOME": str(tmp_path),
    }))
    assert caps["models"] == ([] if corrupt else ["cached-sol"])
    assert note


def test_probe_advertises_dynamic_capabilities(monkeypatch):
    from app.providers import codex_cli
    from app.execution.process import ProcessResult

    monkeypatch.setattr(CodexCliProvider, "_resolve", lambda self: ResolvedExecutable("cli.exe", "test"))

    async def capture(argv, **kwargs):
        return ProcessResult(exit_code=0, stdout="Logged in using ChatGPT" if "status" in argv else "codex-cli latest")

    async def listing(*args):
        return [row()]

    monkeypatch.setattr(codex_cli.proc, "run_capture", capture)
    monkeypatch.setattr(codex_models, "list_models", listing)
    result = asyncio.run(CodexCliProvider().probe())
    assert result.capabilities["models"] == ["future-sol"]
    assert result.capabilities["reasoning_defaults_by_model"] == {"future-sol": "low"}
    assert result.capabilities["model_catalog_source"] == "codex"


@pytest.mark.parametrize("kind", ["patent_analysis", "similarity_search"])
@pytest.mark.parametrize("source", ["codex", "cache", "previous", "unavailable"])
def test_saved_gpt61_passes_execution_check(kind, source, monkeypatch):
    from app.api import jobs
    from app.enums import AuthState
    from app.providers.base import ProbeResult
    from app.schemas import JobCreate

    async def probe(*args, **kwargs):
        return ProbeResult(
            provider="codex", display_name="Codex", installed=True,
            executable_ok=True, auth_state=AuthState.OK,
            capabilities={
                "models": ["gpt-6.1-sol"] if source == "codex" else ["gpt-5.6-sol"],
                "model_catalog_source": source,
            },
        )

    monkeypatch.setattr(jobs, "probe_one", probe)
    values = {
        "default_provider": "codex", "default_models": {"codex": "gpt-6.1-sol"},
        "search_provider": "codex", "search_models": {"codex": "gpt-6.1-sol"},
    }
    assert asyncio.run(jobs._resolve_provider(JobCreate(job_kind=kind), values)) == (
        "codex", "gpt-6.1-sol",
    )


def test_current_catalog_rejects_unknown_model(monkeypatch):
    from fastapi import HTTPException
    from app.api import jobs
    from app.enums import AuthState
    from app.providers.base import ProbeResult
    from app.schemas import JobCreate

    async def probe(*args, **kwargs):
        return ProbeResult(
            provider="codex", display_name="Codex", installed=True,
            executable_ok=True, auth_state=AuthState.OK,
            capabilities={"models": ["gpt-6.1-sol"], "model_catalog_source": "codex"},
        )

    monkeypatch.setattr(jobs, "probe_one", probe)
    with pytest.raises(HTTPException) as error:
        asyncio.run(jobs._resolve_provider(
            JobCreate(provider="codex", model="made-up-model"), {},
        ))
    assert error.value.status_code == 400


def test_failed_refresh_does_not_advertise_a_fixed_model_list(tmp_path, monkeypatch):
    async def fail(*args):
        raise OSError("offline")

    monkeypatch.setattr(codex_models, "list_models", fail)
    caps, note = asyncio.run(codex_models.discover(
        ResolvedExecutable("cli.exe", "test"), {"CODEX_HOME": str(tmp_path)},
    ))
    assert caps["models"] == []
    assert caps["model_catalog_source"] == "unavailable"
    assert note



@pytest.mark.parametrize("key", ["reasoning_effort", "search_reasoning_effort"])
def test_new_advertised_effort_can_be_saved(key, monkeypatch):
    from app import settings_service
    from app.providers import registry
    from app.providers.base import ProbeResult

    monkeypatch.setitem(registry._cache, "codex", ProbeResult(
        provider="codex", display_name="Codex",
        capabilities={"reasoning_efforts": ["future-effort"]},
    ))
    assert settings_service._coerce(key, {"codex": "future-effort"}) == {"codex": "future-effort"}
    with pytest.raises(ValueError):
        settings_service._coerce(key, {"codex": "made-up-effort"})
    with pytest.raises(ValueError):
        settings_service._coerce(key, {"claude": "future-effort"})
