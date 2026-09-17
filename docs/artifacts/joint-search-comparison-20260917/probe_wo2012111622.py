"""Known-number retrieval diagnostic; never count this as claim-only discovery."""
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
from app.search_engine.fetcher import SafeFetcher
from app.search_engine.passages import source_from_fetch
from app.patent_search.artifacts import ArtifactStore

def main():
    sys.stdout.reconfigure(encoding='utf-8')
    with session_scope() as session:
        values = settings_service.get_all(session)
    sources = Sources(values, PATHS.data_dir / 'diagnostics/wo2012111622')
    number = 'WO2012111622A1'
    output = {'purpose': 'known-number lookup, not independent discovery', 'document_number': number, 'checks': []}
    families = ['WO2012111622', 'EP2677502', 'US9208613', 'US20130307850', 'JP2012208536', 'CN103403767']
    previous = json.loads(Path(__file__).with_name('run-comparison.json').read_text(encoding='utf-8'))
    output['previous_claim_searches'] = [{'run': r['depth'], 'job_id': r['job_id'],
        'matching_candidates': [c['document_number'] for c in r['candidates'] if any(
            n in (c.get('document_number', '') + ' ' + c.get('url', '')) for n in families)]} for r in previous]
    calls = [('epo_number_search', lambda: sources.call('epo', {'query': {'type': 'term', 'field': 'pn', 'value': number}, 'max_results': 10})),
             ('epo_bibliography', lambda: sources.fetch(SimpleNamespace(document_number=number), 'biblio')),
             ('epo_claims', lambda: sources.fetch(SimpleNamespace(document_number=number), 'claims'))]
    for name, call in calls:
        row = {'check': name}
        try:
            result = call()
            row.update(records=[{'number': r.get('document_number'), 'title': r.get('title'), 'url': r.get('url'),
                                 'fields': {k: len(str(v)) for k, v in r.get('fields', {}).items()}}
                                for r in result.get('records', [])],
                       raw_artifact_id=result.get('raw_artifact_id'))
        except Exception as exc:
            row['error'] = str(exc)[:400]
        output['checks'].append(row)
    try:
        fetched = SafeFetcher(ArtifactStore(PATHS.evidence_dir)).get('https://patents.google.com/patent/' + number + '/en')
        source, pages = source_from_fetch(fetched)
        output['checks'].append({'check': 'program_direct_page', 'source': source, 'characters': sum(p.char_count for p in pages)})
    except Exception as exc:
        output['checks'].append({'check': 'program_direct_page', 'error': str(exc)[:400]})
    write_json(Path(__file__).with_name('wo2012111622-probe.json'), output)
    print(json.dumps(output, ensure_ascii=False, indent=2))

if __name__ == '__main__':
    main()
