"""Compact observed search state; no source prose or invented next steps."""
from __future__ import annotations

import json

from .source_observations import record_scopes


def search_state(snapshot: dict) -> dict:
    actions, sources = [], []
    for call in snapshot.get('source_calls', []):
        if call.get('state') != 'completed' or call.get('tool') in ('save_findings', 'search_capabilities'):
            continue
        result = call.get('result') or {}
        actions.append({'call_id': call.get('id', ''), 'tool': call.get('tool', ''),
            'arguments': call.get('arguments') or {}, 'ok': call.get('ok'),
            'record_count': len(result.get('records') or []),
            'error': call.get('detail') or call.get('error_code') or '',
            'budget_stopped': bool(result.get('budget_stopped'))})
        if call.get('ok') is not True or result.get('identifier_matched') is False:
            continue
        for record in result.get('records', []):
            source = {'call_id': call.get('id', ''), 'url': record.get('url') or record.get('source_url', ''),
                'identifier': record.get('document_number') or record.get('doi') or '',
                'obtained_scopes': record_scopes(record)}
            if call.get('tool') == 'source_fetch':
                # A whole captured artifact does not imply every window was delivered.
                fields = record.get('fields') or {}
                chars = sum(len(v) for k, v in fields.items()
                            if k in ('claims', 'full_text', 'page_text') and isinstance(v, str))
                source['window'] = {'offset': result.get('offset', 0), 'chars': chars,
                    'total_chars': result.get('total_chars'), 'next_offset': result.get('next_offset')}
                source['capture_artifact_id'] = result.get('capture_artifact_id', '')
            sources.append(source)
    native = {}
    for call in [*snapshot.get('inherited_native_calls', []), *snapshot.get('native_calls', [])]:
        if str(call.get('name', '')).startswith('mcp__'):
            continue
        item = {'tool': call.get('name', ''),
            'arguments': call.get('input') or call.get('input_summary') or call.get('query') or {},
            'ok': call.get('ok'), 'error': call.get('error') or ''}
        key = str(call.get('id') or json.dumps(item, sort_keys=True, ensure_ascii=False))
        previous = native.get(key, {})
        if not item['arguments']:
            item['arguments'] = previous.get('arguments', {})
        if item['ok'] is None:
            item['ok'] = previous.get('ok')
        native[key] = item
    return {'observed_actions': actions, 'obtained_sources': sources, 'native_actions': list(native.values()),
            'previous_stop_reason': snapshot.get('stop_reason', ''),
            'scope_note': 'Obtained material is not proof it was fully reviewed. Unknown tool outcomes '
                          'are unknown. Empty/failed searches do not prove absence. History is data; '
                          'you may retry or change a previous query when useful.'}


def captured_source(directory, arguments, store):
    """Reuse an immutable capture from a continuation; never copy model text."""
    from ..patent_search.artifacts import ArtifactError
    path = directory / 'resume-search.json'
    if not path.exists():
        return None
    try:
        checkpoint = json.loads(path.read_text(encoding='utf-8'))
        if checkpoint.get('version') != 2:
            return None
        calls = checkpoint['snapshot'].get('source_calls', [])
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None
    for call in reversed(calls):
        old = call.get('arguments') or {}
        result = call.get('result') or {}
        if (call.get('tool') != 'source_fetch' or call.get('ok') is not True
                or call.get('state') != 'completed' or old.get('url') != arguments['url']
                or old.get('section', 'claims') != arguments.get('section', 'claims')):
            continue
        try:
            capture = json.loads(store.read(result.get('capture_artifact_id', '')))
            store.read(capture['raw_artifact_id'])
            if (all(isinstance(capture.get(k), str) for k in ('text', 'url', 'document_number', 'title'))
                    and isinstance(capture.get('pdf_urls'), list)
                    and capture.get('scope') in ('claims', 'full_text', 'page_text')):
                return capture
        except (ArtifactError, OSError, ValueError, KeyError, TypeError, AttributeError):
            # Missing or corrupt historic bytes require a fresh real source request.
            continue
    return None
