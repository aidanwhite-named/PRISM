"""Passage selection removes transcription, never provenance checks."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

from app import search_manifest as sm, search_verification as sv, search_report
from app.search_passages import passages, MAX_PASSAGE_CHARS
from tests.test_gpatents import _page_journal, _candidate, tools_store


def selected(result):
    return next(p for p in result["records"][0]["evidence_passages"]
                if p["field"] == "page_claims" and "[claim 2]" in p["preview_start"])


def test_text_spans_preserve_unicode_whitespace_and_are_source_bound():
    text = "[claim 1] 가😀\n  原文\n\n[claim 2] " + "한 문장입니다. " * 600
    refs = {"page_claims": {"artifact_id": "a", "field_path": "page_claims", "profile_id": "p"}}
    spans = passages("JP1A", {"page_claims": text}, refs)
    assert "".join(text[p["start"]:p["end"]] for p in spans) == text
    assert all(0 < p["end"] - p["start"] <= MAX_PASSAGE_CHARS for p in spans)
    assert spans == passages("JP1A", {"page_claims": text}, refs)
    assert spans[0]["passage_id"] != passages("JP2A", {"page_claims": text}, refs)[0]["passage_id"]
    assert spans[0]["passage_id"] != passages("JP1A", {"page_claims": text + "x"}, refs)[0]["passage_id"]


def test_selected_passage_restores_exact_text_and_computed_location(tmp_path, monkeypatch):
    journal, result, _ = _page_journal(tmp_path, monkeypatch)
    passage = selected(result)
    data, fields = _candidate(result, evidence_passage_id=passage["passage_id"], evidence_ref=None)
    candidate = sv.verify(data, {}, journal, store=tools_store(tmp_path))["candidates"][0]
    row = candidate["mapping"][0]
    expected = fields["page_claims"][passage["start"]:passage["end"]]
    assert row["support_text"] == row["verbatim_excerpt"] == expected
    assert row["support_verified"] and row["page_quote_verified"] and not row["quote_verified"]
    assert row["source_location"] == "청구항 2"
    assert row["passage_resolution"]["restored_fields"] == ["support_text", "verbatim_excerpt"]
    assert candidate["group"] == "A" and row["degree"] == "강한 대응"
    rendered = search_report.render({"version": 14, "reported": {"candidates": [candidate]}})
    assert "PRISM이 보존 원문에서 직접 가져왔습니다" in rendered
    # No mutation of the model's original response or source journal.
    assert data["candidates"][0]["mapping"][0]["support_text"] == ""


@pytest.mark.parametrize("mutation", ["id", "candidate", "undelivered", "bounds", "ref", "raw", "tampered"])
def test_invalid_selection_cannot_supply_verified_evidence(tmp_path, monkeypatch, mutation):
    journal, result, _ = _page_journal(tmp_path, monkeypatch)
    passage = selected(result)
    data, _ = _candidate(result, evidence_passage_id=passage["passage_id"], evidence_ref=None)
    row = data["candidates"][0]["mapping"][0]
    record = journal[-1]["result"]["records"][0]
    if mutation == "id":
        row["evidence_passage_id"] = "p_invented"
    elif mutation == "candidate":
        data["candidates"][0]["doc_number"] = "JP9999999B1"
    elif mutation == "undelivered":
        record.pop("evidence_passages")
    elif mutation == "bounds":
        next(p for p in record["evidence_passages"] if p["passage_id"] == passage["passage_id"])["end"] -= 1
    elif mutation == "ref":
        row["evidence_ref"] = {**record["evidence_refs"]["page_claims"], "artifact_id": "another"}
    elif mutation == "tampered":
        tools_store(tmp_path)._path(record["evidence_refs"]["page_claims"]["artifact_id"]).write_bytes(b"tampered")
    else:
        record["fields"]["page_claims"] += "invented text"
    candidate = sv.verify(data, {}, journal, store=tools_store(tmp_path))["candidates"][0]
    row = candidate["mapping"][0]
    assert not row["support_verified"] and not row["page_quote_verified"]
    assert "passage_unverified" in candidate["verification_issues"]
    assert not row.get("passage_resolution")


def test_selection_does_not_launder_model_rewritten_text(tmp_path, monkeypatch):
    journal, result, _ = _page_journal(tmp_path, monkeypatch)
    data, _ = _candidate(result, evidence_passage_id=selected(result)["passage_id"],
                         support_text="A made up passage", verbatim_excerpt="A made up quote")
    row = sv.verify(data, {}, journal, store=tools_store(tmp_path))["candidates"][0]["mapping"][0]
    assert not row["support_verified"] and not row["page_quote_verified"]
    assert row["support_text"] == "A made up passage"


def test_passage_id_is_validated_by_output_parser():
    with pytest.raises(sm.SearchLogError, match="evidence_passage_id"):
        sm.parse(json.dumps({"candidates": [{"mapping": [{"evidence_passage_id": 123}]}]}))


def test_repeated_text_uses_selected_position_and_ignores_unrelated_text():
    text = "same text\nsame text"
    row = {"passage_resolution": {"start": 10, "end": 19}}
    assert sv._text_position(row, text, "same text") == 10
    assert sv._text_position(row, text, "text\n") == 5


def test_undelivered_tail_never_gets_a_selector():
    from app.search_mcp_server import _record
    from app.patent_search.base import PatentRecord, FieldValue, EvidenceRef
    record = PatentRecord(doc_number="JP1A", title="Title", fields={
        "abstract": FieldValue("a" * 1200 + "PRIVATE TAIL", EvidenceRef("a", "abstract", "p"))})
    delivered = _record(record, compact=True)
    assert delivered["truncated_fields"] == ["abstract"]
    assert all(p["end"] <= 1200 for p in delivered["evidence_passages"])
    assert "PRIVATE TAIL" not in json.dumps(delivered)


@pytest.mark.parametrize("source", ["openalex", "arxiv"])
def test_verification_in_fresh_process_registers_literature_parsers(tmp_path, source):
    from app.patent_search import artifacts, arxiv_backend
    from app.search_mcp_server import _response
    from tests.test_openalex import _backend
    from tests import literature_fixtures as fx

    store = artifacts.ArtifactStore(tmp_path / "evidence")
    if source == "openalex":
        response = _backend(store, {fx.TARGET_DOI: (200, fx.OPENALEX_WORK)}).fetch_document(fx.TARGET_DOI)
    else:
        body = b'''<feed xmlns="http://www.w3.org/2005/Atom"><entry>
        <id>http://arxiv.org/abs/2111.13907v1</id><title>Skeletal Animation</title>
        <summary>Original abstract with preserved text.</summary>
        <published>2021-11-27</published></entry></feed>'''
        response = arxiv_backend.materialize(body, store)
    result = _response(response, scope="abstract")
    record = result["records"][0]
    data, _ = sm.parse(json.dumps({"candidates": [{"doi": record["document_number"], "mapping": [
        {"evidence_passage_id": record["evidence_passages"][0]["passage_id"]}]}]}))
    path = tmp_path / "case.json"
    path.write_text(json.dumps({"reported": data, "journal": [
        {"tool": "literature_fetch", "state": "completed", "ok": True, "result": result}]}), encoding="utf-8")
    script = '''
import json, sys
from pathlib import Path
from app.search_verification import verify
from app.patent_search.artifacts import ArtifactStore
data = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
c = verify(data["reported"], {}, data["journal"], store=ArtifactStore(Path(sys.argv[2])))["candidates"][0]
assert c["mapping"][0]["support_verified"], c["verification_issues"]
assert c["mapping"][0]["passage_resolution"]
assert not c["mapping"][0]["quote_verified"]
'''
    check = subprocess.run([sys.executable, "-c", script, str(path), str(tmp_path / "evidence")],
                           cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=20)
    assert check.returncode == 0, check.stderr
