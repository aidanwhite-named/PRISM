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
    from .search_engine.fetcher import SafeFetcher, ArticleHTML, Fetched
    from .search_engine.source_text import source_from_fetch
    from .search_engine.storage import identifier, write_json
    store = ArtifactStore(PATHS.evidence_dir)
    # Re-reading a later text window reuses the captured response, not the network.
    cache = tools.work_dir / ('source-' + identifier(arguments['url'] + arguments.get('section', 'claims')) + '.json')
    capture_origin = 'local_cache'
    if cache.exists() and json.loads(cache.read_text(encoding='utf-8')).get('capture_version') == 2:
        capture = json.loads(cache.read_text(encoding='utf-8'))
        store.read(capture['raw_artifact_id'])
    else:
        from .search_engine.continuation import captured_source
        capture = captured_source(tools.work_dir, arguments, store)
        capture_origin = 'continuation' if capture is not None else 'network'
        if capture is None:
            fetched = None
            for section in ('claims', 'description', 'page'):
                prior = tools.work_dir / ('source-' + identifier(arguments['url'] + section) + '.json')
                if prior.exists():
                    saved = json.loads(prior.read_text(encoding='utf-8'))
                    raw = store.read(saved['raw_artifact_id'])
                    fetched = Fetched(saved['url'], raw,
                        saved.get('content_type') or ('application/pdf' if raw.startswith(b'%PDF-') else 'text/html'),
                        saved['raw_artifact_id'])
                    break
            fetched = fetched or SafeFetcher(store).get(arguments['url'])
            source, pages = source_from_fetch(fetched)
            text = '\n\n'.join(page.text for page in pages)
            page_spans, cursor = [], 0
            for page in pages:
                page_spans.append({'page': page.page_number, 'start': cursor, 'end': cursor + len(page.text)})
                cursor += len(page.text) + 2
            if (arguments.get('section') == 'page' and fetched.content_type != 'application/pdf'
                    and source['scope'] != 'full_text'):
                parser = ArticleHTML()
                parser.feed(fetched.body.decode('utf-8', errors='replace'))
                text = parser.text()
                source['scope'] = 'page_text'
            elif (arguments.get('section') == 'description' and fetched.content_type != 'application/pdf'
                  and source['scope'] != 'full_text'):
                from .search_engine.comparison_sources import DescriptionHTML
                parser = DescriptionHTML()
                parser.feed(fetched.body.decode('utf-8', errors='replace'))
                description = ''.join(parser.parts).strip()
                if source.get('document_number') and len(description) >= 100:
                    text, source['scope'] = description, 'description'
                else:
                    raise ValueError('labelled_description_unavailable; use claims or linked PDF')
            capture = {'capture_version': 2, 'url': fetched.url, 'raw_artifact_id': fetched.artifact_id,
                       'content_type': fetched.content_type,
                       'document_number': source.get('document_number', ''),
                       'title': source.get('title', ''), 'scope': source['scope'], 'text': text,
                       'pdf_urls': source.get('pdf_urls', []),
                       'document_identifiers': source.get('document_identifiers', []),
                       'document_links': source.get('document_links', [])}
            if fetched.content_type == 'application/pdf':
                capture['page_spans'] = page_spans
        write_json(cache, capture)
    offset, limit = arguments.get('offset', 0), arguments.get('max_chars', 16000)
    if arguments.get('find'):
        hit = capture['text'].casefold().find(arguments['find'].casefold(), offset)
        if hit >= 0:
            offset = max(offset, hit - 800)
        else:
            return {'records': [], 'verification_scope': capture['scope'], 'find_found': False,
                    'total_chars': len(capture['text']), 'scope_note': '검색어가 추출 본문에서 발견되지 않았습니다. 기술적 대응 부재의 증거는 아닙니다.'}
    text = capture['text'][offset:offset + limit]
    # Immutable capture with raw-byte provenance. Generic profile proves captured
    # text, never official/original-language status (Google pages may translate).
    aid = store.put(json.dumps(capture, ensure_ascii=False).encode('utf-8'))
    field = capture['scope'] if capture['scope'] in ('claims', 'description', 'full_text') else 'page_text'
    record = {'document_number': capture['document_number'], 'title': capture['title'], 'url': capture['url'],
              'document_identifiers': capture.get('document_identifiers', []),
              'fields': {field: text, 'title': capture['title']},
              'evidence_refs': {field: {'artifact_id': aid, 'field_path': 'text', 'profile_id': 'generic_json'},
                                'title': {'artifact_id': aid, 'field_path': 'title', 'profile_id': 'generic_json'}}}
    from .search_engine.source_passages import options
    passages = options(text, field, offset) if field in ('claims', 'description', 'full_text') else []
    return {'capture_version': capture.get('capture_version', 1), 'records': [record], 'raw_artifact_id': capture['raw_artifact_id'], 'capture_artifact_id': aid,
            'passage_options': passages, 'capture_origin': capture_origin,
            'verification_scope': capture['scope'], 'source_kind': 'public_capture',
            'untrusted_external_data': True, 'offset': offset, 'total_chars': len(capture['text']),
            'next_offset': offset + len(text) if offset + len(text) < len(capture['text']) else None,
            'pdf_urls': capture['pdf_urls'],
            'document_links': capture.get('document_links', []),
            'page_spans': capture.get('page_spans', []),
            'scope_note': 'passage_options provides exact source spans for review with this capture_artifact_id, passage_id, feature, relation and Korean translation. Only labelled article bodies, patent sections and PDFs qualify as body evidence; generic landing pages do not.'}
