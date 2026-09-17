"""Exercise existing citation adapters with explicit known seeds; not blind retrieval."""
import json
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'backend'))
from app import settings_service
from app.config import PATHS
from app.db import session_scope
from app.search_engine.models import write_json
from app.search_engine.sources import Sources

def main():
    sys.stdout.reconfigure(encoding='utf-8')
    with session_scope() as session:
        values = settings_service.get_all(session)
    sources = Sources(values, PATHS.data_dir / 'diagnostics/joint-citation-paths')
    rows = []
    for number, direction in [('JP7475618B1', 'backward'), ('WO2012111622A1', 'backward'),
                              ('WO2012111622A1', 'forward'), ('US9208613B2', 'forward')]:
        row = {'seed': number, 'direction': direction, 'explicitly_seeded': True}
        try:
            result = sources.citation_neighbors(SimpleNamespace(document_number=number), direction)
            row.update({k: result.get(k) for k in ('references', 'references_omitted', 'warning',
                       'raw_artifact_id', 'seed_artifact_id', 'metadata_failures', 'coverage')})
            row['records'] = [{'document_number': r.get('document_number'), 'title': r.get('title'),
                               'url': r.get('url')} for r in result.get('records', [])]
        except Exception as exc:
            row['error'] = type(exc).__name__ + ': ' + str(exc)[:400]
        rows.append(row)
        write_json(Path(__file__).with_name('citation-path-probes.json'), rows)
        print(json.dumps(row, ensure_ascii=False), flush=True)

if __name__ == '__main__':
    main()
