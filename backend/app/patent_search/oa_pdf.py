"""논문 OA 원문 PDF 채널 — 본문을 받아 보존하고 발췌를 글자 그대로 대조한다.

왜 필요한가
-----------
비특허문헌 채널이 주는 것은 **발행사가 등록한 서지와 초록**이다(literature_client
모듈 주석). 그래서 두 프로필 모두 raw_capable=False 이고, 청구항 대응표의 발췌
칸은 논문 후보에서 늘 미확인으로 남았다. 초록에 없는 문장은 대조할 바이트 자체가
없었기 때문이다. 2026-09-16 실측에서 핵심 대응이 확인된 위치는 논문 본문이었다 —
예를 들어 swing 두 파라미터의 함수로 정의된 twist 한계는 초록에 없고 절 본문에만
있다.

그 본문을 받을 수 있는 경로가 OA(Open Access) PDF 다. OpenAlex 레코드의
``best_oa_location.pdf_url`` 은 발행사·저장소가 공개한 사본 주소이므로, PRISM 이
그 바이트를 받아 아티팩트로 보존하면 모델의 발췌를 대조할 수 있다.

무엇을 보증하고 무엇을 보증하지 않는가
--------------------------------------
보증한다 : 받은 바이트를 그대로 보존하고, 보존된 바이트에서 **다시 뽑은** 텍스트
           에서만 발췌를 찾는다. 위치(``논문 p.12``)는 모델이 적지 않고 PRISM 이
           페이지 번호 표시에서 계산한다.
보증하지 않는다 : 그 PDF 가 **게재본**이라는 것. OA 사본은 저자 원고(preprint,
           accepted manuscript)일 수 있고, 그러면 절·페이지·문장이 게재본과 다르다.
           그래서 프로필은 raw_capable=False 이고 등급은 "논문 원문 PDF 대조 확인
           (OA 사본)"까지만 올라간다. 공식 청구항·전문 등급이 되지 않는다.

받지 못하는 것은 실패로 남는다
------------------------------
유료 문헌에는 OA 주소가 없고(``OaPdfNotAvailable``), 발행사 사이트는 403·봇 차단을
돌려준다(2026-09-01 실측: mdpi.com 403). 어느 쪽도 "그런 논문이 없다"가 아니다.
호출부는 이 예외를 그대로 기록해야 한다.

주소는 PRISM 이 고른다
----------------------
모델이 준 URL 을 받지 않는다. DOI 로 OpenAlex 레코드를 받아 그 안의 OA 주소만
쓴다. 임의 주소를 열어 주는 도구가 되면 그것은 검색 채널이 아니라 프록시다.
https 만 받는다.

사람 속도
---------
Google Patents 페이지와 같은 규칙을 쓴다(pace 모듈). 요청 사이 최소 간격,
실행당 새 PDF 수 상한, 429·503 이면 10분 정지. 상태 파일은 채널마다 따로 둔다 —
간격을 나눠 쓰면 한 채널이 다른 채널의 몫을 소모한다.

재현성의 한계
-------------
텍스트 추출은 pypdf 에 기댄다. 우리 파서 구현의 해시는 프로필과 함께 남지만
(parsers 모듈), pypdf 를 올려 추출 결과가 바뀌면 같은 바이트에서 다른 문자열이
나올 수 있다. 그때는 PARSER_VERSION 을 올려 새 프로필로 등록해야 한다 — 과거
판정을 조용히 덮어쓰지 않기 위해서다.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from . import pace, parsers
from .base import SOURCE_NORMALIZED, TRANSLATION_UNKNOWN, PatentSearchError

# --- 설정 키 --------------------------------------------------------------
SETTING_ENABLED = "literature_oa_pdf_enabled"
SETTING_MIN_INTERVAL = "literature_oa_pdf_min_interval_seconds"
SETTING_MAX_FETCHES = "literature_oa_pdf_max_fetches_per_run"

DEFAULT_MIN_INTERVAL_SECONDS = 6
DEFAULT_MAX_FETCHES_PER_RUN = 6
REQUEST_TIMEOUT_SECONDS = 45
USER_AGENT = "Mozilla/5.0 (compatible; PRISM literature review; single-document fetch)"
PACE_NAME = "oapdf"

# --- 파서·프로필 ----------------------------------------------------------
PARSER_ID = "oa_pdf_text"
PARSER_VERSION = "1"
PROFILE_OA_PDF_TEXT = "oa_pdf_text_v1"

TEXT_FIELD = "pdf_text"
NOTE_FIELD = "pdf_source_note"
#: 발췌 대조 대상. 이 필드에서 확인된 발췌만 "논문 원문 PDF 대조"가 된다.
PDF_TEXT_FIELDS = (TEXT_FIELD,)

# 응답·문서 상한. 넘으면 자르지 않고 실패시킨다 — 잘린 바이트의 해시는 원본의
# 해시가 아니고, 그 위에서 내린 판정은 재현되지 않는다.
MAX_PDF_BYTES = 12 * 1024 * 1024
MAX_PAGES = 200
# 한 번에 돌려주는 페이지 수. 논문 한 편이 수십만 자가 되면 MCP 응답이 잘리고,
# 잘린 자리에서 모델이 문장을 이어 쓰면 그 문장은 대조에서 탈락한다.
MAX_PAGE_SPAN = 12
DEFAULT_PAGE_SPAN = 8
MAX_FIELD_CHARS = 40000

_WS = re.compile(r"[ \t ]+")
_PATH = re.compile(r"^pdf_text/(\d{1,3})-(\d{1,3})$")
_MARKER = re.compile(r"\[p\.(\d{1,3})\]")


class OaPdfError(PatentSearchError):
    """OA PDF 를 받거나 읽을 수 없다."""


class OaPdfNotAvailable(OaPdfError):
    """이 DOI 에 공개된 OA PDF 주소가 없다. 문헌 부재가 아니다."""


class OaPdfNotFound(OaPdfError):
    """주소는 있었지만 404 다. 문헌 부재가 아니다."""


class OaPdfRateLimited(OaPdfError):
    """상대가 요청을 거절했다(429·503). 한동안 요청하지 않는다."""


class OaPdfHttpError(OaPdfError):
    """그 밖의 HTTP 실패. 403·로그인 요구가 흔하다."""


class OaPdfNotPdf(OaPdfError):
    """PDF 가 아닌 것이 왔다(로그인 페이지·HTML 안내문 등)."""


class OaPdfTooLarge(OaPdfError):
    """응답이 상한을 넘었다."""


class OaPdfFetchLimit(OaPdfError):
    """이 실행에서 새로 받을 수 있는 PDF 수를 다 썼다."""


# --- OA 주소 선택 ---------------------------------------------------------
def select_pdf_url(work_body: bytes) -> str:
    """OpenAlex 단건 응답에서 OA PDF 주소를 고른다. 없으면 빈 문자열.

    ``best_oa_location.pdf_url`` 을 먼저 본다. 그것이 없을 때만
    ``open_access.oa_url`` 을 보되, **.pdf 로 끝날 때만** 쓴다 — oa_url 은 흔히
    본문이 아니라 랜딩 페이지이고, 랜딩 페이지를 받아 PDF 가 아니라고 실패하는
    것은 사용자에게 아무 정보도 주지 않는다.
    """
    try:
        document = json.loads(work_body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return ""
    if not isinstance(document, dict):
        return ""
    location = document.get("best_oa_location")
    if isinstance(location, dict):
        candidate = str(location.get("pdf_url") or "").strip()
        if candidate.lower().startswith("https://"):
            return candidate
    access = document.get("open_access")
    if isinstance(access, dict):
        candidate = str(access.get("oa_url") or "").strip()
        if candidate.lower().startswith("https://") and candidate.lower().endswith(".pdf"):
            return candidate
    return ""


# --- 페이지 범위와 경로 ---------------------------------------------------
def page_span(page_from, page_to=None) -> tuple[int, int]:
    """요청한 페이지 범위를 검사한다. 고치지 않고 어긋나면 거절한다."""
    if page_from in (None, ""):
        first = 1
    else:
        try:
            first = int(page_from)
        except (TypeError, ValueError) as exc:
            raise OaPdfError("page_from 은 정수여야 합니다.") from exc
    if page_to in (None, ""):
        last = first + DEFAULT_PAGE_SPAN - 1
    else:
        try:
            last = int(page_to)
        except (TypeError, ValueError) as exc:
            raise OaPdfError("page_to 는 정수여야 합니다.") from exc
    if first < 1 or last < first:
        raise OaPdfError(f"페이지 범위가 올바르지 않습니다: {first}-{last}")
    if last > MAX_PAGES:
        raise OaPdfError(f"페이지 번호는 {MAX_PAGES} 이하여야 합니다.")
    if last - first + 1 > MAX_PAGE_SPAN:
        raise OaPdfError(
            f"한 번에 받을 수 있는 페이지는 {MAX_PAGE_SPAN}쪽입니다"
            f"(요청 {last - first + 1}쪽). 범위를 나눠 조회하십시오."
        )
    return first, last


def field_path(page_from: int, page_to: int) -> str:
    """아티팩트 안의 경로. 페이지 범위가 경로에 들어간다.

    범위를 경로에 넣는 이유는 재추출이 결정적이어야 하기 때문이다. 모델이 받은
    텍스트가 3~14쪽이면, 검증기는 같은 바이트에서 같은 3~14쪽을 다시 뽑아야
    같은 문자열을 얻는다.
    """
    return f"{TEXT_FIELD}/{int(page_from)}-{int(page_to)}"


def is_text_path(path) -> bool:
    return bool(_PATH.match(str(path or "")))


# --- 텍스트 추출 ----------------------------------------------------------
def _page_text(raw: str) -> str:
    lines = []
    for line in str(raw or "").splitlines():
        cleaned = _WS.sub(" ", line).strip()
        if cleaned:
            lines.append(cleaned)
    return "\n".join(lines)


def read_pages(data: bytes, page_from: int, page_to: int) -> str:
    """보존된 바이트에서 페이지 범위의 텍스트를 뽑는다. 페이지 앞에 ``[p.N]``.

    정규화는 공백뿐이다. 문자는 바꾸지 않는다(NFKC 금지 — provenance 모듈 참조).
    """
    if not data:
        raise OaPdfError("PDF 가 비어 있습니다.")
    if len(data) > MAX_PDF_BYTES:
        raise OaPdfTooLarge(f"PDF 가 상한({MAX_PDF_BYTES:,} bytes)을 넘습니다.")
    if not data.startswith(b"%PDF"):
        raise OaPdfNotPdf("PDF 형식이 아닙니다.")
    from pypdf import PdfReader
    from pypdf.errors import PyPdfError

    try:
        reader = PdfReader(io_bytes(data))
        total = len(reader.pages)
        blocks = []
        for number in range(page_from, min(page_to, total) + 1):
            text = _page_text(reader.pages[number - 1].extract_text() or "")
            if text:
                blocks.append(f"[p.{number}]\n{text}")
    except PyPdfError as exc:
        raise OaPdfError(f"PDF 를 읽지 못했습니다: {exc}") from exc
    except Exception as exc:  # pypdf 는 다양한 표준 예외도 올린다
        raise OaPdfError(f"PDF 를 읽지 못했습니다: {type(exc).__name__}: {exc}") from exc
    if page_from > total:
        raise OaPdfError(f"이 PDF 는 {total}쪽입니다. {page_from}쪽은 없습니다.")
    joined = "\n".join(blocks)
    if not joined:
        raise OaPdfError(
            f"{page_from}-{min(page_to, total)}쪽에서 텍스트를 얻지 못했습니다. "
            "스캔 이미지 PDF 일 수 있습니다(PRISM 은 OCR 하지 않습니다)."
        )
    if len(joined) > MAX_FIELD_CHARS:
        # 자르지 않는다. 자른 텍스트를 근거로 주면 모델이 잘린 자리에서 문장을
        # 이어 쓰고, 그 문장은 아티팩트 대조에서 탈락한다.
        raise OaPdfError(
            f"이 범위의 텍스트가 {MAX_FIELD_CHARS:,}자를 넘습니다"
            f"({len(joined):,}자). 페이지 범위를 좁혀 조회하십시오."
        )
    return joined


def io_bytes(data: bytes):
    import io

    return io.BytesIO(data)


def page_count(data: bytes) -> int:
    from pypdf import PdfReader

    return len(PdfReader(io_bytes(data)).pages)


def location_of(path: str, text: str, start: int) -> str:
    """발췌 시작 위치 앞의 마지막 페이지 표시로 원문 위치를 만든다."""
    if not is_text_path(path) or start < 0:
        return ""
    last = None
    for match in _MARKER.finditer(text):
        if match.start() > start:
            break
        last = match
    return f"논문 p.{last.group(1)}" if last else "논문 원문"


def _extract(data: bytes, path: str) -> str:
    """신뢰 파서. provenance 가 보존 바이트에서 필드를 다시 뽑을 때 부른다."""
    matched = _PATH.match(str(path or "").strip())
    if not matched:
        raise parsers.FieldPathMissing(f"지원하지 않는 필드 경로입니다: {path!r}")
    first, last = int(matched.group(1)), int(matched.group(2))
    try:
        return read_pages(data, first, last)
    except OaPdfError as exc:
        raise parsers.ParserError(str(exc)) from exc


_REGISTERED = False


def register() -> None:
    """파서와 프로필을 등록한다. 프로세스당 한 번."""
    global _REGISTERED
    if _REGISTERED:
        return
    parsers.register_parser(PARSER_ID, PARSER_VERSION, _extract)
    parsers.register_profile(
        parsers.SourceProfile(
            profile_id=PROFILE_OA_PDF_TEXT,
            parser_id=PARSER_ID,
            parser_version=PARSER_VERSION,
            source_kind=SOURCE_NORMALIZED,
            translation_state=TRANSLATION_UNKNOWN,
            language="",
            raw_capable=False,
            note=(
                "OpenAlex 가 가리킨 OA 사본 PDF 에서 pypdf 로 뽑은 텍스트. 게재본이 "
                "아니라 저자 원고(preprint·accepted manuscript)일 수 있어 절·페이지·"
                "문장이 게재본과 다를 수 있다. 공백만 정규화하며 문자는 바꾸지 않는다. "
                "원문 등급 없음."
            ),
        )
    )
    _REGISTERED = True


register()


# --- 전송 -----------------------------------------------------------------
@dataclass(frozen=True)
class PdfResponse:
    status: int
    body: bytes
    url: str
    content_type: str = ""


def _default_transport(url: str, timeout: float) -> PdfResponse:
    """기본 전송의 진입점. 실제 구현을 **이름으로** 부른다 — conftest 가 막는다."""
    return _live_transport(url, timeout)


def _live_transport(url: str, timeout: float) -> PdfResponse:
    import requests

    from .openalex_client import trust_store_adapter

    session = requests.Session()
    # 재시도하지 않는다. 거절을 조용히 다시 두드리지 않는다.
    session.mount("https://", trust_store_adapter(max_retries=0))
    try:
        response = session.get(
            url,
            headers={"User-Agent": USER_AGENT, "Accept": "application/pdf"},
            timeout=timeout,
            stream=True,
        )
        body = response.raw.read(MAX_PDF_BYTES + 1, decode_content=True)
        return PdfResponse(
            status=int(response.status_code or 0),
            body=body or b"",
            url=str(response.url or url),
            content_type=str(response.headers.get("Content-Type") or ""),
        )
    finally:
        session.close()


@dataclass
class OaPdfClient:
    """OA PDF 한 건을 사람 속도로 받는다. 검색하지 않는다."""

    transport: Callable[[str, float], PdfResponse] = _default_transport
    gate: pace.HumanPaceGate | None = None
    state_dir: Path | None = None
    min_interval: float = DEFAULT_MIN_INTERVAL_SECONDS
    timeout_seconds: float = REQUEST_TIMEOUT_SECONDS
    _calls: int = field(default=0, init=False)

    def usage(self) -> dict:
        return {"pdf_fetches": self._calls, "min_interval_seconds": self.min_interval}

    def _require_gate(self) -> pace.HumanPaceGate:
        if self.gate is None:
            if self.state_dir is None:
                from ..config import PATHS

                self.state_dir = PATHS.data_dir
            self.gate = pace.HumanPaceGate(
                self.state_dir,
                name=PACE_NAME,
                min_interval=self.min_interval,
                error_class=OaPdfRateLimited,
            )
        self.gate.min_interval = float(self.min_interval)
        return self.gate

    def fetch(self, url: str) -> PdfResponse:
        address = str(url or "").strip()
        if not address.lower().startswith("https://"):
            raise OaPdfError(f"https 주소가 아닙니다: {address[:120]!r}")
        response = self._require_gate().run(
            lambda: self.transport(address, self.timeout_seconds)
        )
        self._calls += 1
        status = int(response.status or 0)
        if status == 404:
            raise OaPdfNotFound(
                f"oa_pdf_not_found: {address} 가 404 입니다. 문헌 부재의 증거가 아닙니다."
            )
        if status in (429, 503):
            raise OaPdfRateLimited(
                f"oa_pdf_rate_limited: HTTP {status}. 10분 동안 조회를 멈춥니다. "
                "문헌 부재가 아닙니다."
            )
        if status >= 400 or status == 0:
            raise OaPdfHttpError(
                f"oa_pdf_http_{status}: {address} (로그인 요구·봇 차단일 수 있습니다. "
                "문헌 부재가 아닙니다.)"
            )
        if len(response.body) > MAX_PDF_BYTES:
            raise OaPdfTooLarge(
                f"oa_pdf_too_large: 응답이 상한({MAX_PDF_BYTES:,} bytes)을 넘습니다."
            )
        if not response.body.startswith(b"%PDF"):
            raise OaPdfNotPdf(
                "oa_pdf_not_pdf: PDF 가 아닌 응답입니다"
                f"(Content-Type={response.content_type or '없음'}). "
                "로그인 페이지나 안내문일 수 있습니다."
            )
        return response
