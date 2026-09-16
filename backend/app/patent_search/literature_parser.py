"""Crossref·Europe PMC 응답의 신뢰 파서와 소스 프로필.

epo_parser 와 같은 자리를 차지한다. 어댑터가 보고한 값은 검증에 쓰지 않고,
보존된 아티팩트에서 여기 등록된 파서로 **다시 뽑은** 값만 근거가 된다.

왜 파서를 새로 등록하는가
-------------------------
Crossref 의 초록은 JATS XML 조각으로 등록되어 있다.

    "<jats:p>We propose a complementary metal-oxide-semiconductor ...</jats:p>"

일반 json_path 파서로 뽑으면 이 태그가 그대로 값이 된다. 태그를 호출부에서
떼면 **보고한 값과 재추출한 값이 달라져** 대조가 깨진다. 그래서 태그 제거까지
파서 안에서 하고, 그 구현의 해시를 프로필과 함께 남긴다. 재검증은 같은
바이트에 같은 파서를 다시 돌려 같은 문자열을 얻는다.

왜 raw_capable=False 인가
-------------------------
여기서 오는 초록은 **발행사가 등록한 메타데이터**이지 조판된 논문 원문이 아니다.
같은 문장이 논문 PDF 에 그대로 있는지는 이 응답으로 증명할 수 없다. 그래서 두
프로필 모두 raw_capable=False 이고, 발췌 칸은 여전히 미확인으로 남는다.

번역 여부는 unknown 이다. Crossref 에 등록되는 초록은 저자가 쓴 원문일 수도
발행사가 넣은 번역일 수도 있고, 레코드의 language 필드는 논문의 언어이지 이
초록의 언어가 아니다. 확인하지 않은 것을 "번역이 아니다"로 적지 않는다.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from . import parsers
from .base import SOURCE_NORMALIZED, TRANSLATION_UNKNOWN

PARSER_ID = "literature_json"
PARSER_VERSION = "1"

PROFILE_CROSSREF_JSON = "crossref_work_json"
PROFILE_EUROPEPMC_JSON = "europepmc_result_json"
PROFILE_OPENALEX_JSON = "openalex_work_json"

# OpenAlex 는 초록을 문장이 아니라 단어 위치 색인으로 준다. 복원 규칙이 기존
# literature_json v1 에 없던 것이므로, 이미 내려진 판정의 재현성을 건드리지 않게
# 파서를 따로 등록한다(parsers.register_parser 의 원칙).
OPENALEX_PARSER_ID = "openalex_json"
OPENALEX_PARSER_VERSION = "1"

# 프로필이 붙는 응답 형식. 감사 기록에서 "어느 API 의 응답인가"를 가른다.
PROFILE_BY_SOURCE = {
    "crossref": PROFILE_CROSSREF_JSON,
    "europepmc": PROFILE_EUROPEPMC_JSON,
    "openalex": PROFILE_OPENALEX_JSON,
}

# 값 하나의 상한. 넘으면 자르지 않고 통째로 뺀다 — 잘린 문장을 근거로 주면
# 모델이 잘린 자리에서 문장을 이어 쓰고, 그 문장은 아티팩트 대조에서 탈락한다.
MAX_FIELD_CHARS = 20000

_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")


class LiteratureParseError(parsers.ParserError):
    """응답을 읽을 수 없다."""


def _strip_markup(value: str) -> str:
    """JATS/HTML 태그를 떼고 공백을 정규화한다.

    엔티티는 표준 라이브러리로 풀되 태그 제거 **뒤에** 푼다. 순서를 뒤집으면
    ``&lt;jats:p&gt;`` 가 태그로 되살아나 제거되고, 원문에 있던 부등호가
    사라진다.
    """
    from html import unescape

    text = _TAG.sub(" ", str(value or ""))
    text = unescape(text)
    return _WS.sub(" ", text).strip()


def _walk(document, field_path: str):
    """'message/items/3/title/0' 경로로 노드 하나를 찾는다."""
    node = document
    for part in [p for p in str(field_path or "").split("/") if p]:
        if isinstance(node, list):
            try:
                index = int(part)
            except ValueError as exc:
                raise parsers.FieldPathMissing(
                    f"배열 인덱스가 아닙니다: {part} (경로 {field_path})"
                ) from exc
            if not 0 <= index < len(node):
                raise parsers.FieldPathMissing(
                    f"배열 범위를 벗어났습니다: {field_path}"
                )
            node = node[index]
        elif isinstance(node, dict):
            if part not in node:
                raise parsers.FieldPathMissing(
                    f"경로를 찾을 수 없습니다: {field_path}"
                )
            node = node[part]
        else:
            raise parsers.FieldPathMissing(f"경로를 찾을 수 없습니다: {field_path}")
    return node


def _extract(data: bytes, field_path: str) -> str:
    """등록되는 파서 구현. 바이트에서 경로 하나를 문자열로 뽑는다.

    문자열 노드와 **문자열의 배열**을 받는다. 배열을 받는 이유는 Crossref 의
    저자 목록처럼 값이 여러 조각으로 등록된 필드가 있기 때문이다. 그 밖의
    노드(dict, 숫자)는 거절한다 — 구조를 문자열로 뭉개면 대조의 의미가 없다.
    """
    try:
        document = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LiteratureParseError(
            f"아티팩트를 JSON 으로 읽을 수 없습니다: {exc}"
        ) from exc

    if field_path.endswith("/@authors"):
        return _crossref_authors(_walk(document, field_path[:-9]))
    if field_path.endswith("/@issued"):
        return _crossref_date(_walk(document, field_path[:-8]))
    node = _walk(document, field_path)
    if isinstance(node, str):
        return _strip_markup(node)
    if isinstance(node, list) and all(isinstance(item, str) for item in node):
        return _strip_markup("; ".join(node))
    raise parsers.FieldPathMissing(
        f"경로가 문자열이 아닙니다: {field_path} ({type(node).__name__})"
    )


@dataclass(frozen=True)
class LiteratureWork:
    """서지 레코드 하나. 각 필드가 아티팩트 안의 경로를 함께 든다."""

    doi: str
    source: str
    title: str = ""
    abstract: str = ""
    authors: str = ""
    container: str = ""
    publication_date: str = ""
    url: str = ""
    # 필드 이름 -> 아티팩트 내부 경로
    paths: dict = field(default_factory=dict)

    def text_fields(self) -> dict:
        """근거로 쓸 수 있는 필드만. 빈 값은 넣지 않는다."""
        found = {
            "title": self.title,
            "abstract": self.abstract,
            "authors": self.authors,
            "container": self.container,
            "publication_date": self.publication_date,
        }
        return {
            name: value
            for name, value in found.items()
            if value and name in self.paths
        }


def _clip(value: str) -> str:
    text = _strip_markup(value)
    return "" if len(text) > MAX_FIELD_CHARS else text


def _crossref_authors(message: dict) -> str:
    names = []
    authors = message.get("author") if isinstance(message, dict) else None
    if not isinstance(authors, list):
        return ""
    for author in authors:
        if not isinstance(author, dict):
            continue
        name = " ".join(str(author.get(k) or "").strip() for k in ("given", "family")).strip()
        name = name or str(author.get("name") or "").strip()
        if name:
            names.append(name)
    return _clip("; ".join(names))


def _crossref_date(message: dict) -> str:
    from datetime import date
    if not isinstance(message, dict):
        return ""
    issued = message.get("issued") or {}
    parts_list = issued.get("date-parts") if isinstance(issued, dict) else None
    parts = parts_list[0] if isinstance(parts_list, list) and parts_list else []
    if not isinstance(parts, list) or not 1 <= len(parts) <= 3 or any(type(p) is not int for p in parts):
        return ""
    try:
        date(parts[0], parts[1] if len(parts) > 1 else 1, parts[2] if len(parts) > 2 else 1)
    except ValueError:
        return ""
    # Preserve supplied precision; never invent a month or day.
    return "-".join(str(p).zfill(4 if i == 0 else 2) for i, p in enumerate(parts))


def _crossref_work(message: dict, prefix: str) -> LiteratureWork | None:
    """Crossref 레코드 하나를 읽는다. ``prefix`` 는 아티팩트 안의 경로 앞부분."""
    if not isinstance(message, dict):
        return None
    doi = str(message.get("DOI") or "").strip().lower()
    if not doi:
        return None

    paths: dict = {}
    values: dict = {}

    titles = message.get("title")
    if isinstance(titles, list) and titles and isinstance(titles[0], str):
        values["title"] = _clip(titles[0])
        paths["title"] = f"{prefix}/title/0"

    abstract = message.get("abstract")
    if isinstance(abstract, str) and abstract.strip():
        text = _clip(abstract)
        if text:
            values["abstract"] = text
            paths["abstract"] = f"{prefix}/abstract"

    containers = message.get("container-title")
    if isinstance(containers, list) and containers and isinstance(containers[0], str):
        values["container"] = _clip(containers[0])
        paths["container"] = f"{prefix}/container-title/0"

    display_authors = _crossref_authors(message)
    published = _crossref_date(message)
    if display_authors:
        paths["authors"] = f"{prefix}/@authors"
    if published:
        paths["publication_date"] = f"{prefix}/@issued"
    url = str(message.get("URL") or "").strip()
    return LiteratureWork(
        doi=doi,
        source="crossref",
        title=values.get("title", ""),
        abstract=values.get("abstract", ""),
        authors=display_authors,
        container=values.get("container", ""),
        publication_date=published,
        url=url or f"https://doi.org/{doi}",
        paths=paths,
    )


def read_crossref_work(body: bytes) -> LiteratureWork | None:
    """상세 조회 응답(``/works/<doi>``) 하나를 읽는다."""
    document = _load(body)
    message = document.get("message")
    if not isinstance(message, dict):
        return None
    return _crossref_work(message, "message")


def read_crossref_items(body: bytes) -> list:
    """검색 응답(``/works?query...``)의 결과 목록을 읽는다."""
    document = _load(body)
    message = document.get("message")
    if not isinstance(message, dict):
        return []
    items = message.get("items")
    if not isinstance(items, list):
        return []
    works = []
    for position, item in enumerate(items):
        work = _crossref_work(item, f"message/items/{position}")
        if work is not None:
            works.append(work)
    return works


def read_europepmc_results(body: bytes) -> list:
    """Europe PMC 검색 응답을 읽는다. 상세 조회도 같은 형식이다."""
    document = _load(body)
    result_list = document.get("resultList")
    if not isinstance(result_list, dict):
        return []
    results = result_list.get("result")
    if not isinstance(results, list):
        return []

    works = []
    for position, item in enumerate(results):
        if not isinstance(item, dict):
            continue
        doi = str(item.get("doi") or "").strip().lower()
        if not doi:
            # DOI 없는 레코드는 후보로 쓰지 않는다. 웹 후보·Crossref 후보와
            # 맞출 키가 없어 같은 문헌이 둘로 남는다.
            continue
        prefix = f"resultList/result/{position}"
        paths: dict = {}
        values: dict = {}

        title = item.get("title")
        if isinstance(title, str) and title.strip():
            values["title"] = _clip(title)
            paths["title"] = f"{prefix}/title"

        abstract = item.get("abstractText")
        if isinstance(abstract, str) and abstract.strip():
            text = _clip(abstract)
            if text:
                values["abstract"] = text
                paths["abstract"] = f"{prefix}/abstractText"

        authors = item.get("authorString")
        if isinstance(authors, str) and authors.strip():
            values["authors"] = _clip(authors)
            paths["authors"] = f"{prefix}/authorString"

        journal = ""
        info = item.get("journalInfo")
        if isinstance(info, dict):
            journal_node = info.get("journal")
            if isinstance(journal_node, dict):
                name = journal_node.get("title")
                if isinstance(name, str) and name.strip():
                    journal = _clip(name)
                    paths["container"] = f"{prefix}/journalInfo/journal/title"
        if journal:
            values["container"] = journal

        published = item.get("firstPublicationDate")
        if isinstance(published, str) and published.strip():
            values["publication_date"] = published.strip()
            paths["publication_date"] = f"{prefix}/firstPublicationDate"

        works.append(
            LiteratureWork(
                doi=doi,
                source="europepmc",
                title=values.get("title", ""),
                abstract=values.get("abstract", ""),
                authors=values.get("authors", ""),
                container=values.get("container", ""),
                publication_date=values.get("publication_date", ""),
                url=f"https://doi.org/{doi}",
                paths=paths,
            )
        )
    return works


def _openalex_abstract(node) -> str:
    """단어 위치 색인을 문장으로 되돌린다. 복원은 pyalex 의 규칙을 그대로 쓴다."""
    from pyalex.api import invert_abstract

    index = node.get("abstract_inverted_index") if isinstance(node, dict) else None
    if not isinstance(index, dict) or not index:
        return ""
    return _clip(invert_abstract(index) or "")


def _openalex_authors(node) -> str:
    authorships = node.get("authorships") if isinstance(node, dict) else None
    if not isinstance(authorships, list):
        return ""
    names = []
    for item in authorships:
        author = item.get("author") if isinstance(item, dict) else None
        name = author.get("display_name") if isinstance(author, dict) else None
        if isinstance(name, str) and name.strip():
            names.append(name.strip())
    return _clip("; ".join(names))


def _extract_openalex(data: bytes, field_path: str) -> str:
    """OpenAlex 파서. 계산 필드(@oa_abstract, @oa_authors)와 일반 문자열 경로를 읽는다."""
    try:
        document = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LiteratureParseError(f"아티팩트를 JSON 으로 읽을 수 없습니다: {exc}") from exc
    for suffix, reader in (("/@oa_abstract", _openalex_abstract), ("/@oa_authors", _openalex_authors)):
        if field_path.endswith(suffix):
            text = reader(_walk(document, field_path[: -len(suffix)]))
            if not text:
                raise parsers.FieldPathMissing(f"경로에 값이 없습니다: {field_path}")
            return text
    node = _walk(document, field_path)
    if isinstance(node, str):
        return _strip_markup(node)
    raise parsers.FieldPathMissing(
        f"경로가 문자열이 아닙니다: {field_path} ({type(node).__name__})"
    )


def _openalex_work(item, prefix: str) -> LiteratureWork | None:
    """OpenAlex 작업 하나. ``prefix`` 는 아티팩트 안의 경로 앞부분(단건은 빈 문자열)."""
    if not isinstance(item, dict):
        return None
    raw_doi = str(item.get("doi") or "").strip()
    if not raw_doi:
        # DOI 가 없으면 다른 채널의 후보와 맞출 키가 없다(Europe PMC 와 같은 규칙).
        return None
    from .literature_client import LiteratureError, normalize_doi

    try:
        doi = normalize_doi(raw_doi)
    except LiteratureError:
        return None

    paths: dict = {}
    values: dict = {}
    title = item.get("display_name")
    if isinstance(title, str) and title.strip():
        values["title"] = _clip(title)
        paths["title"] = f"{prefix}/display_name"
    abstract = _openalex_abstract(item)
    if abstract:
        values["abstract"] = abstract
        paths["abstract"] = f"{prefix}/@oa_abstract"
    authors = _openalex_authors(item)
    if authors:
        values["authors"] = authors
        paths["authors"] = f"{prefix}/@oa_authors"
    location = item.get("primary_location")
    source = location.get("source") if isinstance(location, dict) else None
    container = source.get("display_name") if isinstance(source, dict) else None
    if isinstance(container, str) and container.strip():
        values["container"] = _clip(container)
        paths["container"] = f"{prefix}/primary_location/source/display_name"
    published = item.get("publication_date")
    if isinstance(published, str) and published.strip():
        values["publication_date"] = published.strip()
        paths["publication_date"] = f"{prefix}/publication_date"
    return LiteratureWork(
        doi=doi,
        source="openalex",
        title=values.get("title", ""),
        abstract=values.get("abstract", ""),
        authors=values.get("authors", ""),
        container=values.get("container", ""),
        publication_date=values.get("publication_date", ""),
        url=f"https://doi.org/{doi}",
        paths=paths,
    )


def read_openalex_work(body: bytes) -> LiteratureWork | None:
    """단건 조회 응답(``/works/<doi>``)을 읽는다."""
    return _openalex_work(_load(body), "")


def read_openalex_results(body: bytes) -> list:
    """검색 응답(``/works?search=...``)의 결과 목록을 읽는다."""
    results = _load(body).get("results")
    if not isinstance(results, list):
        return []
    works = []
    for position, item in enumerate(results):
        work = _openalex_work(item, f"results/{position}")
        if work is not None:
            works.append(work)
    return works


def _load(body: bytes) -> dict:
    try:
        document = json.loads((body or b"").decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LiteratureParseError(f"응답을 JSON 으로 읽지 못했습니다: {exc}") from exc
    if not isinstance(document, dict):
        raise LiteratureParseError("응답이 객체가 아닙니다.")
    return document


_REGISTERED = False


@parsers.synchronized_registration
def register() -> None:
    """파서와 프로필을 등록한다. 프로세스당 한 번."""
    global _REGISTERED
    if _REGISTERED:
        return
    # 등록은 프로세스당 한 번이다. 이 확인보다 앞에서 부르면 두 번째 백엔드
    # 생성(MCP 서버의 도구 상태 확인)부터 "이미 등록된 파서" 오류로 채널이 멈춘다.
    from .arxiv_backend import register as register_arxiv
    register_arxiv()
    parsers.register_parser(PARSER_ID, PARSER_VERSION, _extract)
    parsers.register_parser(OPENALEX_PARSER_ID, OPENALEX_PARSER_VERSION, _extract_openalex)
    parsers.register_profile(
        parsers.SourceProfile(
            profile_id=PROFILE_OPENALEX_JSON,
            parser_id=OPENALEX_PARSER_ID,
            parser_version=OPENALEX_PARSER_VERSION,
            source_kind=SOURCE_NORMALIZED,
            translation_state=TRANSLATION_UNKNOWN,
            language="",
            raw_capable=False,
            note=(
                "OpenAlex 집계 레코드. 초록은 단어 위치 색인에서 복원한 문장이라 "
                "구두점·줄바꿈이 원문과 다를 수 있고, 논문 원문(PDF)의 발췌라는 "
                "보증은 없다."
            ),
        )
    )
    for profile_id, note in (
        (
            PROFILE_CROSSREF_JSON,
            "Crossref 등록 서지. 발행사가 등록한 메타데이터이지 논문 원문이 "
            "아니다. 초록은 JATS 조각으로 등록되어 있어 파서가 태그를 뗀다.",
        ),
        (
            PROFILE_EUROPEPMC_JSON,
            "Europe PMC 색인 레코드. 초록이 평문으로 오지만 논문 원문(PDF) 의 "
            "발췌라는 보증은 없다.",
        ),
    ):
        parsers.register_profile(
            parsers.SourceProfile(
                profile_id=profile_id,
                parser_id=PARSER_ID,
                parser_version=PARSER_VERSION,
                # 태그를 떼고 공백을 정규화한 텍스트다. 관청·발행사 원문 바이트가
                # 아니므로 official_xml 이 아니다.
                source_kind=SOURCE_NORMALIZED,
                # 확인하지 않은 것을 "번역이 아니다"로 적지 않는다.
                translation_state=TRANSLATION_UNKNOWN,
                language="",
                raw_capable=False,
                note=note,
            )
        )
    _REGISTERED = True
