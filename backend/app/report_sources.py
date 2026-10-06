"""Deterministic excerpt choices shared by prompt delivery and report validation.

V3 annotates exact source slices with sentence IDs. Models select these IDs;
neither quote copying, character counting, nor model-written pages are required.
Older paragraph indexes remain available for reading historical V1/V2 payloads.
"""
from __future__ import annotations

import re

PAGE = re.compile(r'(?m)^--- PAGE (\d+) ---\s*$')
PARAGRAPH = re.compile(r'(?=\[\s*\d{4,6}\s*\]|\b\d{4,6}\.\s)|\n(?=\s*\n)')
SENTENCE = re.compile(r'(?<=[.!?。])(?=\s+[A-Z가-힣])')


def page_texts(body):
    markers = list(PAGE.finditer(body))
    if not markers:
        return [(None, body)]
    return [(int(m[1]), body[m.end():markers[i + 1].start() if i + 1 < len(markers) else len(body)])
            for i, m in enumerate(markers)]


def units(source_id, attachment, page, body):
    boundaries = sorted({0, len(body), *(m.start() for m in PARAGRAPH.finditer(body))})
    if len(boundaries) == 2:
        boundaries = sorted({*boundaries, *(m.start() for m in SENTENCE.finditer(body))})
    spans = []
    for start, end in zip(boundaries, boundaries[1:]):
        cuts = [start]
        if end - start > 1800:
            cuts += [m.start() for m in SENTENCE.finditer(body, start, end)]
        cuts.append(end)
        spans.extend(zip(cuts, cuts[1:]))
    rows = []
    for start, end in spans:
        if not body[start:end].strip():
            continue
        rows.append({'id': f'{source_id}-U{len(rows) + 1}', 'attachment': attachment,
                     'pdf_page': page, 'text': body[start:end], 'start': start, 'end': end})
    return rows


def full_text_units(attachment, body):
    return [unit for page, content in page_texts(body)
            for unit in units(f'{attachment}-P{page or 0}', attachment, page, content)]


def sentences(source_id, attachment, page, body):
    """Addressable exact slices, not rewritten or punctuation-normalized sentences.

    OCR/abbreviations may split a sentence into several pieces. Adjacent pieces
    can be selected together; no model transcription is needed to restore them.
    """
    boundaries = sorted({0, len(body), *(m.start() for m in PARAGRAPH.finditer(body)),
                         *(m.start() for m in SENTENCE.finditer(body))})
    rows = []
    for start, end in zip(boundaries, boundaries[1:]):
        if body[start:end].strip():
            rows.append({'id': f'{source_id}-T{len(rows) + 1}', 'source_id': source_id,
                         'attachment': attachment, 'pdf_page': page, 'text': body[start:end],
                         'start': start, 'end': end, 'order': len(rows)})
    return rows


def render_sentences(source_id, attachment, page, body):
    return '\n\n'.join(f"[원문 문장 {row['id']}]\n{row['text']}"
                       for row in sentences(source_id, attachment, page, body))


def render_full_text(attachment, body):
    return '\n\n'.join((f'--- PAGE {page} ---\n' if page else '') +
                       render_sentences(f'{attachment}-P{page or 0}', attachment, page, content)
                       for page, content in page_texts(body))


def render_index(rows):
    if not rows:
        return ''
    lines = ['[발췌 선택 번호 · 구간 안에서 번역할 연속 원문 문장을 quote로 선택]']
    for row in rows:
        # Truncated previews were copied as quotations (including ellipses).
        # Keep navigation metadata separate from the unchanged source above.
        lines.append(f"{row['id']} [{row['start']}:{row['end']}]")
    return '\n'.join(lines)
