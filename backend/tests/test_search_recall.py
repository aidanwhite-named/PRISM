import json
import copy
from contextlib import nullcontext
import pytest
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from app import search_channels, search_manifest as sm, search_recall, search_report
from app.execution.runner import _search_mcp_servers
from app.patent_search import artifacts, epo_backend, epo_cql, epo_parser, base
from app.search_mcp_server import SearchTools, _distinct_alias_query
from . import epo_fixtures as fx
from .test_epo_search import FakeTransport, make_backend, ok, token_response


def test_provider_reaches_mcp_and_health_is_not_shared(tmp_path):
    health = {"at": datetime.now(timezone.utc).isoformat(), "ok": True}
    values = {"web_search_health": {"codex": health, "claude": {**health, "ok": False}}}
    env = _search_mcp_servers(tmp_path, "", 12, "codex")["prism-search"]["env"]
    assert env["PRISM_SEARCH_PROVIDER"] == "codex"
    tools = SearchTools(values=values, work_dir=tmp_path, provider=env["PRISM_SEARCH_PROVIDER"])
    assert tools.call("search_capabilities", {})["tools"]["web"]["status"] == "available"


def test_completion_does_not_claim_search_or_url_read_success():
    from app.providers.codex_stream import CodexStreamParser
    parser = CodexStreamParser()
    parser.feed(json.dumps({"type": "item.completed", "item": {
        "id": "w", "type": "web_search", "query": "music video motion"}}))
    call = parser.state.tool_calls[0]
    assert call["ok"] is None
    assert call["completed"] is True
    health = search_channels.web_evidence([call], ["web_search"])
    assert health["ok"] is None and health["completed_calls"] == 1
    status = search_channels.web_status(health)
    assert status["status"] == "unverified"
    assert "호출 완료" in status["detail"]
    assert sm.observed([call])["succeeded_fetch_urls"] == []
    assert "만료" in search_channels.web_status(health, now=datetime.now(timezone.utc) + timedelta(days=1))["detail"]


def test_real_epo_request_uses_requested_page(tmp_path):
    transport = FakeTransport(token_response(), ok(fx.SEARCH_BIBLIO))
    backend = make_backend(transport, artifacts.ArtifactStore(tmp_path))
    backend.search_structured(epo_cql.Term("ta", "music video"), start=61, max_results=20)
    from urllib.parse import parse_qs, urlsplit
    assert parse_qs(urlsplit(transport.requests[-1]["url"]).query)["Range"] == ["61-80"]


def test_epo_summary_deduplicates_pages_and_reuses_search_after_restart(tmp_path, monkeypatch):
    store = artifacts.ArtifactStore(tmp_path / "evidence")
    aid = store.put(fx.CLAIMS)
    record = epo_backend._record_for(epo_parser.read_documents(fx.CLAIMS)[0], aid)
    record = replace(record, fields={**record.fields, "abstract": base.FieldValue(value="long abstract " * 200)})
    response = base.PatentSearchResponse(records=(record,), total_found=70, raw_artifact_id=aid)
    requests = []
    class Backend:
        def search_structured(self, node, **kwargs):
            requests.append(kwargs)
            return response
    tools = SearchTools(values={}, work_dir=tmp_path)
    monkeypatch.setattr(tools, "_backend", lambda _: Backend())
    query = {"query": {"field": "ta", "value": "music video"}}
    first = tools._epo_search(query)
    assert first["next_start"] == 2 and first["has_more"]
    assert all(len(text) <= 1200 for text in first["records"][0]["fields"].values())
    assert first["records"][0]["truncated_fields"]
    tools._record({"id": "q1", "tool": "epo_search", "state": "completed", "ok": True,
                   "arguments": query, "result": first})
    restarted = SearchTools(values={}, work_dir=tmp_path)
    monkeypatch.setattr(restarted, "_backend", lambda _: Backend())
    cached = restarted._epo_search(query)
    assert cached["duplicate_query"] and cached["previous_call_id"] == "q1"
    assert len(requests) == 1
    second = restarted._epo_search({**query, "start": 2})
    assert second["records"] == []
    assert second["previously_seen"] == [record.doc_number]
    assert second["returned_count"] == 1 and second["next_start"] == 3
    # Full source remains intact for evidence verification and targeted fetching.
    assert store._path(aid).read_bytes() == fx.CLAIMS


def test_reference_extraction_does_not_take_cited_prior_art():
    assert search_recall.reference_publication("(11) 공개번호   10-2026-0093229\n(43) 공개일자") == "KR20260093229A"
    assert search_recall.reference_publication("선행기술문헌 공개특허 10-2026-0093229") is None
    assert search_recall.reference_publication("(11) EP 1 000 000 A1\n(12) patent") == "EP1000000A1"


def test_identifier_lookup_and_candidate_text_do_not_pass_keyword_check():
    target = "KR20260093229A"
    row = {"id": "id", "tool": "epo_search", "ok": True,
           "arguments": {"query": {"field": "pn", "value": target}},
           "result": {"records": [{"document_number": target}], "cql": "pn = target"}}
    assert search_recall.assess(target, [row])["status"] == "identifier_only"
    keyword = {**row, "id": "kw", "arguments": {"query": {"field": "ta", "value": "music video"}},
               "result": {"records": [], "previously_seen": [target], "cql": 'ta all "music video"'}}
    assert search_recall.assess(target, [row, keyword])["status"] == "keyword_hit"
    failed = {**keyword, "ok": False}
    assert search_recall.assess(target, [failed])["status"] == "not_observed"
    report = search_report.render(sm.build(claim_text="claim", spec_document={"publication_number": target},
        reported={"candidates": [{"doc_number": target, "group": None}]}, tool_journal=[row]))
    assert "키워드 검색 발견 미확인" in report
    assert "키워드 검색 응답에서 발견**" not in report
    assert search_recall.query_kind({"field": "ta", "value": target}) == "identifier"
    assert search_recall.query_kind({"type": "group", "op": "or", "items": [
        {"field": "ta", "value": "music"}, {"field": "cpc", "value": "G06T13/205"}]}) == "classification"


def test_missing_web_is_visible_and_page_coverage_counts_unique_positions():
    from app import search_quality
    row = {"tool": "epo_search", "ok": True, "result": {"cql": "q", "start": 1,
           "returned_count": 10, "total_found": 30, "records": []}}
    quality = search_quality.assess({}, {}, [row, row], {"web": {"status": "unverified"}})
    assert any(x["reason"] == "web_search_not_attempted" for x in quality["constraints"])
    assert any(x.get("detail", "").startswith("10/30") for x in quality["constraints"])


def test_excerpt_and_full_field_are_not_a_source_conflict(monkeypatch):
    from app import search_verification as sv
    ref = {"artifact_id": "a", "field_path": "p", "profile_id": "test"}
    sources = [{"fields": {"abstract:en": {"text": text, "evidence_ref": ref, "truncated": short}}}
               for text, short in (("Some text", True), ("Some text with more detail", False))]
    monkeypatch.setattr(sv, "_matching_sources", lambda *a: sources)
    candidate = sm.parse(json.dumps({"candidates": [{"doc_number": "EP1000000A1", "group": None}]}))[0]
    result = sv.verify(candidate, {}, [])
    assert "source_conflict" not in result["candidates"][0]["verification_issues"]
    sources[0]["fields"]["abstract:en"]["truncated"] = False
    result = sv.verify(candidate, {}, [])
    assert "source_conflict" in result["candidates"][0]["verification_issues"]


def _alias_queries():
    return ({"type": "group", "op": "and", "items": [
        {"field": "ta", "value": "robot robotic", "match": "any"},
        {"field": "ta", "value": "gripper", "match": "all"}]},
        {"type": "group", "op": "and", "items": [
        {"field": "ta", "value": "robot manipulator", "match": "any"},
        {"field": "ta", "value": "dexterity precision", "match": "any"}]})


def test_vocabulary_probe_uses_only_existing_aliases_and_required_anchor():
    previous, query = _alias_queries()
    before = copy.deepcopy(query)
    variant = _distinct_alias_query(query, [previous])
    assert variant["items"][0]["value"] == "manipulator"
    assert variant["items"][-1] == previous["items"][-1]
    assert query == before
    assert _distinct_alias_query(query, []) is None
    assert _distinct_alias_query(previous, [previous]) is None
    assert _distinct_alias_query({"field": "pn", "value": "EP1000000A1"}, [previous]) is None


@pytest.mark.parametrize("budget,fail_probe,expected_calls", [(2, False, 2), (8, False, 3), (8, True, 3)])
def test_vocabulary_probe_budget_journal_and_failure_preserve_original(client, tmp_path, monkeypatch, budget, fail_probe, expected_calls):
    from app import search_mcp_server as server
    calls = []
    class Backend:
        def use_ledger(self, ledger):
            pass
        def search_structured(self, node, **kwargs):
            calls.append(epo_cql.build(node))
            if len(calls) == 3 and fail_probe:
                raise base.PatentSearchError("supplement unavailable")
            return base.PatentSearchResponse(records=(), total_found=2001)
    tools = SearchTools(values={}, work_dir=tmp_path, max_calls=budget)
    monkeypatch.setattr(tools, "_backend", lambda _: Backend())
    monkeypatch.setattr(tools, "statuses", lambda: {name: {"status": "available" if name == "epo" else "disabled"}
                                                   for name in ("epo", "literature", "kiwee")})
    monkeypatch.setattr(server, "_quota_lock", nullcontext)
    monkeypatch.setattr(server.settings_service, "epo_ledger", lambda session: None)
    monkeypatch.setattr(server.settings_service, "epo_persist_error", lambda: False)
    first, second = _alias_queries()
    tools.call("epo_search", {"query": first})
    result = tools.call("epo_search", {"query": second})
    assert len(calls) == tools.calls == expected_calls
    assert result["total_found"] == 2001
    journal = sm.read_tool_journal(tmp_path)
    assert sum(row.get("state") == "started" for row in journal) == expected_calls
    if expected_calls == 3:
        assert any(row.get("query_origin") == "vocabulary_probe" for row in journal)
        assert ("vocabulary_probe_error" in result) == fail_probe
    else:
        assert "vocabulary_probe" not in result
