"""Source identities and observed calls. No output contract or ranking."""
import copy
import json
import re
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
INPUT_KIND_QUERY, INPUT_KIND_URL = "query", "url"
SEARCH_TOOL_NAMES = frozenset(("WebSearch", "search_web", "web_search"))

FETCH_TOOL_NAMES = frozenset(("WebFetch", "read_url_content"))

def normalize_url(raw) -> str:
    value = str(raw or "").strip()
    try:
        parts = urlsplit(value)
        if parts.scheme.lower() not in ("http", "https") or not parts.hostname:
            return ""
        if parts.username is not None or parts.password is not None:
            return ""
        if any(ord(ch) < 32 or ch.isspace() for ch in value):
            return ""
        return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path.rstrip("/"), parts.query, ""))
    except ValueError:
        return ""

def is_linkable_url(raw) -> bool:
    return bool(normalize_url(raw)) and not any(ch in str(raw) for ch in '<>"')

def identity_key(number: str = "", doi: str = "") -> str:
    """Exact publication identity: preserve country and kind code, never family."""
    # arXiv:ID·arxiv.org URL·버전 붙은 DOI 를 한 문헌으로 센다(literature_client 의 규칙).
    from .patent_search.literature_client import ARXIV_DOI_PREFIX, arxiv_identity

    arxiv = arxiv_identity(doi) if doi else arxiv_identity(number)
    if arxiv is not None:
        return "doi:" + ARXIV_DOI_PREFIX + arxiv[0]
    if doi:
        text = str(doi).strip().lower()
        for prefix in ("https://doi.org/", "http://doi.org/", "doi:"):
            if text.startswith(prefix):
                text = text[len(prefix):]
        return "doi:" + text
    return "patent:" + re.sub(r"[\s/.-]", "", str(number).upper())

def observed(calls, tool_uses=None, **_context) -> dict:
    calls = [copy.deepcopy(call) for call in (calls or []) if isinstance(call, dict)]
    queries, attempted, read, lookups = [], [], [], []
    counts, search_count = {}, 0
    for call in calls:
        name = str(call.get("name") or "")
        counts[name] = counts.get(name, 0) + 1
        args = call.get("input") or {}
        if not isinstance(args, dict):
            continue
        args = args.get("arguments", args)
        if not isinstance(args, dict):
            continue
        url = str(args.get("url") or "")
        if args.get("input_kind") == INPUT_KIND_URL:
            if url:
                lookups.append(url)
            continue
        if name in SEARCH_TOOL_NAMES or name.endswith("_search"):
            batch = args.get("queries")
            query = args.get("query")
            if isinstance(batch, list) and batch:
                queries.extend(q for q in batch if isinstance(q, str))
                search_count += 1
            elif query:
                queries.append(json.dumps(query, ensure_ascii=False) if isinstance(query, dict) else str(query))
                search_count += 1
        if url and name in FETCH_TOOL_NAMES:
            attempted.append(url)
            if call.get("ok") is True and (name != "read_url_content" or call.get("content_read") is True):
                read.append(url)
    return {
        "tool_calls": calls, "tool_call_counts": counts,
        "search_queries": list(dict.fromkeys(queries)), "search_call_count": search_count,
        "attempted_fetch_urls": list(dict.fromkeys(attempted)),
        "succeeded_fetch_urls": list(dict.fromkeys(read)),
        "url_lookup_attempts": list(dict.fromkeys(lookups)),
        "tool_failures": [call for call in calls if call.get("ok") is False],
        "unknown_tool_outcomes": [call for call in calls if call.get("ok") is None],
    }

def read_tool_journal(work_dir: Path) -> list[dict]:
    path = work_dir / "search_tool_calls.jsonl"
    if not path.exists():
        return []
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            value = json.loads(line)
            if isinstance(value, dict):
                records.append(value)
        except ValueError:
            records.append({"state": "incomplete", "error_code": "journal_record_incomplete"})
    return records

def retained_records(journal: list) -> list[dict]:
    """Preserve fetched metadata, without choosing candidates or inventing groups."""
    records = {}
    for row in journal:
        if row.get("state") != "completed" or row.get("ok") is not True or not row.get("tool", "").endswith(("_fetch", "_search")):
            continue
        for record in (row.get("result") or {}).get("records", []):
            number = str(record.get("document_number") or record.get("doc_number") or "")
            doi = str(record.get("doi") or "")
            url = str(record.get("source_url") or record.get("url") or "")
            if not (number or doi or url):
                continue
            if not doi and number.startswith("10."):
                doi, number = number, ""
            key = identity_key(number, doi) if number or doi else url
            item = records.setdefault(key, {"document_number": number, "doi": doi,
                "title": str(record.get("title") or ""), "url": url, "scopes": [], "call_ids": []})
            scope = ("bibliographic_search" if row["tool"].endswith("_search") else
                     (row.get("arguments") or {}).get("constituent", "abstract" if row["tool"] == "literature_fetch" else "claims"))
            if not item["title"]:
                item["title"] = str(record.get("title") or "")
            if scope not in item["scopes"]:
                item["scopes"].append(scope)
            item["call_ids"].append(row.get("id", ""))
    return list(records.values())
