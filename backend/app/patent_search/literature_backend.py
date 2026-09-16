"""비특허문헌 백엔드 — Crossref·Europe PMC 를 PRISM 이 직접 조회한다.

EPO 백엔드와 같은 자리를 차지하되 다루는 문헌 종류가 다르다. EPO OPS 는 특허를
주고 논문을 모른다. 청구항과 겨루는 선행문헌의 상당수가 논문이므로, 특허만
받는 공식 채널 하나로는 검증 단계가 논문 후보를 통째로 건너뛴다. 실제로
2026-09-01 실행에서 논문 후보 ``10.3390/s20133649`` 는 DOI 라는 이유로
search_verification.targets 에서 조용히 빠졌고, 웹에서도 mdpi.com 이 403 을
돌려줘 끝까지 "미확인 검색 단서"로 남았다.

두 가지 일을 한다
-----------------
발견(search)   모델이 실제로 사용한 검색어로 PRISM 이 직접 서지 DB 에 묻는다.
               웹 검색 각주가 익명이라 후보가 될 수 없었던 문헌을, 제목과 DOI 가
               붙은 상태로 데려온다.
확보(fetch)    후보의 DOI 로 등록 서지와 초록을 받아 원본을 보존한다. 발행사
               사이트(mdpi.com 등)를 열지 않으므로 403 에 걸리지 않는다.

자격증명이 없다
---------------
EPO 와 달리 키가 필요 없다. 그래서 ``configured`` 는 항상 참이고, 사용자가 켜기만
하면 동작한다. 대신 예의를 지키는 쪽으로 기본값을 잡는다 — 질의 수 상한과
네트워크 시간 예산을 두고, Crossref 에는 연락처를 함께 보낸다.
"""

from __future__ import annotations

from collections.abc import Mapping
import json
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any

from . import artifacts, literature_client, literature_parser, openalex_client
from .base import (
    BackendStatus,
    EvidenceRef,
    FieldValue,
    PatentRecord,
    PatentSearchBackend,
    PatentSearchError,
    PatentSearchQuery,
    PatentSearchResponse,
)

SETTING_ENABLED = "literature_integration_enabled"
SETTING_MAILTO = "literature_contact_email"
SETTING_MAX_RESULTS = "literature_max_results_per_query"
SETTING_HTTP_BUDGET = "literature_http_budget_seconds"
SETTING_OPENALEX_KEY = "literature_openalex_api_key"

BACKEND_ID = "literature"

SOURCE_OPENALEX = openalex_client.SOURCE_OPENALEX

# 검색 응답에서 "색인 전체의 적중 수"를 읽는 자리. 반환 건수와 다른 값이다.
_TOTAL_READERS = {
    literature_client.SOURCE_CROSSREF: lambda raw: (raw.get("message") or {}).get("total-results"),
    literature_client.SOURCE_EUROPEPMC: lambda raw: raw.get("hitCount"),
    SOURCE_OPENALEX: lambda raw: (raw.get("meta") or {}).get("count"),
}

# 논문에는 청구항이 없다. EPO 의 구성요소 목록을 그대로 쓰면 매 후보마다
# claims 조회가 한 번씩 실패하고, 그 실패가 기록에서 "청구항을 못 받았다"로
# 읽힌다. 받을 수 없는 것을 받으려 시도하지 않는다.
CONSTITUENTS = ("abstract", "biblio")

_READY_DETAIL = "Crossref·Europe PMC·OpenAlex 조회 준비됨"
_OPENALEX_NO_KEY_DETAIL = " (OpenAlex API 키 없음 — 일일 무료 한도가 작습니다)"
_DISABLED_DETAIL = "비특허문헌 연동이 꺼져 있습니다."


class LiteratureBackend(PatentSearchBackend):
    """Crossref + Europe PMC + OpenAlex. 함께 쓰고 DOI 로 맞춘다."""

    id = BACKEND_ID
    display_name = "Crossref · Europe PMC · OpenAlex"

    def __init__(self, client=None, store=None, openalex=None) -> None:
        self._client = client
        self._store = store
        self._openalex = openalex
        # OpenAlex 는 설정을 거친 실제 백엔드(get_backend)이거나 클라이언트를 직접
        # 넘긴 경우에만 기본 검색에 넣는다. 전송을 주입하지 않은 테스트가 조용히
        # 세 번째 호출을 만들어 실패 소스로 기록되지 않게 하기 위해서다.
        self._use_openalex = openalex is not None
        self._openalex_key = ""
        self._mailto = ""
        self._max_results = 10
        self._http_budget = literature_client.DEFAULT_HTTP_BUDGET_SECONDS
        self._search_calls = 0
        self._detail_fetches = 0
        literature_parser.register()

    # --- 설정 -----------------------------------------------------------
    def configure(self, values: Mapping[str, Any]) -> None:
        self._mailto = str(values.get(SETTING_MAILTO) or "").strip()
        self._max_results = _positive_int(values.get(SETTING_MAX_RESULTS), 10)
        self._http_budget = float(
            _positive_int(
                values.get(SETTING_HTTP_BUDGET),
                int(literature_client.DEFAULT_HTTP_BUDGET_SECONDS),
            )
        )
        self._openalex_key = str(values.get(SETTING_OPENALEX_KEY) or "").strip()
        self._use_openalex = True
        if self._client is not None:
            self._client.mailto = self._mailto
            self._client.http_budget_seconds = self._http_budget
        if self._openalex is not None:
            self._openalex.api_key = self._openalex_key
            self._openalex.http_budget_seconds = self._http_budget

    def status(self) -> BackendStatus:
        detail = _READY_DETAIL
        if self._use_openalex and not self._openalex_key:
            detail += _OPENALEX_NO_KEY_DETAIL
        return BackendStatus(
            backend_id=self.id,
            display_name=self.display_name,
            enabled=True,
            # Crossref·Europe PMC 는 키가 없고, OpenAlex 키는 한도만 바꾼다.
            configured=True,
            detail=detail,
        )

    def usage(self) -> dict:
        client = self._client
        base = (
            client.usage()
            if client is not None
            else {
                "calls_by_kind": {},
                "http_seconds": 0.0,
                "http_budget_seconds": self._http_budget,
            }
        )
        base["search_calls"] = self._search_calls
        base["detail_fetches"] = self._detail_fetches
        if self._openalex is not None:
            base["openalex"] = self._openalex.usage()
        return base

    # --- 내부 자원 -------------------------------------------------------
    def _require_client(self) -> literature_client.LiteratureClient:
        if self._client is None:
            self._client = literature_client.LiteratureClient(
                mailto=self._mailto,
                http_budget_seconds=self._http_budget,
            )
        return self._client

    def _require_openalex(self) -> openalex_client.OpenAlexClient:
        if self._openalex is None:
            self._openalex = openalex_client.OpenAlexClient(
                api_key=self._openalex_key,
                http_budget_seconds=self._http_budget,
            )
        return self._openalex

    def _require_store(self) -> artifacts.ArtifactStore:
        if self._store is None:
            # 늦게 만든다. import 시점에 디렉터리를 건드리지 않기 위해서다.
            from ..config import PATHS

            PATHS.evidence_dir.mkdir(parents=True, exist_ok=True)
            self._store = artifacts.ArtifactStore(PATHS.evidence_dir.resolve())
        return self._store

    @property
    def artifact_store(self) -> artifacts.ArtifactStore:
        """응답 원본을 보존하는 저장소. 검증 단계가 여기서 다시 읽는다.

        EPO 백엔드와 **같은 디렉터리**를 쓴다. 아티팩트 id 가 내용 해시라
        충돌하지 않고, 후보 검증이 두 채널의 근거를 한 저장소에서 읽을 수 있다.
        """
        return self._require_store()

    # --- 발견 -----------------------------------------------------------
    def search(
        self,
        query: PatentSearchQuery,
        *,
        sources: tuple[str, ...] | None = None,
        openalex_mode: str = openalex_client.MODE_SEARCH,
        cites_doi: str = "",
    ) -> PatentSearchResponse:
        """서지 DB 들에 같은 질의를 보내고 결과를 모은다.

        한쪽만 쓰지 않는 이유는 색인 방식이 다르기 때문이다(literature_client
        모듈 주석의 실측표를 보라). 모든 응답을 아티팩트로 보존하고, 각 필드는
        자기가 나온 응답을 가리킨다 — 합쳤다고 근거가 섞이지는 않는다.

        ``sources`` 를 주지 않으면 쓸 수 있는 곳 전부에 묻는다. ``cites_doi`` 는
        그 DOI 를 **인용한** 문헌 안에서 찾는다(OpenAlex 전용). 키워드가 목표
        문헌의 표현과 어긋날 때, 이미 찾은 가까운 문헌에서 출발하는 길이다.
        """
        store = self._require_store()
        rows = max(1, min(int(query.max_results or 0) or self._max_results,
                          literature_client.MAX_ROWS_PER_QUERY))
        known = (literature_client.SOURCE_CROSSREF, literature_client.SOURCE_EUROPEPMC, SOURCE_OPENALEX)
        if cites_doi:
            if sources and tuple(sources) != (SOURCE_OPENALEX,):
                raise PatentSearchError("인용 확장(cites_doi)은 OpenAlex 에서만 됩니다.")
            sources = (SOURCE_OPENALEX,)
        if sources is None:
            wanted = known if self._use_openalex else known[:2]
        else:
            unknown = set(sources) - set(known)
            if unknown:
                raise PatentSearchError(f"알 수 없는 논문 출처입니다: {', '.join(sorted(unknown))}")
            wanted = tuple(source for source in known if source in sources)

        plan = []
        if literature_client.SOURCE_CROSSREF in wanted:
            client = self._require_client()
            plan.append((literature_client.SOURCE_CROSSREF,
                         lambda text, n: client.search_crossref(text, rows=n),
                         literature_parser.read_crossref_items))
        if literature_client.SOURCE_EUROPEPMC in wanted:
            client = self._require_client()
            plan.append((literature_client.SOURCE_EUROPEPMC,
                         lambda text, n: client.search_europepmc(text, rows=n),
                         literature_parser.read_europepmc_results))
        if SOURCE_OPENALEX in wanted:
            openalex = self._require_openalex()
            plan.append((SOURCE_OPENALEX,
                         lambda text, n: self._openalex_search(openalex, store, text, n, openalex_mode, cites_doi),
                         literature_parser.read_openalex_results))

        records: list[PatentRecord] = []
        # 중복은 **한 DB 안에서만** 없앤다. 두 DB 가 같은 문헌을 돌려준 사실은
        # 지우지 않는다 — "서로 다른 색인이 같은 문헌을 데려왔다"는 교차 확인
        # 신호이고, 호출부가 후보 순위를 매길 때 쓰는 몇 안 되는 관측이다.
        # 여기서 접어 버리면 그 신호가 언제나 1 이 되어 아무 일도 하지 않는다.
        seen: set[tuple[str, str]] = set()
        notes: list[str] = []
        failed: list[str] = []
        artifact_ids: list[str] = []
        status = 0
        request_url = ""
        source_stats = []

        for source, call_fn, read_fn in plan:
            try:
                call = call_fn(query.text, rows)
            except (literature_client.LiteratureError, ImportError) as exc:
                # 한쪽이 죽어도 다른 쪽 결과를 버리지 않는다. 조용히 넘기지도
                # 않는다 — 무엇이 실패했는지 notes 와 failed_sources 에 남는다.
                notes.append(f"{source} 검색 실패: {exc}")
                failed.append(source)
                source_stats.append({"source": source, "status": "failed", "total_results": None, "returned_records": 0})
                continue
            self._search_calls += 1
            # 원본을 먼저 보존하고, 보존된 바이트에서 후보를 만든다.
            artifact_id = store.put(call.body)
            artifact_ids.append(artifact_id)
            status = status or call.status
            request_url = request_url or call.url
            try:
                total = _TOTAL_READERS[source](json.loads(call.body))
                total = int(total) if total is not None else None
            except (ValueError, TypeError, AttributeError):
                total = None
            source_stat = {"source": source, "status": "zero_results", "total_results": total, "returned_records": 0}
            source_stats.append(source_stat)
            if call.no_results:
                continue
            try:
                works = read_fn(call.body)
            except literature_parser.LiteratureParseError as exc:
                notes.append(
                    f"{source} 응답을 읽지 못했습니다: {exc} "
                    f"(원본은 아티팩트 {artifact_id[:12]}… 에 보존되어 있습니다)"
                )
                failed.append(source)
                source_stat["status"] = "parse_failed"
                continue
            for work in works:
                key = (source, work.doi)
                if key in seen:
                    continue
                seen.add(key)
                records.append(_record_for(work, artifact_id))
                source_stat["returned_records"] += 1
                source_stat["status"] = "results"

        return PatentSearchResponse(
            records=tuple(records),
            # 같은 DOI 가 두 번 들어 있을 수 있다(두 DB 가 모두 찾은 문헌).
            # 문헌 수가 아니라 **레코드 수**다.
            total_found=len(records),
            raw_artifact_id=artifact_ids[0] if artifact_ids else "",
            fetched_at=datetime.now(timezone.utc).isoformat(),
            http_status=status,
            request_url=request_url,
            notes=tuple(notes),
            failed_sources=tuple(failed),
            source_stats=tuple(source_stats),
        )

    def _openalex_search(self, openalex, store, text, rows, mode, cites_doi):
        """OpenAlex 검색 한 번. 인용 확장이면 먼저 DOI 로 작업 id 를 받는다.

        DOI 단건 조회는 무료다. 그 응답도 보존한다 — "어느 문헌을 인용한 목록인가"
        의 근거가 이 응답이다.
        """
        if not cites_doi:
            return openalex.search(text, rows=rows, mode=mode)
        seed = openalex.fetch(cites_doi)
        self._detail_fetches += 1
        store.put(seed.body)
        work_id = "" if seed.no_results else openalex_client.openalex_work_id(seed.body)
        if not work_id:
            raise literature_client.LiteratureError(
                f"OpenAlex 에서 인용 확장의 기준 문헌을 찾지 못했습니다: {cites_doi}",
                status=seed.status,
            )
        return openalex.search(text, rows=rows, mode=mode, cites=work_id)

    def search_arxiv(self, query, rows=5):
        from .arxiv_backend import ArxivBackend
        return ArxivBackend(self._require_store()).query(query, rows=rows)

    # --- 확보 -----------------------------------------------------------
    def fetch_document(
        self, doi: str, constituent: str = "abstract", *, agent_budget: bool = True
    ) -> PatentSearchResponse:
        """후보 하나의 등록 서지를 받는다. 발행사 사이트를 열지 않는다.

        ``abstract`` 는 Europe PMC 를 먼저 본다. 초록이 평문으로 오고 한 번에
        제목·저자·저널까지 함께 오기 때문이다. 그 색인에 없는 문헌(생의학
        범위 밖)은 OpenAlex, 그다음 Crossref 로 넘어간다. OpenAlex 를 Crossref
        앞에 두는 이유는 IEEE·Elsevier 초록이 Crossref 에 없기 때문이다 —
        Crossref 가 먼저 제목만 돌려주면 초록을 찾지 못한 채 끝난다.
        ``biblio`` 는 Crossref 를 보고, 없으면 OpenAlex 로 넘어간다.

        ``agent_budget`` 인자는 EPO 백엔드와 시그니처를 맞추기 위한 것이다. 이
        백엔드에는 LLM 루프가 없어 상한이 없고, 호출 횟수는 호출부가 센다.
        """
        notes: list[str] = []
        failed: list[str] = []
        # arXiv:ID, arxiv.org/abs/IDv2, 10.48550/arXiv.ID 가 모두 같은 키가 된다.
        key = literature_client.normalize_doi(doi)
        arxiv_doi = key.startswith(literature_client.ARXIV_DOI_PREFIX)
        if arxiv_doi:
            from .arxiv_backend import ArxivBackend

            try:
                return ArxivBackend(self._require_store()).query(identifier=key, rows=1)
            except PatentSearchError as exc:
                # arXiv API 는 이 PC 의 외부 IP 에서 요청마다 429 로 거절된다
                # (2026-09-15 실측, Retry-After 없음). 같은 DOI 를 OpenAlex·Crossref
                # 에서 이어서 찾는다. 실패는 문헌 없음이 아니라 실패로 남긴다.
                notes.append(f"arxiv 조회 실패: {exc}")
                failed.append("arxiv")
        client = self._require_client()
        store = self._require_store()

        crossref = (literature_client.SOURCE_CROSSREF, client.fetch_crossref,
                    literature_parser.read_crossref_work)
        europepmc = (literature_client.SOURCE_EUROPEPMC, client.fetch_europepmc,
                     _first_europepmc)
        openalex = (
            (SOURCE_OPENALEX, lambda value: self._require_openalex().fetch(value),
             literature_parser.read_openalex_work)
            if self._use_openalex else None
        )
        if constituent == "biblio":
            plan = (crossref, openalex)
        elif arxiv_doi:
            # arXiv DOI 는 DataCite 소속이라 Europe PMC 에 거의 없고 Crossref 는
            # 404 다. 초록을 가진 OpenAlex 를 먼저 본다.
            plan = (openalex, crossref)
        else:
            plan = (europepmc, openalex, crossref)
        plan = tuple(step for step in plan if step is not None)

        last: PatentSearchResponse | None = None
        # 초록을 요청했는데 레코드에 초록이 없으면 거기서 끝내지 않는다. Europe PMC
        # 가 초록 없는 레코드를 주고 끝나면 OpenAlex·Crossref 는 불리지도 않는다.
        # 끝까지 초록이 없을 때 돌려줄, 처음 받은 레코드를 들고 간다.
        want_abstract = constituent != "biblio"
        partial: PatentSearchResponse | None = None
        for source, call_fn, read_fn in plan:
            try:
                call = call_fn(key)
            except (literature_client.LiteratureError, ImportError) as exc:
                if source != SOURCE_OPENALEX and partial is None:
                    raise
                # OpenAlex 한도 소진(429)이 Crossref·Europe PMC 경로까지 막지 않게
                # 한다. 실패는 문헌 없음이 아니라 실패로 남긴다.
                notes.append(f"{source} 조회 실패: {exc}")
                failed.append(source)
                continue
            self._detail_fetches += 1
            artifact_id = store.put(call.body)
            response = PatentSearchResponse(
                records=(),
                total_found=0,
                raw_artifact_id=artifact_id,
                fetched_at=datetime.now(timezone.utc).isoformat(),
                http_status=call.status,
                request_url=call.url,
                notes=tuple(notes),
            )
            last = response
            if call.no_results:
                notes.append(f"{source} 에 이 DOI 의 레코드가 없습니다.")
                continue
            try:
                work = read_fn(call.body)
            except literature_parser.LiteratureParseError as exc:
                notes.append(f"{source} 응답을 읽지 못했습니다: {exc}")
                continue
            if work is None or work.doi != key:
                # 다른 문헌이 온 응답을 이 후보의 근거로 쓰지 않는다. 2026-09-01
                # 실행에서 모델이 엉뚱한 PMC 문서(신경섬유종증 논문)를 열고도
                # 열람 성공으로 세어졌다. 같은 사고를 백엔드에서 차단한다.
                notes.append(
                    f"{source} 응답의 DOI 가 요청한 값과 다릅니다: "
                    f"{(work.doi if work else '없음')!r} != {key!r}"
                )
                continue
            if not work.text_fields():
                notes.append(f"{source} 응답에 쓸 수 있는 필드가 없습니다.")
                continue
            found = replace(
                response,
                records=(_record_for(work, artifact_id),),
                total_found=1,
                notes=tuple(notes),
                failed_sources=tuple(failed),
            )
            if want_abstract and "abstract" not in work.text_fields():
                notes.append(f"{source} 레코드에 초록이 없습니다.")
                partial = partial or found
                continue
            return found

        if partial is not None:
            return replace(partial, notes=tuple(notes), failed_sources=tuple(failed))
        if last is None:
            if failed:
                raise literature_client.LiteratureError("; ".join(notes))
            raise PatentSearchError("조회 계획이 비어 있습니다.")
        return replace(last, notes=tuple(notes), failed_sources=tuple(failed))


def _first_europepmc(body: bytes):
    works = literature_parser.read_europepmc_results(body)
    return works[0] if works else None


def _record_for(work, artifact_id: str) -> PatentRecord:
    """서지 레코드 하나를 Provider 중립 레코드로. 모든 필드가 아티팩트를 가리킨다."""
    profile_id = literature_parser.PROFILE_BY_SOURCE[work.source]
    fields = {
        name: FieldValue(
            value=text,
            evidence=EvidenceRef(
                artifact_id=artifact_id,
                field_path=work.paths[name],
                profile_id=profile_id,
            ),
        )
        for name, text in work.text_fields().items()
    }
    return PatentRecord(
        doc_number=work.doi,
        title=work.title,
        fields=fields,
        source_url=work.url,
    )


def _positive_int(value, fallback: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return fallback
    return number if number > 0 else fallback
