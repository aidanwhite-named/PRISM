"""Google Patents 원문 페이지 HTML 의 신뢰 파서와 소스 프로필.

epo_parser 와 같은 두 가지 일을 한다.

    read_page()   보존된 페이지 바이트에서 필드를 읽는다. 백엔드가 쓴다.
    _extract()    보존된 바이트에서 **필드 하나**를 다시 뽑는다.
                  provenance.verify_excerpt 가 쓴다.

왜 이 페이지를 읽는가
---------------------
EPO OPS 는 US·CN·JP 청구항과 명세서를 주지 않는다. 모델이 웹 도구로 원문
페이지를 열어도 PRISM 은 무엇을 읽었는지 확인할 수 없다(Codex 는 열람 성공 여부를
알려주지 않고, patents.google.com 은 Codex 의 URL 안전 판정에 막히기도 한다).
그래서 PRISM 이 문헌번호로 페이지를 직접 받아 보존하고, 모델의 발췌를 그 바이트에
대조한다. 2026-09-15 실측으로 이 PC 에서 JP·US·CN·EP 페이지의 청구항·문단번호
달린 명세서·인용 목록이 모두 HTML 안에 있었다.

왜 raw_capable=False 인가
-------------------------
Google 은 특허청이 아니다. 페이지의 청구항은 관청 데이터를 Google 이 재수집해
HTML 로 다시 그린 것이고, 문헌에 따라 OCR 이거나 Google 번역일 수 있다. 그래서
source_kind 는 normalized(태그를 걷어낸 텍스트), 번역 여부는 unknown 이다.
이 프로필로 확인된 발췌는 "원문 페이지 대조 확인 (비공식 출처)"까지만 올라간다.
"공식 청구항"이 되지 않는다.

번역 페이지에서는 원문을 뽑는다
------------------------------
JP 문헌의 /en 페이지처럼 Google 번역이 붙은 구역에는 ``google-src-text`` 안에
원어 문장이 들어 있다. 그 구역에서는 번역문을 버리고 원어 문장만 뽑는다. 번역은
검증 대상이 아니라 모델이 따로 쓰는 설명이다.

정규화는 공백뿐이다
-------------------
태그 경계를 줄바꿈으로 바꾸고, 줄 안의 연속 공백을 한 칸으로 접는다. 문자는 바꾸지
않는다(NFKC 금지 — provenance 모듈 참조). 공백 접기는 파서의 결정적 규칙이라 같은
바이트에서 언제나 같은 문자열이 나오고, 모델이 받은 텍스트도 이 규칙의 결과다.

번호 표시
---------
청구항 앞에는 ``[claim N]``, 명세서 문단 앞에는 ``[0045]`` 를 붙인다. 페이지의
``num`` 속성에서 온 값이다. 위치(청구항 N, 문단 [0045])는 모델이 적지 않고 PRISM 이
이 표시에서 계산한다.
"""

from __future__ import annotations

import hashlib
import re
from collections import OrderedDict
from dataclasses import dataclass, field as dataclass_field
from html.parser import HTMLParser

from . import parsers
from .base import SOURCE_NORMALIZED, TRANSLATION_UNKNOWN, PatentSearchError

PARSER_ID = "google_patents_html"
PARSER_VERSION = "1"
PROFILE_GOOGLE_PATENTS_PAGE = "google_patents_page_html_v1"

MAX_HTML_BYTES = 8 * 1024 * 1024

# 검증 대상 본문. 이 필드에서 확인된 발췌만 "원문 페이지 대조"가 된다.
PAGE_TEXT_FIELDS = ("page_claims", "page_description", "page_abstract")
# 페이지가 보여 주는 인용·피인용 목록. PRISM 이 행을 이어 붙인 값이다.
CITATION_FIELDS = ("page_cited_patents", "page_cited_by")
BIBLIO_FIELDS = (
    "title",
    "publication_number",
    "publication_date",
    "priority_date",
    "applicants",
    "inventors",
)
NOTE_FIELD = "page_source_note"
SUPPORTED_FIELDS = PAGE_TEXT_FIELDS + CITATION_FIELDS + BIBLIO_FIELDS + (NOTE_FIELD,)

_VOID_TAGS = frozenset(
    {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta",
     "param", "source", "track", "wbr"}
)
# 줄을 나누는 요소. 인라인 요소(span, u, sub 등)는 줄을 나누지 않는다.
_BLOCK_TAGS = frozenset(
    {"p", "div", "li", "ol", "ul", "br", "heading", "claim", "claim-text", "tr",
     "table", "section", "description", "claims", "abstract", "para-num",
     "h1", "h2", "h3", "h4", "technical-field", "background-art", "summary-of-invention",
     "description-of-embodiments", "invention-title"}
)
_SKIP_TAGS = frozenset({"script", "style"})
_WHITESPACE = re.compile(r"\s+")
_DIGITS = re.compile(r"^\d{1,6}$")
_MARKER = re.compile(r"\[(claim (\d+)|(\d{1,6}))\]")


class GooglePatentsPageError(PatentSearchError):
    """Google Patents 페이지를 읽을 수 없다."""


@dataclass
class _Node:
    tag: str
    attrs: dict
    children: list = dataclass_field(default_factory=list)

    def get(self, name: str) -> str:
        value = self.attrs.get(name)
        return "" if value is None else str(value)

    def has_class(self, name: str) -> bool:
        return name in self.get("class").split()


class _TreeBuilder(HTMLParser):
    """관대한 트리 빌더. 닫히지 않은 태그는 부모가 닫힐 때 함께 닫는다."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = _Node("#root", {})
        self._stack = [self.root]

    def handle_starttag(self, tag, attrs):
        node = _Node(tag, {name: value for name, value in attrs})
        self._stack[-1].children.append(node)
        if tag not in _VOID_TAGS:
            self._stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self._stack[-1].children.append(_Node(tag, {name: value for name, value in attrs}))

    def handle_endtag(self, tag):
        for index in range(len(self._stack) - 1, 0, -1):
            if self._stack[index].tag == tag:
                del self._stack[index:]
                return

    def handle_data(self, data):
        self._stack[-1].children.append(data)


def _walk(node):
    """깊이 우선으로 요소만 돌려준다."""
    pending = [node]
    while pending:
        current = pending.pop()
        yield current
        pending.extend(
            child for child in reversed(current.children) if isinstance(child, _Node)
        )


def _plain_text(node) -> str:
    parts: list[str] = []

    def visit(item):
        if isinstance(item, str):
            parts.append(item)
        elif item.tag not in _SKIP_TAGS:
            for child in item.children:
                visit(child)

    visit(node)
    return _WHITESPACE.sub(" ", "".join(parts)).strip()


class _Lines:
    """줄 단위로 텍스트를 모은다. 번호 표시는 다음 비지 않은 줄 앞에 붙인다."""

    def __init__(self) -> None:
        self.lines: list[str] = []
        self._buffer: list[str] = []
        self._marker = ""

    def text(self, value: str) -> None:
        self._buffer.append(value)

    def newline(self) -> None:
        line = _WHITESPACE.sub(" ", "".join(self._buffer)).strip()
        self._buffer = []
        if not line:
            return
        if self._marker:
            line = f"{self._marker} {line}"
            self._marker = ""
        self.lines.append(line)

    def marker(self, value: str) -> None:
        self.newline()
        self._marker = value

    def result(self) -> str:
        self.newline()
        return "\n".join(self.lines)


def _marker_for(node: _Node, kind: str) -> str:
    num = node.get("num").strip()
    if not num:
        return ""
    if kind == "claims":
        if node.tag == "claim" or node.has_class("claim"):
            return f"[claim {int(num)}]" if num.isdigit() else ""
        return ""
    if kind == "description":
        if node.tag == "para-num":
            inner = num.strip("[] ")
            return f"[{inner}]" if _DIGITS.match(inner) else ""
        if node.tag in ("p", "div") and _DIGITS.match(num):
            return f"[{num}]"
    return ""


def _section_text(section: _Node, kind: str) -> str:
    content = next(
        (node for node in _walk(section) if node.get("itemprop") == "content"), section
    )
    original_only = any(node.has_class("google-src-text") for node in _walk(content))
    lines = _Lines()

    def visit(item, in_source: bool):
        if isinstance(item, str):
            if in_source or not original_only:
                lines.text(item)
            return
        if item.tag in _SKIP_TAGS:
            return
        block = item.tag in _BLOCK_TAGS
        if block:
            lines.newline()
        marker = _marker_for(item, kind)
        if marker:
            lines.marker(marker)
        inside = in_source or item.has_class("google-src-text")
        for child in item.children:
            visit(child, inside)
        if block:
            lines.newline()

    visit(content, False)
    return lines.result()


def _section_note(section: _Node, name: str) -> str:
    content = next(
        (node for node in _walk(section) if node.get("itemprop") == "content"), section
    )
    lang_node = next((node for node in _walk(content) if node.get("lang")), None)
    parts = [name + ":"]
    if lang_node is not None:
        parts.append(f"lang={lang_node.get('lang')}")
        if lang_node.get("load-source"):
            parts.append(f"load-source={lang_node.get('load-source')}")
    translated = next(
        (node for node in _walk(section) if node.get("itemprop") == "translatedLanguage"),
        None,
    )
    if translated is not None:
        parts.append(
            "Google 번역 구역, 원어 문장만 추출(원어: " + _plain_text(translated) + ")"
        )
    return " ".join(parts)


_CITATION_GROUPS = (
    ("page_cited_patents", "backwardReferencesOrig", ""),
    ("page_cited_patents", "backwardReferencesFamily", "[family] "),
    ("page_cited_patents", "detailedNonPatentLiterature", "[npl] "),
    ("page_cited_by", "forwardReferencesOrig", ""),
    ("page_cited_by", "forwardReferencesFamily", "[family] "),
)


def _first_text(node: _Node, itemprop: str) -> str:
    found = next((child for child in _walk(node) if child.get("itemprop") == itemprop), None)
    return _plain_text(found) if found is not None else ""


def _citation_line(row: _Node, prefix: str) -> str:
    number = _first_text(row, "publicationNumber")
    if not number:
        text = _plain_text(row)
        return f"{prefix}{text}" if text else ""
    examiner = any(child.get("itemprop") == "examinerCited" for child in _walk(row))
    cells = [
        f"{prefix}{number}{' *' if examiner else ''}",
        "priority " + (_first_text(row, "priorityDate") or "-"),
        "published " + (_first_text(row, "publicationDate") or "-"),
        _first_text(row, "assigneeOriginal") or "-",
        _first_text(row, "title") or "-",
    ]
    return " | ".join(cells)


@dataclass(frozen=True)
class PageDocument:
    """보존된 페이지에서 읽은 필드. 값은 전부 같은 바이트에서 나온다."""

    fields: dict

    @property
    def publication_number(self) -> str:
        return self.fields.get("publication_number", "")

    @property
    def title(self) -> str:
        return self.fields.get("title", "")


def _meta(root: _Node, name: str) -> str:
    for node in _walk(root):
        if node.tag == "meta" and node.get("name") == name:
            return _WHITESPACE.sub(" ", node.get("content")).strip()
    return ""


def _first_tag_text(root: _Node, tag: str, itemprop: str) -> str:
    for node in _walk(root):
        if node.tag == tag and node.get("itemprop") == itemprop:
            return _plain_text(node)
    return ""


def _all_tag_texts(root: _Node, tag: str, itemprop: str) -> list[str]:
    seen: list[str] = []
    for node in _walk(root):
        if node.tag == tag and node.get("itemprop") == itemprop:
            text = _plain_text(node)
            if text and text not in seen:
                seen.append(text)
    return seen


def _parse(data: bytes) -> PageDocument:
    if not data:
        raise GooglePatentsPageError("페이지가 비어 있습니다.")
    if len(data) > MAX_HTML_BYTES:
        raise GooglePatentsPageError(f"페이지가 상한({MAX_HTML_BYTES:,} bytes)을 넘습니다.")
    builder = _TreeBuilder()
    builder.feed(data.decode("utf-8", errors="replace"))
    builder.close()
    root = builder.root

    fields: dict[str, str] = {}
    notes = ["Google Patents 원문 페이지 (비공식 출처, 특허청 공식 문서 아님)"]
    sections = {
        node.get("itemprop"): node
        for node in _walk(root)
        if node.tag == "section" and node.get("itemprop") in ("claims", "description", "abstract")
    }
    for kind, name in (("claims", "page_claims"), ("description", "page_description"),
                       ("abstract", "page_abstract")):
        section = sections.get(kind)
        if section is None:
            continue
        text = _section_text(section, kind)
        if text:
            fields[name] = text
            notes.append(_section_note(section, kind))

    groups: dict[str, list[str]] = {}
    seen: set[tuple[str, str]] = set()
    for field_name, prop, prefix in _CITATION_GROUPS:
        for node in _walk(root):
            if node.tag != "tr" or node.get("itemprop") != prop:
                continue
            line = _citation_line(node, prefix)
            if line and (field_name, line) not in seen:
                seen.add((field_name, line))
                groups.setdefault(field_name, []).append(line)
    for field_name, lines in groups.items():
        fields[field_name] = "\n".join(["(* = examiner cited)", *lines])

    biblio = {
        "title": _meta(root, "DC.title"),
        "publication_number": _first_tag_text(root, "dd", "publicationNumber"),
        "publication_date": _first_tag_text(root, "time", "publicationDate"),
        "priority_date": _first_tag_text(root, "time", "priorityDate"),
        "applicants": "\n".join(_all_tag_texts(root, "dd", "assigneeOriginal")),
        "inventors": "\n".join(_all_tag_texts(root, "dd", "inventor")),
    }
    fields.update({name: value for name, value in biblio.items() if value})
    fields[NOTE_FIELD] = "\n".join(notes)
    return PageDocument(fields=fields)


# 검증기는 발췌마다 필드를 다시 뽑는다. 수백 KB 페이지를 매번 다시 파싱하지 않도록
# 내용 해시로 최근 결과를 기억한다. 키가 바이트의 해시이므로 결과는 같다.
_CACHE: "OrderedDict[str, PageDocument]" = OrderedDict()
_CACHE_SIZE = 8


def read_page(data: bytes) -> PageDocument:
    key = hashlib.sha256(data or b"").hexdigest()
    cached = _CACHE.get(key)
    if cached is not None:
        _CACHE.move_to_end(key)
        return cached
    document = _parse(data)
    _CACHE[key] = document
    while len(_CACHE) > _CACHE_SIZE:
        _CACHE.popitem(last=False)
    return document


def location_of(field_name: str, text: str, start: int) -> str:
    """발췌 시작 위치 앞의 마지막 번호 표시로 원문 위치를 만든다."""
    label = {"page_claims": "청구항", "page_description": "명세서",
             "page_abstract": "초록"}.get(field_name, "")
    if not label or start < 0:
        return ""
    last = None
    for match in _MARKER.finditer(text):
        if match.start() > start:
            break
        last = match
    if last is None or field_name == "page_abstract":
        return label
    if last.group(2):
        return f"청구항 {last.group(2)}"
    return f"명세서 문단 [{last.group(3)}]"


def _extract(data: bytes, path: str) -> str:
    """신뢰 파서. provenance 가 보존 바이트에서 필드를 다시 뽑을 때 부른다."""
    name = str(path or "").strip()
    if name not in SUPPORTED_FIELDS:
        raise parsers.FieldPathMissing(f"지원하지 않는 필드입니다: {path!r}")
    try:
        document = read_page(data)
    except GooglePatentsPageError as exc:
        raise parsers.ParserError(str(exc)) from exc
    value = document.fields.get(name, "")
    if not value:
        raise parsers.FieldPathMissing(f"페이지에 그 필드가 없습니다: {name}")
    return value


_REGISTERED = False


def register() -> None:
    """파서와 프로필을 등록한다. 프로세스당 한 번."""
    global _REGISTERED
    if _REGISTERED:
        return
    parsers.register_parser(PARSER_ID, PARSER_VERSION, _extract)
    parsers.register_profile(
        parsers.SourceProfile(
            profile_id=PROFILE_GOOGLE_PATENTS_PAGE,
            parser_id=PARSER_ID,
            parser_version=PARSER_VERSION,
            source_kind=SOURCE_NORMALIZED,
            translation_state=TRANSLATION_UNKNOWN,
            language="",
            raw_capable=False,
            note=(
                "Google Patents 원문 페이지 HTML. 특허청 공식 문서가 아니며 문헌에 따라 "
                "OCR·번역일 수 있다. 번역 구역에서는 원어 문장만 뽑는다. 원문 등급 없음."
            ),
        )
    )
    _REGISTERED = True


register()
