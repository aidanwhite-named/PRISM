import asyncio
import json

import pytest

from app.providers.base import CODEX_WEB_SEARCH, ExecutionOutcome
from app.search_engine.autonomous import AutonomousSearch, SearchSession
from app.search_engine import autonomous_store
from app.search_engine.report import render, manifest
from app.search_mcp_server import SearchTools


def finding(i=1, **extra):
    return {'title': f'Found source {i}', 'url': f'https://example.org/paper/{i}',
            'document_number': '', 'reason': f'Corresponding input and output {i}',
            'reported_scope': 'abstract', **extra}


class Provider:
    id = 'codex'
    search_tool_policy = CODEX_WEB_SEARCH
    max_input_bytes = None
    supports_tool_policy = lambda self, policy: True

    def __init__(self, action=None):
        self.action = action
        self.requests = []
        self.cancelled = False

    async def execute(self, request, emit):
        self.requests.append(request)
        await emit('tool_use', {'name': 'web_search', 'query': 'similar invention'})
        if self.action:
            return await self.action(request, emit)
        autonomous_store.merge(request.work_dir, [finding()])
        return ExecutionOutcome(result_text='Search explanation', exit_code=0,
                                tool_calls=[{'name': 'web_search'}])

    async def cancel(self, job_id):
        self.cancelled = True


def engine(tmp_path, provider=None, **kwargs):
    provider = provider or Provider()
    return AutonomousSearch(claim='A controls B based on C', directory=tmp_path,
        inference=SearchSession(provider, 'test'), values={'search_timeout_seconds': 5}, **kwargs)


def test_single_session_preserves_context_and_sources(tmp_path):
    e = engine(tmp_path, specification='context ' * 2000)
    asyncio.run(e.run())
    request, = e.inference.provider.requests
    assert request.timeout_seconds <= 125
    assert not hasattr(request.tool_policy, 'max_tool_calls')
    assert not request.tool_policy.required_tools
    assert 'save_findings' in str(request.mcp_servers) or request.tool_policy.mcp_tools
    assert 'PRISM_SEARCH_AUTONOMOUS' not in request.mcp_servers['prism-search']['env']
    assert len(json.loads(request.user_message)['specification']) > 6000
    snapshot = e.snapshot()
    assert snapshot['mode'] == 'autonomous' and snapshot['stop_reason'] == 'model_complete'
    assert 'classification' not in snapshot
    assert 'X분류' not in render(snapshot)
    assert manifest(snapshot, claim=e.claim, provider='codex')['status'] == 'verification_incomplete'


def test_streamed_findings_preserve_model_explanation_as_audit_only(tmp_path):
    async def action(request, emit):
        await emit('result_stream', {'delta': json.dumps({'records': [finding()]})})
        return ExecutionOutcome(result_text='문헌의 입력과 출력 관계가 유사합니다.', exit_code=0,
                                tool_calls=[{'name': 'web_search'}])
    e = engine(tmp_path, Provider(action))
    asyncio.run(e.run())
    assert e.records and e.snapshot()['model_summary'] == '문헌의 입력과 출력 관계가 유사합니다.'
    assert '근거 확인 미완료 1건' in e.snapshot()['summary']


def test_resolved_events_and_final_summary_do_not_double_count_tools(tmp_path):
    from app.search_manifest import observed
    async def action(request, emit):
        await emit('tool_use', {'id': 'read-1', 'name': 'web_search', 'input': {'url': 'https://example.org'}})
        await emit('tool_use_resolved', {'id': 'read-1', 'name': 'web_search', 'ok': True})
        autonomous_store.merge(tmp_path, [finding()])
        return ExecutionOutcome(result_text='Saved source', exit_code=0,
            tool_calls=[{'name': 'web_search'}, {'id': 'read-1', 'name': 'web_search', 'ok': True,
                        'result': 'Captured source', 'input': {}}])
    e = engine(tmp_path, Provider(action))
    asyncio.run(e.run())
    counts = observed(e.native_calls)['tool_call_counts']
    assert counts == {'web_search': 2}
    call = next(call for call in e.native_calls if call.get('id') == 'read-1')
    assert call['ok'] and call['result'] == 'Captured source'
    assert call['input'] == {'url': 'https://example.org'}


def test_autonomous_source_response_does_not_truncate_original_text():
    from types import SimpleNamespace
    from app.search_mcp_server import _record
    body = 'Source content ' * 6000
    rec = SimpleNamespace(fields={'abstract:en': SimpleNamespace(value=body, evidence=None)},
                          doc_number='EP1000000A1', title='Title', source_url='https://example.com')
    result = _record(rec)
    assert result['fields']['abstract:en'] == body
    assert 'truncated_fields' not in result


def test_large_findings_pass_through_actual_mcp_protocol(client, tmp_path):
    import os
    import subprocess
    import sys
    rows = [finding(i, reason='relation ' * 150) for i in range(130)]
    request = {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
               'params': {'name': 'save_findings', 'arguments': {'records': rows}}}
    wire = json.dumps(request) + '\n'
    assert len(wire) > 65536
    result = subprocess.run([sys.executable, '-m', 'app.search_mcp_server'],
        input=wire, capture_output=True, text=True, encoding='utf-8', timeout=20,
        env={**os.environ, 'PRISM_SEARCH_WORK_DIR': str(tmp_path)})
    assert result.returncode == 0, result.stderr
    reply = json.loads(result.stdout)['result']
    assert not reply['isError'], reply
    assert reply['structuredContent']['saved_findings'] == 130
    assert len(autonomous_store.load(tmp_path)) == 130


def test_findings_have_no_count_or_reason_length_cap_and_keep_model_order(tmp_path):
    tools = SearchTools(work_dir=tmp_path, values={})
    tools.calls = 1000
    rows = [finding(i, reason='detailed relation ' * 100) for i in range(130)]
    assert tools.call('save_findings', {'records': rows})['saved_findings'] == 130
    tools.call('save_findings', {'records': [finding(129), finding(3)], 'ranked': True})
    saved = autonomous_store.load(tmp_path)
    assert [row['title'] for row in saved[:2]] == ['Found source 129', 'Found source 3']
    assert len(saved) == 130
    e = engine(tmp_path)
    e.refresh()
    text = render(e.snapshot())
    assert '## 130.' in text
    assert text.index('Found source 129') < text.index('Found source 3')
    assert 'limit' not in tools.budget()


def test_source_validation_does_not_erase_saved_findings(tmp_path):
    tools = SearchTools(work_dir=tmp_path, values={})
    tools.call('save_findings', {'records': [finding()]})
    assert tools.call('save_findings', {'records': [finding(2, url='javascript:alert(1)')]})['failed_findings']
    assert tools.call('save_findings', {'records': [finding(2, url='https://patents.google.com/patent/US123A1', document_number='US456A1')]})['failed_findings']
    assert len(autonomous_store.load(tmp_path)) == 1


def test_tool_page_size_is_not_forced_to_four(tmp_path, monkeypatch):
    tools = SearchTools(work_dir=tmp_path, values={})
    monkeypatch.setattr(tools, 'statuses', lambda: {'epo': {'status': 'disabled'}, 'kipris': {'status': 'available'}, 'literature': {'status': 'disabled'}})
    seen = []
    monkeypatch.setattr(tools, '_execute', lambda name, args: seen.append(args) or {'records': []})
    tools.call('kipris_search', {'query': 'sensor', 'max_results': 40})
    assert seen[0]['max_results'] == 40


def test_time_is_not_reserved_for_classification(tmp_path, monkeypatch):
    tools = SearchTools(work_dir=tmp_path, values={})
    monkeypatch.setattr('app.search_mcp_server.time.time', lambda: 100)
    (tmp_path / 'search_deadline.json').write_text('105')
    original = tools._execute
    monkeypatch.setattr(tools, '_execute', lambda name, args: original(name, args) if name == 'save_findings' else {'records': []})
    assert 'budget_stopped' not in tools.call('source_fetch', {'url': 'https://example.org'})
    (tmp_path / 'search_deadline.json').write_text('99')
    assert tools.call('source_fetch', {'url': 'https://example.org'})['budget_stopped']
    assert tools.call('save_findings', {'records': [finding()]})['saved_findings'] == 1


def test_hard_deadline_keeps_streamed_findings(tmp_path):
    async def run(request, emit):
        await emit('result_stream', {'delta': json.dumps({'records': [finding()]})})
        await asyncio.sleep(60)
    e = engine(tmp_path, Provider(run))
    e.seconds = .04
    asyncio.run(e.run())
    assert e.inference.provider.cancelled
    assert e.snapshot()['stop_reason'] == 'deadline'
    assert e.snapshot()['candidates'][0]['reason'] == finding()['reason']


def test_cancel_keeps_mcp_findings(tmp_path):
    stopped = [False]
    async def run(request, emit):
        autonomous_store.merge(request.work_dir, [finding()])
        stopped[0] = True
        await asyncio.sleep(60)
    e = engine(tmp_path, Provider(run), cancelled=lambda: stopped[0])
    asyncio.run(e.run())
    assert e.stop_reason == 'cancelled'
    assert e.snapshot()['candidates']


def test_source_receipt_is_separate_from_model_claimed_scope(tmp_path):
    tools = SearchTools(work_dir=tmp_path, values={})
    tools._record({'id': 'source1', 'tool': 'literature_search', 'state': 'completed', 'ok': True,
                   'result': {'verification_scope': 'bibliographic_search',
                              'records': [{'url': finding()['url'], 'title': finding()['title']}]}})
    tools.call('save_findings', {'records': [finding(reported_scope='full text')]})
    e = engine(tmp_path)
    e.refresh()
    candidate = e.snapshot()['candidates'][0]
    assert candidate['reported_scope'] == 'full text'
    assert candidate['source_receipts'][0]['scope'] == 'bibliographic'
    assert candidate['evidence'] == []


def test_actual_search_required_even_if_model_supplies_findings(tmp_path):
    class NoSearch(Provider):
        async def execute(self, request, emit):
            autonomous_store.merge(request.work_dir, [finding()])
            return ExecutionOutcome(result_text='done')
    e = engine(tmp_path, NoSearch())
    with pytest.raises(RuntimeError, match='기록이 없습니다'):
        asyncio.run(e.run())


def test_cutoff_filters_display_not_stored_leads(tmp_path):
    e = engine(tmp_path, cutoff='2024-01-01')
    autonomous_store.merge(tmp_path, [finding(1, publication_date='2025-01-01'), finding(2)])
    e.refresh()
    assert len(e.snapshot()['candidates']) == 2
    assert 'Found source 1' not in render(e.snapshot())
    assert 'Found source 2' in render(e.snapshot())


def test_continuation_retains_sources_and_has_fresh_time(tmp_path):
    previous = engine(tmp_path / 'previous')
    previous.directory.mkdir()
    asyncio.run(previous.run())
    checkpoint = previous.checkpoint()
    checkpoint['snapshot']['elapsed_seconds'] = 300
    current = engine(tmp_path / 'next')
    current.directory.mkdir()
    current.restore(checkpoint)
    asyncio.run(current.run())
    assert current.inference.provider.requests[0].timeout_seconds > 0
    assert json.loads(current.inference.provider.requests[0].user_message)['previous_findings']


@pytest.mark.parametrize('error', ['auth', 'service'])
def test_errors_preserve_candidates_but_do_not_report_success(tmp_path, error):
    async def run(request, emit):
        autonomous_store.merge(request.work_dir, [finding()])
        return ExecutionOutcome(auth_required=error == 'auth', errors=['service failed'] if error == 'service' else [])
    e = engine(tmp_path, Provider(run))
    with pytest.raises(RuntimeError):
        asyncio.run(e.run())
    assert e.snapshot()['candidates']
