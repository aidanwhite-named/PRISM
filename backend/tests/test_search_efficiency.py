import copy
import json

import pytest

from app import search_followup as sf, search_manifest as sm
from app.search_mcp_server import _tool_result
from app.providers.agy_cli import AgyCliProvider
from app.providers.base import ExecutionOutcome


def _reported():
    return sm.parse(json.dumps({"rounds": [{"query": "original search"}], "candidates": [
        {"doc_number": "EP1000000A1", "group": "A", "title": "Unresolved", "mapping": []},
        {"doc_number": "EP2000000A1", "group": "B", "title": "Keep exactly", "mapping": []},
    ]}))[0]


def test_scoped_updates_preserve_unmodified_candidates_and_search_history():
    original = _reported()
    before = copy.deepcopy(original)
    changed = copy.deepcopy(original["candidates"][0])
    changed["title"] = "Verified title"
    result = sf._merge_updates(original, {"candidates": [changed]}, [{"identity": "patent:EP1000000A1"}])
    assert original == before
    assert result["candidates"] == [changed, original["candidates"][1]]
    assert result["rounds"] == original["rounds"]
    assert sf._merge_updates(original, {"candidates": []}, []) == original


@pytest.mark.parametrize("change", ["new", "group", "outside_scope", "duplicate"])
def test_followup_rejects_changes_outside_verification_scope(change):
    original = _reported()
    candidates = [copy.deepcopy(original["candidates"][0])]
    if change == "new":
        candidates[0]["doc_number"] = "EP3000000A1"
    elif change == "group":
        candidates[0]["group"] = "C"
    elif change == "outside_scope":
        candidates = [original["candidates"][1]]
    else:
        candidates *= 2
    with pytest.raises(sm.SearchLogError):
        sf._merge_updates(original, {"candidates": candidates}, [{"identity": "patent:EP1000000A1"}])


def test_followup_reuses_only_relevant_evidence_without_duplicates():
    original = _reported()
    verified = copy.deepcopy(original)
    field = {"text": "Exact original passage", "evidence_ref": {
        "artifact_id": "abc", "field_path": "abstract", "profile_id": "test"}}
    source = {"fields": {"abstract": field}}
    verified["candidates"][0]["evidence_sources"] = [source, source]
    verified["candidates"][1]["evidence_sources"] = [{"fields": {"abstract": {**field, "text": "unrelated"}}}]
    attempts = [{"state": "completed", "tool": "epo_fetch", "arguments": {"publication_number": "EP1000000A1"},
                 "ok": False, "error_code": "unavailable"}]
    data = sf._followup_data(original, verified, [{"identity": "patent:EP1000000A1"}], attempts)
    assert len(data["previous_result_untrusted_data"]["candidates"]) == 1
    assert data["already_delivered_evidence_untrusted_data"] == [{"identity": "patent:EP1000000A1", "field": "abstract", **field}]
    assert data["previous_fetch_attempts"][0]["error_code"] == "unavailable"
    assert "unrelated" not in json.dumps(data)


@pytest.mark.parametrize("error", [False, True])
def test_mcp_json_is_lossless_and_transmitted_once(error):
    value = {"records": [{"title": "원문", "evidence_refs": {"abstract": {"artifact_id": "abc"}}}], "ok": not error}
    result = _tool_result(value, error=error)
    assert result["isError"] == error
    assert "structuredContent" not in result
    assert len(result["content"]) == 1
    assert json.loads(result["content"][0]["text"]) == value


async def test_search_check_tests_selected_model(monkeypatch):
    provider = AgyCliProvider()
    requests = []
    async def execute(request, emit):
        requests.append(request)
        return ExecutionOutcome()
    monkeypatch.setattr(provider, "execute", execute)
    await provider.search_check(model="gemini-3.8-flash-low")
    assert requests[0].model == "gemini-3.8-flash-low"
    assert "exactly once" in requests[0].user_message


def test_saved_literature_evidence_verifies_without_prior_backend_calls(tmp_path, monkeypatch):
    from app import search_verification as sv
    from app.patent_search import literature_parser, parsers
    from app.patent_search.artifacts import ArtifactStore
    # Simulate the separate web process, which has not run the MCP parser.
    monkeypatch.setattr(literature_parser, "_REGISTERED", False)
    monkeypatch.setattr(parsers, "_PARSERS", {})
    monkeypatch.setattr(parsers, "_PROFILES", {})
    store = ArtifactStore(tmp_path)
    artifact = store.put(json.dumps({"message": {"DOI": "10.1234/example", "title": ["Saved title"]}}).encode())
    ref = {"artifact_id": artifact, "field_path": "message/title/0", "profile_id": "crossref_work_json"}
    journal = [{"tool": "literature_fetch", "state": "completed", "ok": True, "result": {"records": [
        {"document_number": "10.1234/example", "fields": {"title": "Saved title"}, "evidence_refs": {"title": ref}}
    ]}}]
    reported = sm.parse(json.dumps({"candidates": [{"doi": "10.1234/example", "group": "B", "title": "Saved title"}]}))[0]
    verified = sv.verify(reported, {}, journal, store=store)["candidates"][0]
    assert verified["evidence_sources"]
    assert "title_unverified" not in verified["verification_issues"]
    assert "identifier_unverified" not in verified["verification_issues"]
