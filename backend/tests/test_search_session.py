import asyncio
import json
import threading
from types import SimpleNamespace

import pytest

from app import search_agent_tools, search_deadline, search_manifest, search_session
from app.config import PATHS
from app.patent_search.artifacts import ArtifactStore
from app.patent_search.kipris_backend import KiprisAPIError
from app.providers.base import ExecutionOutcome, ExecutionRequest, WEB_SEARCH
from app.search_mcp_server import SearchTools


def test_shared_json_retries_windows_reader_without_losing_old_value(tmp_path, monkeypatch):
    from app.search_engine import models
    path = tmp_path / 'counter.json'
    path.write_text('1')
    replace = models.os.replace
    calls = []
    def transient(source, destination):
        calls.append(1)
        if len(calls) == 1:
            assert path.read_text() == '1'
            error = PermissionError('sharing violation')
            error.winerror = 32
            raise error
        replace(source, destination)
    monkeypatch.setattr(models.os, 'replace', transient)
    models.write_json(path, 2)
    assert json.loads(path.read_text()) == 2 and len(calls) == 2
    assert not list(tmp_path.glob('*.tmp'))


def test_parallel_requests_overlap_and_fail_independently(tmp_path, monkeypatch):
    tools = SearchTools(values={}, work_dir=tmp_path, max_calls=40)
    monkeypatch.setattr(tools, 'statuses', lambda: {s: {'status': 'available'} for s in ('epo', 'kipris', 'literature')})
    entered = threading.Barrier(5)
    release = threading.Event()
    observed = []
    original = tools.call

    def call(name, args):
        if name in ('epo_search', 'kipris_search', 'literature_search'):
            observed.append((name, args))
            entered.wait(timeout=5)
            assert release.wait(timeout=5)
            if name == 'kipris_search':
                raise KiprisAPIError('31')
            return {'records': [{'document_number': name}]}
        return original(name, args)

    monkeypatch.setattr(tools, 'call', call)
    started = tools.call('start_collection', {'epo_query': {'type': 'term', 'field': 'ta', 'value': 'joint limits'},
                                            'kipris_query': '관절', 'openalex_query': 'joint limits',
                                            'arxiv_query': 'joint constraints'})
    try:
        entered.wait(timeout=5)
        assert len(started['started']) == 4
        # The main/model thread is free to search the web while all APIs block.
        assert len(tools.call('collect_results', {})['pending']) == 4
        with pytest.raises(ValueError, match='collection_running'):
            tools.call('start_collection', {'openalex_query': 'another query'})
        assert all(args['max_results'] == 3 for _, args in observed)
        assert {args['source'] for name, args in observed if name == 'literature_search'} == {'openalex', 'arxiv'}
    finally:
        release.set()
        tools.collection_pool.shutdown(wait=True)
    result = tools.call('collect_results', {})
    assert not result['pending']
    assert result['completed']['epo_search']['records']
    assert result['completed']['literature_search']['records']
    assert result['completed']['arxiv_search']['records']
    assert result['completed']['kipris_search']['error_code'] == 'KIPRIS.31'
    with pytest.raises(ValueError, match='invalid_integer'):
        tools.call('start_collection', {'openalex_query': 'test', 'max_results': 5})


def test_openalex_and_arxiv_overlap_through_real_dispatch(tmp_path, monkeypatch):
    tools = SearchTools(values={}, work_dir=tmp_path, max_calls=40)
    monkeypatch.setattr(tools, 'statuses', lambda: {s: {'status': 'available'} for s in ('epo', 'kipris', 'literature')})
    entered = threading.Barrier(3)
    release = threading.Event()

    def search(backend_id, arguments):
        entered.wait(timeout=5)
        assert release.wait(timeout=5)
        return {'records': [{'source': arguments['source']}]}

    monkeypatch.setattr(tools, '_plain_search', search)
    tools.call('start_collection', {'openalex_query': 'mesh merging', 'arxiv_query': 'texture atlas'})
    try:
        entered.wait(timeout=5)
        assert set(tools.call('collect_results', {})['pending']) == {'literature_search', 'arxiv_search'}
    finally:
        release.set()
        tools.collection_pool.shutdown(wait=True)
    completed = tools.call('collect_results', {})['completed']
    assert completed['literature_search']['records'] == [{'source': 'openalex'}]
    assert completed['arxiv_search']['records'] == [{'source': 'arxiv'}]
    rows = search_manifest.read_tool_journal(tmp_path)
    assert {r['arguments']['source'] for r in rows if r['tool'] == 'literature_search' and r['state'] == 'completed'} == {'openalex', 'arxiv'}


def test_collection_skips_unavailable_arxiv(tmp_path, monkeypatch):
    tools = SearchTools(values={}, work_dir=tmp_path)
    monkeypatch.setattr(tools, 'statuses', lambda: {
        'epo': {'status': 'unavailable'}, 'kipris': {'status': 'unavailable'},
        'literature': {'status': 'available', 'sources': {'arxiv': 'dependency_missing'}}})
    result = tools.call('start_collection', {'arxiv_query': 'mesh merging'})
    assert result == {'started': [], 'skipped': [{'source': 'arxiv', 'reason': 'dependency_missing'}],
                      'budget': tools.budget()}


def test_shortlist_limit_replacement_and_raw_hits_not_promoted(tmp_path):
    tools = SearchTools(values={}, work_dir=tmp_path)
    candidates = [{'doc_number': f'JP{1000000+i}B1'} for i in range(15)]
    tools.call('save_candidates', {'report': {'candidates': candidates}})
    with pytest.raises(ValueError, match='candidate_limit_15'):
        tools.call('save_candidates', {'report': {'candidates': [{'doc_number': 'JP9999999B1'}]}})
    assert len(search_agent_tools.load_checkpoint(tmp_path)['candidates']) == 15
    tools.call('save_candidates', {'replace': True, 'report': {'candidates': [{'doc_number': 'JP9999999B1'}]}})
    saved = search_agent_tools.load_checkpoint(tmp_path)
    assert len(saved['candidate_dispositions']) == 15
    tools._record({'tool': 'epo_search', 'state': 'completed', 'ok': True, 'result': {
        'records': [{'document_number': f'WO{2012000000+i}A1', 'title': 'Raw hit', 'url': ''} for i in range(44)]}})
    report, rows = search_deadline.material(tmp_path, ExecutionOutcome())
    assert len(rows) == 1 and rows[0]['saved_by_model']
    assert report['candidates'][0]['doc_number'] == 'JP9999999B1'
    bounded = search_session.bound_report({'candidates': candidates + [{'doc_number': 'JP9999999B1'}]})
    assert len(bounded['candidates']) == 15 and len(bounded['candidate_dispositions']) == 1


@pytest.mark.parametrize('requested,expected', [(None, 3), (2, 2), (20, 4)])
def test_direct_api_calls_share_small_page_limit(tmp_path, monkeypatch, requested, expected):
    tools = SearchTools(values={}, work_dir=tmp_path)
    monkeypatch.setattr(tools, 'statuses', lambda: {s: {'status': 'available'} for s in ('epo', 'kipris', 'literature')})
    actual = []
    def execute(name, arguments):
        actual.append(arguments)
        return {'records': []}
    monkeypatch.setattr(tools, '_execute', execute)
    args = {'query': 'joint limits', 'source': 'openalex'}
    if requested is not None:
        args['max_results'] = requested
    tools.call('literature_search', args)
    assert actual[0]['max_results'] == expected
    assert search_manifest.read_tool_journal(tmp_path)[0]['arguments']['max_results'] == expected


def evidence_tools(tmp_path, field='claims'):
    tools = SearchTools(values={}, work_dir=tmp_path)
    text = 'One parameter changes the limits of two other parameters.'
    aid = ArtifactStore(PATHS.evidence_dir).put(json.dumps({'text': text}).encode())
    ref = {'artifact_id': aid, 'field_path': 'text', 'profile_id': 'generic_json'}
    tools._record({'tool': 'source_fetch', 'state': 'completed', 'ok': True, 'result': {
        'source_kind': 'public_capture', 'records': [{'document_number': 'JP7475618B1',
          'fields': {field: text}, 'evidence_refs': {field: ref}}]}})
    report = {'candidates': [{'doc_number': 'JP7475618B1', 'group': 'A',
        'mapping': [{'feature': 'coupled limits', 'support_text': text, 'evidence_ref': ref}]}]}
    review = {'candidate_id': 'patent:JP7475618B1', 'core_features': ['coupled limits'], 'rationale': '전체 구성 대응을 검토함'}
    return tools, report, review


@pytest.mark.parametrize('field,accepted', [('claims', True), ('full_text', True), ('abstract', False)])
def test_x_requires_preserved_fulltext_evidence(tmp_path, field, accepted):
    tools, report, review = evidence_tools(tmp_path, field)
    result = tools.call('save_candidates', {'report': report, 'x_review': review})
    assert result['early_stop']['accepted'] is accepted
    assert (tmp_path / search_session.STOP_FILE).exists() is accepted


def test_x_rejects_missing_feature_fabricated_quote_and_unknown_date(tmp_path):
    tools, report, review = evidence_tools(tmp_path)
    review['core_features'].append('correction inside limit')
    assert not tools.call('save_candidates', {'report': report, 'x_review': review})['early_stop']['accepted']
    review['core_features'].pop()
    tools.cutoff = '2020-01-01'
    assert not tools.call('save_candidates', {'report': report, 'x_review': review})['early_stop']['accepted']
    tools.cutoff = ''
    report['candidates'][0]['mapping'][0]['support_text'] = 'Invented support'
    assert not tools.call('save_candidates', {'report': report, 'x_review': review})['early_stop']['accepted']


@pytest.mark.parametrize('user_cancelled', [False, True])
async def test_runner_stops_on_verified_x_without_overriding_user_cancel(tmp_path, user_cancelled):
    tools, report, review = evidence_tools(tmp_path)
    done = asyncio.Event()

    class Provider:
        async def execute(self, request, emit):
            tools.call('save_candidates', {'report': report, 'x_review': review})
            if user_cancelled:
                return ExecutionOutcome(cancelled=True, exit_code=-1)
            await asyncio.wait_for(done.wait(), timeout=3)
            return ExecutionOutcome(cancelled=True, exit_code=-1, tool_policy=WEB_SEARCH,
                tool_calls=[{'name': 'WebSearch', 'input': {'query': 'joint'}, 'ok': True}])

        async def cancel(self, job_id):
            done.set()
            return True

    async def emit(*args):
        pass
    outcome = await search_session.execute(Provider(), ExecutionRequest(job_id='test', work_dir=tmp_path,
        system_prompt='', user_message=''), emit, cancelled=lambda: user_cancelled)
    if user_cancelled:
        assert outcome.cancelled
    else:
        assert not outcome.cancelled and outcome.terminal_reason == 'verified_x_early_stop'
        assert json.loads(outcome.result_text)['candidates'][0]['group'] == 'A'
        assert done.is_set()


@pytest.mark.parametrize('failure', ['approval', 'disk'])
@pytest.mark.parametrize('user_cancelled', [False, True])
async def test_checkpoint_failure_stops_search_and_preserves_prior_candidates(tmp_path, monkeypatch, failure, user_cancelled):
    from app.providers.codex_stream import CodexStreamParser
    from app.providers.base import ExecutionOutcome, ExecutionRequest
    from app.evaluation.evaluator import evaluate
    from app.enums import ErrorCode
    from app import search_agent_tools

    tools = SearchTools(values={}, work_dir=tmp_path)
    original = {'candidates': [{'doc_number': 'JP7475618B1', 'group': None, 'note': '초기 후보'}]}
    tools.call('save_candidates', {'report': original})
    done = asyncio.Event()
    events = []

    class Provider:
        stopped = False

        async def execute(self, request, emit):
            if failure == 'approval':
                parser = CodexStreamParser()
                for kind, payload in parser.feed(json.dumps({'type': 'item.completed', 'item': {
                    'id': 'save-1', 'type': 'mcp_tool_call', 'server': 'prism-search', 'tool': 'save_candidates',
                    'status': 'failed', 'error': {'message': 'MCP tool call requires approval, but approval policy is never'}}})):
                    await emit(kind, payload)
            else:
                def fail_write(*args):
                    raise OSError('disk full')
                monkeypatch.setattr('app.search_engine.models.write_json', fail_write)
                with pytest.raises(OSError, match='disk full'):
                    tools.call('save_candidates', {'report': original})
            if not user_cancelled:
                await asyncio.wait_for(done.wait(), timeout=2)
            return ExecutionOutcome(cancelled=True, timed_out=True)

        async def cancel(self, job_id):
            self.stopped = True
            done.set()
            return True

    async def emit(kind, payload):
        events.append((kind, payload))

    provider = Provider()
    outcome = await search_session.execute(provider, ExecutionRequest(job_id='test', work_dir=tmp_path,
        system_prompt='', user_message=''), emit, cancelled=lambda: user_cancelled)
    verdict = evaluate(outcome)
    assert verdict.error_code == (ErrorCode.CANCELLED if user_cancelled else ErrorCode.SEARCH_CHECKPOINT_FAILED)
    assert provider.stopped is not user_cancelled
    assert search_agent_tools.load_checkpoint(tmp_path)['candidates'][0]['doc_number'] == 'JP7475618B1'
    if not user_cancelled:
        assert any(p.get('stage') == 'checkpoint_failed' for _, p in events)
        assert ('approval policy is never' if failure == 'approval' else 'disk full') in verdict.errors[-1]


def test_expired_kipris_code_and_no_repeat_within_run(tmp_path, monkeypatch):
    from app.patent_search import kipris_backend
    with pytest.raises(KiprisAPIError, match='이용기간 만료') as error:
        kipris_backend.parse(b'<response><resultCode>31</resultCode><resultMsg>secret echo</resultMsg></response>')
    assert error.value.fault_code == 'KIPRIS.31' and 'secret' not in str(error.value)
    tools = SearchTools(values={'kipris_integration_enabled': True, 'kipris_api_key': 'test'}, work_dir=tmp_path)
    calls = []
    def fail(*args):
        calls.append(1)
        raise KiprisAPIError('31')
    monkeypatch.setattr(tools, '_plain_search', fail)
    with pytest.raises(KiprisAPIError):
        tools.call('kipris_search', {'query': '관절'})
    with pytest.raises(ValueError, match='tool_unavailable'):
        tools.call('kipris_search', {'query': '관절'})
    assert len(calls) == 1
