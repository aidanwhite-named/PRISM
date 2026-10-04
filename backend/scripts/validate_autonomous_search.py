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


async def main(seconds, *, claim_file=None, expected_identifiers=()):
    with session_scope() as session:
        values = settings_service.get_all(session)
    provider_id, models, efforts = settings_service.execution_defaults(values, JobKind.SIMILARITY_SEARCH)
    provider = build_provider(provider_id, values.get('provider_paths') or {})
    directory = PATHS.data_dir / 'validation' / ('autonomous-' + datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S'))
    directory.mkdir(parents=True)
    runtime = SearchSession(provider, 'autonomous-validation', model=models.get(provider_id),
                            reasoning_effort=efforts.get(provider_id, ''))
    claim = (Path(claim_file).read_text(encoding='utf-8') if claim_file else
             'A method for reconstructing a three-dimensional scene using anisotropic Gaussian primitives, optimizing their positions, opacity and covariance from multiple input images, and rendering novel views by differentiable splatting.')
    engine = AutonomousSearch(claim=claim,
        directory=directory, inference=runtime, values={**values, 'search_timeout_seconds': seconds},
        strategy=PROMPT_STORE.get_reserved('search_prompt.md').body)
    error = None
    try:
        await engine.run()
    except Exception as exc:
        error = str(exc)
    snapshot = engine.snapshot()
    (directory / 'result.md').write_text(render(snapshot), encoding='utf-8')
    from app.search_manifest import identity_key
    def key(number):
        return identity_key(doi=number) if number.startswith('10.') else identity_key(number)
    expected = {key(number) for number in expected_identifiers}
    found = {key(c['document_number']) for c in snapshot['candidates'] if c.get('document_number')}
    metrics = {'directory': str(directory), 'error': error, 'seconds': snapshot['elapsed_seconds'],
                      'first_candidate_seconds': snapshot['first_candidate_seconds'],
                      'stop': snapshot['stop_reason'], 'candidates': len(snapshot['candidates']),
                      'source_tools': sorted({r.get('tool', '') for r in snapshot['source_calls']}),
                      'native_tools': sorted({r.get('name', '') for r in snapshot['native_calls']}),
                      'reported_usage': snapshot['usage'], 'persistence': snapshot.get('persistence', {}),
                      'expected_identifier_count': len(expected),
                      'found_expected_identifiers': sorted(expected & found),
                      'missing_expected_identifiers': sorted(expected - found),
                      'known_identifier_recall': len(expected & found) / len(expected) if expected else None,
                      'quality_note': 'Identifier recall measures saved candidates only. Technical relevance, '
                                      'claim coverage and citation support still require independent assessment.'}
    (directory / 'validation_metrics.json').write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(metrics, ensure_ascii=True))
    if error:
        raise SystemExit(1)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--seconds', type=int, default=90)
    parser.add_argument('--claim-file', type=Path, help='UTF-8 claim text for a repeatable representative case')
    parser.add_argument('--expected-identifier', action='append', default=[],
                        help='Known publication number or DOI; repeat for multiple expected sources')
    args = parser.parse_args()
    asyncio.run(main(args.seconds, claim_file=args.claim_file, expected_identifiers=args.expected_identifier))
