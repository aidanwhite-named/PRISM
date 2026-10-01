"""Obtain public article copies and expose captured OPS body evidence."""
import json
from urllib.parse import urlsplit

from ..config import PATHS
from ..patent_search.artifacts import ArtifactStore
from ..patent_search.literature_client import normalize_doi
from .storage import identifier, write_json
from .source_passages import options


def article_full_text(tools, arguments):
    doi = normalize_doi(arguments['doi'])
    store = ArtifactStore(PATHS.evidence_dir)
    cache = tools.work_dir / ('article-locations-' + identifier(doi) + '.json')
    if cache.exists():
        saved = json.loads(cache.read_text(encoding='utf-8'))
        body, aid = saved['body'], saved['raw_artifact_id']
        store.read(aid)
    else:
        backend = tools._backend('literature')
        call = backend._require_openalex().fetch(doi)
        backend._detail_fetches += 1
        aid = store.put(call.body)
        body = json.loads(call.body)
        if call.no_results or not body.get('doi') or normalize_doi(body['doi']) != doi:
            raise ValueError('openalex_exact_doi_not_found; use another identified public source')
        write_json(cache, {'body': body, 'raw_artifact_id': aid})
    locations = [body.get('best_oa_location'), body.get('primary_location'), *body.get('locations', [])]
    copies = []
    for field in ('pdf_url', 'landing_page_url'):
        for location in locations:
            if not isinstance(location, dict) or not location.get('is_oa'):
                continue
            url = location.get(field)
            if not isinstance(url, str):
                continue
            parts = urlsplit(url)
            if parts.scheme != 'https' or not parts.hostname or parts.username or parts.password:
                continue
            if url not in [c['url'] for c in copies]:
                copies.append({'url': url, 'version': location.get('version'),
                               'kind': field, 'source': (location.get('source') or {}).get('display_name')})
    metadata = {'document_number': doi, 'url': 'https://doi.org/' + doi,
                'title': body.get('display_name') or '', 'fields': {'title': body.get('display_name') or ''},
                'evidence_refs': {'title': {'artifact_id': aid, 'field_path': 'display_name',
                                         'profile_id': 'openalex_work_json'}}}
    attempts = []
    result = {'records': [metadata], 'raw_artifact_id': aid, 'identifier_matched': True,
              'source_kind': 'openalex_locations', 'document_links': [c['url'] for c in copies],
              'public_copies': copies, 'acquisition_attempts': attempts,
              'verification_scope': 'bibliographic', 'failed_sources': [],
              'scope_note': 'OpenAlex locations identify public copies, not body evidence. Only actual captured text supports review. Copies may be author manuscripts or preprints; retain version information.'}
    from .. import search_manifest
    blocked = {(r.get('arguments') or {}).get('url') for r in search_manifest.read_tool_journal(tools.work_dir)
        if r.get('tool') == 'source_fetch' and r.get('ok') is False
        and r.get('detail') in ('http_403', 'http_404', 'access_challenge')}
    result['previously_blocked_urls'] = [c['url'] for c in copies if c['url'] in blocked]
    queue = [c['url'] for c in copies]
    seen = set(blocked)
    while queue and len(attempts) < 4 and tools.budget().get('seconds_remaining', 3) > 2:
        url = queue.pop(0)
        if url in seen:
            continue
        seen.add(url)
        try:
            fetched = tools.call('source_fetch', {'url': url, 'section': 'page',
                'max_chars': arguments.get('max_chars', 16000),
                **({'find': arguments['find']} if arguments.get('find') else {})})
            if fetched.get('verification_scope') == 'full_text' and fetched.get('find_found') is False:
                fetched = tools.call('source_fetch', {'url': url, 'section': 'page',
                                      'max_chars': arguments.get('max_chars', 16000)})
            scope = fetched.get('verification_scope')
            attempts.append({'url': url, 'scope': scope, 'captured': bool(fetched.get('records'))})
            if scope == 'full_text' and fetched.get('records'):
                from .document_identity import identities
                expected = identities(metadata)
                conflicting = any({k for k in identities(r) if k.startswith('doi:')
                    and not k.startswith('doi:10.48550/arxiv.')} - expected for r in fetched['records'])
                if conflicting:
                    attempts[-1]['error'] = 'captured_doi_mismatch'
                    rejected_urls = {url, *(r.get('url') for r in fetched['records'])}
                    result['document_links'] = [link for link in result['document_links'] if link not in rejected_urls]
                    continue
                result.update(verification_scope='full_text', captured_source=fetched)
                result['records'].extend(fetched['records'])
                return result
            # A repository landing page can lead to its source-owned PDF.
            queue = [link for link in fetched.get('pdf_urls', []) if link not in seen] + queue
        except Exception as exc:
            attempts.append({'url': url, 'error': str(exc)})
    result['scope_note'] += ' Full text was not obtained; keep the candidate unverified. Try another public copy if valuable.'
    return result


def ops_capture(result, constituent):
    if constituent not in ('claims', 'description') or not result.get('identifier_matched'):
        return result
    store = ArtifactStore(PATHS.evidence_dir)
    from .document_identity import identities
    expected = identities({'document_number': result.get('requested_identifier', '')})
    for record in result.get('records', []):
        if expected and not expected & identities(record):
            continue
        blocks = [value for key, value in record.get('fields', {}).items()
                  if key.split(':')[0] == constituent and isinstance(value, str) and value.strip()]
        if not blocks:
            continue
        text = '\n\n'.join(blocks)
        capture = {'text': text, 'scope': constituent, 'document_number': record['document_number'],
                   'url': record['url'], 'raw_artifact_id': result['raw_artifact_id'],
                   'original_evidence_refs': record.get('evidence_refs', {})}
        aid = store.put(json.dumps(capture, ensure_ascii=False).encode('utf-8'))
        record['fields'][constituent] = text
        record.setdefault('evidence_refs', {})[constituent] = {
            'artifact_id': aid, 'field_path': 'text', 'profile_id': 'generic_json'}
        result.update(capture_artifact_id=aid, capture_version=2, verification_scope=constituent,
                      source_kind='epo_ops_capture', offset=0, total_chars=len(text), page_spans=[],
                      passage_options=options(text, constituent, 0))
        break
    return result
