"""Opt-in live smoke of time-bounded search, without creating or altering jobs."""
import argparse
import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import PATHS
from app.db import session_scope
from app.enums import JobKind
from app import settings_service
from app.providers.registry import build_provider
from app.prompt_store import PROMPT_STORE
from app.search_engine.autonomous import AutonomousSearch, SearchSession
from app.search_engine.report import render


async def main(seconds):
    with session_scope() as session:
        values = settings_service.get_all(session)
    provider_id, models, efforts = settings_service.execution_defaults(values, JobKind.SIMILARITY_SEARCH)
    provider = build_provider(provider_id, values.get('provider_paths') or {})
    directory = PATHS.data_dir / 'validation' / ('autonomous-' + datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S'))
    directory.mkdir(parents=True)
    runtime = SearchSession(provider, 'autonomous-validation', model=models.get(provider_id),
                            reasoning_effort=efforts.get(provider_id, ''))
    engine = AutonomousSearch(claim='A method for reconstructing a three-dimensional scene using anisotropic Gaussian primitives, optimizing their positions, opacity and covariance from multiple input images, and rendering novel views by differentiable splatting.',
        directory=directory, inference=runtime, values={**values, 'search_timeout_seconds': seconds},
        strategy=PROMPT_STORE.get_reserved('search_prompt.md').body)
    error = None
    try:
        await engine.run()
    except Exception as exc:
        error = str(exc)
    snapshot = engine.snapshot()
    (directory / 'result.md').write_text(render(snapshot), encoding='utf-8')
    print(json.dumps({'directory': str(directory), 'error': error, 'seconds': snapshot['elapsed_seconds'],
                      'stop': snapshot['stop_reason'], 'candidates': len(snapshot['candidates']),
                      'source_tools': sorted({r.get('tool', '') for r in snapshot['source_calls']}),
                      'native_tools': sorted({r.get('name', '') for r in snapshot['native_calls']})}, ensure_ascii=True))
    if error:
        raise SystemExit(1)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--seconds', type=int, default=90)
    asyncio.run(main(parser.parse_args().seconds))
