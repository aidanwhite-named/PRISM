"""Observed retrieval of the input publication, never an estimate of overall recall."""
from __future__ import annotations

import re

from .search_manifest import identity_key


def reference_publication(spec_text: str) -> str | None:
    # Only the front-page publication label, never references cited in the description.
    head = str(spec_text or "")[:4000]
    korean = re.search(r"\(11\)\s*공개번호\s*(?:KR\s*)?10[-\s]?(\d{4})[-\s]?(\d{7})", head)
    if korean:
        return "KR" + "".join(korean.groups()) + "A"
    international = re.search(
        r"\(11\)\s*(?:(?:Publication|Pub\.?|공개)\s*(?:Number|No\.?|번호)?\s*:?\s*)?"
        r"((?:EP|WO|US|KR|CN|JP)\s*[\d /.-]{4,25}\s*[AB]\d?)\b", head, re.I)
    if international:
        return identity_key(international.group(1)).removeprefix("patent:")
    return None


def query_kind(node) -> str:
    if not isinstance(node, dict):
        return "unknown"
    if node.get("type", "term") == "group":
        items = node.get("items", [])
        if node.get("op") == "not":
            return query_kind(items[0]) if items else "unknown"
        kinds = {query_kind(item) for item in items}
        if "identifier" in kinds:
            return "identifier"
        if node.get("op") == "or":
            return "keyword" if kinds == {"keyword"} else "classification"
        return "keyword" if "keyword" in kinds else "classification"
    field = node.get("field")
    if field in ("pn", "ap", "pr", "pa", "in"):
        return "identifier"
    if re.search(r"\b[A-Z]{2}\s*[0-9/.-]{5,}[A-Z]?\d?\b", str(node.get("value", "")), re.I):
        return "identifier"
    return "keyword" if field in ("ti", "ab", "ta", "txt") else "classification"


def assess(publication: str | None, journal: list[dict]) -> dict:
    result = {"publication_number": publication, "status": "no_reference" if not publication else "not_observed",
              "scope": "EPO tool responses; native web result bodies are not captured", "hits": []}
    if not publication:
        return result
    target = identity_key(publication)
    for call in journal:
        if call.get("ok") is not True or call.get("tool") != "epo_search":
            continue
        response = call.get("result") or {}
        numbers = [r.get("document_number", "") for r in response.get("records", [])]
        numbers += response.get("previously_seen", [])
        if target not in {identity_key(number) for number in numbers}:
            continue
        kind = query_kind((call.get("arguments") or {}).get("query"))
        result["hits"].append({"kind": kind, "origin": call.get("query_origin", "model"),
                               "call_id": call.get("id"), "cql": response.get("cql", "")})
    kinds = {hit["kind"] for hit in result["hits"]}
    for kind, status in (("keyword", "keyword_hit"), ("classification", "classification_only"), ("identifier", "identifier_only")):
        if kind in kinds:
            result["status"] = status
            break
    return result
