"""agy 전역 MCP 등록·호출 정규화·스키마 열람 감사.

agy 1.2.2 실측(2026-09-14)에 근거한다.
- MCP 서버는 ~/.gemini/config/mcp_config.json 에만 등록된다.
- agy 는 자기 환경을 MCP 자식에게 물려준다.
- 호출은 call_mcp_tool {ServerName, ToolName, Arguments} 로 스트림에 찍힌다.
- 모델은 호출 전에 ~/.gemini/antigravity-cli/mcp/<서버>/<도구>.json 을 view_file 로 읽는다.
"""
import json
import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from app import search_channels
from app.providers import agy_mcp, agy_permissions
from app.providers.agy_cli import audit_content_reads
from app.providers.agy_stream import AgyStreamParser
from app.providers.base import AGY_WEB_SEARCH


@pytest.fixture
def agy_home(tmp_path, monkeypatch):
    config = tmp_path / "config" / "mcp_config.json"
    settings = tmp_path / "antigravity-cli" / "settings.json"
    schemas = tmp_path / "antigravity-cli" / "mcp" / "prism-search"
    monkeypatch.setenv("PRISM_AGY_MCP_CONFIG_PATH", str(config))
    monkeypatch.setenv("PRISM_AGY_SETTINGS_PATH", str(settings))
    monkeypatch.setenv("PRISM_AGY_MCP_SCHEMA_DIR", str(schemas))
    return config, settings, schemas


def test_registration_merges_without_removing_existing_entries(agy_home):
    config, settings, _ = agy_home
    config.parent.mkdir(parents=True)
    config.write_text(json.dumps({"mcpServers": {"other": {"command": "x"}}}), encoding="utf-8")
    settings.parent.mkdir(parents=True)
    settings.write_text(json.dumps({"permissions": {"allow": ["read_url(arxiv.org)"]},
                                    "trustedWorkspaces": ["D:\\w"]}), encoding="utf-8")

    assert not agy_mcp.read_state().ok
    state = agy_mcp.ensure_registered()
    assert state.ok, state.detail()

    servers = json.loads(config.read_text(encoding="utf-8"))["mcpServers"]
    assert servers["other"] == {"command": "x"}
    entry = servers["prism-search"]
    assert entry["args"] == ["-m", "app.search_mcp_server"]
    # 전역 설정에는 실행별 값도 비밀도 넣지 않는다.
    assert set(entry["env"]) == {"PYTHONPATH"}
    document = json.loads(settings.read_text(encoding="utf-8"))
    assert document["permissions"]["allow"] == ["read_url(arxiv.org)", "mcp(prism-search/*)"]
    assert document["trustedWorkspaces"] == ["D:\\w"]
    assert list(config.parent.glob("*.prism-backup-*"))

    # 이미 맞으면 다시 쓰지 않는다 — 백업이 늘지 않는다.
    before = sorted(p.name for p in config.parent.iterdir()) + sorted(p.name for p in settings.parent.iterdir())
    assert agy_mcp.ensure_registered().ok
    after = sorted(p.name for p in config.parent.iterdir()) + sorted(p.name for p in settings.parent.iterdir())
    assert before == after


def test_empty_config_file_is_an_empty_config(agy_home):
    config, _, _ = agy_home
    config.parent.mkdir(parents=True)
    config.write_text("", encoding="utf-8")  # agy 가 서버 없을 때 만드는 모양
    assert agy_mcp.ensure_registered().ok


def test_broken_config_is_not_overwritten(agy_home):
    config, _, _ = agy_home
    config.parent.mkdir(parents=True)
    config.write_text("{broken", encoding="utf-8")
    state = agy_mcp.ensure_registered()
    assert not state.ok and "JSON" in state.error
    assert config.read_text(encoding="utf-8") == "{broken"


def test_availability_follows_registration(agy_home):
    values = {"literature_integration_enabled": True}
    assert search_channels.availability(values, "agy")["literature"]["status"] == "not_registered"
    assert not search_channels.mcp_transport_ready("agy")
    agy_mcp.ensure_registered()
    assert search_channels.availability(values, "agy")["literature"]["status"] == "available"
    assert search_channels.mcp_transport_ready("agy")


def test_run_env_passes_only_run_scoped_keys():
    servers = {"prism-search": {"command": "py", "env": {
        "PYTHONPATH": "p", "PRISM_SEARCH_WORK_DIR": "w", "PRISM_SEARCH_MAX_TOOL_CALLS": 5}}}
    assert agy_mcp.run_env(servers) == {"PRISM_SEARCH_WORK_DIR": "w", "PRISM_SEARCH_MAX_TOOL_CALLS": "5"}
    assert agy_mcp.run_env({}) == {}


def _step(index, state, name, parameters, **extra):
    return json.dumps({"event": "step_update", "step_update": {
        "step_index": index, "state": state, "step_type": "tool", "tool_name": name,
        "tool_info": {"name": name, "parameters": parameters, **extra}}})


def test_call_mcp_tool_is_normalized_to_mcp_name():
    parser = AgyStreamParser()
    params = {"ServerName": "prism-search", "ToolName": "epo_search",
              "Arguments": {"query": {"type": "term", "field": "ta", "value": "beat camera"},
                            "max_results": 20, "secret": "x"}}
    parser.feed(_step(4, "ACTIVE", "call_mcp_tool", params))
    parser.feed(_step(4, "DONE", "call_mcp_tool", params, output="{}"))
    [call] = parser.state.tool_calls
    assert call["name"] == "mcp__prism-search__epo_search"
    assert call["ok"] is True
    assert call["input"] == {"arguments": {"query": params["Arguments"]["query"], "max_results": 20}}
    assert parser.state.tool_uses == ["mcp__prism-search__epo_search"]


def test_other_mcp_servers_stay_outside_the_policy():
    parser = AgyStreamParser()
    parser.feed(_step(1, "DONE", "call_mcp_tool", {"ServerName": "probe", "ToolName": "echo_env", "Arguments": {}}))
    policy = replace(AGY_WEB_SEARCH, mcp_tools=("mcp__prism-search__epo_search",))
    assert policy.unexpected_calls(parser.state.tool_calls) == ["mcp__probe__echo_env"]


def test_schema_view_is_allowed_only_for_enabled_prism_tools(agy_home):
    _, _, schemas = agy_home
    parser = AgyStreamParser()
    parser.feed(json.dumps({"event": "init", "conversation_id": "c1", "init": {}}))
    parser.feed(_step(2, "DONE", "view_file", {"AbsolutePath": str(schemas / "epo_search.json")}))
    parser.feed(_step(3, "DONE", "view_file", {"AbsolutePath": str(schemas / "unregistered_search.json")}))
    parser.feed(_step(5, "DONE", "view_file", {"AbsolutePath": str(schemas.parent / "probe" / "echo_env.json")}))
    policy = replace(AGY_WEB_SEARCH, mcp_tools=("mcp__prism-search__epo_search",))
    audit_content_reads(parser.state, policy)
    scopes = [call.get("scope") for call in parser.state.tool_calls]
    assert scopes == ["mcp_schema", "out_of_scope", "out_of_scope"]
    assert policy.unexpected_calls(parser.state.tool_calls) == ["view_file"]


def test_large_mcp_output_file_is_readable_only_for_its_own_successful_call(tmp_path):
    """run f2e77760: 41~53 KB 검색 결과가 steps/<n>/output.txt 로 넘겨졌다."""
    steps = tmp_path / "brain" / "c1" / ".system_generated" / "steps"
    parser = AgyStreamParser()
    parser.feed(json.dumps({"event": "init", "conversation_id": "c1", "init": {}}))
    parser.feed(_step(10, "DONE", "call_mcp_tool",
                      {"ServerName": "prism-search", "ToolName": "epo_search", "Arguments": {"query": {}}}))
    parser.feed(_step(11, "DONE", "read_url_content", {"Url": "https://arxiv.org/abs/1"}))
    # 구분자가 섞인 경로도 실제로 찍혔다(steps\33/output.txt).
    parser.feed(_step(13, "DONE", "view_file", {"AbsolutePath": f"{steps}\\10/output.txt"}))
    parser.feed(_step(14, "DONE", "view_file", {"AbsolutePath": str(steps / "11" / "output.txt")}))
    parser.feed(_step(15, "DONE", "view_file", {"AbsolutePath": str(tmp_path / "brain" / "other" / ".system_generated" / "steps" / "10" / "output.txt")}))
    policy = replace(AGY_WEB_SEARCH, mcp_tools=("mcp__prism-search__epo_search",))
    audit_content_reads(parser.state, policy)
    views = [call for call in parser.state.tool_calls if call["name"] == "view_file"]
    assert [call["scope"] for call in views] == ["mcp_output:10", "out_of_scope", "out_of_scope"]
    assert "content_read" not in parser.state.tool_calls_by_step["10"]
    assert policy.unexpected_calls(parser.state.tool_calls) == ["view_file"]


def test_server_outside_prism_run_offers_no_tools():
    requests = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "epo_search", "arguments": {}}},
    ]
    env = {key: value for key, value in os.environ.items() if key != "PRISM_SEARCH_WORK_DIR"}
    env["PYTHONIOENCODING"] = "utf-8"
    backend = Path(__file__).resolve().parents[1]
    completed = subprocess.run([sys.executable, "-m", "app.search_mcp_server"],
        input="\n".join(json.dumps(r) for r in requests) + "\n", capture_output=True, text=True,
        encoding="utf-8", env=env, cwd=backend, timeout=30)
    assert completed.returncode == 0, completed.stderr
    replies = [json.loads(line) for line in completed.stdout.splitlines()]
    assert replies[0]["result"]["serverInfo"]["name"] == "prism-search"
    assert replies[1]["result"]["tools"] == []
    assert replies[2]["result"]["isError"] is True
    assert replies[2]["result"]["structuredContent"]["error_code"] == "not_in_prism_search_run"
