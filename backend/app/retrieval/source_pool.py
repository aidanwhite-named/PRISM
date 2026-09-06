"""Lossless sharing of exact source text within one document and PDF page.

Audit bundles keep every original field. Only the report prompt uses references;
no whitespace normalization, fuzzy matching, or cross-page sharing is allowed.
"""

from __future__ import annotations


class SourcePool:
    def __init__(self, entries: list[tuple[str, int, str]]) -> None:
        self.sources: list[dict] = []
        self._references: dict[tuple[str, int, str], dict] = {}
        # Keep documents/pages in reading order, with longer containers first
        # within each page so it owns excerpts regardless of citation order.
        for attachment, page, text in sorted(
            entries, key=lambda item: (item[0], item[1], -len(item[2]))
        ):
            key = (attachment, page, text)
            if not text or key in self._references:
                continue
            for source in self.sources:
                if (source["attachment"], source["pdf_page"]) != (attachment, page):
                    continue
                start = source["text"].find(text)
                if start >= 0:
                    break
            else:
                source = {
                    "id": f"S{len(self.sources) + 1:03d}",
                    "attachment": attachment,
                    "pdf_page": page,
                    "text": text,
                }
                self.sources.append(source)
                start = 0
            self._references[key] = {
                "source_id": source["id"], "start": start, "end": start + len(text)
            }

    @property
    def text_chars(self) -> int:
        return sum(len(source["text"]) for source in self.sources)

    def reference(self, attachment: str, page: int, text: str) -> dict:
        return self._references[(attachment, page, text)]

    def describe(self, attachment: str, page: int, text: str) -> str:
        ref = self.reference(attachment, page, text)
        return f"원문 {ref['source_id']} [{ref['start']}:{ref['end']}] 참조"

    def render(self) -> list[str]:
        if not self.sources:
            return []
        lines = [
            "", "[공유 원문 — PRISM 이 PDF 에서 그대로 꺼낸 텍스트]",
            "동일 문헌·페이지 안의 중복 원문은 한 번만 싣습니다. 각 근거의 S번호는",
            "아래 원문을 가리키며 [시작:끝]은 0부터 세는 문자 위치(끝 제외)입니다.",
            "참조된 발췌와 앞뒤 문맥도 이 원문에 그대로 포함되어 있습니다.",
        ]
        for source in self.sources:
            lines += [
                "", f"--- {source['id']} · {source['attachment']} · PDF {source['pdf_page']}쪽 · 원문 시작 ---",
                source["text"], f"--- {source['id']} 원문 끝 ---",
            ]
        return lines
