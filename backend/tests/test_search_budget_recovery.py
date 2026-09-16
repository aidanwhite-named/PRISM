import json

import pytest

from app import search_budget
from app.search_mcp_server import SearchTools, ToolLimitExceeded, _EPO_FETCH, _LITERATURE_FETCH
from app.patent_search.epo_client import CONSTITUENTS
from .conftest import wait_for_job


def test_shared_native_budget_stops_retrieval_before_hard_cap(tmp_path, monkeypatch):
    monkeypatch.setattr(SearchTools, "tool_definitions", lambda self: [_LITERATURE_FETCH])
    fetched = []
    monkeypatch.setattr(SearchTools, "_execute", lambda *args: fetched.append(args) or {"records": []})
    (tmp_path / search_budget.NATIVE_COUNT_FILE).write_text("35")
    tools = SearchTools(values={}, work_dir=tmp_path, max_calls=40)
    first = tools.call("literature_fetch", {"doi": "10.1234/a"})
    assert first["budget"] == search_budget.budget_status(36, 40)
    assert first["budget"]["action"] == "finalize_now"
    stopped = tools.call("literature_fetch", {"doi": "10.1234/b"})
    assert stopped["budget_stopped"] and len(fetched) == 1
    assert stopped["not_evidence_of_absence"]
    (tmp_path / search_budget.NATIVE_COUNT_FILE).write_text("38")
    restarted = SearchTools(values={}, work_dir=tmp_path, max_calls=40)
    with pytest.raises(ToolLimitExceeded):
        restarted.call("literature_fetch", {"doi": "10.1234/c"})


def test_epo_advertises_only_implemented_constituents():
    assert set(_EPO_FETCH["inputSchema"]["properties"]["constituent"]["enum"]) == set(CONSTITUENTS)


def journal():
    return [{"id": "saved", "tool": "epo_fetch", "state": "completed", "ok": True,
             "arguments": {"publication_number": "EP1234567A1", "constituent": "abstract"},
             "result": {"records": [{"document_number": "EP1234567A1", "title": "Saved source", "url": "https://example.com/patent"}]}}]


@pytest.mark.parametrize("flag", ["tool_budget_exceeded", "timed_out", "cancelled"])
def test_interrupted_search_keeps_records_without_final_json(client, monkeypatch, flag):
    from .fake_provider import DeterministicSearchProvider
    from app.providers.base import ExecutionOutcome

    async def execute(self, request, emit):
        assert "최대 40회" in request.system_prompt
        assert "36회" in request.system_prompt
        (request.work_dir / "search_tool_calls.jsonl").write_text(json.dumps(journal()[0]), encoding="utf-8")
        return ExecutionOutcome(tool_policy=request.tool_policy, **{flag: True})

    monkeypatch.setattr(DeterministicSearchProvider, "execute", execute)
    created = client.post("/api/jobs", json={"job_kind": "similarity_search", "provider": "test-search", "claim_text": "A sensor"})
    assert created.status_code == 201
    job = wait_for_job(client, created.json()["id"])
    assert job["status"] != "SUCCEEDED"
    assert job["search_manifest"]["reported"] is None
    assert job["search_manifest"]["retained_records"][0]["document_number"] == "EP1234567A1"
    assert "중단 전에 확보한 문헌" in job["result_text"]
    assert job["search_manifest"]["quality"]["candidate_count"] == 0


def test_failed_and_inflight_fetches_are_not_retained():
    rows = journal()
    rows += [{**rows[0], "ok": False}, {**rows[0], "state": "started"}]
    result = search_budget.retained_records(rows)
    assert len(result) == 1 and result[0]["call_ids"] == ["saved"]
