"""Known-number integration diagnostic, not a blind discovery measurement."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'backend'))
from app.config import PATHS
from app.search_mcp_server import SearchTools
from app.search_engine.models import write_json


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    tools = SearchTools(work_dir=PATHS.run_dir('model-directed-tools-probe'), max_calls=40)
    result = {'diagnostic_only': True}
    for name, args in (
        ('citation_search', {'identifier': 'WO2012111622A1', 'direction': 'forward'}),
        ('source_fetch', {'url': 'https://patents.google.com/patent/JP7475618B1/en', 'section': 'claims'}),
    ):
        try:
            response = tools.call(name, args)
            result[name] = {'ok': True, 'identifiers': [r.get('document_number') for r in response.get('records', [])],
                            'scope': response.get('verification_scope'),
                            'raw_artifact_id': response.get('raw_artifact_id'),
                            'record_count': len(response.get('records', [])),
                            'total_chars': response.get('total_chars'),
                            'next_offset': response.get('next_offset')}
        except Exception as exc:
            result[name] = {'ok': False, 'error': str(exc)}
    write_json(Path(__file__).with_name('model-directed-tools-probe.json'), result)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
