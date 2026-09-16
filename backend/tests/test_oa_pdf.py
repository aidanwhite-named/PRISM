"""OpenAlex OA PDF 원문 채널, 사람 속도 게이트, MCP 도구, 검증 등급 승격 테스트.

지키려는 것:
1. OpenAlex 레코드에서 OA PDF 주소를 올바르게 선별한다.
2. pypdf 로 추출한 텍스트에서 [p.N] 마커를 붙이고, location_of 로 "논문 p.N"을 기계적으로 계산한다.
3. 사람 속도 게이트와 실행당 상한을 지킨다.
4. MCP literature_fetch_pdf 도구가 캐시를 재사용하고, ct 종류코드 함정 경고를 생성한다.
5. OA PDF 본문에서 확인된 발췌는 "source_page_text_verified"로 승격되고 보고서에 바르게 표시된다.
"""

from __future__ import annotations

import io
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app import search_channels, search_manifest as sm, search_report, search_verification as sv
from app import search_mcp_server as mcp
from app.patent_search import artifacts, literature_backend, literature_client, oa_pdf, openalex_client, pace
from app.patent_search.base import PatentRecord, PatentSearchResponse


@pytest.fixture()
def store(tmp_path):
    return artifacts.ArtifactStore(tmp_path / "evidence")


# --- 1. OA 주소 선별 ------------------------------------------------------
def test_select_pdf_url_prioritizes_best_oa_location() -> None:
    body = json.dumps({
        "best_oa_location": {"pdf_url": "https://example.org/paper.pdf"},
        "open_access": {"oa_url": "https://example.org/landing"},
    }).encode("utf-8")
    assert oa_pdf.select_pdf_url(body) == "https://example.org/paper.pdf"


def test_select_pdf_url_falls_back_to_oa_url_only_if_pdf() -> None:
    body_landing = json.dumps({
        "best_oa_location": None,
        "open_access": {"oa_url": "https://example.org/landing/abs/123"},
    }).encode("utf-8")
    assert oa_pdf.select_pdf_url(body_landing) == ""

    body_pdf = json.dumps({
        "best_oa_location": None,
        "open_access": {"oa_url": "https://example.org/landing/paper.PDF"},
    }).encode("utf-8")
    assert oa_pdf.select_pdf_url(body_pdf) == "https://example.org/landing/paper.PDF"


def test_select_pdf_url_rejects_non_https() -> None:
    body = json.dumps({
        "best_oa_location": {"pdf_url": "http://insecure.org/paper.pdf"},
    }).encode("utf-8")
    assert oa_pdf.select_pdf_url(body) == ""


# --- 2. 페이지 범위 및 텍스트 추출/위치 --------------------------------------
def test_page_span_validation() -> None:
    assert oa_pdf.page_span(1, 8) == (1, 8)
    assert oa_pdf.page_span(3) == (3, 10)  # 기본 스팬 8

    with pytest.raises(oa_pdf.OaPdfError):
        oa_pdf.page_span(0, 5)  # 1 미만

    with pytest.raises(oa_pdf.OaPdfError):
        oa_pdf.page_span(10, 5)  # 역순

    with pytest.raises(oa_pdf.OaPdfError):
        oa_pdf.page_span(1, 20)  # MAX_PAGE_SPAN(12) 초과


def test_read_pages_and_location_of() -> None:
    mock_p1 = MagicMock()
    mock_p1.extract_text.return_value = "Title: Swing Twist Limits in Skeletal Models"
    mock_p2 = MagicMock()
    mock_p2.extract_text.return_value = "The twist angle depends on both swing angles."
    mock_reader = MagicMock()
    mock_reader.pages = [mock_p1, mock_p2]

    sample_pdf_bytes = b"%PDF-1.4 dummy content"
    with patch("pypdf.PdfReader", return_value=mock_reader):
        text = oa_pdf.read_pages(sample_pdf_bytes, 1, 2)
        assert "[p.1]\nTitle: Swing Twist Limits" in text
        assert "[p.2]\nThe twist angle depends on both swing angles." in text

        start_pos = text.find("twist angle depends")
        loc = oa_pdf.location_of("pdf_text/1-2", text, start_pos)
        assert loc == "논문 p.2"

        title_pos = text.find("Title: Swing")
        assert oa_pdf.location_of("pdf_text/1-2", text, title_pos) == "논문 p.1"


# --- 3. 사람 속도 게이트 및 상태 ------------------------------------------
def test_oa_pdf_rate_limited_and_404(tmp_path) -> None:
    gate = pace.HumanPaceGate(tmp_path, name="test_oa", min_interval=0.01)

    def transport_404(url, timeout):
        return oa_pdf.PdfResponse(status=404, body=b"", url=url)

    client_404 = oa_pdf.OaPdfClient(transport=transport_404, gate=gate)
    with pytest.raises(oa_pdf.OaPdfNotFound):
        client_404.fetch("https://example.org/test.pdf")

    def transport_429(url, timeout):
        return oa_pdf.PdfResponse(status=429, body=b"", url=url)

    client_429 = oa_pdf.OaPdfClient(transport=transport_429, gate=gate)
    with pytest.raises(oa_pdf.OaPdfRateLimited):
        client_429.fetch("https://example.org/test.pdf")


# --- 4. LiteratureBackend.fetch_pdf ---------------------------------------
def test_literature_backend_fetch_pdf(tmp_path, store) -> None:
    doi = "10.1234/test.5678"
    work_json = json.dumps({
        "doi": f"https://doi.org/{doi}",
        "best_oa_location": {"pdf_url": "https://example.org/paper.pdf"},
    }).encode("utf-8")

    openalex_mock = MagicMock()
    openalex_mock.fetch.return_value = literature_client.HttpResponse(
        status=200, headers={}, body=work_json
    )

    pdf_bytes = b"%PDF-1.4 sample pdf content"
    oa_client_mock = MagicMock()
    oa_client_mock.fetch.return_value = oa_pdf.PdfResponse(
        status=200, body=pdf_bytes, url="https://example.org/paper.pdf"
    )

    backend = literature_backend.LiteratureBackend(
        store=store,
        openalex=openalex_mock,
        oa_pdf_client=oa_client_mock,
    )

    with patch("app.patent_search.oa_pdf.read_pages", return_value="[p.1]\nSample text"), \
         patch("app.patent_search.oa_pdf.page_count", return_value=5):
        resp = backend.fetch_pdf(doi, page_from=1, page_to=2)

    assert resp.total_found == 1
    record = resp.records[0]
    assert record.doc_number == doi
    assert oa_pdf.TEXT_FIELD in record.fields
    assert oa_pdf.NOTE_FIELD in record.fields
    assert record.fields[oa_pdf.TEXT_FIELD].evidence.profile_id == oa_pdf.PROFILE_OA_PDF_TEXT
    assert store.exists(resp.raw_artifact_id)


# --- 5. MCP SearchTools literature_fetch_pdf 및 ct 경고 -------------------
def test_mcp_ct_kind_code_zero_results_warning(tmp_path) -> None:
    tools = mcp.SearchTools(
        values={"epo_consumer_key": "k", "epo_consumer_secret": "s"},
        work_dir=tmp_path,
    )
    fake_empty_response = PatentSearchResponse(
        records=(),
        total_found=0,
        raw_artifact_id="art1",
        fetched_at="",
        http_status=200,
        request_url="",
    )
    mock_epo = MagicMock()
    mock_epo.search_structured.return_value = fake_empty_response
    mock_epo.status.return_value = MagicMock(configured=True)
    tools.backends["epo"] = mock_epo

    # 종류코드 A가 붙은 ct 질의
    res = tools._epo_search({
        "query": {"type": "term", "field": "ct", "value": "JP2009070340A"},
    })
    warnings = res.get("search_warnings", [])
    codes = [w.get("code") for w in warnings]
    assert "ct_kind_code_zero_results" in codes


def test_mcp_literature_fetch_pdf_cap_and_cache(tmp_path, store) -> None:
    tools = mcp.SearchTools(
        values={
            "literature_integration_enabled": True,
            "literature_oa_pdf_enabled": True,
            "literature_oa_pdf_max_fetches_per_run": 2,
        },
        work_dir=tmp_path,
    )
    backend = MagicMock()
    backend.max_pdf_fetches_per_run = 2
    backend.status.return_value = MagicMock(configured=True)
    tools.backends["literature"] = backend

    def make_resp(doi):
        return PatentSearchResponse(
            records=(PatentRecord(doc_number=doi, title="", fields={}, source_url=""),),
            total_found=1,
            raw_artifact_id="art_" + doi.replace("/", "_"),
            fetched_at="",
            http_status=200,
            request_url="https://example.org/" + doi,
        )

    backend.fetch_pdf.side_effect = lambda doi, **kw: make_resp(doi)

    with patch("app.patent_search.oa_pdf.page_span", return_value=(1, 5)):
        # 1회차: 새 요청
        res1 = tools.call("literature_fetch_pdf", {"doi": "10.1000/1"})
        assert res1["network_fetch"] is True

        # 2회차: 동일 DOI 조회 (캐시 재사용, 네트워크 호출 없음)
        res2 = tools.call("literature_fetch_pdf", {"doi": "10.1000/1", "page_from": 3})
        assert res2["network_fetch"] is False

        # 3회차: 다른 DOI (상한 2건 중 2번째)
        res3 = tools.call("literature_fetch_pdf", {"doi": "10.1000/2"})
        assert res3["network_fetch"] is True

        # 4회차: 또 다른 DOI -> 상한 2건 초과로 OaPdfFetchLimit 예외
        with pytest.raises(oa_pdf.OaPdfFetchLimit):
            tools.call("literature_fetch_pdf", {"doi": "10.1000/3"})


# --- 6. 검증 등급 승격 및 보고서 마크다운 -----------------------------------
def test_search_verification_promotes_oa_pdf_excerpt(tmp_path, store) -> None:
    doi = "10.1000/skeletal_model"
    excerpt = "The twist angle depends on both swing angles."
    full_text = f"[p.1]\nIntroduction\n[p.2]\n{excerpt}\nConclusion"

    # 아티팩트에 원본 바이트 저장
    artifact_id = store.put(b"%PDF-1.4 dummy content")

    candidate = {
        "index": 1,
        "rank": 1,
        "doi": doi,
        "doc_number": "",
        "title": "Skeletal Model Constraints",
        "group": "A",
        "mapping": [
            {
                "feature": "제1 제한",
                "counterpart": "직접 대응",
                "similar": "스윙에 따른 트위스트 한계",
                "different": "",
                "support_text": excerpt,
                "verbatim_excerpt": excerpt,
                "evidence_ref": {
                    "artifact_id": artifact_id,
                    "field_path": "pdf_text/1-2",
                    "profile_id": oa_pdf.PROFILE_OA_PDF_TEXT,
                },
            }
        ],
    }

    journal = [
        {
            "sequence": 1,
            "tool": "literature_fetch_pdf",
            "state": "completed",
            "ok": True,
            "arguments": {"doi": doi, "page_from": 1, "page_to": 2},
            "result": {
                "records": [
                    {
                        "document_number": doi,
                        "fields": {
                            oa_pdf.TEXT_FIELD: full_text,
                        },
                        "evidence_refs": {
                            oa_pdf.TEXT_FIELD: {
                                "artifact_id": artifact_id,
                                "field_path": "pdf_text/1-2",
                                "profile_id": oa_pdf.PROFILE_OA_PDF_TEXT,
                            }
                        },
                    }
                ],
                "identifier_matched": True,
                "raw_artifact_id": artifact_id,
            },
        }
    ]

    with patch("app.patent_search.oa_pdf.read_pages", return_value=full_text):
        res = sv.verify(
            {"candidates": [candidate]},
            {"succeeded_fetch_urls": []},
            journal,
            store=store,
        )

    cand = res["candidates"][0]
    # 비공식 원문 PDF 본문 발췌가 일치하여 source_page_text_verified 등급으로 승격
    assert cand["evidence_level"] == "source_page_text_verified"
    mapping_row = cand["mapping"][0]
    assert mapping_row["support_verified"] is True
    assert mapping_row["support_origin"] == "oa_pdf"
    assert mapping_row["support_location"] == "논문 p.2"
    assert mapping_row["source_location"] == "논문 p.2"
    assert mapping_row["page_quote_verified"] is True

    # 보고서 렌더러 확인 (v14 매니페스트)
    report_md = search_report.render({"version": 14, "reported": {"candidates": [cand]}})
    assert "논문 원문 PDF 대조 확인 (OA 사본) · 논문 p.2" in report_md
    assert "논문 원문 PDF 발췌 (OA 사본)" in report_md
    assert "논문 p.2" in report_md