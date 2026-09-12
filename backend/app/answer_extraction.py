"""Audited, cancellable library extraction through existing CLI providers."""
from __future__ import annotations

import asyncio
import json
from dataclasses import asdict

from . import answer_library as library, settings_service
from .answer_models import AnswerCase, AnswerExtraction
from .db import session_scope
from .enums import JobStatus
from .evaluation.evaluator import evaluate
from .models import utcnow
from .prompt_assembly import char_gate
from .providers.base import ExecutionRequest, NO_TOOLS
from .providers.model_limits import token_budget, estimate_tokens
from .providers.registry import build_provider

_tasks: dict[str, asyncio.Task] = {}
_providers: dict[str, object] = {}


def create_attempt(session, case: AnswerCase, provider: str, model: str | None) -> AnswerExtraction:
    if session.query(AnswerExtraction).filter(AnswerExtraction.case_id == case.id,
            AnswerExtraction.status.in_(["queued", "running"])).first():
        raise ValueError("이미 이 보고서를 정리하고 있습니다.")
    prompt = json.dumps({"claim_elements": case.claim_text, "report": case.report_text,
        "sources": [{"id": f["id"], "name": f["name"]} for f in case.files if f["kind"] == "source"]},
        ensure_ascii=False, indent=2)
    attempt = AnswerExtraction(case_id=case.id, input_version=case.edit_version,
        provider=provider, model=model, prompt_snapshot=prompt,
        system_snapshot=library.EXTRACTION_SYSTEM,
        prompt_sha256=library.digest(library.EXTRACTION_SYSTEM + "\0" + prompt))
    session.add(attempt)
    session.flush()
    return attempt


async def _run(attempt_id: str):
    try:
        with session_scope() as session:
            attempt = session.get(AnswerExtraction, attempt_id)
            if attempt is None or attempt.status != "queued":
                return
            values = settings_service.get_all(session)
            provider = build_provider(attempt.provider, values.get("provider_paths") or {})
            if provider is None or not provider.supports_tool_policy(NO_TOOLS):
                raise ValueError("이 Provider로 정답 보고서를 정리할 수 없습니다.")
            work_dir = library.case_dir(attempt.case_id) / "extractions" / attempt.id
            work_dir.mkdir(parents=True, exist_ok=True)
            request = ExecutionRequest(job_id=attempt.id, work_dir=work_dir,
                system_prompt=attempt.system_snapshot, user_message=attempt.prompt_snapshot,
                model=attempt.model, timeout_seconds=int(values["default_timeout_seconds"]),
                reasoning_effort=str((values.get("reasoning_effort") or {}).get(attempt.provider, "")),
                tool_policy=NO_TOOLS)
            char_gate(len(request.system_prompt) + len(request.user_message), settings_service.inline_char_budget(values))
            size = provider.payload_bytes(request.system_prompt, request.user_message)
            if provider.max_input_bytes is not None and size > provider.max_input_bytes:
                raise ValueError("정답 보고서가 Provider 입력 한도를 넘습니다. 보고서를 나눠 등록하십시오.")
            budget = token_budget(provider_id=attempt.provider, model=attempt.model,
                overrides=values.get("model_context_tokens"),
                reserve_tokens=int(values["model_output_reserve_tokens"]),
                fallback_context_tokens=int(values["unknown_model_context_tokens"]))
            if estimate_tokens(request.system_prompt, request.user_message) > budget.input_tokens:
                raise ValueError("정답 보고서가 모델 입력 예산을 넘습니다. 보고서를 나눠 등록하십시오.")
            attempt.execution_manifest = {"input_bytes": size, "estimated_input_tokens":
                estimate_tokens(request.system_prompt, request.user_message), "budget": budget.to_dict()}
            provider_id = attempt.provider
        from .execution.runner import RUNNER
        async with RUNNER.provider_slot(provider_id, int(values["max_concurrency_per_provider"])):
            with session_scope() as session:
                attempt = session.get(AnswerExtraction, attempt_id)
                if attempt is None or attempt.status != "queued":
                    return
                attempt.status = "running"
            _providers[attempt_id] = provider
            (work_dir / "input.json").write_text(json.dumps(asdict(request), ensure_ascii=False,
                default=str, indent=2), encoding="utf-8")
            async def emit(event, payload):
                with (work_dir / "events.jsonl").open("a", encoding="utf-8") as stream:
                    stream.write(json.dumps({"type": event, "payload": payload}, ensure_ascii=False) + "\n")
            outcome = await provider.execute(request, emit)
            verdict = evaluate(outcome, [], fail_on_tool_use=True)
            (work_dir / "output.json").write_text(json.dumps(asdict(outcome), ensure_ascii=False,
                default=str, indent=2), encoding="utf-8")
            with session_scope() as session:
                attempt = session.get(AnswerExtraction, attempt_id)
                if attempt is None:
                    return
                attempt.result_text = outcome.result_text
                attempt.execution_manifest = {**(attempt.execution_manifest or {}),
                    "usage": outcome.usage, "cli_path": outcome.cli_path,
                    "cli_version": outcome.cli_version, "cli_args": outcome.cli_args,
                    "verdict": asdict(verdict), "tool_calls": outcome.tool_calls}
                attempt.completed_at = utcnow()
                if attempt.status == "cancelled":
                    return
                if verdict.status != JobStatus.SUCCEEDED:
                    attempt.status = "failed"
                    attempt.error = " / ".join(verdict.errors) or verdict.error_code or "AI 실행 실패"
                    return
                case = session.get(AnswerCase, attempt.case_id)
                if case.edit_version != attempt.input_version:
                    attempt.status = "failed"
                    attempt.error = "AI 정리 중 보고서가 수정되어 결과를 적용하지 않았습니다. 원본 응답은 보존됩니다."
                    return
                try:
                    parsed = library.parse_extraction(case, outcome.result_text)
                except (ValueError, OSError) as exc:
                    attempt.status = "failed"
                    attempt.error = str(exc)
                    return
                attempt.parsed_result = parsed
                attempt.status = "succeeded"
                case.draft = parsed
                case.status = "draft"
                case.edit_version += 1
                case.updated_at = utcnow()
    except asyncio.CancelledError:
        _mark_failure(attempt_id, "정리를 취소했습니다.", "cancelled")
        raise
    except Exception as exc:
        _mark_failure(attempt_id, f"{type(exc).__name__}: {exc}")
    finally:
        _providers.pop(attempt_id, None)


def _mark_failure(attempt_id, error, status="failed"):
    with session_scope() as session:
        attempt = session.get(AnswerExtraction, attempt_id)
        if attempt and attempt.status in ("queued", "running"):
            attempt.status, attempt.error, attempt.completed_at = status, error, utcnow()


def start(attempt_id: str):
    task = asyncio.create_task(_run(attempt_id))
    _tasks[attempt_id] = task
    task.add_done_callback(lambda _: _tasks.pop(attempt_id, None))


async def cancel(attempt_id: str):
    _mark_failure(attempt_id, "정리를 취소했습니다.", "cancelled")
    provider = _providers.get(attempt_id)
    if provider:
        await provider.cancel(attempt_id)
    task = _tasks.get(attempt_id)
    if task:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


def recover():
    with session_scope() as session:
        for attempt in session.query(AnswerExtraction).filter(AnswerExtraction.status.in_(["queued", "running"])):
            attempt.status = "failed"
            attempt.error = "프로그램 종료로 정리가 중단되었습니다. 다시 정리할 수 있습니다."
            attempt.completed_at = utcnow()


async def shutdown():
    for attempt_id in list(_tasks):
        await cancel(attempt_id)
