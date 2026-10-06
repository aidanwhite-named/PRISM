"""Continue observed work without inserting raw text or fabricating review scope."""
import asyncio
import json
from types import SimpleNamespace

import pytest

from app.config import PATHS
from app.patent_search.artifacts import ArtifactStore
from app.search_engine.continuation import search_state
from app.search_engine.storage import write_json
from app.search_mcp_server import SearchTools
from app.search_source_tools import source_fetch
from .test_autonomous_search import engine, finding


def receipt(**extra):
    return {'id': 'read-1', 'tool': 'source_fetch', 'state': 'completed', 'ok': True,
            'arguments': {'url': 'https://example.org/source', 'offset': 100, 'max_chars': 200},
            'result': {'offset': 100, 'total_chars': 10000, 'next_offset': 300,
                'records': [{'url': 'https://example.org/source',
                             'fields': {'page_text': 'SOURCE SECRET PROSE ' * 10}}]}, **extra}


def test_state_has_queries_failures_and_actual_windows_without_source_prose():
    snapshot = {'source_calls': [receipt(), receipt(id='failed', tool='literature_search', ok=False,
        arguments={'query': 'a failed query'}, detail='service unavailable', result={})],
        'native_calls': [{'id': 'web', 'name': 'web_search', 'query': 'native query'},
                         {'id': 'web', 'name': 'web_search', 'ok': False, 'error': 'timeout'}]}
    state = search_state(snapshot)
    assert state['observed_actions'][1]['error'] == 'service unavailable'
    window = state['obtained_sources'][0]['window']
    assert window['offset'] == 100 and window['chars'] == len('SOURCE SECRET PROSE ' * 10)
    assert window['total_chars'] == 10000 and window['next_offset'] == 300
    assert state['native_actions'] == [{'tool': 'web_search', 'arguments': 'native query',
                                        'ok': False, 'error': 'timeout'}]
    assert 'SOURCE SECRET PROSE' not in json.dumps(state)


def test_multi_generation_continuation_retains_native_queries_and_mcp_reads(tmp_path):
    first = engine(tmp_path / 'first')
    first.directory.mkdir()
    asyncio.run(first.run())
    checkpoint = first.checkpoint()
    checkpoint['snapshot']['source_calls'] = [receipt()]
    checkpoint['snapshot']['native_calls'] = [{'id': 'web', 'name': 'web_search', 'query': 'old query'}]
    second = engine(tmp_path / 'second')
    second.directory.mkdir()
    second.restore(checkpoint)
    asyncio.run(second.run())
    delivered = json.loads(second.inference.provider.requests[0].user_message)['previous_search_state']
    assert delivered['observed_actions'][0]['arguments']['offset'] == 100
    assert delivered['native_actions'][0]['arguments'] == 'old query'
    third = engine(tmp_path / 'third')
    third.directory.mkdir()
    third.restore(second.checkpoint())
    assert any(c['arguments'] == 'old query' for c in third.previous_state['native_actions'])


def capture_checkpoint(tmp_path, *, section='claims', corrupt=False):
    store = ArtifactStore(PATHS.evidence_dir)
    raw = store.put(b'captured original bytes')
    capture = {'url': 'https://example.org/source', 'raw_artifact_id': raw,
               'document_number': '', 'title': 'Original', 'scope': 'page_text',
               'text': 'original text ' * 1000, 'pdf_urls': []}
    artifact = store.put(json.dumps(capture).encode())
    if corrupt:
        store._path(artifact).write_bytes(b'corrupt')
    call = receipt(arguments={'url': capture['url'], 'section': section},
                   result={'capture_artifact_id': artifact})
    write_json(tmp_path / 'resume-search.json', {'version': 2, 'snapshot': {'source_calls': [call]}})
    return capture


def test_continuation_reuses_original_capture_for_a_new_window_without_network(tmp_path, monkeypatch):
    capture = capture_checkpoint(tmp_path)
    def no_network(*args):
        pytest.fail('A valid previous capture must not be downloaded again')
    monkeypatch.setattr('app.search_engine.fetcher.SafeFetcher.get', no_network)
    tools = SearchTools(work_dir=tmp_path, values={})
    result = tools.call('source_fetch', {'url': capture['url'], 'offset': 1000, 'max_chars': 1000})
    assert result['records'][0]['fields']['page_text'] == capture['text'][1000:2000]
    assert result['next_offset'] == 2000
    assert result['raw_artifact_id'] == capture['raw_artifact_id']


@pytest.mark.parametrize('case', ['corrupt', 'different_section', 'different_url'])
def test_invalid_or_unrelated_capture_requires_fresh_network(tmp_path, monkeypatch, case):
    capture = capture_checkpoint(tmp_path, corrupt=case == 'corrupt')
    store = ArtifactStore(PATHS.evidence_dir)
    body = b'<html><title>Fresh</title><p>' + b'Fresh actual original text ' * 20 + b'</p></html>'
    calls = []
    def fetch(self, url):
        calls.append(url)
        return SimpleNamespace(url=url, artifact_id=store.put(body), body=body, content_type='text/html')
    monkeypatch.setattr('app.search_engine.fetcher.SafeFetcher.get', fetch)
    args = {'url': capture['url'], 'section': 'page' if case == 'different_section' else 'claims'}
    if case == 'different_url':
        args['url'] = 'https://example.org/other'
    result = source_fetch(SearchTools(work_dir=tmp_path, values={}), args)
    assert calls == [args['url']]
    assert 'Fresh actual original' in result['records'][0]['fields']['page_text']


def test_unchanged_poll_does_not_read_or_write_full_results_but_final_flush_does(tmp_path, monkeypatch):
    e = engine(tmp_path)
    refreshed = []
    original = e.refresh
    def refresh():
        refreshed.append(True)
        original()
    monkeypatch.setattr(e, 'refresh', refresh)
    async def exercise():
        await e.publish()
        first_stamp = (tmp_path / 'checkpoint.json').stat().st_mtime_ns
        for _ in range(12):
            await e.publish()
        assert len(refreshed) == 1
        assert (tmp_path / 'checkpoint.json').stat().st_mtime_ns == first_stamp
        from app.search_engine import autonomous_store
        autonomous_store.merge(tmp_path, [finding()])
        await e.publish()
        assert len(refreshed) == 2 and e.records
        e.phase, e.stop_reason = 'complete', 'deadline'
        await e.publish(force=True)
    asyncio.run(exercise())
    saved = json.loads((tmp_path / 'checkpoint.json').read_text(encoding='utf-8'))['snapshot']
    assert saved['stop_reason'] == 'deadline' and saved['findings']
    assert e.persistence['snapshots_written'] == 3
    assert e.persistence['unchanged_polls'] == 12


def test_source_result_arriving_during_emit_is_published_next_time(tmp_path):
    e = engine(tmp_path)
    emitted = []
    async def emit(snapshot):
        emitted.append(snapshot)
        if len(emitted) == 1:
            SearchTools(work_dir=tmp_path, values={})._record(receipt())
    e.emit = emit
    async def exercise():
        await e.publish()
        await e.publish()
    asyncio.run(exercise())
    assert len(emitted) == 2
    assert emitted[1]['source_calls'][0]['id'] == 'read-1'


def test_continuation_keeps_description_and_page_metadata(tmp_path, monkeypatch):
    capture = capture_checkpoint(tmp_path, section='description')
    store = ArtifactStore(PATHS.evidence_dir)
    capture.update(capture_version=2, scope='description',
                   page_spans=[{'page': 1, 'start': 0, 'end': len(capture['text'])}])
    artifact = store.put(json.dumps(capture).encode())
    call = receipt(arguments={'url': capture['url'], 'section': 'description'},
                   result={'capture_artifact_id': artifact})
    write_json(tmp_path / 'resume-search.json', {'version': 2, 'snapshot': {'source_calls': [call]}})
    def no_network(*args):
        pytest.fail('A description capture must be reused with its original metadata')
    monkeypatch.setattr('app.search_engine.fetcher.SafeFetcher.get', no_network)
    result = source_fetch(SearchTools(work_dir=tmp_path, values={}),
                          {'url': capture['url'], 'section': 'description', 'max_chars': 1000})
    assert result['capture_origin'] == 'continuation'
    assert result['capture_version'] == 2
    assert result['records'][0]['fields']['description'] == capture['text'][:1000]
    assert result['page_spans'] == capture['page_spans']
    state = search_state({'source_calls': [receipt(result=result)]})
    assert state['obtained_sources'][0]['window']['chars'] == 1000
