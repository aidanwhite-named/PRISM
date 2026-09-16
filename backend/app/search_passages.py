"""Stable selectors for delivered text; no summarization or relevance ranking."""
from __future__ import annotations

import hashlib
import json
import re

TEXT_FIELDS = {"abstract", "claims", "description", "full_text", "page_claims",
               "page_description", "page_abstract", "pdf_text"}
MAX_PASSAGE_CHARS = 1200


def passages(document: str, fields: dict, refs: dict) -> list[dict]:
    """Index exact spans in fields. Previews are navigation aids, never evidence.

    Keep the full text in fields only once. Preserve every character and prefer
    paragraph/claim boundaries, then sentence boundaries for long paragraphs.
    IDs bind the document, source, entire delivered field and span.
    """
    result = []
    for name, text in fields.items():
        ref = refs.get(name)
        if name.split(":")[0] not in TEXT_FIELDS or not text or not isinstance(ref, dict):
            continue
        digest = hashlib.sha256(json.dumps(
            [document, ref, text], ensure_ascii=False, sort_keys=True,
        ).encode("utf-8")).hexdigest()
        boundaries = [m.end() for m in re.finditer(
            r"(?<=\n)(?=\[(?:claim \d+|\d{3,5}|p\.\d+)\])|\n\s*\n", text
        )]
        start = 0
        while start < len(text):
            ceiling = min(start + MAX_PASSAGE_CHARS, len(text))
            nearby = [p for p in boundaries if start < p <= ceiling]
            end = nearby[0] if nearby else ceiling
            if end == ceiling and ceiling < len(text):
                breaks = list(re.finditer(r"[.!?。！？](?:\s|$)|\n", text[start:ceiling]))
                if breaks and breaks[-1].end() >= MAX_PASSAGE_CHARS // 3:
                    end = start + breaks[-1].end()
            fragment = text[start:end]
            if fragment.strip():
                token = hashlib.sha256(f"{digest}:{start}:{end}".encode()).hexdigest()[:24]
                result.append({"passage_id": "p_" + token, "field": name,
                               "start": start, "end": end,
                               "preview_start": fragment[:90], "preview_end": fragment[-40:]})
            start = end
    return result
