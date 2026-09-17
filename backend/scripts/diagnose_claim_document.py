"""Explicitly seed one known document to test acquisition/classification, NOT discovery."""
import argparse
import asyncio
import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.config import PATHS
from app.db import session_scope
from app import settings_service
from app.providers.registry import build_provider
from app.search_engine.engine import Engine
from app.search_engine.inference import Inference
from app.search_engine.models import write_json


async def run(args):
    cases = json.loads((Path(__file__).resolve().parents[1] / 'evaluation/search_cases.json').read_text(encoding='utf-8'))
    case = next(c for c in cases['cases'] if c['id'] == args.case)
    with session_scope() as session:
        values = settings_service.get_all(session)
        provider_id, models, _ = settings_service.execution_defaults(values, 'similarity_search')
    job_id = 'diagnostic-' + str(uuid.uuid4())
    directory = PATHS.run_dir(job_id)
    inference = Inference(build_provider(provider_id), job_id=job_id, directory=directory / 'inference',
                          model=models.get(provider_id), max_calls=1)
    engine = Engine(claim=case['claim'], directory=directory, inference=inference, values=values, depth='exhaustive')
    candidate = engine.ledger.add({'document_number': args.document_number, 'url': args.url, 'title': args.document_number},
                                 source='diagnostic_seed', query_id='explicit_input', feature='context', rank=1)
    await engine.verify_candidates(1, selected=[candidate])
    output = {'diagnostic': 'explicitly seeded; not an independent search result', 'job_id': job_id,
              'provider': provider_id, 'model': models.get(provider_id), 'document_number': candidate.document_number,
              'classification': candidate.document_classification, 'evidence': candidate.evidence,
              'acquisitions': candidate.acquisitions, 'warnings': engine.warnings, 'usage': inference.usage()}
    write_json(args.output, output)
    print(json.dumps({k: v for k, v in output.items() if k not in ('evidence', 'usage')}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--case', required=True)
    parser.add_argument('--document-number', required=True)
    parser.add_argument('--url', required=True)
    parser.add_argument('--output', type=Path, required=True)
    asyncio.run(run(parser.parse_args()))
