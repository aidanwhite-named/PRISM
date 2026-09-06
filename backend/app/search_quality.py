"""Observed completion and outstanding evidence; never infer search recall."""
from __future__ import annotations

from . import search_manifest as sm

REASON_LABELS = {
    "unsupported_transport": "실행별 도구 연결 미지원", "not_implemented": "접속 미구현",
    "disabled": "연동 꺼짐", "not_configured": "인증 미설정",
    "limit_exhausted": "호출·쿼터 한도 소진", "access_failed": "조회 실패",
    "timeout": "시간 한도 소진", "rate_limited": "Provider 사용량 제한",
    "cancelled": "취소됨", "outcome_unknown": "호출 완료 여부 미확인",
    "page_read_without_provenance": "페이지 열람 성공 · 보존 근거 대조 경로 없음",
}


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
    for call in journal + observed.get("tool_failures", []):
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
