"""Source operations chosen by the LLM; no candidate selection or quotas."""
import json
import re
from .config import PATHS
from .patent_search.artifacts import ArtifactStore
def citation_search(tools, arguments):
    from .search_engine.citations import paper_citations, patent_references
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
    return paper_citations(tools, number, direction, arguments.get('begin', 1))

def source_fetch(tools, arguments):
    from .search_engine.fetcher import SafeFetcher, ArticleHTML
    from .search_engine.source_text import source_from_fetch
    from .search_engine.storage import identifier, write_json
    store = ArtifactStore(PATHS.evidence_dir)
    # Re-reading a later text window reuses the captured response, not the network.
    cache = tools.work_dir / ('source-' + identifier(arguments['url'] + arguments.get('section', 'claims')) + '.json')
    capture_origin = 'local_cache'
    if cache.exists():
        capture = json.loads(cache.read_text(encoding='utf-8'))
        store.read(capture['raw_artifact_id'])
    else:
        from .search_engine.continuation import captured_source
        capture = captured_source(tools.work_dir, arguments, store)
        capture_origin = 'continuation' if capture is not None else 'network'
        if capture is None:
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
            'capture_origin': capture_origin,
            'verification_scope': capture['scope'], 'source_kind': 'public_capture',
            'untrusted_external_data': True, 'offset': offset, 'total_chars': len(capture['text']),
            'next_offset': offset + len(text) if offset + len(text) < len(capture['text']) else None,
            'pdf_urls': capture['pdf_urls'],
            'scope_note': 'Captured page text, not certified original text. section=page includes description/family/citation tables; use offset to read later sections.'}
