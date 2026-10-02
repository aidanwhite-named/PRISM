import asyncio
import json

import pytest

from app.config import PATHS, DEFAULTS
from app.dependent_search import parse_draft, prepare_draft
from app.providers.base import ExecutionOutcome, NO_TOOLS
from app.providers.codex_cli import CodexCliProvider
from app.providers.agy_cli import AgyCliProvider

from .conftest import wait_for_job
from .fake_provider import DeterministicTestProvider

CLAIMS = '청구항 1. 카메라로 객체를 검출하고 경고하는 장치.'
DEPENDENT = '청구항 2. 제1항에 있어서, 상기 객체의 이동속도에 따라 경고 임계값을 변경하는 장치.'
FEATURE = '객체의 이동속도에 따라 위험 경고의 임계값을 변경하는 기술.'


def body(**changes):
    return {'job_kind': 'similarity_search', 'provider': 'test-search', 'claim_text': CLAIMS,
        'search_mode': 'dependent', 'dependent_claim_text': DEPENDENT,
        'search_feature_text': FEATURE, **changes}


def test_reviewed_target_is_preserved_in_execution_history_and_report(client):
    edited = FEATURE + ' 속도가 높을수록 경고 임계값을 낮추는 관계.'
    response = client.post('/api/jobs', json=body(search_feature_text=edited))
    assert response.status_code == 201, response.text
    job = wait_for_job(client, response.json()['id'])
    focus = job['search_focus']
    assert focus['origin'] == 'dependent_claims'
    assert focus['components'][0]['feature'] == edited
    assert job['claim_text'] == CLAIMS
    final = client.get(f"/api/jobs/{job['id']}/final-prompt").text
    user = json.loads(final.split('===== USER MESSAGE =====', 1)[1])
    assert user['claim'] == CLAIMS
    assert user['focus']['dependent_claim_text'] == DEPENDENT
    assert user['focus']['components'][0]['feature'] == edited
    assert job['search_manifest']['engine']['search_focus'] == focus
    assert '# 종속항만 따로 검색 결과' in job['result_text']
    assert edited in job['result_text']
    history = client.get(f"/api/history/{job['id']}").json()
    assert history['search_focus'] == focus


@pytest.mark.parametrize('changes', [
    {'dependent_claim_text': ''},
    {'search_mode': 'full'}, {'source_job_id': 'other'}, {'search_component_ids': ['C001']},
])
def test_invalid_or_ambiguous_targets_are_rejected_before_execution(client, changes):
    assert client.post('/api/jobs', json=body(**changes)).status_code == 400


def test_analysis_cannot_silently_ignore_dependent_search_inputs(client):
    assert client.post('/api/jobs', json=body(job_kind='patent_analysis')).status_code == 422


@pytest.mark.parametrize('feature', ['', '   ', None])
def test_search_can_start_from_dependent_claim_without_a_separate_target(client, feature):
    payload = body(search_feature_text=feature)
    if feature is None:
        payload.pop('search_feature_text')
    preflight = client.post('/api/jobs/preflight', json=payload)
    assert preflight.status_code == 200, preflight.text
    response = client.post('/api/jobs', json=payload)
    assert response.status_code == 201, response.text
    job = wait_for_job(client, response.json()['id'])
    assert job['search_focus']['target_source'] == 'dependent_claim'
    assert job['search_focus']['components'][0]['feature'] == DEPENDENT
    final = client.get(f"/api/jobs/{job['id']}/final-prompt").text
    assert '종속항의 추가·한정된 특징을 스스로 구분하여 검색' in final
    user = json.loads(final.split('===== USER MESSAGE =====', 1)[1])
    assert user['focus']['dependent_claim_text'] == DEPENDENT
    assert user['claim'] == CLAIMS
    assert '## 검색할 종속항 원문' in job['result_text']
    assert '## 사용자가 확인한 검색 대상' not in job['result_text']


def test_preflight_counts_the_confirmed_target_and_context(client):
    short = client.post('/api/jobs/preflight', json=body()).json()
    large = client.post('/api/jobs/preflight', json=body(search_feature_text=FEATURE * 100)).json()
    assert large['bytes'] > short['bytes']
    assert large['chars'] > short['chars']
    assert large['delivery_plan'] == 'autonomous_search'


def test_draft_uses_configured_model_without_searching_and_preserves_warnings(client, monkeypatch):
    requests = []
    async def execute(self, request, emit):
        requests.append(request)
        return ExecutionOutcome(result_text=json.dumps({'search_feature_text': FEATURE,
            'explanation': '이동속도와 경고 임계값의 관계를 검색합니다.',
            'warnings': ['인용한 항의 나머지 조건을 확인하세요.']}, ensure_ascii=False),
            exit_code=0, tool_policy=request.tool_policy, usage={'input_tokens': 123})
    monkeypatch.setattr(DeterministicTestProvider, 'execute', execute)
    response = client.post('/api/jobs/dependent-search-draft', json={
        'provider': 'test', 'model': 'chosen-model', 'claim_text': CLAIMS, 'dependent_claim_text': DEPENDENT})
    assert response.status_code == 200, response.text
    draft = response.json()
    assert draft['search_feature_text'] == FEATURE
    assert draft['warnings'] == ['인용한 항의 나머지 조건을 확인하세요.']
    request, = requests
    assert request.model == 'chosen-model' and request.tool_policy == NO_TOOLS
    assert json.loads(request.user_message) == {'claim_text': CLAIMS, 'dependent_claim_text': DEPENDENT}
    stored = json.loads((PATHS.data_dir / 'dependent-search-drafts' / draft['draft_id'] / 'output.json').read_text(encoding='utf-8'))
    assert stored['usage'] == {'input_tokens': 123}


def test_draft_model_failure_does_not_become_a_search_target(client, monkeypatch):
    async def execute(self, request, emit):
        return ExecutionOutcome(result_text='', is_error=True, error_message='model unavailable', exit_code=1)
    monkeypatch.setattr(DeterministicTestProvider, 'execute', execute)
    response = client.post('/api/jobs/dependent-search-draft', json={'provider': 'test', 'dependent_claim_text': DEPENDENT})
    assert response.status_code == 400
    assert 'search_feature_text' not in response.json()


@pytest.mark.parametrize('text', ['not JSON', '``````', '{}', '[]', '{"search_feature_text":" "}'])
def test_invalid_model_drafts_are_not_used(text):
    with pytest.raises(ValueError):
        parse_draft(text)


@pytest.mark.parametrize('provider_type', [CodexCliProvider, AgyCliProvider])
@pytest.mark.parametrize('used_tool', ['', 'web_search'])
def test_real_provider_adapters_support_text_drafts_but_reject_actual_tool_calls(provider_type, used_tool, monkeypatch):
    provider = provider_type()
    assert not provider.supports_tool_policy(NO_TOOLS)
    requests = []
    async def execute(request, emit):
        requests.append(request)
        return ExecutionOutcome(result_text=json.dumps({'search_feature_text': FEATURE}), exit_code=0,
            tools_advertised=['run_command', 'web_search'], tools_uncontrollable=True,
            tool_uses=[used_tool] if used_tool else [],
            tool_calls=[{'name': used_tool}] if used_tool else [])
    monkeypatch.setattr(provider, 'execute', execute)
    run = prepare_draft(provider, dict(DEFAULTS), 'selected-model', CLAIMS, DEPENDENT)
    if used_tool:
        with pytest.raises(ValueError, match='도구가 호출'):
            asyncio.run(run)
    else:
        assert asyncio.run(run)['search_feature_text'] == FEATURE
    request, = requests
    assert request.tool_policy.allowed_tools == ()
    assert request.tool_policy.required_tools == ()
    assert request.model == 'selected-model'
    assert not request.mcp_servers
    if provider.id == 'codex':
        assert 'tools.web_search=false' in provider.build_args(request)
