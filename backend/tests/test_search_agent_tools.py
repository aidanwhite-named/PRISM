import json
from types import SimpleNamespace

import pytest

from app import search_source_tools as agent, search_manifest
from app.config import PATHS
from app.patent_search.artifacts import ArtifactStore
from app.search_mcp_server import SearchTools




def test_source_capture_pages_are_cached_and_do_not_become_official(monkeypatch, tmp_path):
    from app.search_engine.fetcher import Fetched, SafeFetcher
    store = ArtifactStore(PATHS.evidence_dir)
    html = ('<html><title>Test</title><dd itemprop="publicationNumber">JP7475618B1</dd>'
            '<section itemprop="claims">' + 'one parameter limits two other parameters. ' * 80 + '</section></html>').encode()
    aid = store.put(html)
    calls = []
    def get(self, url):
        calls.append(url)
        return Fetched(url, html, 'text/html', aid)
    monkeypatch.setattr(SafeFetcher, 'get', get)
    tools = SearchTools(values={}, work_dir=tmp_path)
    url = 'https://patents.google.com/patent/JP7475618B1/en'
    first = tools.call('source_fetch', {'url': url, 'max_chars': 1000})
    second = tools.call('source_fetch', {'url': url, 'max_chars': 1000, 'offset': first['next_offset']})
    assert calls == [url]
    assert second['offset'] == 1000
    assert first['records'][0]['document_number'] == 'JP7475618B1'
    ref = first['records'][0]['evidence_refs']['claims']
    assert first['source_kind'] == 'public_capture'
    capture = json.loads(store.read(ref['artifact_id']))
    assert 'one parameter limits two other parameters.' in capture[ref['field_path']]
    assert first['raw_artifact_id'] == aid


def test_forward_citation_request_is_model_selected_and_paged():
    calls = []
    def call(name, args):
        calls.append((name, args))
        return {'records': [{'document_number': 'JP7475618B1'}]}
    result = agent.citation_search(SimpleNamespace(call=call),
        {'identifier': 'WO2012111622A1', 'direction': 'forward', 'begin': 21})
    assert calls == [('epo_search', {'query': {'type': 'term', 'field': 'ct', 'value': 'WO2012111622'},
                                    'max_results': 20, 'begin': 21})]
    assert result['records'][0]['document_number'] == 'JP7475618B1'
    assert result['seed'] == 'WO2012111622A1'


def test_disabled_literature_cannot_be_reached_through_citations(tmp_path):
    tools = SearchTools(values={'literature_integration_enabled': False}, work_dir=tmp_path)
    with pytest.raises(ValueError, match='literature_tool_unavailable'):
        tools.call('citation_search', {'identifier': '10.1234/example', 'direction': 'forward'})


def test_description_and_term_window_reuse_claim_capture(monkeypatch, tmp_path):
    from app.search_engine.fetcher import Fetched, SafeFetcher
    store = ArtifactStore(PATHS.evidence_dir)
    body = ('<dd itemprop="publicationNumber">JP7475618B1</dd><section itemprop="claims">'
            + 'A control circuit generates a signal. ' * 10 + '</section>'
            + '<section itemprop="description">' + 'Context details. ' * 1000
            + 'The frame number initializes the key stream generator.' + '</section>').encode()
    aid = store.put(body)
    calls = []
    def get(self, url):
        calls.append(url)
        return Fetched(url, body, 'text/html', aid)
    monkeypatch.setattr(SafeFetcher, 'get', get)
    tools = SearchTools(values={}, work_dir=tmp_path)
    url = 'https://patents.google.com/patent/JP7475618B1/en'
    tools.call('source_fetch', {'url': url})
    result = tools.call('source_fetch', {'url': url, 'section': 'description',
                                       'find': 'frame number', 'max_chars': 1000})
    assert calls == [url]
    assert result['verification_scope'] == 'description'
    assert result['offset'] > 10000
    assert 'frame number' in result['records'][0]['fields']['description']
    missing = tools.call('source_fetch', {'url': url, 'section': 'description', 'find': 'absent terminology'})
    assert missing['find_found'] is False and missing['records'] == []
