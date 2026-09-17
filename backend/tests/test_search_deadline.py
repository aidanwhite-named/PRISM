import json
import time

import pytest

from app import search_deadline as deadline, search_agent_tools, search_manifest
from app.providers.base import ExecutionOutcome, ExecutionRequest, Provider, WEB_SEARCH, NO_TOOLS
from app.search_mcp_server import SearchTools


def report():
    return {'candidates': [{'doc_number': 'JP7475618B1', 'group': None, 'note': '초기 후보'},
                           {'doc_number': 'WO2012111622A1', 'group': None, 'note': '초기 후보'}]}


def assessment(number='JP7475618B1', group='A', status='classified'):
    return {'candidate_id': 'patent:' + number, 'group': group, 'status': status,
            'reason': '제공된 청구항과 보존 본문의 회전 파라미터 결합 관계를 비교한 판단.', 'mapping': []}


def test_time_is_reserved_inside_total_budget():
    value = deadline.allocation(300)
    assert value == {'total_seconds': 300, 'search_seconds': 195,
                     'classification_reserve_seconds': 100, 'save_reserve_seconds': 5}
    for seconds in (30, 60, 120, 300, 900):
        value = deadline.allocation(seconds)
        assert sum(value[k] for k in ('search_seconds', 'classification_reserve_seconds', 'save_reserve_seconds')) == seconds


def test_model_groups_and_insufficient_information_remain_distinct():
    text = json.dumps({'assessments': [assessment(), assessment('WO2012111622A1', None, 'insufficient_information')]})
    updated, missing = deadline.apply_assessments(report(), text)
    assert not missing
    assert [c['group'] for c in updated['candidates']] == ['A', None]
    assert updated['candidates'][1]['note'].startswith('자료 부족:')


@pytest.mark.parametrize('rows', [
    [assessment('JP9999999B1')], [assessment(), assessment()], [assessment(group=None)],
    [assessment(group='A', status='low_relevance')],
])
def test_unknown_duplicate_or_inconsistent_assessments_are_rejected(rows):
    with pytest.raises(ValueError):
        deadline.apply_assessments(report(), json.dumps({'assessments': rows}))


def test_missing_assessments_keep_good_rows_without_claiming_completion():
    updated, missing = deadline.apply_assessments(report(), json.dumps({'assessments': [assessment()]}))
    assert updated['candidates'][0]['group'] == 'A'
    assert updated['candidates'][1]['group'] is None
    assert missing == ['patent:WO2012111622A1']


def test_usage_preserves_partial_status_and_does_not_sum_boolean_flags():
    usage = deadline.merge_usage({'input_tokens': 50, 'usage_complete': False}, {'input_tokens': 30, 'output_tokens': 5})
    assert usage['input_tokens'] == 80
    assert usage['usage_complete'] is False
    assert len(usage['stages']) == 2


def test_native_run_metrics_count_model_groups_and_deferred_documents():
    from app.search_engine.evaluation import metrics
    data = {'reported': {'candidates': [{'group': 'C', 'mapping': [{'quote_verified': True}]},
                                       {'group': None, 'note': '자료 부족: 본문 없음'},
                                       {'group': None, 'note': '관련성 낮음: 다른 분야'}]},
            'started_at': '2026-09-17T06:00:00', 'completed_at': '2026-09-17T06:04:00',
            'deadline_classification': {'completed': True}}
    result = metrics(data, {'id': 'test', 'known_relevant': []})
    assert result['classified_documents'] == 1
    assert result['insufficient_information_documents'] == 1
    assert result['low_relevance_documents'] == 1
    assert result['relation_rows'] == result['verified_evidence_rows'] == 1
    assert result['elapsed_seconds'] == 240


class Finisher:
    max_input_bytes = 180000
    payload_bytes = Provider.payload_bytes

    def __init__(self, output=None):
        self.requests = []
        self.output = output or ExecutionOutcome(exit_code=0, tool_policy=NO_TOOLS,
            result_text=json.dumps({'assessments': [assessment(), assessment('WO2012111622A1', None, 'insufficient_information')],
                                    'stop_reason': '탐색 마감 후 보존 자료를 평가함.'}))

    async def execute(self, request, emit):
        self.requests.append(request)
        return self.output


async def noop(*args):
    pass


async def test_soft_search_timeout_finishes_with_classification_in_remaining_time(tmp_path):
    SearchTools(values={}, work_dir=tmp_path).call('save_candidates', {'report': report()})
    initial = ExecutionOutcome(timed_out=True, tool_policy=WEB_SEARCH, tool_uses=['WebSearch'],
                               tool_calls=[{'name': 'WebSearch', 'input': {'query': 'joint limits'}, 'ok': True}])
    request = ExecutionRequest(job_id='x', work_dir=tmp_path, system_prompt='', user_message='', timeout_seconds=195,
                               tool_policy=WEB_SEARCH)
    provider = Finisher()
    merged, audit = await deadline.finish(provider, request, initial, noop, claim='청구항',
                                         deadline=time.monotonic() + 100, cancelled=lambda: False)
    assert audit['completed'] and audit['search_timed_out']
    assert not merged.timed_out
    assert merged.tool_calls == initial.tool_calls
    assert len(provider.requests) == 1
    final_request = provider.requests[0]
    assert final_request.tool_policy == NO_TOOLS and not final_request.mcp_servers
    assert 90 <= final_request.timeout_seconds <= 95
    assert json.loads(merged.result_text)['candidates'][0]['group'] == 'A'
    assert search_agent_tools.load_checkpoint(tmp_path)['candidates'][0]['group'] == 'A'


@pytest.mark.parametrize('cancelled,remaining', [(True, 100), (False, 3)])
async def test_cancellation_and_overall_deadline_do_not_start_new_model(tmp_path, cancelled, remaining):
    SearchTools(values={}, work_dir=tmp_path).call('save_candidates', {'report': report()})
    provider = Finisher()
    initial = ExecutionOutcome(timed_out=True, tool_uses=['WebSearch'])
    request = ExecutionRequest(job_id='x', work_dir=tmp_path, system_prompt='', user_message='')
    merged, audit = await deadline.finish(provider, request, initial, noop, claim='청구항',
        deadline=time.monotonic() + remaining, cancelled=lambda: cancelled)
    assert merged.timed_out and not audit['attempted']
    assert not provider.requests


def test_mcp_deadline_closes_retrieval_without_closing_save(tmp_path, monkeypatch):
    from app import search_mcp_server
    monkeypatch.setattr(search_mcp_server.time, 'time', lambda: 100)
    (tmp_path / 'search_deadline.json').write_text('105', encoding='utf-8')
    tools = SearchTools(values={}, work_dir=tmp_path)
    result = tools.call('source_fetch', {'url': 'https://example.com/source'})
    assert result['budget_stopped'] and result['not_evidence_of_absence']
    tools.call('save_candidates', {'report': report()})
    assert search_agent_tools.load_checkpoint(tmp_path)['candidates']


@pytest.mark.usefixtures('legacy_search')
def test_runner_soft_deadline_is_success_after_model_classifies(client, monkeypatch):
    from .fake_provider import DeterministicSearchProvider
    from .conftest import wait_for_job
    calls = []
    original = DeterministicSearchProvider.execute
    async def execute(self, request, emit):
        calls.append(request)
        if request.tool_policy.name == 'no_tools':
            return Finisher().output
        outcome = await original(self, request, emit)
        SearchTools(values={}, work_dir=request.work_dir).call('save_candidates', {'report': report()})
        outcome.result_text = ''
        outcome.timed_out = True
        return outcome
    monkeypatch.setattr(DeterministicSearchProvider, 'execute', execute)
    created = client.post('/api/jobs', json={'job_kind': 'similarity_search', 'provider': 'test-search',
                                          'claim_text': '청구항 1. 회전 파라미터 한계를 보정하는 방법'}).json()
    job = wait_for_job(client, created['id'])
    assert job['status'] == 'SUCCEEDED', job['errors']
    assert calls[0].timeout_seconds == 195
    assert calls[1].tool_policy == NO_TOOLS
    manifest = job['search_manifest']
    assert manifest['deadline_classification']['completed']
    assert manifest['reported']['candidates'][0]['group'] == 'A'
    assert manifest['reported']['candidates'][1]['note'].startswith('자료 부족:')
