import copy
import json
import time
from dataclasses import replace

import pytest

from app import search_followup, search_manifest as sm, search_quality, search_verification as sv
from app.providers.base import ExecutionOutcome, ExecutionRequest, WEB_SEARCH


def candidate():
    return {"doc_number": "EP1000000A1", "group": "A", "title": "Reported title",
            "url": "https://patents.google.com/patent/EP1000000A1/en",
            "mapping": [{"feature": "F", "support_text": "unsupported"}]}


def test_latest_run_shape_cannot_be_verification_complete():
    reported = sv.verify(sm.parse(json.dumps({"candidates": [candidate()]}))[0], {}, [])
    observed = sm.observed([{"name": "search_web", "ok": True, "input": {"query": "query"}}] * 3)
    quality = search_quality.assess(reported, observed, [], {"epo": {"status": "unsupported_transport"}})
    assert quality["execution_status"] == "complete"
    assert quality["verification_status"] == "incomplete"
    assert quality["outstanding"][0]["reason"] == "not_attempted"
    assert quality["verified_candidate_count"] == 0
    assert quality["constraints"] == [{"source": "epo", "reason": "unsupported_transport"}]
    assert sm.build(claim_text="", reported=reported, quality=quality)["status"] == "verification_incomplete"


def test_failed_fetch_differs_from_unattempted_and_quota():
    reported = sv.verify(sm.parse(json.dumps({"candidates": [candidate()]}))[0], {}, [])
    journal = [{"tool": "epo_fetch", "arguments": {"publication_number": "EP1000000A1"},
                "state": "completed", "ok": False, "error_code": "quota_exceeded"}]
    quality = search_quality.assess(reported, {}, journal, {})
    assert quality["outstanding"][0]["reason"] == "verification_unresolved"
    assert quality["constraints"][0]["reason"] == "limit_exhausted"


def test_title_and_applicant_mismatch_are_not_certified(monkeypatch):
    monkeypatch.setattr(sv, "_matching_sources", lambda *args: [{"fields": {
        "title:en": {"text": "Actual title", "evidence_ref": {}},
        "applicants": {"text": "Adobe Inc", "evidence_ref": {}},
    }}])
    data = sm.parse(json.dumps({"candidates": [{**candidate(), "applicant": "Google LLC"}]}))[0]
    verified = sv.verify(data, {}, [])["candidates"][0]
    assert {"title_mismatch", "applicant_mismatch"} <= set(verified["verification_issues"])
    assert verified["verified_titles"] == ["Actual title"]
    assert verified["group"] == "A"


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["success", "timeout", "changed_identity", "malformed", "budget", "cancel", "input"])
async def test_followup_preserves_budget_failures_and_initial_output(tmp_path, mode):
    payload = json.dumps({"candidates": [candidate()]})
    initial = ExecutionOutcome(result_text=payload, exit_code=0, terminal_reason="completed",
        raw_stdout="first", usage={"input_tokens": 10}, tool_policy=replace(WEB_SEARCH, required_tools=()),
        tool_calls=[{"id": "1", "name": "WebSearch", "ok": True, "input": {"query": "q"}}])
    calls = []
    class Provider:
        max_input_bytes = 1 if mode == "input" else None
        def payload_bytes(self, *parts):
            return sum(len(part.encode()) for part in parts)
        async def execute(self, request, emit):
            calls.append(request)
            result = copy.deepcopy(initial)
            result.raw_stdout = "second"
            result.tool_policy = request.tool_policy
            result.timed_out = mode == "timeout"
            if mode == "changed_identity":
                result.result_text = payload.replace("EP1000000A1", "EP2000000A1")
            if mode == "malformed":
                result.result_text = "bad JSON"
            return result
    request = ExecutionRequest(job_id="job", work_dir=tmp_path, system_prompt="system", user_message="input",
        tool_policy=replace(WEB_SEARCH, max_tool_calls=1 if mode == "budget" else 40, required_tools=()))
    async def emit(*args):
        pass
    merged, audit = await search_followup.run(Provider(), request, initial, emit, attachments=[],
        fail_on_tool_use=True, deadline=time.monotonic() + 100, availability={}, cancelled=lambda: mode == "cancel")
    if mode in ("budget", "cancel", "input"):
        assert not calls
        assert not audit["attempted"]
    else:
        assert len(calls) == 1
        assert calls[0].tool_policy.max_tool_calls == 39
        assert 0 < calls[0].timeout_seconds <= 100
        assert merged.tool_policy.max_tool_calls == 40
        assert len(merged.tool_calls) == 2
        assert merged.usage == {"input_tokens": 20}
        assert merged.timed_out == (mode == "timeout")
        assert audit["output_accepted"] == (mode == "success")
        assert (tmp_path / "verification_followup" / "initial_output.txt").read_text(encoding="utf-8") == payload
    assert merged.result_text == payload


def test_empty_candidates_do_not_establish_search_coverage():
    quality = search_quality.assess({"candidates": []}, {}, [], {})
    assert quality["verification_status"] == "no_candidates"
    assert quality["search_coverage"] == "not_established"


@pytest.mark.asyncio
async def test_read_web_page_without_provenance_does_not_repeat_impossible_check(tmp_path):
    payload = json.dumps({"candidates": [candidate()]})
    url = candidate()["url"]
    observed = sm.observed([{"name": "WebFetch", "ok": True, "input": {"url": url}}])
    verified = sv.verify(sm.parse(payload)[0], observed, [])
    quality = search_quality.assess(verified, observed, [], {"epo": {"status": "unsupported_transport"}})
    assert quality["outstanding"][0]["reason"] == "page_read_without_provenance"
    class Provider:
        async def execute(self, *args):
            pytest.fail("Cannot resolve missing provenance by repeating the same web read")
    initial = ExecutionOutcome(result_text=payload, tool_calls=observed["tool_calls"])
    request = ExecutionRequest(job_id="x", work_dir=tmp_path, system_prompt="", user_message="", tool_policy=WEB_SEARCH)
    async def emit(*args):
        pass
    result, audit = await search_followup.run(Provider(), request, initial, emit, attachments=[],
        fail_on_tool_use=True, deadline=time.monotonic() + 100, availability={"epo": {"status": "unsupported_transport"}}, cancelled=lambda: False)
    assert result is initial
    assert not audit["attempted"]
    assert "반복 조회 생략" in audit["reason"]
