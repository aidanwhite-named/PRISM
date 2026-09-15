"""Google Patents 원문 페이지 백엔드 — 문헌번호 하나의 페이지를 PRISM 이 받아 보존한다.

검색은 하지 않는다. 문헌번호로 페이지를 **조회만** 한다. 후보를 찾는 일은 EPO·논문
채널과 모델의 몫이고, 이 백엔드는 이미 찾은 후보의 청구항·명세서·인용 목록을
보존 가능한 바이트로 가져온다. 그래야 모델의 발췌를 PRISM 이 대조할 수 있다.

주소는 PRISM 이 만든다
----------------------
모델이 준 URL 을 받지 않는다. 문헌번호를 검사하고 ``/patent/<번호>/<언어>`` 를
여기서 조립한다. 임의 주소를 열어 주는 도구가 되지 않기 위해서다. 언어는 원어
페이지를 고른다(JP→ja, CN→zh, KR→ko …). 원어 페이지에는 번역이 섞이지 않는다.

사람 속도
---------
사용자 결정(2026-09-15): 자동 접근은 사람이 브라우저로 읽는 속도를 넘지 않는다.

- 요청 사이에 최소 간격(기본 6초)과 0~2초의 흔들림을 둔다.
- 간격은 **프로세스를 넘어** 지킨다. MCP 서버와 후속 검증이 서로 다른 프로세스라
  메모리 안의 시계만으로는 두 곳이 동시에 요청할 수 있다. 데이터 폴더의 잠금
  파일로 직렬화하고 마지막 요청 시각을 거기 적는다.
- 실행 하나에서 새로 받는 페이지 수에 상한을 둔다(기본 12). 같은 문헌의 다른
  구역은 이미 받은 페이지를 다시 읽는다 — 재요청하지 않는다.
- 429·503 을 받으면 10분 동안 모든 프로세스가 요청을 멈춘다. 다시 두드리지 않는다.

비공식 출처
-----------
Google 은 특허청이 아니다. 여기서 확인된 발췌는 "원문 페이지 대조 확인 (비공식
출처)"이며 공식 청구항 등급이 되지 않는다(gpatents_parser 참조). 404 는 그
페이지가 없다는 뜻이지 문헌이 없다는 뜻이 아니다. 최근 공개된 CN 문헌은
2026-09-15 실측에서 404 였다.
"""

from __future__ import annotations

import json
import os
import random
import re
import time
from collections.abc import Callable, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import artifacts, gpatents_parser
from .base import (
    BackendStatus,
    EvidenceRef,
    FieldValue,
    PatentRecord,
    PatentSearchBackend,
    PatentSearchError,
    PatentSearchNotConfigured,
    PatentSearchQuery,
    PatentSearchResponse,
)

SETTING_ENABLED = "gpatents_page_enabled"
SETTING_MIN_INTERVAL = "gpatents_min_interval_seconds"
SETTING_MAX_FETCHES = "gpatents_max_fetches_per_run"

BACKEND_ID = "gpatents"
PAGE_BASE_URL = "https://patents.google.com/patent/"

DEFAULT_MIN_INTERVAL_SECONDS = 6
DEFAULT_MAX_FETCHES_PER_RUN = 12
JITTER_SECONDS = 2.0
BLOCK_SECONDS = 600
REQUEST_TIMEOUT_SECONDS = 30
LOCK_WAIT_SECONDS = 180
USER_AGENT = "Mozilla/5.0 (compatible; PRISM patent review; single-document fetch)"

CONSTITUENTS = ("claims", "description", "abstract", "citations")
_CONSTITUENT_FIELDS = {
    "claims": ("page_claims",),
    "description": ("page_description",),
    "abstract": ("page_abstract",),
    "citations": gpatents_parser.CITATION_FIELDS,
}
# 조회 결과마다 같이 주는 식별·출처 필드. 문헌이 맞는지 확인하는 데 필요하다.
_ALWAYS_FIELDS = gpatents_parser.BIBLIO_FIELDS + (gpatents_parser.NOTE_FIELD,)

# 국가코드 + (JP 연호 등 한 글자) + 숫자 + 종류코드. 종류코드가 없으면 페이지가
# 다른 문헌으로 넘어가거나 404 가 되므로 받지 않는다.
_NUMBER = re.compile(r"^[A-Z]{2}[A-Z]?\d{4,}[A-Z]\d?$")
_ORIGINAL_LANGUAGE = {
    "JP": "ja", "KR": "ko", "CN": "zh", "TW": "zh", "DE": "de", "FR": "fr",
    "ES": "es", "RU": "ru", "IT": "it", "NL": "nl", "SE": "sv", "DK": "da",
    "FI": "fi", "PT": "pt", "PL": "pl", "CZ": "cs", "AT": "de", "CH": "de",
}

_READY_DETAIL = "문헌번호로 Google Patents 원문 페이지를 사람 속도로 조회합니다 (비공식 출처)."


class GooglePatentsNotFound(PatentSearchError):
    """그 문헌번호의 페이지가 없다. 문헌 부재의 증거가 아니다."""


class GooglePatentsRateLimited(PatentSearchError):
    """Google 이 요청을 거절했다(429·503). 한동안 요청하지 않는다."""


class GooglePatentsHttpError(PatentSearchError):
    """그 밖의 HTTP 실패."""


class GooglePatentsFetchLimit(PatentSearchError):
    """이 실행에서 새로 받을 수 있는 페이지 수를 다 썼다."""


def normalize_number(raw: str) -> str:
    text = re.sub(r"[\s/.,\-]", "", str(raw or "")).upper()
    if not _NUMBER.match(text):
        raise PatentSearchError(
            "gpatents_invalid_number: 종류코드까지 포함한 공개번호여야 합니다 "
            f"(예: JP2009070340A, US9208613B2): {raw!r}"
        )
    return text


def page_url(number: str) -> str:
    language = _ORIGINAL_LANGUAGE.get(number[:2], "en")
    return f"{PAGE_BASE_URL}{number}/{language}"


@dataclass(frozen=True)
class PageResponse:
    status: int
    body: bytes
    url: str


def _default_transport(url: str, timeout: float) -> PageResponse:
    """기본 전송의 진입점. 실제 구현을 **이름으로** 부른다 — conftest 가 막는다."""
    return _live_transport(url, timeout)


def _live_transport(url: str, timeout: float) -> PageResponse:
    import requests

    from .openalex_client import trust_store_adapter

    session = requests.Session()
    # 재시도하지 않는다. 거절을 조용히 다시 두드리지 않는다.
    session.mount("https://", trust_store_adapter(max_retries=0))
    try:
        response = session.get(
            url,
            headers={"User-Agent": USER_AGENT, "Accept-Language": "en;q=0.8"},
            timeout=timeout,
            stream=True,
        )
        body = response.raw.read(gpatents_parser.MAX_HTML_BYTES + 1, decode_content=True)
        return PageResponse(status=int(response.status_code or 0), body=body or b"",
                            url=str(response.url or url))
    finally:
        session.close()


class HumanPaceGate:
    """프로세스를 넘어 요청 간격을 지키는 잠금.

    잠금을 쥔 채 기다리고 요청한다. 다른 프로세스는 잠금이 풀릴 때까지 기다리므로
    두 요청이 간격 안에 겹치지 않는다.
    """

    def __init__(
        self,
        state_dir: Path,
        *,
        min_interval: float = DEFAULT_MIN_INTERVAL_SECONDS,
        jitter: float = JITTER_SECONDS,
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], None] = time.sleep,
        rng: Callable[[], float] = random.random,
    ) -> None:
        self.state_dir = Path(state_dir)
        self.min_interval = float(min_interval)
        self.jitter = float(jitter)
        self._clock = clock
        self._sleep = sleep
        self._rng = rng

    @property
    def _state_path(self) -> Path:
        return self.state_dir / "gpatents-pace.json"

    @contextmanager
    def _locked(self):
        self.state_dir.mkdir(parents=True, exist_ok=True)
        path = self.state_dir / "gpatents-pace.lock"
        with path.open("a+b") as handle:
            if path.stat().st_size == 0:
                handle.write(b"0")
                handle.flush()
            deadline = time.monotonic() + LOCK_WAIT_SECONDS
            while True:
                try:
                    handle.seek(0)
                    if os.name == "nt":
                        import msvcrt

                        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl

                        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise GooglePatentsRateLimited(
                            "gpatents_pace_lock_timeout: 다른 조회가 끝나기를 기다리다 시간이 지났습니다."
                        )
                    time.sleep(0.2)
            try:
                yield
            finally:
                handle.seek(0)
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle, fcntl.LOCK_UN)

    def _read_state(self) -> dict:
        try:
            value = json.loads(self._state_path.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else {}
        except (OSError, ValueError):
            return {}

    def _write_state(self, state: dict) -> None:
        tmp = self._state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(state), encoding="utf-8")
        tmp.replace(self._state_path)

    def run(self, request: Callable[[], PageResponse]) -> PageResponse:
        with self._locked():
            state = self._read_state()
            now = self._clock()
            blocked_until = float(state.get("blocked_until") or 0)
            if blocked_until > now:
                raise GooglePatentsRateLimited(
                    "gpatents_rate_limited: Google 이 최근 요청을 거절해 "
                    f"{int(blocked_until - now)}초 동안 조회를 멈춥니다. 문헌 부재가 아닙니다."
                )
            wait = float(state.get("last_request") or 0) + self.min_interval + self.jitter * self._rng() - now
            if wait > 0:
                self._sleep(wait)
            try:
                response = request()
            finally:
                state["last_request"] = self._clock()
                self._write_state(state)
            if response.status in (429, 503):
                state["blocked_until"] = self._clock() + BLOCK_SECONDS
                self._write_state(state)
            return response


class GooglePatentsPageBackend(PatentSearchBackend):
    id = BACKEND_ID
    display_name = "Google Patents 원문 페이지 (비공식)"

    def __init__(self, transport=None, store=None, gate=None) -> None:
        self._transport = transport or _default_transport
        self._store = store
        self._gate = gate
        self._min_interval = DEFAULT_MIN_INTERVAL_SECONDS
        self.max_fetches_per_run = DEFAULT_MAX_FETCHES_PER_RUN
        gpatents_parser.register()

    def configure(self, values: Mapping[str, Any]) -> None:
        self._min_interval = _positive_int(values.get(SETTING_MIN_INTERVAL), DEFAULT_MIN_INTERVAL_SECONDS)
        self.max_fetches_per_run = _positive_int(values.get(SETTING_MAX_FETCHES), DEFAULT_MAX_FETCHES_PER_RUN)
        if self._gate is not None:
            self._gate.min_interval = float(self._min_interval)

    def status(self) -> BackendStatus:
        return BackendStatus(
            backend_id=self.id,
            display_name=self.display_name,
            enabled=True,
            configured=True,
            detail=_READY_DETAIL,
        )

    def search(self, query: PatentSearchQuery) -> PatentSearchResponse:
        raise PatentSearchNotConfigured(
            "Google Patents 백엔드는 검색하지 않습니다. 문헌번호로 페이지를 조회만 합니다."
        )

    def _require_store(self) -> artifacts.ArtifactStore:
        if self._store is None:
            from ..config import PATHS

            PATHS.evidence_dir.mkdir(parents=True, exist_ok=True)
            self._store = artifacts.ArtifactStore(PATHS.evidence_dir.resolve())
        return self._store

    def _require_gate(self) -> HumanPaceGate:
        if self._gate is None:
            from ..config import PATHS

            self._gate = HumanPaceGate(PATHS.data_dir, min_interval=self._min_interval)
        return self._gate

    @property
    def artifact_store(self) -> artifacts.ArtifactStore:
        return self._require_store()

    def fetch_document(
        self,
        publication_number: str,
        constituent: str = "claims",
        *,
        cached_artifact_id: str = "",
        agent_budget: bool = True,
    ) -> PatentSearchResponse:
        """페이지 하나를 받아(또는 이미 받은 페이지를 다시 읽어) 레코드로 돌려준다."""
        if constituent not in CONSTITUENTS:
            raise PatentSearchError(
                f"gpatents_invalid_constituent: {constituent!r} (가능: {', '.join(CONSTITUENTS)})"
            )
        number = normalize_number(publication_number)
        url = page_url(number)
        store = self._require_store()
        notes: list[str] = []

        if cached_artifact_id and store.exists(cached_artifact_id):
            body = store.read(cached_artifact_id)
            artifact_id = cached_artifact_id
            status = 200
            notes.append("같은 실행에서 이미 받은 페이지를 다시 읽었습니다(재요청 없음).")
        else:
            response = self._require_gate().run(
                lambda: self._transport(url, REQUEST_TIMEOUT_SECONDS)
            )
            status = response.status
            if status == 404:
                raise GooglePatentsNotFound(
                    f"gpatents_page_not_found: {number} 의 Google Patents 페이지가 없습니다. "
                    "문헌 부재의 증거가 아닙니다(최근 공개 문헌은 아직 없을 수 있습니다)."
                )
            if status in (429, 503):
                raise GooglePatentsRateLimited(
                    f"gpatents_rate_limited: HTTP {status}. 10분 동안 조회를 멈춥니다. 문헌 부재가 아닙니다."
                )
            if status != 200:
                raise GooglePatentsHttpError(f"gpatents_http_{status}: {url}")
            if len(response.body) > gpatents_parser.MAX_HTML_BYTES:
                raise GooglePatentsHttpError("gpatents_page_too_large")
            body = response.body
            # 원본을 먼저 보존하고, 보존된 바이트에서 필드를 만든다.
            artifact_id = store.put(body)

        try:
            document = gpatents_parser.read_page(body)
        except gpatents_parser.GooglePatentsPageError as exc:
            raise PatentSearchError(
                f"gpatents_parse_failed: {exc} (원본은 아티팩트 {artifact_id[:12]}… 에 보존)"
            ) from exc

        wanted = _ALWAYS_FIELDS + _CONSTITUENT_FIELDS[constituent]
        fields = {
            name: FieldValue(
                value=document.fields[name],
                evidence=EvidenceRef(
                    artifact_id=artifact_id,
                    field_path=name,
                    profile_id=gpatents_parser.PROFILE_GOOGLE_PATENTS_PAGE,
                ),
            )
            for name in wanted
            if document.fields.get(name)
        }
        if not any(name in fields for name in _CONSTITUENT_FIELDS[constituent]):
            notes.append(f"페이지에 {constituent} 구역이 없습니다.")
        record = PatentRecord(
            doc_number=document.publication_number or number,
            title=document.title,
            fields=fields,
            source_url=url,
        )
        return PatentSearchResponse(
            records=(record,),
            total_found=1,
            raw_artifact_id=artifact_id,
            fetched_at=datetime.now(timezone.utc).isoformat(),
            http_status=status,
            request_url=url,
            notes=tuple(notes),
        )


def _positive_int(value, fallback: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return fallback
    return number if number > 0 else fallback
