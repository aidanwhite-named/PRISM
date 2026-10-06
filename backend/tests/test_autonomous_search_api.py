import asyncio
import json

import pytest

from app.config import DEFAULTS
from app.providers.base import ExecutionOutcome
from app.search_engine import autonomous_store
from .conftest import wait_for_job
from .fake_provider import DeterministicSearchProvider
from .test_autonomous_search import finding


@pytest.fixture
def autonomous_runtime(monkeypatch):
    requests = []
    async def execute(self, request, emit):
        requests.append(request)
        autonomous_store.merge(request.work_dir, [finding()])
        await emit('tool_use', {'name': 'WebSearch', 'query': 'similar patent'})
        await asyncio.sleep(.05)
        return ExecutionOutcome(result_text='유사한 문헌을 확인했습니다.', exit_code=0,
                                tool_calls=[{'name': 'WebSearch'}], usage={'input_tokens': 10})
    monkeypatch.setattr(DeterministicSearchProvider, 'execute', execute)
    return requests


@pytest.mark.parametrize('old_flag', [True, False])
def test_new_searches_refine_unchecked_findings_in_same_job(client, monkeypatch, autonomous_runtime, old_flag):
    monkeypatch.setitem(DEFAULTS, 'progressive_search_enabled', old_flag)
    monkeypatch.setitem(DEFAULTS, 'progressive_search_limits', {'deep': {'queries': 1, 'llm_calls': 1, 'pool': 1}})
    payload = {'job_kind': 'similarity_search', 'provider': 'test-search', 'claim_text': 'A controls B using C'}
    ahead = client.post('/api/jobs/preflight', json=payload)
    assert ahead.status_code == 200, ahead.text
    assert '360초' in ahead.json()['message']
    created = client.post('/api/jobs', json=payload)
    assert created.status_code == 201, created.text
    job = wait_for_job(client, created.json()['id'])
    assert job['status'] == 'SUCCEEDED', job['errors']
    assert len(autonomous_runtime) == 1
    request = autonomous_runtime[0]
    assert json.loads(request.user_message)['claim'] == payload['claim_text']
    assert not hasattr(request.tool_policy, 'max_tool_calls')
    assert job['search_manifest']['engine']['mode'] == 'autonomous'
    assert job['search_manifest']['reported']['candidates'][0]['title'] == finding()['title']
    assert job['search_manifest']['status'] == 'verification_incomplete'
    assert 'X분류' not in job['result_text']


def test_session_timeout_preserves_partial_findings(client, monkeypatch, autonomous_runtime):
    original = DeterministicSearchProvider.execute
    async def execute(self, request, emit):
        result = await original(self, request, emit)
        result.timed_out = True
        return result
    monkeypatch.setattr(DeterministicSearchProvider, 'execute', execute)
    created = client.post('/api/jobs', json={'job_kind': 'similarity_search', 'provider': 'test-search',
                                           'claim_text': 'A controls B'}).json()
    job = wait_for_job(client, created['id'])
    assert len(autonomous_runtime) == 1
    assert job['status'] == 'SUCCEEDED', job['errors']
    assert job['search_manifest']['status'] == 'incomplete'
    assert job['search_manifest']['engine']['stop_reason'] == 'deadline'
    assert job['search_manifest']['engine']['candidates']


def test_historical_scope_is_refreshed_on_read_without_rewriting_saved_report(client, autonomous_runtime):
    from app.db import session_scope
    from app.models import ExecutionJob
    from .test_search_request_regressions import receipt
    created = client.post('/api/jobs', json={'job_kind': 'similarity_search', 'provider': 'test-search',
                                           'claim_text': 'A controls B'}).json()
    completed = wait_for_job(client, created['id'])
    assert completed['status'] == 'SUCCEEDED'
    with session_scope() as session:
        job = session.get(ExecutionJob, created['id'])
        stored = job.search_manifest
        candidate = {**stored['engine']['candidates'][0], 'document_number': '10.1234/example',
                     'reported_scope': '서지정보만 확인'}
        candidate.pop('observed_scope', None)
        candidate.pop('observed_scopes', None)
        stored = {**stored, 'engine': {**stored['engine'], 'candidates': [candidate],
                                      'source_calls': [receipt({'abstract': 'Actual abstract'})]}}
        job.search_manifest = stored
        session.commit()
    refreshed = client.get('/api/jobs/' + created['id']).json()
    assert refreshed['search_manifest']['engine']['candidates'][0]['observed_scopes'] == ['abstract']
    assert '프로그램이 확보한 자료: 초록 확보' in refreshed['result_text']
    with session_scope() as session:
        saved = session.get(ExecutionJob, created['id']).search_manifest['engine']['candidates'][0]
        assert 'observed_scope' not in saved
        assert saved['reported_scope'] == '서지정보만 확인'


def test_selected_strategy_is_delivered_and_continuation_keeps_findings(client, autonomous_runtime):
    from app.task_instructions import SEARCH
    created = client.post('/api/jobs', json={'job_kind': 'similarity_search', 'provider': 'test-search',
        'claim_text': 'A controls B'}).json()
    source = wait_for_job(client, created['id'])
    assert source['status'] == 'SUCCEEDED', source['errors']
    assert json.loads(autonomous_runtime[0].user_message)['search_strategy'] == SEARCH.body
    previous_count = len(autonomous_runtime)
    response = client.post(f"/api/jobs/{source['id']}/continue-search")
    assert response.status_code == 201, response.text
    child = wait_for_job(client, response.json()['id'])
    assert child['status'] == 'SUCCEEDED', child['errors']
    assert json.loads(autonomous_runtime[previous_count].user_message)['previous_findings']
    assert client.post(f"/api/jobs/{source['id']}/continue-search").json()['id'] == child['id']
    assert child['search_manifest']['engine']['can_continue']


def test_time_setting_is_the_only_search_budget_in_preflight(client):
    from app.db import session_scope
    from app.models import AppSetting
    key = 'search_total_seconds'
    with session_scope() as session:
        old = session.get(AppSetting, key)
        old_value = old.value if old else None
    try:
        assert client.put('/api/settings', json={'values': {key: 75}}).status_code == 200
        ahead = client.post('/api/jobs/preflight', json={'job_kind': 'similarity_search', 'provider': 'test-search', 'claim_text': 'A controls B'})
        assert '75초' in ahead.json()['message']
        assert client.put('/api/settings', json={'values': {key: 0}}).status_code == 400
    finally:
        with session_scope() as session:
            row = session.get(AppSetting, key)
            if row and old_value is None:
                session.delete(row)
            elif row:
                row.value = old_value
