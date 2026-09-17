"""Typed public-literature adapters. Network workers never mutate candidate state."""
from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path

from ..search_mcp_server import SearchTools
from ..patent_search import epo_parser
from ..patent_search.artifacts import ArtifactStore, ArtifactError
from ..config import PATHS
from .models import identifier, write_json


class Sources:
    def __init__(self, values: dict, directory: Path, cutoff=''):
        self.values, self.directory, self.cutoff = values, directory, cutoff
        self.locks = {source: threading.Lock() for source in ('epo', 'kipris', 'arxiv', 'openalex', 'crossref_epmc')}

    def call(self, source: str, arguments: dict, *, fetch=False):
        with self.locks[source]:
            channel = source if source in ('epo', 'kipris') else 'literature'
            name = channel + ('_fetch' if fetch else '_search')
            cache_identity = [source, name, arguments, self.cutoff]
            if source == 'openalex':
                cache_identity.append('phrase-boolean-v2')
            key = identifier(json.dumps(cache_identity, sort_keys=True))
            cache = PATHS.data_dir / 'search_cache' / 'queries' / (key + '.json')
            if not fetch and cache.exists() and time.time() - cache.stat().st_mtime < 900:
                try:
                    cached = json.loads(cache.read_text(encoding='utf-8'))
                    # Cache references must still exist after history/evidence cleanup.
                    if cached.get('raw_artifact_id'):
                        ArtifactStore(PATHS.evidence_dir).read(cached['raw_artifact_id'])
                    return {**cached, 'cache_hit': True}
                except (OSError, ValueError, ArtifactError):
                    pass
            tools = SearchTools(values=self.values, work_dir=self.directory / key,
                                max_calls=200, cutoff=self.cutoff)
            result = tools.call(name, arguments)
            # Full fields remain in the backend artifact. The engine is not limited
            # to the 40k-character MCP presentation intended for agent transcripts.
            store = ArtifactStore(PATHS.evidence_dir)
            aid = result.get('raw_artifact_id')
            if source == 'epo' and aid:
                docs = {d.publication_number: d for d in epo_parser.read_documents(store.read(aid))}
                for record in result.get('records', []):
                    doc = docs.get(record['document_number'])
                    if doc:
                        fields = doc.text_fields()
                        if fetch:
                            record['fields'].update(fields)
                        elif doc.family_id:
                            record['fields']['family_id'] = doc.family_id
            if source == 'openalex' and aid:
                raw = json.loads(store.read(aid))
                works = raw.get('results', [raw])
                by_doi = {str(w.get('doi') or '').removeprefix('https://doi.org/').lower(): w for w in works}
                for record in result.get('records', []):
                    work = by_doi.get(record['document_number'].lower(), {})
                    if work.get('id'):
                        record['fields']['openalex_id'] = work['id']
                    for location in (work.get('best_oa_location'), work.get('primary_location')):
                        if isinstance(location, dict):
                            url = location.get('pdf_url') or location.get('landing_page_url')
                            if url:
                                record['fields']['oa_url'] = url
                                break
                # OpenAlex works without DOI are still discoverable literature.
                from ..patent_search.literature_parser import _openalex_abstract
                for work in works:
                    if work.get('doi') or not work.get('id') or not work.get('display_name'):
                        continue
                    location = work.get('best_oa_location') or work.get('primary_location') or {}
                    result['records'].append({'document_number': 'openalex:' + str(work['id']).rsplit('/', 1)[-1],
                        'title': work['display_name'], 'url': location.get('landing_page_url') or work['id'],
                        'publication_date': work.get('publication_date') or '',
                        'fields': {'abstract': _openalex_abstract(work),
                                   'oa_url': location.get('pdf_url') or location.get('landing_page_url') or ''},
                        'evidence_refs': {'abstract': {'artifact_id': aid,
                            'source': 'openalex', 'work_id': work['id'], 'field': 'abstract_inverted_index'}}})
            if not fetch and not result.get('failed_sources'):
                write_json(cache, result)
            return result

    def search(self, source: str, query: str, *, fulltext=False, classification='', begin=1, openalex_mode='search'):
        if source == 'kipris':
            return self.call(source, {'query': query, 'max_results': 20, 'begin': begin})
        if source == 'epo':
            # Historical fulltext flag means broad bibliographic txt search in OPS.
            # It does not search claims/description; those are fetched for passage retrieval.
            clean = ' '.join(query.replace('"', '').split()[:10])[:100]
            node = {'type': 'term', 'field': 'txt' if fulltext else 'ta', 'value': clean, 'match': 'all'}
            if classification:
                node = {'type': 'group', 'op': 'and', 'items': [node,
                        {'type': 'term', 'field': 'ipc', 'value': classification}]}
            return self.call(source, {'query': node, 'max_results': 20, 'begin': begin})
        if source == 'arxiv':
            # Preserve intended multiword concepts in the arXiv field query.
            if not any(mark in query for mark in ('all:', 'ti:', 'au:', 'id:')):
                query = ' AND '.join('all:' + word for word in query.replace('"', '').split()[:8])
        arguments = {'query': query, 'source': source, 'max_results': 10}
        if source == 'openalex' and openalex_mode != 'search':
            arguments['openalex_mode'] = openalex_mode
        return self.call(source, arguments)

    def fetch(self, candidate, scope='abstract'):
        paper = candidate.document_number.lower().startswith('10.')
        return self.call('openalex' if paper else 'epo',
                         {'doi' if paper else 'publication_number': candidate.document_number,
                          'constituent': scope}, fetch=True)

    def citation_neighbors(self, candidate, direction):
        """One hop only. Edges discover candidates; they never establish relevance."""
        if direction not in ('backward', 'forward'):
            raise ValueError('invalid_citation_direction')
        number = candidate.document_number
        if re.fullmatch(r'[A-Z]{2}\d+[A-Z]\d?', number, re.I):
            if direction == 'forward':
                seed = re.sub(r'[A-Z]\d?$', '', number.upper())
                result = self.call('epo', {'query': {'type': 'term', 'field': 'ct', 'value': seed}, 'max_results': 20})
                return {**result, 'seed': number, 'direction': direction}
            response = self.fetch(candidate, 'biblio')
            aid = response.get('raw_artifact_id')
            if not aid:
                return {'records': [], 'warning': 'citation_biblio_unavailable'}
            numbers = patent_references(ArtifactStore(PATHS.evidence_dir).read(aid), number)
            if not numbers:
                return {'records': [], 'seed_artifact_id': aid, 'references': [], 'direction': direction}
            records, failures = [], []
            # pn search may substitute an A publication for the cited B grant.
            # Fetch exact identifiers so an equivalent is not mislabeled a direct citation.
            from types import SimpleNamespace
            for cited in numbers[:3]:
                matched = []
                try:
                    detail = self.fetch(SimpleNamespace(document_number=cited), 'biblio')
                    matched = [r for r in detail.get('records', []) if r.get('document_number') == cited]
                except Exception as exc:
                    failures.append(type(exc).__name__)
                records.extend(matched or [{'document_number': cited, 'title': '',
                    'url': 'https://patents.google.com/patent/' + cited,
                    'evidence_refs': {'citation_reference': {'artifact_id': aid, 'seed': number}}}])
            return {'records': records, 'seed_artifact_id': aid, 'references': numbers, 'direction': direction,
                    'metadata_failures': failures, 'references_omitted': max(0, len(numbers) - 3)}
        return self.paper_citations(candidate, direction)

    def paper_citations(self, candidate, direction):
        from ..patent_search.openalex_client import OpenAlexClient, _with_page, _works, SELECT_FIELDS, openalex_work_id
        from ..patent_search.literature_parser import _openalex_abstract
        with self.locks['openalex']:
            client = OpenAlexClient(api_key=str(self.values.get('literature_openalex_api_key') or ''),
                                    timeout_seconds=10, http_budget_seconds=20)
            store = ArtifactStore(PATHS.evidence_dir)
            wid = str(candidate.fields.get('openalex_id') or '').rsplit('/', 1)[-1]
            seed_artifact = None
            if not re.fullmatch(r'W\d+', wid):
                if candidate.document_number.startswith('openalex:'):
                    wid = candidate.document_number.split(':', 1)[1]
                else:
                    seed = client.fetch(candidate.document_number)
                    seed_artifact = store.put(seed.body)
                    wid = openalex_work_id(seed.body)
            if not re.fullmatch(r'W\d+', wid):
                raise ValueError('citation_seed_has_no_openalex_id')
            # Build fixed-host URLs through pyalex; do not follow URLs in returned text.
            url = _with_page(_works().filter(**{'cites' if direction == 'forward' else 'cited_by': wid})
                             .select(list(SELECT_FIELDS)).url, 20)
            call = client._send(url, kind='search')
            aid = store.put(call.body)
            raw = json.loads(call.body)
            records = []
            for work in raw.get('results', [])[:20]:
                doi = str(work.get('doi') or '').removeprefix('https://doi.org/')
                location = work.get('primary_location') or {}
                records.append({'document_number': doi or 'openalex:' + str(work['id']).rsplit('/', 1)[-1],
                    'title': work.get('display_name') or '', 'publication_date': work.get('publication_date') or '',
                    'url': location.get('landing_page_url') or work['id'],
                    'fields': {'abstract': _openalex_abstract(work), 'openalex_id': work['id'],
                               'oa_url': location.get('pdf_url') or ''},
                    'evidence_refs': {'abstract': {'artifact_id': aid, 'source': 'openalex', 'work_id': work['id']}}})
            result = {'records': records, 'raw_artifact_id': aid, 'seed_artifact_id': seed_artifact,
                      'seed': wid, 'direction': direction, 'coverage': raw.get('meta'), 'usage': client.usage()}
            write_json(self.directory / ('citations-' + identifier(wid + direction) + '.json'), result)
            return result


def patent_references(data, seed_number):
    """Only patcit children of the requested document, not family or application IDs."""
    root = epo_parser._parse_xml(data)
    numbers = []
    for doc in epo_parser._iter(root, 'exchange-document'):
        identity = doc.get('country', '') + doc.get('doc-number', '') + doc.get('kind', '')
        if identity.upper() != seed_number.upper():
            continue
        for citation in epo_parser._iter(doc, 'patcit'):
            for did in epo_parser._iter(citation, 'document-id'):
                if did.get('document-id-type') != 'docdb':
                    continue
                parts = {epo_parser._local(child.tag): (child.text or '').strip() for child in did}
                number = parts.get('country', '') + parts.get('doc-number', '') + parts.get('kind', '')
                if re.fullmatch(r'[A-Z]{2}\d+[A-Z]\d?', number) and number != seed_number:
                    numbers.append(number)
    return list(dict.fromkeys(numbers))
