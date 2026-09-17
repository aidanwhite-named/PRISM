"""Diagnostic only: test shorter queries through the unchanged PRISM adapters.

Run after baseline jobs have started; results are never fed to those jobs.
No known document title, identifier, or author is supplied as a query.
"""
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'backend'))
from app import settings_service
from app.config import PATHS
from app.db import session_scope
from app.search_engine.models import write_json
from app.search_engine.sources import Sources

QUERIES = [
    ('openalex', 'joint limits dependencies'),
    ('openalex', 'joint constraints projection'),
    ('openalex', 'joint limits quaternion'),
    ('epo', 'skeletal posture parameters'),
    ('epo', 'bone rotation limit'),
]


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    with session_scope() as session:
        values = settings_service.get_all(session)
    sources = Sources(values, PATHS.data_dir / 'diagnostics/joint-comparison-20260917/probes')
    rows = []
    for source, query in QUERIES:
        start = time.monotonic()
        row = {'source': source, 'query': query}
        try:
            response = sources.search(source, query)
            row.update(cache_hit=response.get('cache_hit', False), coverage=response.get('coverage'),
                       failed_sources=response.get('failed_sources', []),
                       records=[{'rank': i, 'document_number': r.get('document_number'),
                                 'title': r.get('title'), 'url': r.get('url')}
                                for i, r in enumerate(response.get('records', []), 1)])
        except Exception as exc:
            row['error'] = type(exc).__name__ + ': ' + str(exc)[:200]
        row['seconds'] = round(time.monotonic() - start, 3)
        rows.append(row)
        write_json(Path(__file__).with_name('query-probes.json'), rows)
        print(json.dumps(row, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
