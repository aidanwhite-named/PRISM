import asyncio
import json
import socket
import sys

import pytest

import desktop
from app import config, runtime


def test_frozen_prompt_path_and_defaults_preserve_user_edits(tmp_path, monkeypatch):
    bundle = tmp_path / "bundle"
    (bundle / "prompt").mkdir(parents=True)
    for name in ("patent-analysis-master-prompt.md", "search_prompt.md"):
        (bundle / "prompt" / name).write_text("default", encoding="utf-8")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(bundle), raising=False)
    monkeypatch.delenv("PRISM_PROMPT_DIR")
    monkeypatch.setenv("PRISM_DATA_DIR", str(tmp_path / "사용자 data"))
    destination = config.default_prompt_dir()
    assert destination == tmp_path / "사용자 data" / "prompts"
    runtime.seed_prompts(destination)
    edited = destination / "search_prompt.md"
    edited.write_text("user edits", encoding="utf-8")
    runtime.seed_prompts(destination)
    assert edited.read_text(encoding="utf-8") == "user edits"
    assert runtime.resource_root() == bundle


def test_search_mcp_uses_frozen_entrypoint_in_both_callers(monkeypatch, tmp_path):
    from app.execution.runner import _search_mcp_servers
    from app.providers.agy_mcp import desired_entry
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "설치 경로" / "PRISM.exe"))
    for entry in (desired_entry(), _search_mcp_servers(tmp_path, "2026-01-01", 3)["prism-search"]):
        assert entry["command"] == sys.executable
        assert entry["args"] == ["--search-mcp"]
        assert "PYTHONPATH" not in entry["env"]


def test_source_search_mcp_keeps_python_module_command(monkeypatch):
    monkeypatch.delattr(sys, "frozen", raising=False)
    launch = runtime.search_server_command()
    assert launch["args"] == ["-m", "app.search_mcp_server"]
    assert launch["env"]["PYTHONPATH"].endswith("backend")


def test_lock_prevents_second_owner_and_recovers_after_release(tmp_path):
    first = desktop.InstanceLock(tmp_path)
    assert first.stream
    first.path.write_text(json.dumps({"port": 8765}), encoding="utf-8")
    second = desktop.InstanceLock(tmp_path)
    assert second.stream is None
    second.close()
    assert first.path.exists()
    first.close()
    third = desktop.InstanceLock(tmp_path)
    assert third.stream
    third.close()


def test_port_collision_keeps_existing_listener():
    with socket.socket() as occupied:
        occupied.bind(("127.0.0.1", 0))
        occupied.listen()
        port = occupied.getsockname()[1]
        if port > 65514:
            pytest.skip("Ephemeral port near maximum")
        with desktop.bind_port(port) as selected:
            assert port < selected.getsockname()[1] <= port + 20
        assert occupied.getsockname()[1] == port


@pytest.mark.asyncio
async def test_shutdown_cancels_queued_tasks(monkeypatch):
    from app.execution.runner import JobRunner
    runner = JobRunner()
    task = asyncio.create_task(asyncio.sleep(60))
    runner._tasks["queued"] = task
    calls = []

    async def cancel(job_id):
        calls.append(job_id)
        task.cancel()

    monkeypatch.setattr(runner, "cancel", cancel)
    await runner.shutdown()
    assert calls == ["queued"]
    assert task.cancelled()
