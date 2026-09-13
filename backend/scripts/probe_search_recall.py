"""Bounded live discovery check using a saved claim, with target identity withheld.

Run from backend: .venv/Scripts/python scripts/probe_search_recall.py PATH_TO_RUN
Uses the installed Codex provider and account; writes only a new diagnostics folder.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import config, search_channels, search_manifest, search_prompt, search_recall, settings_service
from app.db import session_scope
from app.execution.runner import _search_mcp_servers
from app.providers.base import ExecutionRequest
from app.providers.codex_cli import CodexCliProvider


async def main():
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source_run", type=Path)
    parser.add_argument("--max-calls", type=int, default=12)
    parser.add_argument("--initial-only", action="store_true", help="Stop after web and the first two EPO queries")
    parser.add_argument("--with-spec", action="store_true", help="Include specification terminology, with publication identities removed")
    args = parser.parse_args()
    source = json.loads((args.source_run / "search_manifest.json").read_text(encoding="utf-8"))
    target = (source.get("input", {}).get("spec_document") or {}).get("publication_number")
    spec_text = ""
    for path in (args.source_run / "normalized").glob("*.txt"):
        text = path.read_text(encoding="utf-8")
        reference = search_recall.reference_publication(text)
        if reference:
            target = target or reference
            # Use the same terminology reference as the product; omit front-page identities.
            title = text.find("(54)")
            if args.with_spec and title >= 0:
                spec_text = re.sub(r"(?:공개특허\s*)?10-\d{4}-\d{7}", "[번호 생략]", text[title:])
                spec_text = re.sub(r"\b(?:KR|EP|US|WO|CN|JP)\d+[AB]\d?\b", "[번호 생략]", spec_text)
            break
    folder = config.PATHS.data_dir / "diagnostics" / ("recall-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f"))
    folder.mkdir(parents=True)
    with session_scope() as session:
        values = settings_service.get_all(session)
    status = search_channels.availability(values, "codex")
    provider = CodexCliProvider()
    policy = replace(provider.search_tool_policy, mcp_tools=search_channels.available_mcp_names(status),
                     required_tools=(), max_tool_calls=args.max_calls,
                     max_search_calls=0, max_url_lookup_calls=0, max_content_read_calls=0)
    # The target identifier is used by the local evaluator only, never injected into the query.
    message = search_prompt.compose(
        "청구항의 핵심 기술 내용으로 공개특허 후보를 찾으십시오. 식별자·인명 조회 없이 키워드 검색을 사용하십시오.",
        source["input"]["claim_text"], spec_text,
    ).body
    system = config.CODEX_SEARCH_RUNTIME_CONTEXT + "\n[이 실행의 도구 상태]\n" + json.dumps(status, ensure_ascii=False)
    system += """\n이번 실행은 후보 발견 단계만 측정하는 제한된 진단입니다.
검색 전략은 위 규칙을 따르되 상세 fetch·원문 열람·구성대비·후속 검증은 생략합니다.
웹과 EPO 키워드 질의를 사용하고 총 도구 호출을 예산 안에서 끝내십시오.
청구항에 있는 기술적 특징을 조합하고 한국어와 영어 대안 용어를 검색하십시오.
결과는 candidates 배열(번호·제목·url·group:null·mapping:[])과 간단한 rounds만 반환하십시오.
"""
    if args.initial_only:
        system += "\n비용을 줄이기 위해 웹 검색 1회와 위 전략의 첫 EPO 질의 2회까지만 수행한 뒤 즉시 최종 JSON을 출력하십시오. 추가 탐색은 생략합니다.\n"
    request = ExecutionRequest(job_id=folder.name, work_dir=folder, system_prompt=system,
        user_message=message, model=source.get("model") or None, timeout_seconds=420,
        tool_policy=policy, mcp_servers=_search_mcp_servers(folder, "", args.max_calls, "codex"))
    (folder / "prompt.txt").write_text(system + "\n\n" + message, encoding="utf-8")
    print(json.dumps({"diagnostics": str(folder), "target_withheld": target, "max_calls": args.max_calls}), flush=True)
    async def emit(kind, payload):
        if kind in ("tool_use_resolved", "tool_error"):
            print(json.dumps({"event": kind, **payload}, ensure_ascii=False), flush=True)
    outcome = await provider.execute(request, emit)
    journal = search_manifest.read_tool_journal(folder)
    summary = {"target_withheld": target, "spec_terminology_used": bool(spec_text),
               "reference_retrieval": search_recall.assess(target, journal),
               "usage": outcome.usage, "timed_out": outcome.timed_out, "errors": outcome.errors,
               "tool_budget_exceeded": outcome.tool_budget_exceeded,
               "observed": search_manifest.observed(outcome.tool_calls, outcome.tool_uses)}
    (folder / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    (folder / "output.json").write_text(outcome.result_text, encoding="utf-8")
    print(json.dumps({k: v for k, v in summary.items() if k != "observed"}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    asyncio.run(main())
