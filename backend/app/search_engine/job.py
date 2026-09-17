"""Bind the new engine to existing jobs, SSE, artifacts and cancellation."""
from __future__ import annotations

from pathlib import Path
import hashlib
from datetime import datetime, timezone

from ..db import session_scope
from ..models import ExecutionJob, ResultArtifact
from ..enums import JobStatus, ErrorCode
from ..execution.bus import BUS
from ..patent_search import retention
from .engine import Engine
from .inference import Inference
from .models import Limits, write_json
from .report import manifest, render


async def run_job(runner, provider, *, job_id, work_dir, claim, cutoff, depth, values,
                  model, reasoning_effort, strategy, attachments, focus):
    limits = Limits.for_depth(depth, values)
    with session_scope() as session:
        current_job = session.get(ExecutionJob, job_id)
        prompt_metadata = {'prompt_id': current_job.prompt_id if current_job else '',
                           'prompt_name': current_job.prompt_name if current_job else '',
                           'prompt_sha256': hashlib.sha256(strategy.encode('utf-8')).hexdigest()}
    cancelled = lambda: job_id in runner._cancel_requested
    inference = Inference(provider, job_id=job_id, directory=work_dir / 'inference', model=model,
        reasoning_effort=reasoning_effort, emit=lambda kind, payload: runner._emit(job_id, kind, payload),
        cancelled=cancelled, max_calls=limits.llm_calls, max_input_tokens=limits.input_tokens)
    specification = ''
    for attachment in attachments:
        if attachment.normalized_text_path:
            specification += Path(attachment.normalized_text_path).read_text(encoding='utf-8') + '\n'
    # Specifications clarify vocabulary, but are not a second implicit claim.
    spec_truncated = len(specification) > 6000
    specification = specification[:6000]
    from .planner import PLAN_SYSTEM
    import json
    (work_dir / 'final_prompt.txt').write_text(PLAN_SYSTEM + '\n\n' + json.dumps({
        'claim': claim, 'specification': specification, 'focus': focus,
        'search_strategy': strategy}, ensure_ascii=False), encoding='utf-8')

    async def publish(snapshot):
        data = manifest(snapshot, claim=claim, provider=provider.id, model=model, **prompt_metadata)
        text = render(snapshot)
        with session_scope() as session:
            job = session.get(ExecutionJob, job_id)
            if job:
                job.search_manifest = data
                job.result_text = text
                job.usage = snapshot['usage']
        await runner._emit(job_id, 'search_preview_ready', {'candidate_count': len(snapshot['candidates']),
                                                         'phase': snapshot['phase']})

    engine = Engine(claim=claim, directory=work_dir, inference=inference, values=values, depth=depth,
                    cutoff=cutoff, strategy=strategy, specification=specification, focus=focus,
                    emit=publish, cancelled=cancelled)
    resume_path = work_dir / 'resume-search.json'
    if resume_path.exists():
        checkpoint = json.loads(resume_path.read_text(encoding='utf-8'))
        engine.restore(checkpoint)
        inference.calls = list(checkpoint['snapshot']['usage'].get('stages', []))
    error = None
    try:
        await engine.run()
    except Exception as exc:
        error = type(exc).__name__ + ': ' + str(exc)[:500]
        engine.warnings.append(error)
        engine.stop_reason = 'engine_error'
    finally:
        if spec_truncated:
            engine.warnings.append('specification_context_limited_to_6000_characters; claim preserved')
        if cancelled():
            engine.stop_reason = 'cancelled'
        snapshot = engine.snapshot()
        if not snapshot['candidates'] and snapshot['warnings'] and not cancelled():
            error = error or '검색 후보를 확보하지 못했습니다. 채널·질의 계획 오류를 확인하십시오.'
        engine.ledger.save()
        data = manifest(snapshot, claim=claim, provider=provider.id, model=model, **prompt_metadata)
        text = render(snapshot)
        write_json(work_dir / 'engine.json', snapshot)
        write_json(work_dir / 'checkpoint.json', engine.checkpoint())
        write_json(work_dir / 'search_manifest.json', data)
        (work_dir / 'result.md').write_text(text, encoding='utf-8')
        status = JobStatus.CANCELLED if cancelled() else JobStatus.FAILED if error else JobStatus.SUCCEEDED
        code = ErrorCode.CANCELLED if cancelled() else ErrorCode.PROCESS_ERROR if error else None
        with session_scope() as session:
            job = session.get(ExecutionJob, job_id)
            if job:
                job.status, job.error_code = status, code
                job.errors = [error] if error else []
                job.search_manifest, job.result_text, job.usage = data, text, snapshot['usage']
                job.final_prompt_path = str(work_dir / 'final_prompt.txt')
                job.search_manifest_error = error
                job.completed_at = datetime.now(timezone.utc)
                job.duration_ms = int(snapshot['elapsed_seconds'] * 1000)
                outcome = inference.last_outcome
                if outcome:
                    job.cli_path, job.cli_version, job.cli_args = outcome.cli_path, outcome.cli_version, outcome.cli_args
                    job.exit_code, job.terminal_reason = outcome.exit_code, outcome.terminal_reason
                from ..execution.runner import _evidence_artifact_ids
                for aid in _evidence_artifact_ids(data):
                    retention.reference(session, job_id, aid)
                for kind, name in [('result', 'result.md'), ('search_manifest', 'search_manifest.json'),
                                   ('search_engine', 'engine.json'), ('search_candidates', 'candidates.json'),
                                   ('search_trace', 'search_trace.jsonl')]:
                    path = work_dir / name
                    if path.exists():
                        session.add(ResultArtifact(job_id=job_id, kind=kind, path=str(path), size_bytes=path.stat().st_size))
        runner._providers.pop(job_id, None)
        await runner._emit(job_id, 'status', {'status': status, 'error_code': code})
        await runner._emit(job_id, 'done', {'status': status})
        await BUS.close(job_id)
