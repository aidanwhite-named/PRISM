"""Observed completion and outstanding evidence; never infer search recall."""
from __future__ import annotations

import json

from . import search_manifest as sm

REASON_LABELS = {
    "web_search_not_attempted": "웹 키워드 검색 미수행",
    "partial_search_results": "검색 결과 일부만 조회",
    "unsupported_transport": "실행별 도구 연결 미지원", "not_implemented": "접속 미구현",
    "disabled": "연동 꺼짐", "not_configured": "인증 미설정",
    "limit_exhausted": "호출·쿼터 한도 소진", "access_failed": "조회 실패",
    "timeout": "시간 한도 소진", "rate_limited": "Provider 사용량 제한",
    "cancelled": "취소됨", "outcome_unknown": "호출 완료 여부 미확인",
    "page_read_without_provenance": "페이지 열람 성공 · 보존 근거 대조 경로 없음",
    "unverified": "연결 미확인", "unreachable": "연결 실패",
}

_MCP_PREFIX = "mcp__prism-search__"


def _flatten(value):
    # 스트림은 인수 값을 문자열로 눌러 담는다(Codex 는 query 객체도 JSON 문자열).
    # journal 은 서버가 받은 값 그대로다. 비교하려면 같은 모양으로 편다.
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except ValueError:
            return value
        return _flatten(parsed) if isinstance(parsed, (dict, list)) else value
    if isinstance(value, dict):
        return {str(key): _flatten(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_flatten(item) for item in value]
    return json.dumps(value)


def _arguments_key(arguments) -> str:
    return json.dumps(_flatten(arguments or {}), ensure_ascii=False, sort_keys=True)


def _stream_mcp_key(call: dict) -> tuple[str, str] | None:
    name = str(call.get("name") or "")
    if not name.startswith(_MCP_PREFIX):
        return None
    payload = call.get("input") if isinstance(call.get("input"), dict) else {}
    # Codex 는 {server, tool, arguments} 로, Claude 는 인수 자체로 싣는다.
    arguments = payload.get("arguments")
    if not isinstance(arguments, dict):
        arguments = {key: value for key, value in payload.items() if key not in ("server", "tool")}
    return name.removeprefix(_MCP_PREFIX), _arguments_key(arguments)


def stream_only_failures(journal, observed) -> list[dict]:
    """Provider 스트림의 도구 실패 중 journal 에 없는 것만 돌려준다.

    PRISM 검색 도구의 실패는 두 곳에 남는다. journal 은 실제 오류 코드
    (CLIENT.InvalidCountryCode 등)를 들고 있고, 스트림은 같은 호출을 "failed" 로만
    적는다. 그냥 더하면 2026-09-13 실행처럼 실패 4건이 8건으로 찍힌다.

    도구와 인수가 같은 것끼리 먼저 짝짓고, 남은 것은 도구 이름끼리 하나씩 짝짓는다 —
    스트림은 긴 인수를 잘라 담아 인수로는 짝을 못 찾을 때가 있다. 짝이 없는
    실패(웹 검색, 서버에 닿기 전에 끊긴 MCP 호출)는 그대로 남긴다.
    """
    pending = [(str(row.get("tool") or ""), _arguments_key(row.get("arguments")))
               for row in journal or [] if row.get("ok") is False]
    failures = [call for call in (observed or {}).get("tool_failures", []) if isinstance(call, dict)]
    keys = [_stream_mcp_key(call) for call in failures]
    mirrored = [False] * len(failures)
    for exact in (True, False):
        for index, key in enumerate(keys):
            if mirrored[index] or key is None:
                continue
            for position, (tool, arguments) in enumerate(pending):
                if tool == key[0] and (not exact or arguments == key[1]):
                    del pending[position]
                    mirrored[index] = True
                    break
    return [call for call, hit in zip(failures, mirrored) if not hit]


def assess(reported, observed, journal, availability, *, execution_error=None, outcome=None):
    candidates = (reported or {}).get("candidates", [])
    outstanding = []
    attempted = {sm.normalize_url(url) for url in observed.get("attempted_fetch_urls", [])
                 + observed.get("url_lookup_attempts", [])}
    for candidate in candidates:
        issues = list(candidate.get("verification_issues", []))
        rows = candidate.get("mapping", [])
        missing = sum(not row.get("support_verified") for row in rows)
        if not candidate.get("evidence_sources") or issues or missing or not rows:
            key = sm.identity_key(candidate.get("doc_number", ""), candidate.get("doi", ""))
            fetches = [call for call in journal if call.get("tool", "").endswith("_fetch")
                       and sm.identity_key((call.get("arguments") or {}).get("publication_number", ""),
                                           (call.get("arguments") or {}).get("doi", "")) == key]
            attempted_here = bool(fetches) or sm.normalize_url(candidate.get("url")) in attempted
            reason = "verification_unresolved" if attempted_here else "not_attempted"
            if candidate.get("evidence_level") == "source_page_reviewed" and not candidate.get("evidence_sources"):
                reason = "page_read_without_provenance"
            outstanding.append({"identity": key, "title": candidate.get("title", ""),
                                "reason": reason,
                                "issues": issues, "unverified_mapping_count": missing})
    constraints = [{"source": name, "reason": value.get("status")}
                   for name, value in availability.items() if value.get("status") != "available"]
    web_queries = [call for call in observed.get("tool_calls", [])
                   if call.get("name") in sm.SEARCH_TOOL_NAMES
                   and (call.get("input") or {}).get("input_kind") != "url"
                   and ((call.get("input") or {}).get("query") or (call.get("input") or {}).get("queries"))]
    if "web" in availability and not web_queries:
        constraints.append({"source": "web", "reason": "web_search_not_attempted"})
    # Report database coverage without converting it into a recall percentage.
    pages = {}
    for call in journal:
        result = call.get("result") or {}
        if call.get("tool") != "epo_search" or call.get("ok") is not True or result.get("duplicate_query"):
            continue
        entry = pages.setdefault(result.get("cql", ""), {"total": 0, "positions": set()})
        entry["total"] = max(entry["total"], result.get("total_found") or 0)
        start = result.get("start", 1)
        count = result.get("returned_count", len(result.get("records", [])))
        entry["positions"].update(range(start, start + count))
    for cql, page in pages.items():
        if len(page["positions"]) < page["total"]:
            constraints.append({"source": "epo", "reason": "partial_search_results",
                                "detail": f"{len(page['positions'])}/{page['total']}건 조회: {cql}"})
    for call in journal + stream_only_failures(journal, observed):
        if call.get("ok") is False or call.get("state") == "incomplete":
            code = call.get("error_code") or call.get("error") or "unknown_failure"
            constraints.append({"source": call.get("tool") or call.get("name", "journal"),
                                "reason": "limit_exhausted" if "limit" in str(code).lower() or "quota" in str(code).lower() else "access_failed",
                                "detail": str(code)})
    for flag, reason in (("timed_out", "timeout"), ("tool_budget_exceeded", "limit_exhausted"),
                         ("content_read_budget_exceeded", "limit_exhausted"), ("rate_limited", "rate_limited"),
                         ("cancelled", "cancelled")):
        if outcome and getattr(outcome, flag, False):
            constraints.append({"source": "provider", "reason": reason})
    completed = {call.get("id") for call in journal if call.get("state") == "completed"}
    unknown = observed.get("unknown_tool_outcomes", []) + [call for call in journal
               if call.get("state") == "started" and call.get("id") not in completed]
    constraints += [{"source": call.get("tool") or call.get("name", "provider"), "reason": "outcome_unknown"}
                    for call in unknown]
    return {"execution_status": "incomplete" if execution_error else "complete",
            "verification_status": "incomplete" if execution_error or outstanding else ("no_candidates" if not candidates else "complete"),
            "search_coverage": "not_established",
            "candidate_count": len(candidates), "verified_candidate_count": len(candidates) - len(outstanding),
            "outstanding": outstanding, "constraints": constraints}
