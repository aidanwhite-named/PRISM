"""Export comparison data from completed jobs, without settings or credentials."""
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'backend'))
from app.config import PATHS
from app.search_engine.models import write_json

JOBS = {
    'deep': '03ff303a-5829-49c9-890e-326f38f91d58',
    'exhaustive': '29633f0e-0db7-4c40-ad00-d4c98463a6c5',
    'improved_exhaustive_v1': 'b5d17bf4-074b-44b7-8d57-e2063fd0d784',
    'improved_exhaustive_v2': '2c29f9f3-bb8e-4f79-a57d-46a42f9cb6f9',
    'improved_final_deep': '498fc266-b6ad-4477-b798-fa874521755e',
}
LEADS = {
    'same_claim_patent': ['JP7475618', 'JP2025089942', 'WO2025120925'],
    'herda_2003': ['10.1177/0278364903022006005', 'Automatic Determination of Shoulder Joint Limits'],
    'distance_cones_2010': ['using Distance Field Cones'],
    'distance_fields_2012': ['10.1007/s11044-011-9296-1', 'A joint-constraint model for human joints'],
    'swing_twist_lead': ['10.1007/978-0-306-47002-8_16', 'Parametrization and Range of Motion'],
}


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    rows = []
    for depth, job_id in JOBS.items():
        directory = PATHS.run_dir(job_id)
        if not (directory / 'search_manifest.json').exists():
            continue
        engine = json.loads((directory / 'engine.json').read_text(encoding='utf-8'))
        manifest = json.loads((directory / 'search_manifest.json').read_text(encoding='utf-8'))
        stages = engine.get('usage', {}).get('stages', [])
        trace = [json.loads(x) for x in (directory / 'search_trace.jsonl').read_text(encoding='utf-8').splitlines()]
        row = {'depth': depth, 'job_id': job_id, 'work_dir': str(directory),
               'elapsed_seconds': engine['elapsed_seconds'], 'limits': engine['limits'],
               'provider': manifest.get('provider'), 'model': manifest.get('model'),
               'manifest_status': manifest.get('status'),
               'stop_reason': engine['stop_reason'], 'classification': engine['classification'],
               'warnings': engine['warnings'], 'seed_queries': engine['seed_queries'],
               'queries': engine['queries'], 'route': engine['route'],
               'first_candidate_seconds': engine['first_candidate_seconds'],
               'http_fetches': engine['http_fetches'],
               'plan_cache_hit': any(x.get('event') == 'plan_cache_hit' for x in trace),
               'query_cache_hits': sum(bool(x.get('cache_hit')) for x in engine['queries']),
               'candidate_count': len(engine['candidates']),
               'candidate_sources': dict(Counter(c['source'] for c in engine['candidates'])),
               'stage_details': [{k: s.get(k) for k in ('phase', 'seconds', 'model', 'tool_calls', 'tool_events', 'output_partial', 'timed_out', 'outcome', 'error')} for s in stages],
               'candidates': [{k: c.get(k) for k in ('id', 'document_number', 'title', 'url', 'source', 'data_status', 'document_classification', 'evidence', 'acquisitions')} for c in engine['candidates']]}
        row['lead_ranks'] = {}
        for label, terms in LEADS.items():
            row['lead_ranks'][label] = [i for i,c in enumerate(row['candidates'], 1)
                if any(t.lower() in (str(c['document_number']) + ' ' + str(c['title']) + ' ' + str(c['url'])).lower() for t in terms)]
        rows.append(row)
    write_json(Path(__file__).with_name('run-comparison.json'), rows)
    print(json.dumps([{k:v for k,v in r.items() if k not in ('candidates', 'stage_details', 'queries', 'route')} for r in rows], ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
