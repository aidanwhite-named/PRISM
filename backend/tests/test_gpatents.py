"""Google Patents 원문 페이지 채널과 OPS 인용 확장.

지키려는 것:

1. 페이지는 PRISM 이 문헌번호로 받고, 사람 속도를 넘지 않는다.
2. 번역 페이지에서도 원어 문장을 뽑고, 청구항·문단 번호로 위치를 계산한다.
3. 페이지에서 글자 그대로 확인된 발췌는 "원문 페이지 대조 확인"까지만 오른다.
   공식 청구항 등급이 되지 않고, 페이지 서지는 공식 확보 범위로 세지 않는다.
4. OPS 피인용 필드(ct)와 인용문헌(references_cited)을 모델이 쓸 수 있다.

HTML 표본은 2026-09-15 에 받은 실제 페이지의 구조(JP 번역 구역의 google-src-text,
US 의 num="00001" 청구항, EP 의 para-num)를 줄여 옮긴 것이다.
"""

from __future__ import annotations

import json

import pytest

from app import search_channels, search_followup, search_manifest as sm, search_report
from app import search_verification as sv
from app import search_mcp_server as mcp
from app.patent_search import artifacts, epo_cql, epo_parser, gpatents_backend as gb
from app.patent_search import gpatents_parser as gp
from app.patent_search import parsers
from app.providers.base import CODEX_WEB_SEARCH, NO_TOOLS, WEB_SEARCH, ExecutionRequest
from app.providers.claude_cli import WRITE_TOOLS, ClaudeCliProvider
from app.providers.codex_cli import CodexCliProvider

JP_TRANSLATED = """<html lang="en"><head>
<meta name="DC.title" content="
  Method and program for determining posture of skeletal model
">
</head><body>
<section itemprop="metadata"><dl>
<dd itemprop="publicationNumber">JP7475618B1</dd>
<dd itemprop="assigneeOriginal">Celsys Inc</dd>
<dd itemprop="inventor">聡 葛見</dd>
</dl>
<time itemprop="priorityDate" datetime="2023-12-04">2023-12-04</time>
<time itemprop="publicationDate" datetime="2024-04-30">2024-04-30</time>
</section>
<section itemprop="abstract" itemscope><h2>Abstract</h2>
<aside>Translated from <span itemprop="translatedLanguage">Japanese</span></aside>
<div itemprop="content" html><abstract lang="EN" load-source="google">
<span class="notranslate"><span class="google-src-text">【課題】骨の姿勢を決定する。</span>To determine bone posture.</span>
</abstract></div></section>
<section itemprop="claims" itemscope>
<h2>Claims (<span itemprop="count">2</span>)</h2>
<aside>Translated from <span itemprop="translatedLanguage">Japanese</span></aside>
<div itemprop="content" html><claims lang="EN" load-source="google">
<claim num="1"><claim-text><span class="notranslate"> <span class="google-src-text">
  親骨と子骨とが関節によって連結された骨格モデルにおいて、<br/>
  前記第１の制限は、少なくとも一つのパラメータが他の二つ以上のパラメータに係る制限に影響を与える制限で表される、
</span> In a skeletal model in which a parent bone and a child bone are connected.</span></claim-text></claim>
<claim num="2"><claim-text><span class="notranslate"><span class="google-src-text">前記第１の制限は、単調減少する制限を含む。</span>The first restriction decreases monotonically.</span></claim-text></claim>
</claims></div></section>
<section itemprop="description" itemscope><h2>Description</h2>
<div itemprop="content" html><description lang="EN" load-source="google">
<technical-field><p num="0001"><span class="notranslate"><span class="google-src-text">本開示の技術は、骨格モデルに関する。</span>This relates to skeletal models.</span></p></technical-field>
<p num="0045"><span class="notranslate"><span class="google-src-text">捻りの範囲は曲げに応じて狭くなる。</span>The twist range narrows.</span></p>
</description></div></section>
<table>
<tr itemprop="backwardReferencesOrig" itemscope repeat><td><a href="/patent/JP2009070340A/en">
<span itemprop="publicationNumber">JP2009070340A</span> (<span itemprop="primaryLanguage">en</span)</a>
<span itemprop="examinerCited">*</span></td>
<td itemprop="priorityDate">2007-09-18</td><td itemprop="publicationDate">2009-04-02</td>
<td><span itemprop="assigneeOriginal">Namco Bandai Games Inc</span></td>
<td itemprop="title">Skeletal motion control system</td></tr>
<tr itemprop="backwardReferencesFamily" itemscope repeat><td>
<span itemprop="publicationNumber">JP5490080B2</span></td>
<td itemprop="priorityDate">2011-12-06</td><td itemprop="publicationDate">2014-05-14</td>
<td><span itemprop="assigneeOriginal">Celsys Inc</span></td><td itemprop="title">Posture control</td></tr>
</table>
</body></html>""".encode("utf-8")

US_PAGE = """<html><head><meta name="DC.title" content="Action modeling device"></head><body>
<dd itemprop="publicationNumber">US9208613B2</dd>
<time itemprop="publicationDate">2015-12-08</time>
<section itemprop="claims" itemscope><h2>Claims</h2><div itemprop="content" html>
<div lang="EN" load-source="patent-office" class="claims">
<claim-statement>The invention claimed is:</claim-statement>
<div class="claim"> <div id="CLM-00001" num="00001" class="claim">
<div class="claim-text">1. An action modeling device comprising: <div class="claim-text">an angle range data storage;</div></div></div></div>
</div></div></section>
<section itemprop="description" itemscope><div itemprop="content" html>
<div lang="EN" load-source="patent-office" class="description">
<heading id="h-0001">TECHNICAL FIELD</heading>
<div id="p-0046" num="0045" class="description-paragraph">The range of motion varies with the hand position.</div>
</div></div></section>
<tr itemprop="forwardReferencesOrig"><td><span itemprop="publicationNumber">US20210394068A1</span>
<span itemprop="examinerCited">*</span></td><td itemprop="publicationDate">2021-12-23</td>
<td><span itemprop="assigneeOriginal">Nintendo Co., Ltd.</span></td><td itemprop="title">Game apparatus</td></tr>
</body></html>""".encode("utf-8")

EP_DESCRIPTION = """<html><body><dd itemprop="publicationNumber">EP2677502A1</dd>
<section itemprop="description" itemscope><div itemprop="content" html>
<ul lang="EN" load-source="patent-office" class="description">
<heading>BACKGROUND ART</heading>
<li> <para-num num="[0012]"> </para-num> <div num="p0012" class="description-line">The elbow range depends on the hand.</div> </li>
</ul></div></section></body></html>""".encode("utf-8")


# ------------------------------------------------------------------ 파서


def test_translated_page_yields_original_text_with_claim_markers() -> None:
    fields = gp.read_page(JP_TRANSLATED).fields
    claims = fields["page_claims"]
    assert claims.startswith("[claim 1] 親骨と子骨とが関節によって連結された骨格モデルにおいて、")
    assert "[claim 2] 前記第１の制限は、単調減少する制限を含む。" in claims
    # 번역문은 뽑지 않는다.
    assert "parent bone" not in claims
    assert fields["page_abstract"] == "【課題】骨の姿勢を決定する。"
    assert fields["title"] == "Method and program for determining posture of skeletal model"
    assert fields["publication_number"] == "JP7475618B1"
    assert fields["publication_date"] == "2024-04-30"
    assert fields["priority_date"] == "2023-12-04"
    assert fields["applicants"] == "Celsys Inc"
    assert "원어 문장만 추출(원어: Japanese)" in fields["page_source_note"]
    assert "비공식" in fields["page_source_note"]


def test_paragraph_markers_for_each_page_layout() -> None:
    assert "[0045] 捻りの範囲は曲げに応じて狭くなる。" in gp.read_page(JP_TRANSLATED).fields["page_description"]
    us = gp.read_page(US_PAGE).fields
    assert "[claim 1] 1. An action modeling device comprising:" in us["page_claims"]
    assert "[0045] The range of motion varies with the hand position." in us["page_description"]
    ep = gp.read_page(EP_DESCRIPTION).fields["page_description"]
    assert "[0012] The elbow range depends on the hand." in ep
    assert "p0012" not in ep


def test_citation_lists_keep_examiner_mark_and_family_rows() -> None:
    cited = gp.read_page(JP_TRANSLATED).fields["page_cited_patents"]
    assert "JP2009070340A * | priority 2007-09-18 | published 2009-04-02 | Namco Bandai Games Inc" in cited
    assert "[family] JP5490080B2 | priority 2011-12-06" in cited
    assert "US20210394068A1 *" in gp.read_page(US_PAGE).fields["page_cited_by"]


def test_verifier_reextracts_the_same_text_through_the_profile() -> None:
    extracted = parsers.extract(JP_TRANSLATED, "page_claims", gp.PROFILE_GOOGLE_PATENTS_PAGE)
    assert extracted.text == gp.read_page(JP_TRANSLATED).fields["page_claims"]
    assert extracted.raw_capable is False
    assert extracted.source_kind == "normalized"
    assert extracted.translation_state == "unknown"
    with pytest.raises(parsers.FieldPathMissing):
        parsers.extract(JP_TRANSLATED, "../secret", gp.PROFILE_GOOGLE_PATENTS_PAGE)


def test_location_comes_from_markers_not_from_the_model() -> None:
    fields = gp.read_page(US_PAGE).fields
    claims = fields["page_claims"]
    assert gp.location_of("page_claims", claims, claims.find("angle range")) == "청구항 1"
    text = fields["page_description"]
    assert gp.location_of("page_description", text, text.find("hand position")) == "명세서 문단 [0045]"
    assert gp.location_of("page_description", text, text.find("TECHNICAL")) == "명세서"


# ---------------------------------------------------------------- 백엔드


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0
        self.sleeps: list[float] = []

    def time(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def _backend(tmp_path, pages: dict, *, clock=None, interval=6):
    clock = clock or FakeClock()
    requests: list[str] = []

    def transport(url, timeout):
        requests.append(url)
        number = url.split("/patent/", 1)[1].split("/", 1)[0]
        if number not in pages:
            return gb.PageResponse(status=404, body=b"", url=url)
        status, body = pages[number] if isinstance(pages[number], tuple) else (200, pages[number])
        return gb.PageResponse(status=status, body=body, url=url)

    gate = gb.HumanPaceGate(tmp_path / "state", min_interval=interval, jitter=0,
                            clock=clock.time, sleep=clock.sleep)
    backend = gb.GooglePatentsPageBackend(
        transport=transport, store=artifacts.ArtifactStore(tmp_path / "evidence"), gate=gate)
    return backend, requests, clock


def test_backend_builds_original_language_url_and_preserves_bytes(tmp_path) -> None:
    backend, requests, _ = _backend(tmp_path, {"JP7475618B1": JP_TRANSLATED})
    response = backend.fetch_document("jp 7475618 b1", "claims")
    assert requests == ["https://patents.google.com/patent/JP7475618B1/ja"]
    record = response.records[0]
    assert record.doc_number == "JP7475618B1"
    assert "page_claims" in record.fields and "page_description" not in record.fields
    ref = record.fields["page_claims"].evidence
    assert ref.profile_id == gp.PROFILE_GOOGLE_PATENTS_PAGE and ref.field_path == "page_claims"
    assert backend.artifact_store.read(ref.artifact_id) == JP_TRANSLATED


@pytest.mark.parametrize("raw", ["JP2009070340", "https://evil.example/patent", "EP12"])
def test_numbers_without_kind_code_or_urls_are_refused_before_any_request(tmp_path, raw) -> None:
    backend, requests, _ = _backend(tmp_path, {})
    with pytest.raises(gb.PatentSearchError):
        backend.fetch_document(raw)
    assert requests == []


def test_missing_page_is_reported_as_not_found_not_absence(tmp_path) -> None:
    backend, _, _ = _backend(tmp_path, {})
    with pytest.raises(gb.GooglePatentsNotFound, match="문헌 부재의 증거가 아닙니다"):
        backend.fetch_document("CN122340114A")


def test_requests_keep_human_pace_and_stop_after_a_refusal(tmp_path) -> None:
    clock = FakeClock()
    backend, requests, clock = _backend(
        tmp_path, {"JP7475618B1": JP_TRANSLATED, "US9208613B2": US_PAGE,
                   "EP2677502A1": (429, b"")}, clock=clock)
    backend.fetch_document("JP7475618B1")
    clock.now += 1
    backend.fetch_document("US9208613B2")
    assert clock.sleeps == [pytest.approx(5.0)]
    with pytest.raises(gb.GooglePatentsRateLimited):
        backend.fetch_document("EP2677502A1")
    clock.now += 60
    with pytest.raises(gb.GooglePatentsRateLimited, match="멈춥니다"):
        backend.fetch_document("US9208613B2", "description")
    # 거절 뒤에는 요청 자체를 보내지 않는다.
    assert len(requests) == 3


def test_pace_is_shared_across_backend_instances(tmp_path) -> None:
    """MCP 서버와 후속 검증은 다른 프로세스다. 간격 기록은 파일에 남는다."""
    clock = FakeClock()
    first, _, _ = _backend(tmp_path, {"JP7475618B1": JP_TRANSLATED}, clock=clock)
    first.fetch_document("JP7475618B1")
    second, _, _ = _backend(tmp_path, {"US9208613B2": US_PAGE}, clock=clock)
    second.fetch_document("US9208613B2")
    assert clock.sleeps == [pytest.approx(6.0)]


# ------------------------------------------------------------- MCP 도구

_VALUES = {"gpatents_page_enabled": True, "gpatents_max_fetches_per_run": 1}


def _tools(tmp_path, monkeypatch, pages):
    backend, requests, _ = _backend(tmp_path, pages)
    backend.max_fetches_per_run = 1
    monkeypatch.setattr(mcp, "get_backend", lambda values, backend_id: backend)
    return mcp.SearchTools(values=dict(_VALUES), work_dir=tmp_path / "run"), requests


def test_tool_reuses_a_fetched_page_and_caps_new_pages_per_run(tmp_path, monkeypatch) -> None:
    tools, requests = _tools(tmp_path, monkeypatch, {"JP7475618B1": JP_TRANSLATED, "US9208613B2": US_PAGE})
    names = [tool["name"] for tool in tools.tool_definitions()]
    assert "gpatents_fetch" in names and "gpatents_search" not in names

    first = tools.call("gpatents_fetch", {"publication_number": "JP7475618B1"})
    assert first["network_fetch"] is True and first["identifier_matched"] is True
    assert "비공식" in first["source_notice"]
    again = tools.call("gpatents_fetch", {"publication_number": "JP7475618B1", "constituent": "citations"})
    assert again["network_fetch"] is False
    assert "JP2009070340A *" in again["records"][0]["fields"]["page_cited_patents"]
    with pytest.raises(gb.GooglePatentsFetchLimit):
        tools.call("gpatents_fetch", {"publication_number": "US9208613B2"})
    assert len(requests) == 1
    journal = sm.read_tool_journal(tmp_path / "run")
    assert journal[-1]["ok"] is False and journal[-1]["error_code"] == "GooglePatentsFetchLimit"


def test_channel_exposes_fetch_only_and_counts_as_retrieval() -> None:
    status = search_channels.availability({"gpatents_page_enabled": True})
    assert status["gpatents"]["status"] == "available"
    names = search_channels.available_mcp_names(status)
    assert "mcp__prism-search__gpatents_fetch" in names
    assert "mcp__prism-search__gpatents_search" not in names
    assert search_channels.availability({"gpatents_page_enabled": False})["gpatents"]["status"] == "disabled"
    assert sm.has_retrieval_attempt([], [], [{"state": "started", "tool": "gpatents_fetch"}])


# --------------------------------------------------------------- 검증 등급


def _page_journal(tmp_path, monkeypatch, constituent="claims"):
    tools, _ = _tools(tmp_path, monkeypatch, {"JP7475618B1": JP_TRANSLATED})
    result = tools.call("gpatents_fetch", {"publication_number": "JP7475618B1", "constituent": constituent})
    return sm.read_tool_journal(tmp_path / "run"), result, tools


def _candidate(result, **row):
    fields = result["records"][0]["fields"]
    refs = result["records"][0]["evidence_refs"]
    return sm.parse(json.dumps({"candidates": [{
        "doc_number": "JP7475618B1", "group": "A", "title": "骨格モデルの姿勢決定方法",
        "mapping": [{"feature": "(D) 제1 한계", "degree": "강한 대응",
                     "evidence_ref": refs.get("page_claims"), **row}]}]}))[0], fields


def test_exact_page_excerpt_is_page_verified_not_official(tmp_path, monkeypatch) -> None:
    journal, result, _ = _page_journal(tmp_path, monkeypatch)
    excerpt = "前記第１の制限は、単調減少する制限を含む。"
    data, fields = _candidate(result, support_text=excerpt, verbatim_excerpt=excerpt,
                              translation="상기 제1 한계는 단조 감소하는 한계를 포함한다.",
                              source_location="모델이 지어낸 위치")
    candidate = sv.verify(data, {}, journal, store=tools_store(tmp_path))["candidates"][0]
    row = candidate["mapping"][0]
    assert candidate["evidence_level"] == "source_page_text_verified"
    assert candidate["verification_scope"]["page_text"] == "verified"
    assert candidate["verification_scope"]["claims"] == "not_requested"
    assert candidate["verification_scope"]["bibliographic"] == "not_requested"
    assert row["support_verified"] and row["page_quote_verified"] and not row["quote_verified"]
    assert row["support_origin"] == "google_patents_page"
    assert row["source_location"] == "청구항 2" and row["support_location"] == "청구항 2"
    assert row["translation"].startswith("상기 제1 한계")
    assert candidate["group"] == "A"
    rendered = search_report.render({"version": 14, "status": "complete", "group_definitions": {},
                                     "reported": {"candidates": [candidate]}})
    assert "원문 페이지 대조 확인 (Google Patents, 비공식) · 청구항 2" in rendered
    assert "원문 페이지 발췌 (비공식 출처) \\[청구항 2\\]" in rendered
    assert "번역 (LLM 작성, 원문 대조 대상 아님)" in rendered


def test_paraphrase_is_not_accepted_as_page_excerpt(tmp_path, monkeypatch) -> None:
    journal, result, _ = _page_journal(tmp_path, monkeypatch)
    data, _ = _candidate(result, support_text="第１の制限は単調減少", verbatim_excerpt="第１の制限は単調減少",
                         translation="번역")
    candidate = sv.verify(data, {}, journal, store=tools_store(tmp_path))["candidates"][0]
    row = candidate["mapping"][0]
    assert not row["support_verified"] and not row["page_quote_verified"]
    assert row["verbatim_excerpt"] == "" and row["translation"] == ""
    assert "quote_unverified" in candidate["verification_issues"]
    assert candidate["evidence_level"] == "source_page_reviewed"


@pytest.mark.parametrize("row", [{}, {"support_text": "invented", "verbatim_excerpt": "invented"},
                                 {"support_text": "前記第１の制限は、単調減少する制限を含む。", "evidence_ref": None}])
def test_page_delivery_without_matched_passage_does_not_verify_candidate(tmp_path, monkeypatch, row):
    journal, result, _ = _page_journal(tmp_path, monkeypatch)
    data, _ = _candidate(result, **row)
    candidate = sv.verify(data, {}, journal, store=tools_store(tmp_path))["candidates"][0]
    assert candidate["verification_scope"]["page_text"] == "verified"  # field preserved
    assert candidate["evidence_level"] == "source_page_reviewed"


def test_exact_page_support_can_verify_without_a_separate_quote(tmp_path, monkeypatch):
    journal, result, _ = _page_journal(tmp_path, monkeypatch)
    data, _ = _candidate(result, support_text="前記第１の制限は、単調減少する制限を含む。")
    candidate = sv.verify(data, {}, journal, store=tools_store(tmp_path))["candidates"][0]
    assert candidate["evidence_level"] == "source_page_text_verified"
    assert candidate["mapping"][0]["support_verified"]
    assert not candidate["mapping"][0]["page_quote_verified"]


def test_citation_only_fetch_does_not_raise_evidence_level(tmp_path, monkeypatch) -> None:
    journal, result, _ = _page_journal(tmp_path, monkeypatch, constituent="citations")
    data = sm.parse(json.dumps({"candidates": [{"doc_number": "JP7475618B1", "group": "B", "mapping": []}]}))[0]
    candidate = sv.verify(data, {}, journal, store=tools_store(tmp_path))["candidates"][0]
    assert candidate["evidence_level"] == "source_page_reviewed"
    assert candidate["verification_scope"]["page_text"] == "not_requested"
    assert "identifier_unverified" not in candidate["verification_issues"]


def tools_store(tmp_path):
    return artifacts.ArtifactStore(tmp_path / "evidence")


def test_followup_plans_one_page_fetch_for_patent_mapping_without_text() -> None:
    verified = {"candidates": [
        {"doc_number": "US9208613B2", "mapping": [{"feature": "F"}], "verification_issues": [],
         "verification_scope": {"claims": "unavailable"}},
        {"doc_number": "EP2677502A1", "mapping": [{"feature": "F"}], "verification_issues": [],
         "verification_scope": {"page_text": "verified"}},
    ]}
    availability = {"gpatents": {"status": "available"}, "epo": {"status": "disabled"}}
    allowed = ("mcp__prism-search__gpatents_fetch",)
    plan = search_followup.retrieval_plan(verified, [], availability, allowed)
    assert plan == [("gpatents_fetch", {"publication_number": "US9208613B2", "constituent": "claims"})]
    attempted = [{"tool": "gpatents_fetch", "arguments": {"publication_number": "US9208613B2"}}]
    assert search_followup.retrieval_plan(verified, attempted, availability, allowed) == []


# ------------------------------------------------------ Provider 실행 인수


def _request(tmp_path, policy):
    return ExecutionRequest(job_id="j", work_dir=tmp_path, system_prompt="s",
                            user_message="u", tool_policy=policy)


def test_codex_search_uses_live_web_search_but_analysis_does_not(tmp_path) -> None:
    search = CodexCliProvider().build_args(_request(tmp_path, CODEX_WEB_SEARCH))
    assert 'web_search="live"' in search
    assert search[search.index("--sandbox") + 1] == "read-only"
    analysis = CodexCliProvider().build_args(_request(tmp_path, NO_TOOLS))
    assert not any("web_search=\"live\"" in arg for arg in analysis)


@pytest.mark.parametrize("policy", [NO_TOOLS, WEB_SEARCH])
def test_claude_denies_write_and_shell_tools_by_name(tmp_path, policy) -> None:
    args = ClaudeCliProvider().build_args(_request(tmp_path, policy))
    start = args.index("--disallowedTools")
    assert tuple(args[start + 1:start + 1 + len(WRITE_TOOLS)]) == WRITE_TOOLS
    assert "--dangerously-skip-permissions" not in args


# ------------------------------------------------------------ OPS 인용 확장


def test_cql_accepts_forward_citation_field_as_exact_number() -> None:
    node = mcp._query_node({"type": "term", "field": "ct", "value": "JP2009070340", "match": "any"})
    assert epo_cql.build(node) == 'ct = "JP2009070340"'
    with pytest.raises(epo_cql.CqlError):
        epo_cql.build(mcp._query_node({"type": "term", "field": "ct", "value": "skeleton joint limit"}))


def test_biblio_exposes_backward_citations_when_ops_has_them() -> None:
    xml = b"""<ops:world-patent-data xmlns:ops="http://ops.epo.org" xmlns="http://www.epo.org/exchange">
<exchange-documents><exchange-document country="US" doc-number="2013141431" kind="A1" family-id="7">
<bibliographic-data>
<publication-reference><document-id document-id-type="docdb"><country>US</country>
<doc-number>2013141431</doc-number><kind>A1</kind><date>20130606</date></document-id></publication-reference>
<references-cited>
<citation cited-phase="search" cited-by="examiner" sequence="1"><patcit dnum-type="publication number" num="1">
<document-id document-id-type="epodoc"><doc-number>US6577315</doc-number></document-id>
<document-id document-id-type="docdb"><country>US</country><doc-number>6577315</doc-number><kind>B1</kind></document-id>
</patcit><category>X</category></citation>
<citation cited-by="applicant" sequence="2"><nplcit num="2"><text>Baerlocher P., Boulic R.  Parametrization and range of motion</text></nplcit></citation>
</references-cited>
</bibliographic-data></exchange-document></exchange-documents></ops:world-patent-data>"""
    document = epo_parser.read_documents(xml)[0]
    text = document.text_fields()["references_cited"]
    assert text.splitlines() == [
        "US6577315B1 | cited-by=examiner | cited-phase=search | category=X",
        "NPL: Baerlocher P., Boulic R. Parametrization and range of motion | cited-by=applicant",
    ]
    assert epo_parser._extract(xml, "documents/US.2013141431.A1/references_cited") == text
