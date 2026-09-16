"""Run one live search with a saved user message and model, without restarting the UI.

Requires --live. Writes a new diagnostics folder; never rewrites the source run.
Uses the configured account and normal search quotas.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sqlite3
import sys
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import config, search_channels, search_manifest as sm, search_quality, search_report, search_verification as sv, settings_service
from app.db import session_scope
from app.execution.runner import _search_mcp_servers
from app.evaluation.evaluator import evaluate
from app.providers.base import ExecutionRequest
from app.providers.codex_cli import CodexCliProvider


async def main():
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source_run", type=Path)
    parser.add_argument("--live", action="store_true", required=True)
    args = parser.parse_args()
    source = json.loads((args.source_run / "search_manifest.json").read_text(encoding="utf-8"))
    if source.get("provider") != "codex":
        raise ValueError("This diagnostic currently supports saved Codex runs only")
    saved_prompt = (args.source_run / "final_prompt.txt").read_text(encoding="utf-8")
    _, marker, message = saved_prompt.partition("===== USER MESSAGE =====\n")
    if not marker:
        raise ValueError("Saved user-message boundary missing")
    # Read the original CLI effort rather than silently adopting a new default.
    with sqlite3.connect(config.PATHS.db_path.as_uri() + "?mode=ro", uri=True) as db:
        row = db.execute("SELECT cli_args FROM execution_jobs WHERE id=?", (args.source_run.name,)).fetchone()
    cli_args = json.loads(row[0]) if row and row[0] else []
    effort = next((value.split("=", 1)[1].strip('"') for value in cli_args
                   if value.startswith("model_reasoning_effort=")), "")
    with session_scope() as session:
        values = settings_service.get_all(session)
    available = search_channels.availability(values, "codex")
    folder = config.PATHS.data_dir / "diagnostics" / ("search-recovery-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f"))
    folder.mkdir(parents=True)
    provider = CodexCliProvider()
    limit = source["limits"]["max_tool_calls"]
    policy = replace(provider.search_tool_policy, mcp_tools=search_channels.available_mcp_names(available),
                     max_tool_calls=limit, required_tools=(), max_search_calls=0,
                     max_url_lookup_calls=0, max_content_read_calls=0)
    system = config.CODEX_SEARCH_RUNTIME_CONTEXT + "\n[이 실행의 도구 상태]\n" + json.dumps(available, ensure_ascii=False)
    request = ExecutionRequest(job_id=folder.name, work_dir=folder, system_prompt=system,
        user_message=message, model=source.get("model"), reasoning_effort=effort,
        timeout_seconds=source["limits"]["timeout_seconds"], tool_policy=policy,
        mcp_servers=_search_mcp_servers(folder, source.get("date_filter", {}).get("cutoff", ""), limit))
    (folder / "final_prompt.txt").write_text("===== SYSTEM PROMPT =====\n" + system + "\n\n===== USER MESSAGE =====\n" + message, encoding="utf-8")
    print(json.dumps({"folder": str(folder), "source_run": args.source_run.name,
                      "model": request.model, "effort": effort, "max_calls": limit}, ensure_ascii=False), flush=True)
    async def emit(kind, payload):
        if kind in ("tool_use", "tool_use_resolved", "tool_error"):
            print(json.dumps({"event": kind, "name": payload.get("name"), "id": payload.get("id")}), flush=True)
    outcome = await provider.execute(request, emit)
    (folder / "initial_output.txt").write_text(outcome.result_text, encoding="utf-8")
    (folder / "stdout.log").write_text(outcome.raw_stdout, encoding="utf-8")
    (folder / "stderr.log").write_text(outcome.raw_stderr, encoding="utf-8")
    observed = sm.observed(outcome.tool_calls, outcome.tool_uses)
    journal = sm.read_tool_journal(folder)
    verdict = evaluate(outcome, [], fail_on_tool_use=True)
    reported = None
    parse_error = None
    try:
        reported, _ = sm.parse(outcome.result_text)
        reported = sv.verify(reported, observed, journal)
    except sm.SearchLogError as exc:
        parse_error = str(exc)
    quality = search_quality.assess(reported, observed, journal, available,
        execution_error=parse_error or (None if verdict.status == "SUCCEEDED" else verdict.status), outcome=outcome)
    manifest = {"version": 14, "status": "complete" if quality["verification_status"] == "complete" else "verification_incomplete",
        "reported": reported, "observed": observed, "tool_journal": journal, "quality": quality,
        "tool_availability": available, "group_definitions": source.get("group_definitions", {}),
        "usage": outcome.usage, "error": parse_error, "date_filter": source.get("date_filter", {})}
    (folder / "search_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    if reported is not None:
        (folder / "result.md").write_text(search_report.render(manifest), encoding="utf-8")
    calls = [c for c in journal if c.get("tool") == "epo_search" and c.get("state") == "completed"]
    rows = [r for c in (reported or {}).get("candidates", []) for r in c.get("mapping", [])]
    summary = {"source_run": args.source_run.name, "model": request.model, "effort": effort,
        "execution_status": verdict.status, "errors": verdict.errors, "parse_error": parse_error,
        "epo_attempts": len(calls), "epo_successes": sum(c.get("ok") is True for c in calls),
        "epo_errors": [{"code": c.get("error_code"), "detail": c.get("detail")} for c in calls if not c.get("ok")],
        "candidates": quality["candidate_count"], "verified_candidates": quality["verified_candidate_count"],
        "mapping_rows": len(rows), "verified_support": sum(bool(r.get("support_verified")) for r in rows),
        "verified_quotes": sum(bool(r.get("quote_verified") or r.get("page_quote_verified")) for r in rows),
        "usage": outcome.usage, "qualification": "One uncontrolled live rerun, not a recall benchmark; no follow-up repair stage."}
    (folder / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    asyncio.run(main())
