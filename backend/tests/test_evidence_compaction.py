"""Lossless report packaging: provenance, context, budget and coverage contracts."""
from copy import deepcopy

import pytest

from app.retrieval import evidence, pages
from app.retrieval.agent import RetrievalBudget
from app.retrieval.source_pool import SourcePool
from .test_delivery_modes import _FakeDocument
from .test_retrieval import _stress_bundle


def _bundle():
    bundle, _ = _stress_bundle(2, 1, 1, RetrievalBudget())
    before = "[0001] 부정 조건: 연결하지 않는다.\n  들여쓰기 유지\t"
    source = "[0002] 한글 😀 원문은 공백  두 개와 끝 공백을 유지한다. "
    after = "[0003] 제한: 전압이 5V인 경우에만 적용한다."
    finding = dict(attachment="ATT-01", chunk_id="P0001-002", pdf_page=1,
                   channels=["substring"], extraction_status="ok", source_text=source,
                   context_before=before, context_after=after, ai_relevance="구성별 판단")
    bundle["components"][0]["findings"] = [finding]
    bundle["components"][1]["findings"] = [{**finding, "ai_relevance": "다른 구성 판단"}]
    text = "\n".join((before, source, after))
    bundle["evidence_pages"] = pages.build(corpus=[_FakeDocument({1: text})],
        finding_pages={"doc": {1}}, neighbours=0, char_budget=10000)
    bundle["documents"] = [dict(attachment="ATT-01", filename="doc.pdf", pdf_pages=1,
        extraction_status="ok", identity_excerpt=before, identity_excerpt_pdf_page=1)]
    return bundle, text


def _assert_references(bundle):
    pool = evidence.source_pool(bundle)
    sources = {entry["id"]: entry for entry in pool.sources}
    for component in bundle["components"]:
        for finding in component["findings"]:
            for key in ("source_text", "context_before", "context_after"):
                if not finding.get(key):
                    continue
                ref = pool.reference(finding["attachment"], finding["pdf_page"], finding[key])
                source = sources[ref["source_id"]]
                assert source["attachment"] == finding["attachment"]
                assert source["pdf_page"] == finding["pdf_page"]
                assert source["text"][ref["start"]:ref["end"]] == finding[key]


def test_full_page_excerpts_context_and_identity_share_exact_source():
    bundle, page = _bundle()
    snapshot = deepcopy(bundle)
    rendered = evidence.render(bundle)
    assert bundle == snapshot
    assert rendered.count(page) == 1
    assert len(evidence.source_pool(bundle).sources) == 1
    assert rendered.count("chunk_id: P0001-002") == 2
    assert "구성별 판단" in rendered and "다른 구성 판단" in rendered
    assert "서지사항 원문 발췌 · PDF 1쪽" in rendered
    _assert_references(bundle)


@pytest.mark.parametrize("other", [("ATT-02", 1), ("ATT-01", 2)])
def test_identical_text_in_other_documents_or_pages_keeps_its_own_provenance(other):
    pool = SourcePool([("ATT-01", 1, "same exact text"), (*other, "same exact text")])
    assert len(pool.sources) == 2
    assert pool.reference("ATT-01", 1, "same exact text") != pool.reference(*other, "same exact text")


def test_source_pool_does_not_normalize_whitespace_or_invent_overlap():
    pool = SourcePool([("ATT-01", 1, value) for value in
                       ("prefix ABC", "ABC suffix", "two  spaces", "two spaces")])
    assert len(pool.sources) == 4
    assert "prefix ABC suffix" not in "\n".join(pool.render())


def test_removing_page_or_first_component_cannot_leave_a_dangling_reference():
    bundle, _ = _bundle()
    bundle["evidence_pages"] = []
    bundle["components"][0]["findings"] = []
    _assert_references(bundle)
    rendered = evidence.render(bundle)
    for key in ("source_text", "context_before", "context_after"):
        assert bundle["components"][1]["findings"][0][key] in rendered


def test_partial_page_never_substitutes_for_unincluded_source_or_claims_full_review():
    bundle, _ = _bundle()
    original = bundle["evidence_pages"][0]["pages"][0]["text"]
    page = bundle["evidence_pages"][0]["pages"][0]
    page.update(text=original[:12], truncated=True, included_chars=12,
                omitted_chars=len(original)-12, source_end=12)
    rendered = evidence.fit(bundle, RetrievalBudget())
    _assert_references(bundle)
    assert "부분 수록" in rendered
    assert pages.unverified_pages(bundle["evidence_pages"][0]) == [1]
    assert bundle["page_truncations"][0]["omitted_chars"] == len(original) - 12
    assert bundle["budget_exhausted"]


def test_same_budget_can_keep_full_page_that_duplicate_rendering_would_exceed():
    bundle, _ = _bundle()
    finding = bundle["components"][0]["findings"][0]
    finding["source_text"] = "한글 근거 😀 및 조건을 정확히 보존한다.\n" * 160
    bundle["components"][1]["findings"] = [{**finding}]
    full = "\n".join(finding[k] for k in ("context_before", "source_text", "context_after"))
    bundle["evidence_pages"] = pages.build(corpus=[_FakeDocument({1: full})],
        finding_pages={"doc": {1}}, neighbours=0, char_budget=100000)
    size = len(evidence.render(bundle))
    budget = RetrievalBudget(max_evidence_chars=size, max_evidence_bytes=len(evidence.render(bundle).encode()))
    rendered = evidence.fit(bundle, budget)
    assert not bundle["page_reductions"] and not bundle["page_truncations"]
    assert not bundle["package_reductions"]
    assert len(rendered) == budget.max_evidence_chars
    assert len(rendered.encode()) == budget.evidence_byte_limit
    _assert_references(bundle)


def test_repeated_global_limitations_are_shared_but_local_limitations_remain():
    bundle, _ = _bundle()
    bundle["coverage_blockers"] = ["공통 추출 실패 사유"]
    for index, component in enumerate(bundle["components"]):
        component["status_reasons"] = ["공통 추출 실패 사유", f"구성 {index}의 미확인 사유"]
        component["priority_reasons"] = ["실행 스케줄 우선순위 이력"]
    rendered = evidence.render(bundle)
    assert rendered.count("공통 추출 실패 사유") == 1
    assert "구성 0의 미확인 사유" in rendered and "구성 1의 미확인 사유" in rendered
    assert "실행 스케줄 우선순위 이력" not in rendered
    assert bundle["components"][0]["priority_reasons"]  # retained for audit


def test_identity_fallback_records_actual_pdf_page():
    doc = _FakeDocument({1: "", 2: "실제 서지 원문"})
    # IndexedDocument exposes page_count, whereas this small test fake does not.
    doc.page_count = 2
    assert evidence.identity_excerpt_location(doc) == (2, "실제 서지 원문")
