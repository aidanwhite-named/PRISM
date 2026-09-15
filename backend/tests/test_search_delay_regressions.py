import copy
import json
import time
from dataclasses import replace
from types import SimpleNamespace

import pytest

from app import search_followup, search_manifest as sm, search_verification as sv
from app.search_mcp_server import SearchTools, _record, _LITERATURE_FETCH
from app.providers.base import ExecutionOutcome, ExecutionRequest, WEB_SEARCH
from .test_search_quality import candidate
from .test_search_api import wait_for_job


def test_fetch_reuses_success_across_process_restart_but_retries_failure(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(SearchTools, "tool_definitions", lambda self: [_LITERATURE_FETCH])
    def execute(self, name, args):
        calls.append(args)
        if len(calls) == 1:
            raise ValueError("temporary failure")
        return {"records": [{"document_number": args["doi"]}], "http_status": 200}
    monkeypatch.setattr(SearchTools, "_execute", execute)
    tools = SearchTools(values={}, work_dir=tmp_path)
    with pytest.raises(ValueError):
        tools.call("literature_fetch", {"doi": "10.1234/a"})
    first = tools.call("literature_fetch", {"doi": "10.1234/a"})
    restarted = SearchTools(values={}, work_dir=tmp_path)
    cached = restarted.call("literature_fetch", {"doi": "10.1234/a", "constituent": "abstract"})
    assert cached["records"] == first["records"]
    assert cached["reused_from_call_id"]
    assert len(calls) == 2
    restarted.call("literature_fetch", {"doi": "10.1234/a", "constituent": "biblio"})
    assert len(calls) == 3


def test_search_summary_bounds_text_without_shortening_fetch():
    fields = {"abstract:en": SimpleNamespace(value="a" * 5000, evidence=None),
              "abstract:fr": SimpleNamespace(value="b" * 5000, evidence=None),
              "publication_date": SimpleNamespace(value="20200101", evidence=None)}
    rec = SimpleNamespace(fields=fields, doc_number="EP1000000A1", title="Title", source_url="https://example.com")
    summary = _record(rec, compact=True)
    assert len(summary["fields"]["abstract:en"]) == 1200
    assert "abstract:fr" in summary["omitted_fields"]
    assert summary["publication_date"] == "20200101"
    assert len(_record(rec)["fields"]["abstract:en"]) == 5000


@pytest.mark.parametrize("reported,expected", [
    ("HUAWEI TECH CO LTD; HUAWEI TECHNOLOGIES CO., LTD.", False),
    ("Google LLC", True),
    ("HUAWEI TECH CO LTD", True),
])
def test_country_tags_do_not_trigger_reread_but_different_or_missing_parties_do(monkeypatch, reported, expected):
    monkeypatch.setattr(sv, "_matching_sources", lambda *args: [{"fields": {
        "applicants": {"text": "HUAWEI TECH CO LTD [CN]\nHUAWEI TECHNOLOGIES CO., LTD.", "evidence_ref": {}}
    }}])
    data = sm.parse(json.dumps({"candidates": [{**candidate(), "applicant": reported}]}))[0]
    issues = sv.verify(data, {}, [])["candidates"][0]["verification_issues"]
    assert ("applicant_mismatch" in issues) == expected


@pytest.mark.asyncio
async def test_followup_receives_evidence_and_keeps_initial_usage_on_cancel(tmp_path, monkeypatch):
    payload = json.dumps({"candidates": [{**candidate(), "mapping": [{"feature": "F", "support_text": "unsupported", "evidence_ref": {"artifact_id": "saved"}}]}]})
    original_verify = sv.verify
    def verify(*args):
        data = original_verify(*args)
        data["candidates"][0]["evidence_sources"] = [{"document_number": "EP1000000A1",
            "url": "https://example.com/patent", "fields": {"claims:en": {
                "text": "actual preserved claim", "evidence_ref": {"artifact_id": "saved"}}}}]
        return data
    monkeypatch.setattr(sv, "verify", verify)
    policy = replace(WEB_SEARCH, required_tools=())
    initial = ExecutionOutcome(result_text=payload, usage={"input_tokens": 181273}, tool_policy=policy)
    class Provider:
        async def execute(self, request, emit):
            data = json.loads(request.user_message)
            assert request.mcp_servers == {}
            assert request.tool_policy.tools_disabled
            assert data["untrusted_tasks"][0]["source_text"] == "actual preserved claim"
            return ExecutionOutcome(cancelled=True, usage=None, tool_policy=policy)
    async def emit(*args):
        pass
    request = ExecutionRequest(job_id="test", work_dir=tmp_path, user_message="input", system_prompt="", tool_policy=policy)
    merged, audit = await search_followup.run(Provider(), request, initial, emit, attachments=[],
        fail_on_tool_use=True, deadline=time.monotonic() + 100, availability={}, cancelled=lambda: False)
    assert merged.cancelled
    assert merged.result_text == payload
    assert merged.usage == initial.usage
    assert audit["initial_output_preserved"] and not audit["usage_complete"]


@pytest.mark.parametrize("status,flag", [("CANCELLED", "cancelled"), ("FAILED", "timed_out")])
def test_api_preserves_initial_candidates_after_followup_interruption(client, monkeypatch, status, flag):
    async def interrupted(provider, request, initial, emit, **kwargs):
        from app.db import session_scope
        from app.models import ExecutionJob
        with session_scope() as session:
            running = session.get(ExecutionJob, request.job_id)
            assert running.status == "RUNNING"
            assert running.search_manifest["reported"]["candidates"]
            assert "최초 검색 결과" in running.search_manifest["error"]
        result = copy.deepcopy(initial)
        setattr(result, flag, True)
        result.usage = {"input_tokens": 123}
        return result, {"attempted": True, "reason": "interrupted", "initial_output_preserved": True,
                        "execution_status": status, "error_code": "TIMED_OUT" if flag == "timed_out" else "CANCELLED", "usage_complete": False}
    monkeypatch.setattr(search_followup, "run", interrupted)
    created = client.post("/api/jobs", json={"job_kind": "similarity_search", "provider": "test-search",
        "claim_text": "청구항 1. 센서를 포함하는 장치"}).json()
    job = wait_for_job(client, created["id"])
    assert job["status"] == status
    assert job["search_manifest"]["reported"]["candidates"]
    assert job["search_manifest"]["quality"]["execution_status"] == "incomplete"
    assert job["usage"]["input_tokens"] == 123
