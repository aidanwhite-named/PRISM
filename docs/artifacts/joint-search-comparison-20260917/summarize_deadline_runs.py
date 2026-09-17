"""Summarize saved real runs, without making model or search requests."""
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'backend'))
from app.db import session_scope
from app.models import ExecutionJob, ExecutionEvent
from app.search_engine.evaluation import metrics
from app.search_engine.models import write_json
from app.search_engine.categories import normalize
from sqlalchemy import select

RUNS = ['922b701c-0fd5-4759-bcac-c90e316d50f2', '3c3a6963-64e8-4fac-bff8-3ca66718c473']


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    case = next(c for c in json.loads((ROOT / 'backend/evaluation/search_cases.json').read_text(encoding='utf-8'))['cases']
                if c['id'] == 'coupled_joint_limits')
    result = []
    with session_scope() as session:
        for jid in RUNS:
            job = session.get(ExecutionJob, jid)
            manifest = job.search_manifest or {}
            candidates = (manifest.get('reported') or {}).get('candidates', [])
            events = session.scalars(select(ExecutionEvent).where(ExecutionEvent.job_id == jid).order_by(ExecutionEvent.seq)).all()
            stages = [{'stage': e.payload.get('stage'), 'seconds': (e.ts - job.started_at).total_seconds()}
                      for e in events if e.type == 'stage']
            result.append({'job_id': jid, 'status': job.status, 'error_code': job.error_code,
                'errors': job.errors, 'duration_seconds': (job.duration_ms or 0) / 1000,
                'metrics': metrics(manifest, case), 'time_budget': manifest.get('time_budget'),
                'groups': dict(Counter(normalize(c.get('group')) or 'null' for c in candidates)),
                'stages': stages, 'tool_counts': dict(Counter(c.get('name') for c in (manifest.get('observed') or {}).get('tool_calls', []))),
                'target_patents_found': [c.get('doc_number') for c in candidates if c.get('doc_number') in ('JP7475618B1', 'WO2012111622A1')]})
    write_json(Path(__file__).with_name('deadline-runs-summary.json'), result)
    print(json.dumps([{k: v for k, v in row.items() if k != 'metrics'} for row in result], ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
