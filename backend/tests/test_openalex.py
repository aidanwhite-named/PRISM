"""OpenAlex 채널 — pyalex 요청, 원본 보존, 재추출.

test_literature 와 같은 계약을 지킨다. **논문 후보의 근거는 보존된 응답에서 다시
뽑은 값이어야 한다.** 여기에 OpenAlex 고유의 사실 셋을 더 고정한다.

  - pyalex 가 가공한 객체가 아니라, 세션 훅이 잡은 **원본 바이트**가 보존된다.
    pyalex 를 올렸는데 훅 경로가 바뀌면 이 테스트가 먼저 깨져야 한다.
  - HTTP 429 는 "문헌 없음"이 아니라 한도 소진으로 기록된다.
  - 초록 조회는 OpenAlex 를 Crossref 앞에 둔다. Crossref 가 IEEE 논문에 제목만
    돌려주고 끝나면 초록을 받을 길이 사라진다.
"""

from __future__ import annotations

import json
import urllib.parse

import pytest
import requests

from app.patent_search import (
    artifacts,
    compute_id,
    literature_backend,
    literature_client,
    literature_parser,
    openalex_client,
)
from app.patent_search.base import PatentSearchError, PatentSearchQuery

from . import literature_fixtures as fx


@pytest.fixture()
def store(tmp_path):
    return artifacts.ArtifactStore(tmp_path / "evidence")


def _openalex(routes: dict, calls: list | None = None):
    def send(url, api_key, timeout):
        if calls is not None:
            calls.append(url)
        for needle, (status, body) in routes.items():
            if needle in urllib.parse.unquote(url):
                return literature_client.HttpResponse(status=status, headers={}, body=body)
        raise AssertionError(f"예상하지 못한 OpenAlex 요청입니다: {url}")

    return send


def _literature(routes: dict, calls: list | None = None):
    def send(request, timeout):
        url = request.full_url
        if calls is not None:
            calls.append(url)
        for needle, (status, body) in routes.items():
            if needle in url:
                return literature_client.HttpResponse(status=status, headers={}, body=body)
        raise AssertionError(f"예상하지 못한 서지 요청입니다: {url}")

    return send


def _backend(store, openalex_routes, *, literature_routes=None, openalex_calls=None,
             literature_calls=None, key=""):
    return literature_backend.LiteratureBackend(
        client=literature_client.LiteratureClient(
            transport=_literature(literature_routes or {}, literature_calls)
        ),
        store=store,
        openalex=openalex_client.OpenAlexClient(
            api_key=key, transport=_openalex(openalex_routes, openalex_calls)
        ),
    )


# --- 파서 ------------------------------------------------------------------
def test_work_fields_are_reextractable_from_the_raw_bytes():
    work = literature_parser.read_openalex_work(fx.OPENALEX_WORK)
    assert work is not None
    assert work.doi == fx.TARGET_DOI
    assert work.title == fx.TARGET_TITLE
    # 단어 위치 색인이 문장으로 복원되어야 한다.
    assert len(work.abstract.split()) > 50
    assert ";" in work.authors
    for name, value in work.text_fields().items():
        assert literature_parser._extract_openalex(fx.OPENALEX_WORK, work.paths[name]) == value


def test_ieee_abstract_absent_from_crossref_comes_from_openalex():
    work = literature_parser.read_openalex_work(fx.OPENALEX_WORK_IEEE)
    assert work is not None
    assert work.doi == fx.IEEE_DOI
    assert work.abstract


def test_search_results_keep_their_position_in_the_artifact():
    works = literature_parser.read_openalex_results(fx.OPENALEX_SEARCH)
    assert len(works) == 3
    assert works[1].paths["title"] == "results/1/display_name"
    for work in works:
        for name, value in work.text_fields().items():
            assert literature_parser._extract_openalex(fx.OPENALEX_SEARCH, work.paths[name]) == value


# --- 요청 주소 (pyalex 가 만든다) ---------------------------------------------
def test_search_url_is_built_by_pyalex():
    url = urllib.parse.unquote(
        openalex_client.search_url('"edge detection" OR sensor', rows=5)
    )
    assert url.startswith("https://api.openalex.org/works?")
    # OpenAlex supports both exact phrases and Boolean alternatives.
    assert 'search="edge detection" OR sensor' in url.replace("+", " ")
    assert "per-page=5" in url
    assert "abstract_inverted_index" in url

    narrow = urllib.parse.unquote(
        openalex_client.search_url("edge", rows=3, mode=openalex_client.MODE_TITLE_ABSTRACT)
    )
    assert "title_and_abstract.search:edge" in narrow

    citing = urllib.parse.unquote(openalex_client.search_url("edge", rows=3, cites="W1"))
    assert "cites:W1" in citing


def test_work_url_is_a_doi_lookup():
    url = urllib.parse.unquote(openalex_client.work_url("https://doi.org/10.3390/S25103219"))
    assert url.startswith("https://api.openalex.org/works/https://doi.org/10.3390/s25103219?")


# --- 원본 캡처 --------------------------------------------------------------
class _FakeAdapter(requests.adapters.BaseAdapter):
    def __init__(self, status: int, body: bytes, seen: list):
        super().__init__()
        self.status, self.body, self.seen = status, body, seen

    def send(self, request, **kwargs):
        self.seen.append((request.url, request.headers.get("Authorization"), kwargs.get("timeout")))
        response = requests.models.Response()
        response.status_code = self.status
        response._content = self.body
        response.url = request.url
        response.request = request
        response.headers["Content-Type"] = "application/json"
        return response

    def close(self):
        pass


def _factory(status, body, seen):
    def make(timeout, captured):
        session = openalex_client._session(timeout, captured)
        session.mount("https://", _FakeAdapter(status, body, seen))
        return session

    return make


def test_pyalex_get_returns_the_bytes_the_server_sent():
    import pyalex

    seen: list = []
    response = openalex_client.pyalex_get(
        openalex_client.work_url(fx.TARGET_DOI), "KEY-1", 7.0,
        session_factory=_factory(200, fx.OPENALEX_WORK, seen),
    )
    assert response.status == 200
    assert response.body == fx.OPENALEX_WORK
    url, authorization, timeout = seen[0]
    assert authorization == "Bearer KEY-1"
    # pyalex 는 timeout 을 주지 않는다. 세션이 기본값을 넣어야 한다.
    assert timeout == 7.0
    # 전역 설정에 키를 남기지 않는다.
    assert pyalex.config.api_key in (None, "")


def test_pyalex_errors_still_hand_back_the_response():
    response = openalex_client.pyalex_get(
        openalex_client.work_url(fx.TARGET_DOI), "", 5.0,
        session_factory=_factory(429, b'{"error":"rate limited"}', []),
    )
    assert response.status == 429


# --- 백엔드 -----------------------------------------------------------------
def test_openalex_search_preserves_raw_bytes_and_index_total(store):
    backend = _backend(store, {"works?": (200, fx.OPENALEX_SEARCH)})
    response = backend.search(PatentSearchQuery("computer vision sensor", 3), sources=("openalex",))
    assert len(response.records) == 3
    assert response.failed_sources == ()
    assert response.source_stats == ({
        "source": "openalex", "status": "results",
        "total_results": fx.OPENALEX_SEARCH_TOTAL, "returned_records": 3,
    },)
    artifact_id = compute_id(fx.OPENALEX_SEARCH)
    for record in response.records:
        for field in record.fields.values():
            assert field.evidence.artifact_id == artifact_id
            assert field.evidence.profile_id == literature_parser.PROFILE_OPENALEX_JSON


def test_http_429_is_a_quota_failure_not_absence(store):
    backend = _backend(store, {"works?": (429, b"{}")})
    response = backend.search(PatentSearchQuery("edge", 3), sources=("openalex",))
    assert response.records == ()
    assert response.failed_sources == ("openalex",)
    assert response.source_stats[0]["status"] == "failed"
    note = " ".join(response.notes)
    assert "429" in note and "문헌이 없다는 뜻이 아닙니다" in note
    assert "API 키가 없어" in note


def test_default_search_includes_openalex_only_when_configured(store):
    """설정을 거친 백엔드(get_backend)는 세 곳에 묻고, 직접 만든 백엔드는 두 곳에 묻는다."""
    routes = {
        "api.crossref.org": (200, fx.CROSSREF_EMPTY),
        "europepmc": (200, fx.EUROPEPMC_EMPTY),
    }
    plain = literature_backend.LiteratureBackend(
        client=literature_client.LiteratureClient(transport=_literature(routes)), store=store
    )
    assert [s["source"] for s in plain.search(PatentSearchQuery("edge", 3)).source_stats] == [
        "crossref", "europepmc"
    ]
    calls: list = []
    configured = _backend(store, {"works?": (200, fx.OPENALEX_SEARCH)},
                          literature_routes=routes, openalex_calls=calls)
    configured.configure({})
    stats = configured.search(PatentSearchQuery("edge", 3)).source_stats
    assert [s["source"] for s in stats] == ["crossref", "europepmc", "openalex"]
    assert len(calls) == 1


def test_abstract_fetch_asks_openalex_before_crossref(store):
    literature_calls: list = []
    backend = _backend(
        store,
        {f"doi.org/{fx.IEEE_DOI}": (200, fx.OPENALEX_WORK_IEEE)},
        literature_routes={"europepmc": (200, fx.EUROPEPMC_EMPTY)},
        literature_calls=literature_calls,
    )
    response = backend.fetch_document(fx.IEEE_DOI, "abstract")
    assert len(response.records) == 1
    abstract = response.records[0].fields["abstract"]
    assert abstract.evidence.profile_id == literature_parser.PROFILE_OPENALEX_JSON
    assert not any("crossref" in url for url in literature_calls)


def test_openalex_failure_falls_back_to_crossref(store):
    backend = _backend(
        store,
        {"doi.org": (429, b"{}")},
        literature_routes={
            "europepmc": (200, fx.EUROPEPMC_EMPTY),
            "api.crossref.org": (200, fx.CROSSREF_WORK),
        },
    )
    response = backend.fetch_document(fx.TARGET_DOI, "abstract")
    assert len(response.records) == 1
    assert response.records[0].fields["title"].evidence.profile_id == literature_parser.PROFILE_CROSSREF_JSON
    assert response.failed_sources == ("openalex",)


def test_a_different_work_is_not_evidence_for_the_requested_doi(store):
    backend = _backend(
        store,
        {"doi.org": (200, fx.OPENALEX_WORK)},
        literature_routes={
            "europepmc": (200, fx.EUROPEPMC_EMPTY),
            "api.crossref.org": (404, b""),
        },
    )
    response = backend.fetch_document(fx.IEEE_DOI, "abstract")
    assert response.records == ()
    assert any("DOI 가 요청한 값과 다릅니다" in note for note in response.notes)


def test_citation_expansion_starts_from_the_seed_work(store):
    calls: list = []
    backend = _backend(
        store,
        {"cites:": (200, fx.OPENALEX_CITES), "works/https://doi.org": (200, fx.OPENALEX_WORK)},
        openalex_calls=calls,
    )
    response = backend.search(PatentSearchQuery("edge", 3), cites_doi=fx.TARGET_DOI)
    assert len(calls) == 2
    assert f"cites:{fx.OPENALEX_WORK_ID}" in urllib.parse.unquote(calls[1])
    assert fx.OPENALEX_CITING_DOI in {record.doc_number for record in response.records}
    # 기준 문헌 응답도 보존된다 — "무엇을 인용한 목록인가"의 근거다.
    assert store.exists(compute_id(fx.OPENALEX_WORK))


def test_citation_expansion_is_openalex_only(store):
    backend = _backend(store, {})
    with pytest.raises(PatentSearchError):
        backend.search(PatentSearchQuery("edge", 3), sources=("crossref",), cites_doi=fx.TARGET_DOI)


def _without(body: bytes, strip) -> bytes:
    document = json.loads(body)
    strip(document)
    return json.dumps(document).encode("utf-8")


def _no_epmc_abstract(document):
    for item in document["resultList"]["result"]:
        item.pop("abstractText", None)


def test_a_record_without_abstract_does_not_end_the_abstract_search(store):
    """Europe PMC 가 초록 없는 레코드를 줘도 OpenAlex 까지 가야 한다."""
    backend = _backend(
        store,
        {f"doi.org/{fx.TARGET_DOI}": (200, fx.OPENALEX_WORK)},
        literature_routes={"europepmc": (200, _without(fx.EUROPEPMC_DETAIL, _no_epmc_abstract))},
    )
    response = backend.fetch_document(fx.TARGET_DOI, "abstract")
    abstract = response.records[0].fields["abstract"]
    assert abstract.evidence.profile_id == literature_parser.PROFILE_OPENALEX_JSON
    assert "europepmc 레코드에 초록이 없습니다." in response.notes


def test_when_no_source_has_an_abstract_the_first_record_is_kept(store):
    def no_openalex_abstract(document):
        document["abstract_inverted_index"] = None

    def no_crossref_abstract(document):
        document["message"].pop("abstract", None)

    backend = _backend(
        store,
        {f"doi.org/{fx.TARGET_DOI}": (200, _without(fx.OPENALEX_WORK, no_openalex_abstract))},
        literature_routes={
            "europepmc": (200, _without(fx.EUROPEPMC_DETAIL, _no_epmc_abstract)),
            "api.crossref.org": (200, _without(fx.CROSSREF_WORK, no_crossref_abstract)),
        },
    )
    response = backend.fetch_document(fx.TARGET_DOI, "abstract")
    assert len(response.records) == 1
    record = response.records[0]
    assert "abstract" not in record.fields
    assert record.fields["title"].evidence.profile_id == literature_parser.PROFILE_EUROPEPMC_JSON
    assert sum("초록이 없습니다" in note for note in response.notes) == 3


# --- arXiv DOI -------------------------------------------------------------
_ATOM = b"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/">
<opensearch:totalResults>1</opensearch:totalResults>
<entry><id>http://arxiv.org/abs/2412.19860v1</id><published>2024-12-27T00:00:00Z</published>
<updated>2024-12-27T00:00:00Z</updated><title>UniAvatar</title><summary>An abstract.</summary>
<author><name>A B</name></author><category term="cs.CV"/></entry></feed>"""


def test_arxiv_doi_falls_back_to_openalex_when_arxiv_refuses(store, monkeypatch):
    from app.patent_search import arxiv_backend

    def refuse(self, query="", *, identifier="", rows=5):
        raise PatentSearchError("arxiv_request_failed: HTTPError")

    monkeypatch.setattr(arxiv_backend.ArxivBackend, "query", refuse)
    # Crossref 경로가 없다 — OpenAlex 가 먼저 답하면 Crossref 는 불리지 않아야 한다.
    backend = _backend(store, {f"doi.org/{fx.ARXIV_DOI}": (200, fx.OPENALEX_WORK_ARXIV)})
    response = backend.fetch_document("10.48550/arXiv.2412.19860", "abstract")
    assert [record.doc_number for record in response.records] == [fx.ARXIV_DOI]
    abstract = response.records[0].fields["abstract"]
    assert abstract.evidence.profile_id == literature_parser.PROFILE_OPENALEX_JSON
    assert response.failed_sources == ("arxiv",)
    assert any(note.startswith("arxiv 조회 실패") for note in response.notes)


def test_arxiv_answer_is_used_when_arxiv_responds(store, monkeypatch):
    """막힘이 풀린 날의 경로. 응답을 여러 번 받아도 파서 재등록 오류가 나지 않는다."""
    from app.patent_search import arxiv_backend

    def answer(self, query="", *, identifier="", rows=5):
        return arxiv_backend.materialize(_ATOM, self.store, "https://export.arxiv.org/api/query", 200)

    monkeypatch.setattr(arxiv_backend.ArxivBackend, "query", answer)
    calls: list = []
    backend = _backend(store, {}, openalex_calls=calls)
    for _ in range(2):
        response = backend.fetch_document(fx.ARXIV_DOI, "abstract")
        assert [record.doc_number for record in response.records] == [fx.ARXIV_DOI]
    assert calls == []


# --- 설정·MCP ---------------------------------------------------------------
def test_openalex_key_is_stored_but_never_returned(client):
    updated = client.put("/api/settings", json={"values": {"literature_openalex_api_key": "OA-KEY"}})
    assert updated.status_code == 200
    body = updated.json()
    assert body["values"]["literature_openalex_api_key"] == ""
    assert body["secrets_set"]["literature_openalex_api_key"] is True
    assert "OA-KEY" not in updated.text

    rejected = client.put("/api/settings", json={"values": {"literature_openalex_api_key": "OA KEY"}})
    assert rejected.status_code == 400


def test_connection_check_uses_a_free_doi_lookup(client, monkeypatch):
    seen: list = []

    def live(url, api_key, timeout):
        seen.append(url)
        return literature_client.HttpResponse(status=200, headers={}, body=fx.OPENALEX_WORK)

    monkeypatch.setattr(openalex_client, "_live_transport", live)
    # 설정은 테스트 사이에 남는다. 키 없는 상태를 명시한다.
    client.put("/api/settings", json={"values": {"literature_openalex_api_key": ""}})
    result = client.post("/api/settings/openalex/check").json()
    assert result["ok"] is True
    assert "키 없이" in result["detail"]
    assert "/works/" in seen[0] and "search=" not in seen[0]


def test_mcp_offers_openalex_and_scrubs_its_key(tmp_path):
    from app import search_mcp_server as mcp

    properties = mcp._LITERATURE_SEARCH["inputSchema"]["properties"]
    assert "openalex" in properties["source"]["enum"]
    assert set(properties["openalex_mode"]["enum"]) == set(openalex_client.MODES)
    assert "cites_doi" in properties
    tools = mcp.SearchTools(values={"literature_openalex_api_key": "OA-SECRET"}, work_dir=tmp_path)
    assert "OA-SECRET" in tools.secrets
