"""Crossref·Europe PMC 응답 고정 자료.

epo_fixtures 와 달리 **실제 호출로 받은 응답**이다(2026-09-01 채취). 형태만
흉내 낸 자료를 쓰지 않는 이유는, 이 파서가 다루어야 하는 것이 바로 실제 응답의
지저분한 부분이기 때문이다 — Crossref 초록은 JATS 조각으로 오고, 저자는
given/family 로 쪼개져 있고, Europe PMC 는 제목 끝에 마침표를 붙인다. 손으로
만든 자료로는 그 셋 다 놓친다.

크기를 줄이려고 참고문헌 목록(reference)과 전문 링크 목록(fullTextUrlList) 등
파서가 읽지 않는 배열은 지웠다. 파서가 읽는 경로는 하나도 건드리지 않았으며,
배열 인덱스가 바뀌지 않도록 항목을 지우는 대신 키만 지웠다.

자격증명은 어디에도 들어 있지 않다. 두 API 모두 인증이 없다.
"""

from __future__ import annotations

from pathlib import Path

_DIR = Path(__file__).parent / "fixtures" / "literature"

# 목표 문헌. 2026-09-01 조사에서 웹 채널이 식별하지 못한 논문이다.
TARGET_DOI = "10.3390/s25103219"
TARGET_TITLE = (
    "The Design of a Computer Vision Sensor Based on a Low-Power Edge "
    "Detection Circuit"
)


def _read(name: str) -> bytes:
    return (_DIR / name).read_bytes()


#: ``/works/10.3390/s25103219`` 응답. 초록이 JATS 조각으로 들어 있다.
CROSSREF_WORK = _read("crossref_work.json")

#: ``/works?query.bibliographic=computer vision sensor low-power edge detection
#: circuit`` 응답. 목표 문헌이 1위로 온다.
CROSSREF_SEARCH = _read("crossref_search.json")

#: Europe PMC 개념 검색 응답. 같은 문헌이 1위로 온다 — Crossref 가 제목으로
#: 찾는 것과 달리 이쪽은 초록 문장으로 찾는다.
EUROPEPMC_SEARCH = _read("epmc_search.json")

#: ``DOI:"10.3390/s25103219"`` 로 지정한 Europe PMC 응답. 초록이 평문이다.
EUROPEPMC_DETAIL = _read("epmc_detail.json")

#: 결과 0건인 Crossref 검색 응답.
CROSSREF_EMPTY = (
    b'{"status":"ok","message-type":"work-list","message":'
    b'{"total-results":0,"items":[]}}'
)

#: 결과 0건인 Europe PMC 응답.
EUROPEPMC_EMPTY = b'{"version":"6.9","hitCount":0,"resultList":{"result":[]}}'

# --- OpenAlex (2026-09-15 채취, 키 없이, select 로 필드를 줄여 받은 실제 응답) ---

#: ``/works/https://doi.org/10.3390/s25103219`` 응답. 초록이 단어 위치 색인으로 온다.
OPENALEX_WORK = _read("openalex_work.json")
#: 목표 문헌의 OpenAlex 작업 id. 인용 확장의 기준이다.
OPENALEX_WORK_ID = "W4410539571"

#: Crossref 에는 초록이 없던 IEEE 논문. OpenAlex 에는 초록이 있다.
IEEE_DOI = "10.1109/icdh.2012.31"
OPENALEX_WORK_IEEE = _read("openalex_work_ieee.json")

#: ``search=computer vision sensor low-power edge detection circuit`` 응답(3건).
#: 전문 색인 검색이라 목표 문헌이 상위에 오지 않는다 — 실제 동작 그대로다.
OPENALEX_SEARCH = _read("openalex_search.json")
OPENALEX_SEARCH_TOTAL = 36912

#: ``filter=cites:W4410539571`` 응답. 목표 문헌을 인용한 2건.
OPENALEX_CITES = _read("openalex_cites.json")
OPENALEX_CITING_DOI = "10.3390/s26030962"

#: arXiv 논문의 OpenAlex 단건 응답. arXiv API 가 이 PC 에서 429 로 막혀 있어
#: arXiv DOI 조회는 이 응답으로 이어받는다.
ARXIV_DOI = "10.48550/arxiv.2412.19860"
OPENALEX_WORK_ARXIV = _read("openalex_work_arxiv.json")
