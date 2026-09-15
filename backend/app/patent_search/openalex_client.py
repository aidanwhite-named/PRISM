"""OpenAlex 조회 클라이언트 — pyalex 로 요청을 만들고, 받은 바이트는 PRISM 이 보존한다.

왜 OpenAlex 인가
----------------
Crossref 는 IEEE·Elsevier 논문의 초록을 갖고 있지 않다(발행사가 등록하지 않는다).
Europe PMC 는 생의학 색인이라 영상·회로 분야 논문이 드물다. 2026-09-15 실측에서
Crossref 로 제목만 오던 ``10.1109/icdh.2012.31`` 의 초록이 OpenAlex 에서는 왔다.

왜 pyalex 를 쓰되 응답은 직접 잡는가
------------------------------------
pyalex 는 검색 파라미터 조립·인증 헤더·오류 해석을 맡는다. 그런데 결과를 가공된
객체로만 돌려주고 원본 바이트를 내주지 않는다. PRISM 의 검증은 **받은 바이트를
보존하고 거기서 다시 뽑은 값만 근거로 쓴다**(literature_client 모듈 주석). 그래서
pyalex 에 우리 세션을 넘기고, 그 세션의 응답 훅으로 원본을 받는다.

왜 truststore 인가
------------------
이 PC 는 외부 HTTPS 를 사내 장비가 재서명한다. ``requests`` 는 certifi 목록만 믿어
OpenAlex 연결부터 실패하고(2026-09-15 실측: SSLCertVerificationError), 표준 ``ssl``
은 Windows 인증서 저장소를 믿어 통과한다. truststore 는 requests 가 같은 저장소를
믿게 한다. **검증을 끄는 것이 아니다.** 프로세스 전역에 주입하지 않고 이 세션에만
붙인다 — 전역으로 바꾸면 EPO·Crossref 경로의 TLS 까지 함께 바뀐다.

요금과 키
---------
2026-02-13 부터 API 키가 필수이고 사용량 과금이다. 키가 있으면 하루 $1, 없으면
$0.10 이 무료다. DOI 단건 조회는 무료, 검색은 1,000회에 $1 이다. 키 없이도 동작은
하지만 한도가 작아 429 가 나기 쉽다. 429 는 "문헌 없음"이 아니라 한도 소진이다.
"""

from __future__ import annotations

import json
import ssl
import threading
import time
import urllib.parse
from collections.abc import Callable
from dataclasses import dataclass, field

from .literature_client import (
    DEFAULT_HTTP_BUDGET_SECONDS,
    DEFAULT_TIMEOUT_SECONDS,
    MAX_RESPONSE_BYTES,
    MAX_ROWS_PER_QUERY,
    HttpResponse,
    LiteratureBudgetExceeded,
    LiteratureCall,
    LiteratureError,
    normalize_doi,
    plain_query,
)

SOURCE_OPENALEX = "openalex"

# 검색 방식. search 는 전문 색인 전체, title_and_abstract 는 제목·초록으로 좁힌다.
# 같은 질의에서 전자는 1~3위가 무관한 문헌이었고 후자는 1위가 관련 문헌이었다
# (2026-09-15 실측). 어느 쪽이 나은지는 질의마다 달라 모델이 고르게 둔다.
MODE_SEARCH = "search"
MODE_TITLE_ABSTRACT = "title_and_abstract"
MODES = (MODE_SEARCH, MODE_TITLE_ABSTRACT)

# 받아 올 필드. 전부 받으면 참고문헌·개념 목록까지 와서 응답이 수십 KB 가 된다.
SELECT_FIELDS = (
    "id",
    "doi",
    "display_name",
    "publication_date",
    "authorships",
    "primary_location",
    "abstract_inverted_index",
    "type",
    "cited_by_count",
)


def _quota_note(has_key: bool) -> str:
    if has_key:
        return "OpenAlex 일일 사용 한도를 넘었을 수 있습니다."
    return (
        "OpenAlex API 키가 없어 일일 한도가 작습니다($0.10). 설정에서 키를 넣으면 "
        "한도가 10배가 됩니다."
    )


# --- 전송 --------------------------------------------------------------------
# pyalex 는 인증 정보를 모듈 전역 config 에서 읽는다. 두 스레드가 서로 다른 키로
# 동시에 부르면 한쪽 키가 다른 요청에 실린다. 전역을 바꾸는 구간을 직렬화한다.
_PYALEX_LOCK = threading.Lock()


def trust_store_adapter(**kwargs):
    """Windows 인증서 저장소로 TLS 를 검증하는 requests 어댑터.

    requests 를 쓰는 다른 연동(arxiv_backend)도 같은 이유로 이것을 붙인다.
    """
    import truststore
    from requests.adapters import HTTPAdapter

    class _TrustStoreAdapter(HTTPAdapter):
        def init_poolmanager(self, *args, **pool_kwargs):
            pool_kwargs["ssl_context"] = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            return super().init_poolmanager(*args, **pool_kwargs)

    return _TrustStoreAdapter(**kwargs)


def _session(timeout: float, captured: list):
    """truststore TLS·기본 시간 제한·원본 캡처가 붙은 requests 세션."""
    import requests

    class _Session(requests.Session):
        # pyalex 는 session.get 에 timeout 을 주지 않는다. 주지 않으면 응답 없는
        # 서버 하나가 검색 실행 전체를 붙잡는다.
        def request(self, *args, **kwargs):
            kwargs.setdefault("timeout", timeout)
            return super().request(*args, **kwargs)

    session = _Session()
    # 재시도는 하지 않는다. 429 를 조용히 다시 두드리면 한도를 더 태운다.
    session.mount("https://", trust_store_adapter(max_retries=0))
    session.hooks["response"].append(lambda response, *a, **k: captured.append(response))
    return session


def pyalex_get(url: str, api_key: str, timeout: float, *, session_factory=_session) -> HttpResponse:
    """pyalex 로 GET 하고, 세션 훅이 잡은 **원본 응답**을 돌려준다.

    pyalex 가 오류를 던져도(404·429·401) 응답을 받았다면 그 응답을 돌려준다.
    상태 해석은 호출부 한 곳(OpenAlexClient._send)에서 한다. 응답 자체가 없으면
    (연결·TLS 오류) 예외를 그대로 올린다.
    """
    import pyalex

    captured: list = []
    session = session_factory(timeout, captured)
    try:
        with _PYALEX_LOCK:
            previous_key, previous_retries = pyalex.config.api_key, pyalex.config.max_retries
            pyalex.config.api_key = api_key or None
            pyalex.config.max_retries = 0
            try:
                pyalex.Works()._get_from_url(url, session=session)
            except Exception:
                if not captured:
                    raise
            finally:
                pyalex.config.api_key = previous_key
                pyalex.config.max_retries = previous_retries
    finally:
        session.close()
    response = captured[-1]
    return HttpResponse(
        status=int(response.status_code or 0),
        headers=dict(response.headers or {}),
        body=response.content or b"",
    )


def _default_transport(url: str, api_key: str, timeout: float) -> HttpResponse:
    """기본 전송의 진입점. literature_client._default_transport 와 같은 이유로
    실제 구현을 **이름으로** 부른다 — conftest 가 막을 수 있어야 한다."""
    return _live_transport(url, api_key, timeout)


def _live_transport(url: str, api_key: str, timeout: float) -> HttpResponse:
    return pyalex_get(url, api_key, timeout)


# --- 요청 주소 ---------------------------------------------------------------
def _works(**params):
    import pyalex

    return pyalex.Works(params) if params else pyalex.Works()


def _with_page(url: str, rows: int) -> str:
    # pyalex 의 url 은 페이지 파라미터를 넣지 않는다(get 호출 때 붙인다). 주소를
    # 먼저 알아야 원본과 함께 기록할 수 있으므로 여기서 붙인다.
    separator = "&" if urllib.parse.urlparse(url).query else "?"
    return f"{url}{separator}per-page={_clamp(rows)}"


def search_url(query: str, *, rows: int, mode: str = MODE_SEARCH, cites: str = "") -> str:
    works = _works()
    text = plain_query(query)
    if cites:
        works = works.filter(cites=cites)
    if mode == MODE_TITLE_ABSTRACT:
        works = works.search_filter(title_and_abstract=text)
    elif mode == MODE_SEARCH:
        works = works.search(text)
    else:
        raise LiteratureError(f"알 수 없는 OpenAlex 검색 방식입니다: {mode}")
    return _with_page(works.select(list(SELECT_FIELDS)).url, rows)


def work_url(doi: str) -> str:
    works = _works()
    works.params = f"https://doi.org/{normalize_doi(doi)}"
    select = urllib.parse.urlencode({"select": ",".join(SELECT_FIELDS)})
    return f"{works.url}?{select}"


def _clamp(rows) -> int:
    try:
        value = int(rows)
    except (TypeError, ValueError):
        value = 10
    return max(1, min(value, MAX_ROWS_PER_QUERY))


def openalex_work_id(body: bytes) -> str:
    """단건 응답에서 OpenAlex 작업 id(W…)를 꺼낸다. 인용 확장에 쓴다."""
    try:
        document = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return ""
    raw = str(document.get("id") or "") if isinstance(document, dict) else ""
    return raw.rsplit("/", 1)[-1] if raw.rsplit("/", 1)[-1].startswith("W") else ""


# --- 클라이언트 --------------------------------------------------------------
@dataclass
class OpenAlexClient:
    """OpenAlex 검색·단건 조회. 네트워크 예산은 literature_client 와 같은 방식으로 센다."""

    api_key: str = ""
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    http_budget_seconds: float = DEFAULT_HTTP_BUDGET_SECONDS
    transport: Callable[[str, str, float], HttpResponse] = _default_transport
    _spent_seconds: float = field(default=0.0, init=False)
    _calls_by_kind: dict = field(default_factory=dict, init=False)

    @property
    def remaining_budget(self) -> float:
        if not self.http_budget_seconds:
            return float("inf")
        return max(0.0, self.http_budget_seconds - self._spent_seconds)

    def usage(self) -> dict:
        return {
            "calls_by_kind": dict(self._calls_by_kind),
            "http_seconds": round(self._spent_seconds, 3),
            "http_budget_seconds": self.http_budget_seconds,
            "api_key_configured": bool(self.api_key),
        }

    def _send(self, url: str, *, kind: str) -> LiteratureCall:
        if self.remaining_budget <= 0:
            raise LiteratureBudgetExceeded(
                f"OpenAlex 네트워크 시간 예산({self.http_budget_seconds:.0f}초)을 모두 사용했습니다."
            )
        started = time.monotonic()
        try:
            response = self.transport(url, self.api_key, self.timeout_seconds)
        except LiteratureError:
            raise
        except Exception as exc:  # 연결·TLS 오류
            self._spent_seconds += time.monotonic() - started
            detail = str(exc).replace(self.api_key, "***") if self.api_key else str(exc)
            raise LiteratureError(
                f"{SOURCE_OPENALEX} 조회에 실패했습니다: {type(exc).__name__}: {detail[:300]}"
            ) from exc
        elapsed = time.monotonic() - started
        self._spent_seconds += elapsed
        self._calls_by_kind[kind] = self._calls_by_kind.get(kind, 0) + 1

        body = response.body or b""
        if len(body) > MAX_RESPONSE_BYTES:
            raise LiteratureError(
                f"{SOURCE_OPENALEX} 응답이 상한({MAX_RESPONSE_BYTES} 바이트)을 넘었습니다.",
                status=response.status,
            )
        no_results = response.status == 404
        if response.status == 429:
            raise LiteratureError(
                f"{SOURCE_OPENALEX} 조회가 HTTP 429 로 거절되었습니다. 문헌이 없다는 뜻이 "
                f"아닙니다. {_quota_note(bool(self.api_key))}",
                status=429,
            )
        if response.status in (401, 403):
            raise LiteratureError(
                f"{SOURCE_OPENALEX} 가 HTTP {response.status} 로 접근을 거절했습니다. "
                "API 키가 올바른지 확인하십시오.",
                status=response.status,
            )
        if response.status >= 400 and not no_results:
            raise LiteratureError(
                f"{SOURCE_OPENALEX} 조회가 HTTP {response.status} 로 실패했습니다.",
                status=response.status,
            )
        return LiteratureCall(
            url=url,
            status=int(response.status or 0),
            headers=dict(response.headers or {}),
            body=body,
            elapsed_seconds=round(elapsed, 3),
            source=SOURCE_OPENALEX,
            kind=kind,
            no_results=no_results,
        )

    def search(self, query: str, *, rows: int = 10, mode: str = MODE_SEARCH, cites: str = "") -> LiteratureCall:
        return self._send(search_url(query, rows=rows, mode=mode, cites=cites), kind="search")

    def fetch(self, doi: str) -> LiteratureCall:
        return self._send(work_url(doi), kind="detail")


# 연결 확인에 쓰는 문헌. DOI 단건 조회는 무료라 확인 버튼이 한도를 쓰지 않는다.
CHECK_DOI = "10.3390/s25103219"


def check_access(api_key: str, *, transport=None) -> tuple[bool, str, int | None]:
    """설정 화면의 「연결 테스트」. (성공 여부, 안내 문구, HTTP 상태)."""
    options = {"transport": transport} if transport is not None else {}
    client = OpenAlexClient(api_key=(api_key or "").strip(), http_budget_seconds=30, **options)
    try:
        call = client.fetch(CHECK_DOI)
    except LiteratureError as exc:
        return False, str(exc), exc.status or None
    if call.no_results:
        return False, "OpenAlex 가 응답했지만 확인용 문헌을 찾지 못했습니다.", call.status
    if client.api_key:
        detail = (
            "OpenAlex 에 연결했고 API 키가 거절되지 않았습니다. 단건 조회는 무료이며 "
            "검색은 하루 $1 무료 한도에서 차감됩니다."
        )
    else:
        detail = (
            "OpenAlex 에 키 없이 연결했습니다. 키 없는 무료 한도는 하루 $0.10 이라 "
            "검색이 429 로 거절되기 쉽습니다."
        )
    return True, detail, call.status
