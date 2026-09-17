"""Model-selected actions; no query planning, ranking or automatic expansion."""
from __future__ import annotations

import json
import re
from types import SimpleNamespace

from . import search_manifest
from .config import PATHS
from .patent_search.artifacts import ArtifactStore

CHECKPOINT = 'search_agent_checkpoint.json'
MAX_CANDIDATES = 15


def load_checkpoint(directory):
    try:
        value = json.loads((directory / CHECKPOINT).read_text(encoding='utf-8'))
        return search_manifest.parse(json.dumps(value, ensure_ascii=False))[0]
    except (OSError, ValueError, search_manifest.SearchLogError):
        return None


def save_candidates(tools, arguments):
    """Merge model assessments by identity. Saving is never evidence retrieval."""
    from .search_engine.models import write_json
    incoming, _ = search_manifest.parse(json.dumps(arguments['report'], ensure_ascii=False))
    previous = load_checkpoint(tools.work_dir) or {'candidates': []}
    def key(item):
        return (search_manifest.identity_key(item.get('doc_number'), item.get('doi'))
                if item.get('doc_number') or item.get('doi') else search_manifest.normalize_url(item.get('url')))
    merged = {} if arguments.get('replace') else {key(item): item for item in previous['candidates']}
    for item in incoming['candidates']:
        if not key(item):
            raise ValueError('candidate_identity_required')
        merged[key(item)] = item
    if len(merged) > MAX_CANDIDATES:
        raise ValueError('candidate_limit_15: select and rank at most 15, then save with replace=true')
    removed = [item for item in previous['candidates'] if key(item) not in merged]
    dispositions = [*previous.get('candidate_dispositions', []), *incoming.get('candidate_dispositions', []),
                    *({**item, 'reason': '모델이 유력 후보 목록을 교체하며 제외. 이전 평가는 호출 이력에 보존.'} for item in removed)]
    previous.update({k: v for k, v in incoming.items() if k in arguments['report'] and k != 'candidates'})
    previous['candidate_dispositions'] = list({key(item): item for item in dispositions if key(item) not in merged}.values())
    previous['candidates'] = list(merged.values())
    for index, item in enumerate(previous['candidates'], 1):
        item.update(index=index, rank=index)
    write_json(tools.work_dir / CHECKPOINT, previous)
    result = {'saved_candidates': len(merged), 'checkpoint': CHECKPOINT, 'candidate_limit': MAX_CANDIDATES,
              'scope_note': 'Model-selected shortlist. Raw search hits remain in the journal, not automatically added.'}
    if arguments.get('x_review'):
        from .search_session import review_x
        result['early_stop'] = review_x(tools, previous, arguments['x_review'])
    return result


def citation_search(tools, arguments):
    from .search_engine.sources import Sources, patent_references
    number, direction = arguments['identifier'], arguments['direction']
    if re.fullmatch(r'[A-Z]{2}\d+[A-Z]\d?', number, re.I):
        number = number.upper()
        if direction == 'forward':
            result = tools.call('epo_search', {'query': {'type': 'term', 'field': 'ct',
                'value': re.sub(r'[A-Z]\d?$', '', number)}, 'max_results': 20,
                'begin': arguments.get('begin', 1)})
        else:
            response = tools.call('epo_fetch', {'publication_number': number, 'constituent': 'biblio'})
            aid = response.get('raw_artifact_id')
            refs = patent_references(ArtifactStore(PATHS.evidence_dir).read(aid), number) if aid else []
            # Return all cited identifiers. The model chooses which details to read.
            result = {'records': [{'document_number': ref, 'url': 'https://patents.google.com/patent/' + ref,
                                  'title': '', 'fields': {}} for ref in refs],
                      'references': refs, 'seed_artifact_id': aid}
        return {**result, 'seed': number, 'direction': direction,
                'scope_note': 'One publication, one hop. No family union. Empty response is not proof of no citations; read the source/family page if useful.'}
    if tools.statuses().get('literature', {}).get('status') != 'available':
        raise ValueError('literature_tool_unavailable')
    sources = Sources(tools.values, tools.work_dir / 'citation_calls', tools.cutoff)
    return sources.paper_citations(SimpleNamespace(document_number=number, fields={}), direction)


def source_fetch(tools, arguments):
    from .search_engine.fetcher import SafeFetcher, ArticleHTML
    from .search_engine.passages import source_from_fetch
    from .search_engine.models import identifier, write_json
    store = ArtifactStore(PATHS.evidence_dir)
    # Re-reading a later text window reuses the captured response, not the network.
    cache = tools.work_dir / ('source-' + identifier(arguments['url'] + arguments.get('section', 'claims')) + '.json')
    if cache.exists():
        capture = json.loads(cache.read_text(encoding='utf-8'))
        store.read(capture['raw_artifact_id'])
    else:
        fetched = SafeFetcher(store).get(arguments['url'])
        source, pages = source_from_fetch(fetched)
        text = '\n\n'.join(page.text for page in pages)
        if arguments.get('section') == 'page' and fetched.content_type != 'application/pdf':
            parser = ArticleHTML()
            parser.feed(fetched.body.decode('utf-8', errors='replace'))
            text = parser.text()
            source['scope'] = 'page_text'
        capture = {'url': fetched.url, 'raw_artifact_id': fetched.artifact_id,
                   'document_number': source.get('document_number', ''),
                   'title': source.get('title', ''), 'scope': source['scope'], 'text': text,
                   'pdf_urls': source.get('pdf_urls', [])}
        write_json(cache, capture)
    offset, limit = arguments.get('offset', 0), arguments.get('max_chars', 16000)
    text = capture['text'][offset:offset + limit]
    # Immutable capture with raw-byte provenance. Generic profile proves captured
    # text, never official/original-language status (Google pages may translate).
    aid = store.put(json.dumps(capture, ensure_ascii=False).encode('utf-8'))
    field = 'claims' if capture['scope'] == 'claims' else 'full_text' if capture['scope'] == 'full_text' else 'page_text'
    record = {'document_number': capture['document_number'], 'title': capture['title'], 'url': capture['url'],
              'fields': {field: text, 'title': capture['title']},
              'evidence_refs': {field: {'artifact_id': aid, 'field_path': 'text', 'profile_id': 'generic_json'},
                                'title': {'artifact_id': aid, 'field_path': 'title', 'profile_id': 'generic_json'}}}
    return {'records': [record], 'raw_artifact_id': capture['raw_artifact_id'], 'capture_artifact_id': aid,
            'verification_scope': capture['scope'], 'source_kind': 'public_capture',
            'untrusted_external_data': True, 'offset': offset, 'total_chars': len(capture['text']),
            'next_offset': offset + len(text) if offset + len(text) < len(capture['text']) else None,
            'pdf_urls': capture['pdf_urls'],
            'scope_note': 'Captured page text, not certified original text. section=page includes description/family/citation tables; use offset to read later sections.'}
