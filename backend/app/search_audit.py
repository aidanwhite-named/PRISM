"""Check observable search bookkeeping. Never infer technical relevance or recall."""
from __future__ import annotations

import json
import re

from . import search_manifest as sm


def _identity(item):
    if item.get("doc_number") or item.get("doi"):
        return sm.identity_key(item.get("doc_number", ""), item.get("doi", ""))
    url = sm.normalize_url(item.get("url", ""))
    patent = re.search(r"/patent/([A-Z]{2}\d+[A-Z]\d?)(?:/|$)", url, re.I)
    if patent:
        return sm.identity_key(patent.group(1))
    if "doi.org/" in url:
        return sm.identity_key(doi=url.split("doi.org/", 1)[1])
    return "url:" + url if url else ""


def assess(reported, observed, journal, date_filter=None):
    reported = reported or {}
    # Only fetched leads are mandatory here; broad result pages may contain
    # hundreds of unrelated records. Do not force them into candidate rankings.
    attempted = set()
    for call in journal + observed.get("tool_calls", []):
        name = call.get("tool") or call.get("name", "")
        args = call.get("arguments") or call.get("input") or {}
        if not isinstance(args, dict):
            continue
        args = args.get("arguments", args)
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except ValueError:
                continue
        if not isinstance(args, dict):
            continue
        item = {}
        if name.endswith("_fetch"):
            item = {"doc_number": args.get("publication_number", ""), "doi": args.get("doi", "")}
        elif name in sm.FETCH_TOOL_NAMES or args.get("input_kind") == sm.INPUT_KIND_URL:
            item = {"url": args.get("url", "")}
        key = _identity(item)
        if key:
            attempted.add(key)
    accounted = {_identity(c) for c in reported.get("candidates", [])}
    accounted.update(_identity(c) for c in (date_filter or {}).get("excluded", []))
    accounted.update(_identity(c) for c in reported.get("candidate_dispositions", []) if c.get("reason", "").strip())
    # Also accept an exact URL disposition for a DOI-less document.
    accounted.update("url:" + sm.normalize_url(c["url"]) for c in reported.get("candidates", []) if c.get("url"))
    dropped = sorted(attempted - accounted)
    broad = []
    for call in journal:
        if call.get("tool") != "epo_search" or call.get("state") != "completed" or call.get("ok") is not True:
            continue
        result = call.get("result") or {}
        coverage = result.get("coverage") or {}
        returned = coverage.get("returned_records", len(result.get("records", [])))
        total = coverage.get("total_results", result.get("total_found"))
        if isinstance(total, (int, float)) and returned and total > returned * 10:
            broad.append({"call_id": call.get("id"), "cql": result.get("cql", ""),
                          "total_results": total, "returned_records": returned,
                          "result_range": coverage.get("result_range"),
                          "artifact_id": result.get("raw_artifact_id", "")})
    review = reported.get("search_review") or {}
    missing = [key for key in ("stop_reason", "expansion_summary") if not review.get(key, "").strip()]
    if broad and not review.get("sampling_review", "").strip():
        missing.append("sampling_review")
    return {"status": "incomplete" if dropped or missing else "recorded",
            "unaccounted_fetches": dropped, "broad_searches": broad,
            "missing_review_fields": missing,
            "reported_review": review,
            "meaning": "Audit of observed fetches and model-reported stopping rationale; not proof of recall or semantic adequacy."}
