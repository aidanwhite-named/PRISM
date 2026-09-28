from app.search_engine.citations import patent_references, paper_citations
from app.search_mcp_server import SearchTools
import json
from types import SimpleNamespace

import pytest

from app.patent_search.artifacts import ArtifactStore


XML = b'''<world><exchange-document country="EP" doc-number="123" kind="A1">
<citation><patcit><document-id document-id-type="docdb"><country>US</country><doc-number>456</doc-number><kind>B2</kind></document-id></patcit></citation>
<application-reference><document-id document-id-type="docdb"><country>US</country><doc-number>999</doc-number><kind>A1</kind></document-id></application-reference>
</exchange-document><exchange-document country="EP" doc-number="789" kind="A1">
<citation><patcit><document-id document-id-type="docdb"><country>US</country><doc-number>777</doc-number><kind>A1</kind></document-id></patcit></citation>
</exchange-document></world>'''


def test_patent_edges_exclude_other_documents_and_application_ids():
    assert patent_references(XML, 'EP123A1') == ['US456B2']
    with pytest.raises(Exception, match='DOCTYPE'):
        patent_references(b'<!DOCTYPE world>' + XML, 'EP123A1')






@pytest.mark.parametrize('direction,parameter', [('backward', 'cited_by'), ('forward', 'cites')])
def test_paper_citations_preserve_artifact_and_direction_without_keyword_filter(tmp_path, monkeypatch, direction, parameter):
    from app.patent_search import openalex_client
    monkeypatch.setattr('app.search_engine.citations.PATHS', SimpleNamespace(evidence_dir=tmp_path))
    urls = []
    def send(client, url, **kwargs):
        urls.append(url)
        return SimpleNamespace(body=json.dumps({'results': [{'id': 'https://openalex.org/W2', 'display_name': 'Sensor',
            'publication_date': '2026-01-01', 'abstract_inverted_index': {'Sensor': [0]}}]}).encode())
    monkeypatch.setattr(openalex_client.OpenAlexClient, '_send', send)
    result = paper_citations(SearchTools(values={}, work_dir=tmp_path), 'openalex:W1', direction, begin=2)
    from urllib.parse import unquote
    assert parameter + ':' in unquote(urls[0]) and 'search=' not in urls[0]
    assert result['records'][0]['publication_date'] == '2026-01-01'
    assert result['records'][0]['document_number'] == 'openalex:W2'
    assert ArtifactStore(tmp_path).read(result['raw_artifact_id'])
