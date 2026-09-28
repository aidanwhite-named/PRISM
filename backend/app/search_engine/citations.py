"""Source citation pages; the model chooses whether and how far to follow them."""
import json
import re
from ..patent_search import epo_parser
from ..patent_search.artifacts import ArtifactStore
from ..config import PATHS
from .storage import identifier, write_json
def paper_citations(tools, number, direction, begin=1):
    from ..patent_search.openalex_client import OpenAlexClient, _with_page, _works, SELECT_FIELDS, openalex_work_id
    from ..patent_search.literature_parser import _openalex_abstract
    with tools._source_locks['literature']:
        client = OpenAlexClient(api_key=str(tools.values.get('literature_openalex_api_key') or ''),
                                timeout_seconds=10)
        store = ArtifactStore(PATHS.evidence_dir)
        wid = ''
        seed_artifact = None
        if not re.fullmatch(r'W\d+', wid):
            if number.startswith('openalex:'):
                wid = number.split(':', 1)[1]
            else:
                seed = client.fetch(number)
                seed_artifact = store.put(seed.body)
                wid = openalex_work_id(seed.body)
        if not re.fullmatch(r'W\d+', wid):
            raise ValueError('citation_seed_has_no_openalex_id')
        # Build fixed-host URLs through pyalex; do not follow URLs in returned text.
        url = _with_page(_works().filter(**{'cites' if direction == 'forward' else 'cited_by': wid})
                         .select(list(SELECT_FIELDS)).url, 20)
        from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
        parts = urlsplit(url)
        query = dict(parse_qsl(parts.query))
        query['page'] = str(begin)
        url = urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))
        call = client._send(url, kind='search')
        aid = store.put(call.body)
        raw = json.loads(call.body)
        records = []
        for work in raw.get('results', []):
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
        write_json(tools.work_dir / ('citations-' + identifier(wid + direction) + '.json'), result)
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
