"""Search sampling and lead-accounting regressions, with no live services."""
import copy
import json
from urllib.parse import parse_qs, urlsplit

import pytest

from app import search_audit, search_manifest as sm, search_quality, search_report
from app.search_mcp_server import epo_search_advice, error_response, _validate, _EPO_SEARCH
from app.patent_search import epo_cql, artifacts
from . import epo_fixtures as fx
from .test_epo_search import FakeTransport, token_response, ok, make_backend


def review():
    return {"stop_reason": "Different terminology and source searches yielded no further lead.",
            "expansion_summary": "Read sources, then searched the missing technical relation.",
            "remaining_gaps": ["One relation remains unconfirmed."]}


def fetch(number):
    return {"id": number, "tool": "epo_fetch", "state": "completed", "ok": False,
            "arguments": {"publication_number": number, "constituent": "claims"}}


def test_dropped_failed_leads_are_reported_without_inserting_candidates():
    reported = {"candidates": [{"doc_number": "CN122244264A", "group": "B"}], "search_review": review()}
    before = copy.deepcopy(reported)
    journal = [fetch(number) for number in ("US9626789B2", "US9536343B2", "US20200357176A1")]
    # Provider and MCP logs of one call must count as one lost lead.
    observed = {"tool_calls": [{"name": "mcp__prism-search__epo_fetch", "input": {
        "arguments": {"publication_number": "US9626789B2", "constituent": "claims"}}}]}
    audit = search_audit.assess(reported, observed, journal)
    assert audit["status"] == "incomplete"
    assert len(audit["unaccounted_fetches"]) == 3
    assert reported == before
    reported["candidate_dispositions"] = [{"doc_number": "US9626789B2", "reason": "Different processing relation"}]
    reported["candidates"].append({"doc_number": "US9536343B2", "group": None})
    assert search_audit.assess(reported, observed, journal)["unaccounted_fetches"] == ["patent:US20200357176A1"]


def test_date_exclusion_is_accounted_and_doi_less_web_document_is_retained():
    url = "https://author.example/thesis.pdf"
    reported = {"candidates": [{"url": url}], "search_review": review()}
    observed = {"tool_calls": [{"name": "WebFetch", "input": {"url": url}}]}
    audit = search_audit.assess(reported, observed, [fetch("EP1000000A1")],
                              {"excluded": [{"doc_number": "EP1000000A1"}]})
    assert audit["status"] == "recorded"
    assert audit["unaccounted_fetches"] == []


def test_first_page_bias_requires_a_review_but_never_claims_recall():
    journal = [{"tool": "epo_search", "state": "completed", "ok": True, "result": {
        "total_found": 51567, "coverage": {"total_results": 51567, "returned_records": 10, "result_range": "1-10"}}}]
    reported = {"candidates": [], "search_review": review()}
    audit = search_audit.assess(reported, {}, journal)
    assert audit["missing_review_fields"] == ["sampling_review"]
    reported["search_review"]["sampling_review"] = "Partitioned periods and tried observed classifications."
    audit = search_audit.assess(reported, {}, journal)
    assert audit["status"] == "recorded"
    assert len(audit["broad_searches"]) == 1
    assert search_quality.assess(reported, {}, journal, {})["search_coverage"] == "not_established"


def test_unaccounted_fetch_blocks_complete_manifest_even_if_evidence_complete():
    quality = search_quality.assess({"candidates": [], "search_review": review()}, {}, [fetch("US9626789B2")], {})
    quality["verification_status"] = "complete"
    manifest = sm.build(claim_text="claim", reported={"candidates": []}, quality=quality)
    assert manifest["status"] == "search_incomplete"
    report = search_report.render(manifest)
    assert "조회 후 후보·제외 사유에서 누락: patent:US9626789B2" in report
    assert "탐색 종료 감사 미완료" in report


@pytest.mark.parametrize("payload", [
    {"search_review": []}, {"search_review": {"remaining_gaps": "wrong"}},
    {"candidate_dispositions": ["wrong"]}, {"candidate_dispositions": [{"doc_number": "US1A1", "reason": ""}]},
])
def test_review_schema_rejects_malformed_data(payload):
    with pytest.raises(sm.SearchLogError):
        sm.parse(json.dumps({"candidates": [], **payload}))


def test_review_and_dispositions_survive_parser_roundtrip():
    payload = {"candidates": [], "search_review": review(), "candidate_dispositions": [
        {"doc_number": "US1A1", "doi": "", "url": "", "reason": "Duplicate family publication; see EP1A1"}]}
    parsed = sm.parse(json.dumps(payload))[0]
    assert parsed["search_review"]["remaining_gaps"] == payload["search_review"]["remaining_gaps"]
    assert parsed["candidate_dispositions"] == payload["candidate_dispositions"]
    # Old saved outputs still parse; missing rationale is an audit gap, not lost output.
    assert sm.parse('{"candidates":[]}')[0]["candidates"] == []


def test_doi_less_thesis_does_not_trigger_an_empty_epo_fetch():
    from app.search_followup import retrieval_plan
    plan = retrieval_plan({"candidates": [{"doc_type": "literature", "doi": "", "doc_number": "",
        "url": "https://author.example/thesis.pdf", "verification_issues": ["title_unverified"], "mapping": [{}]}]},
        [], {"epo": {"status": "available"}}, ("mcp__prism-search__epo_fetch",))
    assert plan == []


@pytest.mark.parametrize("begin,expected", [(11, "11-20"), (1998, "1998-2000")])
def test_epo_paging_reaches_transport_with_bounded_range(tmp_path, begin, expected):
    transport = FakeTransport(token_response(), ok(fx.SEARCH_BIBLIO))
    backend = make_backend(transport, artifacts.ArtifactStore(tmp_path))
    backend.search_structured(epo_cql.Term("ta", "robot arm"), max_results=10, begin=begin)
    query = parse_qs(urlsplit(transport.requests[-1]["url"]).query)
    assert query["Range"] == [expected]


def test_page_metadata_and_warning_do_not_rewrite_query():
    result = {"coverage": {"returned_records": 2, "total_results": 787}, "raw_artifact_id": "saved",
              "cql": 'ta all "robot arm"', "records": [{"publication_date": "20260101"}, {"publication_date": "20250101"}]}
    epo_search_advice(result, 11)
    assert result["coverage"]["result_range"] == "11-12"
    assert result["coverage"]["next_begin"] == 13
    assert result["coverage"]["publication_date_range"]["earliest"] == "20250101"
    assert result["search_warnings"][0]["code"] == "broad_query_sample"
    assert result["cql"] == 'ta all "robot arm"'
    for begin in (0, 2001, True):
        with pytest.raises(ValueError, match="arguments.begin"):
            _validate({"query": {"field": "ta", "value": "robot arm"}, "begin": begin}, _EPO_SEARCH["inputSchema"])


@pytest.mark.parametrize("code", ["CLIENT.InvalidCountryCode", "SERVER.EntityNotFound"])
def test_scope_error_retains_fault_and_gives_non_assertive_recovery(code):
    exc = RuntimeError("failure secret")
    exc.fault_code = code
    error = error_response(exc, "epo_fetch", {"publication_number": "US123A1", "constituent": "claims"}, ("secret",))
    assert error["error_code"] == code and error["requested_constituent"] == "claims"
    assert error["not_evidence_of_absence"] and error["recovery"]["next_step"]
    assert "secret" not in error["detail"]
    assert "recovery" not in error_response(exc, "literature_search", {})
