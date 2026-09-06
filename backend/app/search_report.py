"""Render model judgments separately from mechanical evidence observations."""
from __future__ import annotations
from urllib.parse import quote
from .search_channels import cell, STATUS_LABELS
from .search_legacy import view
from .search_manifest import GROUP_DEFINITIONS, is_linkable_url
from .search_verification import ISSUE_LABELS, LEVEL_LABELS
from .search_quality import REASON_LABELS

def _link(raw) -> str:
    if not is_linkable_url(raw):
        return "링크 미확인"
    return "[문헌 보기](" + quote(str(raw), safe=":/?&=%#@+;,~.-_") + ")"

def render(manifest: dict) -> str:
    data = view(manifest)
    lines = ["# 유사문헌 검색 결과", ""]
    if data.get("legacy"):
        lines += [f"이전 형식(v{data['legacy_version']})의 저장 기록입니다. 재분류·재검증하지 않았습니다.", ""]
    if data.get("status") == "verification_incomplete":
        lines += ["검색 실행 종료 · 검증 미완료. 아래 후보와 LLM 분류는 확인이 더 필요합니다.", ""]
    elif data.get("status") != "complete":
        lines += ["검색이 완료되지 않았습니다. 최종 후보 보고서가 아닙니다.", cell(data.get("error")), ""]
    quality = data.get("quality")
    if quality:
        lines += [f"근거 검증 후보: {quality['verified_candidate_count']}/{quality['candidate_count']}건. 검색의 충분성·누락 없음은 보증하지 않습니다.", ""]
        for item in quality.get("outstanding", []):
            reason = REASON_LABELS.get(item["reason"], "원문 조회 미시도" if item["reason"] == "not_attempted" else "조회 후 검증 미해결")
            lines.append(f"- {cell(item['identity'])}: {reason}, 대응 근거 미검증 {item['unverified_mapping_count']}개")
        for item in quality.get("constraints", []):
            lines.append(f"- 제약 {cell(item['source'])}: {cell(REASON_LABELS.get(item['reason'], item['reason']))} {cell(item.get('detail', ''))}")
        followup = data.get("verification_followup") or {}
        lines += ["", "추가 확인: " + cell(followup.get("reason", "기록 없음")), ""]
    lines += ["A/B/C는 LLM의 기술적 판단이며, 증거 확보 수준과 독립적입니다.", ""]
    definitions = data.get("group_definitions") or GROUP_DEFINITIONS
    for group, meaning in definitions.items():
        lines.append(f"- {cell(group)}: {cell(meaning)}")
    lines += ["", "## 사용 가능한 도구", ""]
    for name, status in data.get("tool_availability", {}).items():
        lines.append(f"- {cell(name)}: {cell(STATUS_LABELS.get(status.get('status'), status.get('status')))}")
    failures = [row for row in data.get("tool_journal", []) if row.get("ok") is False]
    failures += (data.get("observed") or {}).get("tool_failures", [])
    if failures:
        lines += ["", "도구 호출 실패 (문헌 부재를 뜻하지 않음):", ""]
        for row in failures:
            lines.append("- " + cell(row.get("tool") or row.get("name")) + ": " +
                         cell(row.get("detail") or row.get("error_code") or row.get("error")))
    focus = (data.get("input") or {}).get("search_focus")
    if focus:
        lines += ["", "## 검색 대상 미대응 구성", ""]
        lines += ["- " + cell(item.get("feature")) for item in focus.get("components", [])]
    dates = data.get("date_filter") or {}
    cutoff = dates.get("cutoff")
    lines += ["", "## 검색 기준일", "",
              cell(cutoff) + " 까지 공개된 문헌" if cutoff else "날짜 제한 없음",
              "기준: 공개일(publication date). 출원일·우선일로 대체하지 않습니다.",
              f"공개일 미확인 후보: {dates.get('unknown_publication_date', 0)}건"]
    candidates = (data.get("reported") or {}).get("candidates", [])
    if not candidates:
        lines += ["", "최종 후보가 없습니다. 미검색·접속 실패는 관련 문헌의 부재를 뜻하지 않습니다."]
    ordered = [(rank, item) for group in ("A", "B", "C", None)
               for rank, item in enumerate(candidates, 1) if item.get("group") == group]
    previous_group = object()
    for rank, item in ordered:
        group = item.get("group")
        if group != previous_group:
            count = sum(candidate.get("group") == group for candidate in candidates)
            lines += ["", f"## LLM 그룹 {cell(group or '미분류')} · {count}건", "",
                      cell(definitions.get(group, "분류되지 않은 참고 후보"))]
            previous_group = group
        lines += ["", f"### {rank}. {cell(item.get('doc_number') or item.get('doi') or item.get('title'))}", "",
                  f"LLM 제목: {cell(item.get('title') or item.get('reported_title'))}",
                  "", _link(item.get("url")), "",
                  "증거: " + cell(LEVEL_LABELS.get(item.get("evidence_level"), "이전 형식 / 재검증 안 함")),
                  "", "LLM 설명: " + cell(item.get("note"))]
        scopes = item.get("verification_scope") or {}
        for field, label in (("verified_titles", "보존 원문 명칭"), ("verified_applicants", "보존 원문 저자·출원인")):
            if item.get(field):
                lines += ["", label + ": " + cell(" / ".join(item[field]))]
        if scopes:
            lines += ["", "확보 범위: " + cell(", ".join(f"{k}={v}" for k, v in scopes.items()))]
        issues = item.get("verification_issues") or []
        if issues:
            lines += ["", "확인 사항: " + cell(" / ".join(ISSUE_LABELS.get(x, x) for x in issues))]
        rows = item.get("mapping") or []
        if rows:
            lines += ["", "| 청구항 구성 | LLM 대응 판단 | 유사점 / 차이점 | 근거 대조 |",
                      "| --- | --- | --- | --- |"]
            for row in rows:
                evidence = "보존 응답과 일치" if row.get("support_verified") else "모델 설명 / 미검증"
                lines.append("| " + " | ".join(cell(x) for x in (
                    row.get("feature"), row.get("counterpart") or row.get("degree"),
                    str(row.get("similar") or "") + " / " + str(row.get("different") or ""),
                    evidence + ": " + str(row.get("support_text") or ""))) + " |")
                if row.get("quote_verified") and row.get("verbatim_excerpt"):
                    lines += ["", "확인된 원문: " + cell(row["verbatim_excerpt"])]
    dates = data.get("date_filter") or {}
    excluded = dates.get("excluded") or []
    if excluded:
        lines += ["", "## 기준일 뒤에 공개돼 제외한 후보 (감사 기록)", ""]
        for item in excluded:
            candidate = item.get("candidate", item)
            lines.append("- " + cell(candidate.get("doc_number") or candidate.get("doi")) + ": " + cell(candidate.get("publication_date")))
    lines += ["", "실제 도구 호출·검색어·응답 참조와 LLM 원출력은 검색 감사 기록에 별도로 보존됩니다.", ""]
    return "\n".join(lines)
