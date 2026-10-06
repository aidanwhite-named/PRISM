import json
from types import SimpleNamespace

import pytest

from app.config import PATHS
from app.patent_search.artifacts import ArtifactStore
from app.patent_search.literature_client import LiteratureClient, HttpResponse, LiteratureError
from app.patent_search import parsers
from app.search_mcp_server import SearchTools
from app.search_engine.source_acquisition import article_full_text

DOI = '10.1016/j.aei.2025.104191'
TITLE = 'Single image-based Gaussian splatting for 3D reconstruction of movable articulated objects'


@pytest.mark.parametrize('failure', ['missing', 'quota', 'no_copies'])
def test_unavailable_openalex_returns_independent_repository_searches(tmp_path, failure):
    def fetch(doi):
        if failure == 'quota':
            raise LiteratureError('api-key=SECRET', status=429)
        body = {} if failure == 'missing' else {'doi': DOI, 'display_name': TITLE, 'locations': []}
        return SimpleNamespace(body=json.dumps(body).encode(), no_results=failure == 'missing')
    backend = SimpleNamespace(_require_openalex=lambda: SimpleNamespace(fetch=fetch), _detail_fetches=0)
    calls = []
    def call(name, args):
        calls.append((name, args))
        assert args['constituent'] == 'biblio'
        return {'identifier_matched': True, 'records': [{'document_number': DOI, 'title': TITLE}]}
    tools = SimpleNamespace(work_dir=tmp_path, _backend=lambda _: backend, call=call,
                            budget=lambda: {'seconds_remaining': 100})
    result = article_full_text(tools, {'doi': DOI})
    assert result['verification_scope'] == 'bibliographic'
    assert 'captured_source' not in result
    assert result['fallback_searches'][0]['arguments']['source'] == 'arxiv'
    assert result['fallback_searches'][1]['arguments']['source'] == 'openreview'
    queries = [s.get('query', '') for s in result['fallback_searches']]
    assert any('site:openaccess.thecvf.com' in q for q in queries)
    assert any('site:core.ac.uk' in q for q in queries)
    assert 'Semantic Scholar' not in json.dumps(result)
    assert 'SECRET' not in json.dumps(result)
    assert len(calls) == (0 if failure == 'no_copies' else 1)


def test_missing_metadata_does_not_certify_a_supplied_title(tmp_path):
    def unavailable(*_):
        raise LiteratureError('unavailable')
    tools = SimpleNamespace(work_dir=tmp_path, _backend=unavailable, call=unavailable,
                            budget=lambda: {'seconds_remaining': 100})
    result = article_full_text(tools, {'doi': DOI, 'title': TITLE})
    assert result['records'] == []
    assert result['identifier_matched'] is False
    assert TITLE in result['fallback_searches'][1]['arguments']['query']


def test_arxiv_body_is_acquired_without_openalex_and_wrong_identity_is_rejected(tmp_path):
    attempted = []
    def call(name, args):
        attempted.append(args['url'])
        return {'verification_scope': 'full_text', 'records': [{'url': args['url'],
            'document_number': 'arxiv:2502.19459', 'fields': {'full_text': 'body'}}]}
    def unexpected(*_):
        raise AssertionError('arXiv full text must not depend on OpenAlex')
    tools = SimpleNamespace(work_dir=tmp_path, _backend=unexpected, call=call,
                            budget=lambda: {'seconds_remaining': 100})
    result = article_full_text(tools, {'doi': 'arxiv:2502.19459v2', 'title': 'ArtGS'})
    assert result['source_kind'] == 'arxiv_direct'
    assert result['verification_scope'] == 'full_text'
    assert attempted == ['https://arxiv.org/html/2502.19459v2']
    tools.call = lambda *_: {'verification_scope': 'full_text', 'records': [
        {'url': 'https://arxiv.org/html/9999.99999', 'fields': {'full_text': 'wrong paper'}}]}
    rejected = article_full_text(tools, {'doi': 'arxiv:2502.19459'})
    assert rejected['verification_scope'] == 'bibliographic'
    assert all(a['error'] == 'captured_doi_mismatch' for a in rejected['acquisition_attempts'])


def test_openreview_search_preserves_metadata_and_own_pdf_without_openalex(tmp_path, monkeypatch):
    paper = {'id': 'Abc123', 'pdate': 1735689600000, 'content': {
        'title': {'value': 'Gaussian articulated reconstruction'},
        'abstract': {'value': 'Two states of the object are aligned.'},
        'pdf': {'value': '/pdf?id=Abc123'}}}
    comment = {'id': 'review1', 'replyto': 'Abc123', 'content': {'title': {'value': 'Review'}}}
    bad = {'id': 'bad', 'content': {'title': {'value': 'Unsafe link'}, 'pdf': {'value': 'file:///private'}}}
    missing_pdf = {'id': 'nopdf', 'content': {'title': {'value': 'Metadata only'}}}
    body = json.dumps({'notes': [paper, comment, bad, missing_pdf], 'count': 4}).encode()
    requests = []
    def transport(request, timeout):
        requests.append(request.full_url)
        assert request.full_url.startswith('https://api2.openreview.net/notes/search?')
        return HttpResponse(status=200, headers={}, body=body)
    client = LiteratureClient(transport=transport)
    tools = SearchTools(values={}, work_dir=tmp_path)
    monkeypatch.setattr(tools, 'statuses', lambda: {s: {'status': 'available'} for s in ('epo', 'literature', 'kipris')})
    monkeypatch.setattr(tools, '_backend', lambda _: SimpleNamespace(_require_client=lambda: client))
    result = tools.call('literature_search', {'source': 'openreview', 'query': 'Gaussian articulated', 'max_results': 5})
    first, unsafe, missing = result['records']
    assert first['pdf_urls'] == ['https://openreview.net/pdf?id=Abc123']
    assert first['publication_date'] == '2025-01-01'
    assert first['document_number'] == ''
    assert unsafe['pdf_urls'] == [] and missing['pdf_urls'] == []
    ref = first['evidence_refs']['abstract']
    assert parsers.extract(ArtifactStore(PATHS.evidence_dir).read(ref['artifact_id']),
                           ref['field_path'], ref['profile_id']).text == paper['content']['abstract']['value']
    assert result['verification_scope'] == 'bibliographic_search'
    assert len(requests) == 1
