from types import SimpleNamespace

import pytest

from app import analysis_completeness
from app.retrieval import evidence, pages, search
from app.retrieval.agent import RetrievalBudget
from app.retrieval.content_filter import bibliography_only
from .test_delivery_modes import _FakeDocument
from .test_retrieval import _corpus, _pdf_attachment


def _bundle():
    return {"documents": [], "components": [{
        "component_id": "R001", "claim_component": "청구항 1 (B)",
        "feature": "좌표 변환", "status": evidence.STATUS_NOT_FOUND_SCOPE,
        "status_label": evidence.STATUS_LABEL[evidence.STATUS_NOT_FOUND_SCOPE],
        "status_reasons": [], "findings": [], "needs_original_review": True,
    }], "budget_exhausted": False, "budget_limited": False,
        "evidence_pages": pages.build(corpus=[_FakeDocument({1: "x" * 20000})],
            finding_pages={"doc": {1}}, neighbours=0, char_budget=12000)}


def test_discarded_partial_page_does_not_leave_stale_component_limit():
    bundle = _bundle()
    assert pages.truncations(bundle["evidence_pages"])
    text = evidence.fit(bundle, RetrievalBudget(max_evidence_chars=3000))
    assert not bundle["page_truncations"]
    assert bundle["page_reductions"]
    assert bundle["evidence_budget_limited"]
    component = bundle["components"][0]
    assert component["status"] == evidence.STATUS_NOT_FOUND_SCOPE
    assert not component["status_reasons"]
    assert text == evidence.render(bundle)
    assert evidence.fit(bundle, RetrievalBudget(max_evidence_chars=3000)) == text


def test_retained_partial_page_keeps_true_limit():
    bundle = _bundle()
    text = evidence.fit(bundle, RetrievalBudget(max_evidence_chars=20000))
    assert bundle["page_truncations"]
    assert bundle["components"][0]["status"] == evidence.STATUS_COVERAGE
    assert "페이지 일부" in bundle["components"][0]["status_reasons"][0]
    assert text == evidence.render(bundle)


def test_real_search_limitation_is_not_cleared_when_partial_page_removed():
    bundle = _bundle()
    bundle["budget_exhausted"] = True
    bundle["components"][0].update(status=evidence.STATUS_COVERAGE,
        status_label=evidence.STATUS_LABEL[evidence.STATUS_COVERAGE], status_reasons=["미처리 검색 요청"])
    evidence.fit(bundle, RetrievalBudget(max_evidence_chars=3000))
    assert bundle["components"][0]["status"] == evidence.STATUS_COVERAGE
    assert bundle["components"][0]["status_reasons"] == ["미처리 검색 요청"]


@pytest.mark.parametrize("text,excluded", [
    ("(52) CPC특허분류 G06T 15/04\n(72) 발명자\n미국 캘리포니아 주소", True),
    ("(72) 발명자\n김센서\n서울시 강남구", True),
    ("Inventors: Alice Smith\nApplicants: Example Inc.", True),
    ("(72) 발명자 김센서\n(57) 요약\n센서 데이터를 결합하는 방법", False),
    ("(71) 출원인 회사\n[0001] 정점 좌표를 월드 좌표로 변환한다.", False),
    ("청구항 1. 정점 좌표를 변환하는 방법", False),
    ("This paper describes a vertex transformation method.", False),
    (("개별 방향의 삼각형들을 분류하고 정점 데이터를 병합하는 단계; " * 6)
     + "\n(72) 발명자 홍길동", False),
])
def test_bibliography_filter_preserves_mixed_technical_chunks(text, excluded):
    assert bibliography_only(SimpleNamespace(text=text, section="")) is excluded


def test_bibliographic_chunks_do_not_consume_technical_candidate_quota(tmp_path):
    item = _pdf_attachment(tmp_path, "patent.pdf", [
        "(72) 발명자\n센서 센서 센서 센서 센서\n서울특별시 강남구",
        "[0001] 센서 신호를 결합하여 제어한다.",
    ])
    docs, _ = _corpus(tmp_path, [item])
    try:
        result = search.search_document(docs[0], queries=["센서"], limit=1, per_channel_limit=1)
        assert len(result.hits) == 1
        assert result.hits[0].row.page_number == 2
        # Kept in the index for identity lookup and explicit original-page access.
        assert docs[0].index.page_rows(1)
    finally:
        for doc in docs:
            doc.index.close()


def test_package_only_warning_does_not_claim_search_exhaustion():
    text = analysis_completeness.render({"manifest_parsed": True, "scope": {
        "limited": True, "budget_exhausted": True, "search_budget_exhausted": False,
        "evidence_budget_limited": True, "pending_actions": 0, "limited_components": [],
        "rounds": 5, "max_rounds": 5,
    }})
    assert "전달할 근거" in text
    assert "검색 예산 소진" not in text
    assert "다 훑지 못한" not in text
