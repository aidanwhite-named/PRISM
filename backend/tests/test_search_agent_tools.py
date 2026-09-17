import json
from types import SimpleNamespace

import pytest

from app import search_agent_tools as agent, search_manifest, search_verification
from app.config import PATHS
from app.patent_search.artifacts import ArtifactStore
from app.search_mcp_server import SearchTools


def test_checkpoint_survives_restart_and_updates_without_losing_candidates(tmp_path):
    tools = SearchTools(values={}, work_dir=tmp_path, max_calls=1)
    tools.call('save_candidates', {'report': {'candidates': [
        {'doc_number': 'WO2012111622A1', 'group': None}, {'doc_number': 'JP7475618B1', 'group': None}]}})
    restarted = SearchTools(values={}, work_dir=tmp_path, max_calls=1)
    # Saving remains possible when retrieval budget is exhausted.
    restarted.call('save_candidates', {'report': {'candidates': [{'doc_number': 'JP7475618B1', 'group': 'A'}]}})
    saved = agent.load_checkpoint(tmp_path)
    assert [c['doc_number'] for c in saved['candidates']] == ['WO2012111622A1', 'JP7475618B1']
    assert saved['candidates'][1]['group'] == 'A'
    journal = search_manifest.read_tool_journal(tmp_path)
    assert not search_manifest.has_retrieval_attempt([], [], journal)
    assert not search_manifest.build(claim_text='x', tool_journal=journal, error='interrupted')['retained_records']
    with pytest.raises(search_manifest.SearchLogError):
        restarted.call('save_candidates', {'report': {'candidates': 'bad'}})
    assert agent.load_checkpoint(tmp_path) == saved


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
    tools = SearchTools(values={}, work_dir=tmp_path, max_calls=20)
    url = 'https://patents.google.com/patent/JP7475618B1/en'
    first = tools.call('source_fetch', {'url': url, 'max_chars': 1000})
    second = tools.call('source_fetch', {'url': url, 'max_chars': 1000, 'offset': first['next_offset']})
    assert calls == [url]
    assert second['offset'] == 1000
    assert first['records'][0]['document_number'] == 'JP7475618B1'
    ref = first['records'][0]['evidence_refs']['claims']
    data, _ = search_manifest.parse(json.dumps({'candidates': [{'doc_number': 'JP7475618B1', 'url': url,
        'group': 'A', 'mapping': [{'feature': 'D', 'support_text': 'one parameter limits two other parameters.',
                                 'verbatim_excerpt': 'one parameter limits two other parameters.', 'evidence_ref': ref}]}]}))
    journal = search_manifest.read_tool_journal(tmp_path)
    assert search_manifest.has_retrieval_attempt([], [], journal)
    verified = search_verification.verify(data, {}, journal, store=store)['candidates'][0]
    assert verified['group'] == 'A'
    assert verified['evidence_level'] == 'public_capture'
    assert verified['mapping'][0]['support_verified']
    assert not verified['mapping'][0]['quote_verified']
    data['candidates'][0]['doc_number'] = 'JP0000001B1'
    assert not search_verification.verify(data, {}, journal, store=store)['candidates'][0]['evidence_sources']


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


def test_deadline_advice_still_allows_checkpoint(tmp_path, monkeypatch):
    from app import search_mcp_server
    monkeypatch.setattr(search_mcp_server.time, 'time', lambda: 100)
    (tmp_path / 'search_deadline.json').write_text('120', encoding='utf-8')
    tools = SearchTools(values={}, work_dir=tmp_path)
    assert tools.budget()['seconds_remaining'] == 20
    assert tools.budget()['action'] == 'finalize_now'
    tools.call('save_candidates', {'report': {'candidates': [{'doc_number': 'JP7475618B1'}]}})
    assert agent.load_checkpoint(tmp_path)['candidates']


@pytest.mark.usefixtures('legacy_search')
@pytest.mark.parametrize('interrupted', [True, False])
def test_runner_keeps_checkpoint_when_deadline_classification_also_fails(client, monkeypatch, interrupted):
    from .fake_provider import DeterministicSearchProvider
    from .conftest import wait_for_job
    calls = []
    original = DeterministicSearchProvider.execute
    async def execute(self, request, emit):
        calls.append(request)
        outcome = await original(self, request, emit)
        tools = SearchTools(values={}, work_dir=request.work_dir)
        tools.call('save_candidates', {'report': {'candidates': [{'doc_number': 'JP7475618B1', 'group': 'A'}]}})
        await emit('analyzing', {'message': 'Saved candidates; choosing next action'})
        from app.db import session_scope
        from app.models import ExecutionJob
        with session_scope() as session:
            preview = session.get(ExecutionJob, request.job_id).search_manifest
            assert preview['reported']['candidates'][0]['doc_number'] == 'JP7475618B1'
            assert preview['status'] == 'incomplete'
        outcome.result_text = ''
        outcome.timed_out = interrupted
        return outcome
    monkeypatch.setattr(DeterministicSearchProvider, 'execute', execute)
    created = client.post('/api/jobs', json={'job_kind': 'similarity_search', 'provider': 'test-search',
                                          'claim_text': '청구항 1. 회전 파라미터의 한계를 보정하는 방법'}).json()
    job = wait_for_job(client, created['id'])
    assert len(calls) == 2
    assert calls[1].tool_policy.name == 'no_tools'
    manifest = job['search_manifest']
    assert manifest['execution_mode'] == 'model_directed'
    assert manifest['status'] == 'incomplete'
    assert manifest['reported']['candidates'][0]['doc_number'] == 'JP7475618B1'
    assert job['status'] != 'SUCCEEDED'
    assert manifest['verification_followup'] is None
