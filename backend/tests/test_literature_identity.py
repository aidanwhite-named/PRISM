"""논문 신원과 검색 범위 표시.

두 가지를 고정한다.

  - arXiv 논문은 표기가 셋이다(DOI, arXiv:ID, arxiv.org URL). 표기가 다르다고
    다른 문헌으로 세면 같은 논문이 후보에 둘로 남고 검증 대조가 빗나간다.
    버전(v2)은 신원이 아니다.
  - MCP 응답의 coverage 는 모델이 "더 찾아야 하는가"를 판단할 객관적 신호다.
    받은 범위와 중복 수가 있어야 한다. 재현율을 주장하지는 않는다.
"""

from __future__ import annotations

import pytest

from app import search_manifest
from app import search_mcp_server as mcp
from app.patent_search import literature_client
from app.patent_search.base import PatentRecord, PatentSearchResponse

ARXIV = "10.48550/arxiv.2412.19860"


@pytest.mark.parametrize(
    "raw",
    [
        "10.48550/arXiv.2412.19860",
        "10.48550/arXiv.2412.19860v2",
        "https://doi.org/10.48550/arXiv.2412.19860",
        "arXiv:2412.19860",
        "arxiv:2412.19860v1",
        "https://arxiv.org/abs/2412.19860v3",
        "https://arxiv.org/pdf/2412.19860v1.pdf",
    ],
)
def test_arxiv_notations_share_one_identity(raw):
    assert literature_client.normalize_doi(raw) == ARXIV
    assert search_manifest.identity_key(doi=raw) == "doi:" + ARXIV
    # 모델이 DOI 칸이 아니라 문헌번호 칸에 적어도 같은 문헌이다.
    assert search_manifest.identity_key(raw) == "doi:" + ARXIV


def test_arxiv_version_is_kept_apart_from_identity():
    assert literature_client.arxiv_identity("arXiv:2412.19860v2") == ("2412.19860", "v2")
    assert literature_client.arxiv_identity("arXiv:hep-th/9901001v1") == ("hep-th/9901001", "v1")
    # 표시 없는 번호는 다른 체계와 구별할 수 없어 받지 않는다.
    assert literature_client.arxiv_identity("2412.19860") is None


def test_patents_and_ordinary_dois_keep_their_identity():
    assert search_manifest.identity_key("US10123456B2") == "patent:US10123456B2"
    assert search_manifest.identity_key(doi="https://doi.org/10.3390/S25103219") == "doi:10.3390/s25103219"
    assert literature_client.normalize_doi("10.1109/ICDH.2012.31") == "10.1109/icdh.2012.31"


def _records(*numbers):
    return tuple(PatentRecord(doc_number=number, title="t") for number in numbers)


def test_single_source_coverage_reports_range_and_duplicates():
    response = PatentSearchResponse(records=_records("a", "a", "b"), total_found=50)
    coverage = mcp._response(response, scope="bibliographic_search")["coverage"]
    assert coverage["result_range"] == "1-3"
    assert coverage["unique_documents"] == 2
    assert coverage["duplicate_records"] == 1
    assert coverage["more_results_available"] is True


def test_multi_source_ranges_are_reported_per_source():
    response = PatentSearchResponse(
        records=_records("a", "a"),
        total_found=2,
        source_stats=(
            {"source": "crossref", "status": "results", "total_results": 10, "returned_records": 1},
            {"source": "openalex", "status": "zero_results", "total_results": 0, "returned_records": 0},
        ),
    )
    coverage = mcp._response(response, scope="bibliographic_search")["coverage"]
    # 합친 반환 건수에 하나의 범위를 붙이면 거짓이 된다.
    assert coverage["result_range"] is None
    assert [stat["result_range"] for stat in coverage["source_stats"]] == ["1-1", None]
    assert coverage["duplicate_records"] == 1


def test_zero_results_have_no_range():
    response = PatentSearchResponse(records=(), total_found=0)
    coverage = mcp._response(response, scope="bibliographic_search")["coverage"]
    assert coverage["result_range"] is None
    assert coverage["status"] == "zero_results"
