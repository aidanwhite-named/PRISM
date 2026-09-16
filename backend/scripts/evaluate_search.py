"""Evaluate saved manifests, or explicitly run a live progressive search.

Live mode consumes the configured provider and public API quotas. Expected IDs are
used only after the search completes, never as retrieval inputs.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.search_engine.evaluation import metrics
from app.search_engine.models import write_json


async def live(case, depth):
    from app.db import session_scope
    from app.models import ExecutionJob
    from app import settings_service
    from app.config import PATHS
    from app.api.jobs import _resolve_search_prompt
    from app.execution.runner import JobRunner
    with session_scope() as session:
        values = settings_service.get_all(session)
        provider, models, _ = settings_service.execution_defaults(values, 'similarity_search')
        prompt = _resolve_search_prompt(None, values)
        job = ExecutionJob(job_kind='similarity_search', prompt_id=prompt.id,
            prompt_name=prompt.name, prompt_snapshot=prompt.body,
            claim_text=case['claim'], search_cutoff_date=case.get('cutoff'), search_depth=depth,
            provider=provider, model=models.get(provider))
        session.add(job)
        session.flush()
        job_id = job.id
        job.work_dir = str(PATHS.run_dir(job_id))
    print('live job:', job_id, flush=True)
    await JobRunner()._run(job_id)
    with session_scope() as session:
        job = session.get(ExecutionJob, job_id)
        return job.search_manifest or {}, {'job_id': job_id, 'status': job.status, 'errors': job.errors}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cases', type=Path, default=Path(__file__).resolve().parents[1] / 'evaluation/search_cases.json')
    parser.add_argument('--case', default='gaussian_3dgs')
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--manifest', type=Path)
    source.add_argument('--live', action='store_true')
    parser.add_argument('--depth', choices=['fast', 'deep', 'exhaustive'], default='deep')
    parser.add_argument('--k', type=int, default=20)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    case = next(case for case in json.loads(args.cases.read_text(encoding='utf-8'))['cases'] if case['id'] == args.case)
    metadata = {}
    if args.live:
        manifest, metadata = asyncio.run(live(case, args.depth))
    else:
        manifest = json.loads(args.manifest.read_text(encoding='utf-8'))
    result = {**metrics(manifest, case, args.k), **metadata}
    write_json(args.output, result)
    print(json.dumps({k: v for k, v in result.items() if k != 'usage'}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    main()
