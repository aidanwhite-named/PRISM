import json

import pytest

from app.config import PATHS, DEFAULTS
from app.patent_search.artifacts import ArtifactStore
from .conftest import wait_for_job


@pytest.fixture
def progressive_runtime(monkeypatch):
    calls = []
    monkeypatch.setitem(DEFAULTS, 'progressive_search_enabled', True)
    monkeypatch.setitem(DEFAULTS, 'progressive_search_web_enabled', False)

    class Sources:
        def __init__(self, *args, **kwargs): pass
        def search(self, source, query, **kwargs):
            text = 'Gaussian cloning uses neighbor distance to make the cloning decision. ' * 8
            aid = ArtifactStore(PATHS.evidence_dir).put(text.encode('utf-8'))
            return {'records': [{'document_number': '10.1234/test', 'title': 'Gaussian distance cloning',
                'url': '', 'publication_date': '2024-01-01', 'fields': {'abstract': text},
                'evidence_refs': {'abstract': {'artifact_id': aid}}}]}

    class Inference:
        def __init__(self, provider, **kwargs):
            self.provider, self.model, self.last_outcome = provider, None, None
        def usage(self): return {'input_tokens': 10, 'usage_complete': True, 'stages': []}
        async def call(self, phase, system, payload, **kwargs):
            calls.append((phase, payload))
            if phase == 'plan':
                return {'features': [{'text': payload['claim'], 'terms': ['Gaussian', 'distance', 'cloning'],
                                      'queries': ['Gaussian cloning distance']}], 'context_query': ''}
            if phase == 'verify':
                p = payload['passages'][0]
                return {'evidence': [{'candidate_id': p['candidate_id'], 'feature': p['feature'],
                    'match': 'explicit', 'passage_id': p['id'],
                    'quote': 'Gaussian cloning uses neighbor distance to make the cloning decision.',
                    'relation': '거리 -> 복제 판단'}], 'classifications': [
                        {'candidate_id': p['candidate_id'], 'group': 'B', 'reason': '핵심 관계 유사'}]}
            return {'candidate_ids': []}

    monkeypatch.setattr('app.search_engine.engine.Sources', Sources)
    monkeypatch.setattr('app.search_engine.job.Inference', Inference)
    return calls


@pytest.mark.parametrize('depth,seconds', [('fast', 45), ('deep', 120), ('exhaustive', 300)])
def test_default_api_runs_progressive_engine_and_persists_evidence(client, progressive_runtime, depth, seconds):
    payload = {'job_kind': 'similarity_search', 'provider': 'test-search',
               'claim_text': 'Gaussian cloning uses neighbor distance.', 'search_depth': depth}
    ahead = client.post('/api/jobs/preflight', json=payload)
    assert ahead.status_code == 200, ahead.text
    assert ahead.json()['delivery_plan'] == 'progressive_search'
    assert str(seconds) in ahead.json()['message']
    created = client.post('/api/jobs', json=payload)
    assert created.status_code == 201, created.text
    assert created.json()['prompt_id'] == 'search_prompt.md'
    job = wait_for_job(client, created.json()['id'])
    assert job['status'] == 'SUCCEEDED', job['errors']
    data = job['search_manifest']['engine']
    assert data['limits']['seconds'] == seconds
    assert data['candidates'][0]['evidence'][0]['quote_verified']
    assert data['candidates'][0]['data_status'] == 'ABSTRACT_ONLY'
    assert data['candidates'][0]['document_classification']['group'] == 'Y'
    assert data['candidates'][0]['evidence'][0]['feature'] == 'A'
    assert job['search_manifest']['reported']['candidates'][0]['group'] == 'Y'
    assert '문헌 분류 Y' in job['result_text']
    assert '거리' in job['result_text']
    assert client.get(f"/api/jobs/{job['id']}/final-prompt").status_code == 200


def test_selected_strategy_and_original_claim_reach_planner_and_verifier(client, progressive_runtime):
    strategy = '핵심 관계를 중시하고 문헌을 A/B/C로 분류해줘.'
    prompt = client.post('/api/prompts', json={'name': '분류 회귀', 'body': strategy, 'kind': 'search'}).json()
    claim = 'Gaussian cloning uses neighbor distance.'
    try:
        created = client.post('/api/jobs', json={'job_kind': 'similarity_search', 'provider': 'test-search',
            'prompt_id': prompt['id'], 'claim_text': claim, 'search_depth': 'fast'})
        assert created.status_code == 201, created.text
        assert created.json()['prompt_id'] == prompt['id']
        job = wait_for_job(client, created.json()['id'])
        assert job['status'] == 'SUCCEEDED', job['errors']
        assert job['search_manifest']['prompt']['id'] == prompt['id']
        import hashlib
        assert job['search_manifest']['prompt']['sha256'] == hashlib.sha256(strategy.encode('utf-8')).hexdigest()
        phases = [phase for phase, _ in progressive_runtime]
        assert phases.count('verify') == 1
        assert all(phase in ('plan', 'triage', 'verify') for phase in phases)
        for phase, payload in progressive_runtime:
            if phase in ('plan', 'verify'):
                assert payload['search_strategy'] == strategy
                assert payload['claim'] == claim
    finally:
        client.delete('/api/prompts/' + prompt['id'])


def test_progressive_api_keeps_unknown_dates_and_excludes_future(client, progressive_runtime):
    created = client.post('/api/jobs', json={'job_kind': 'similarity_search', 'provider': 'test-search',
        'claim_text': 'Gaussian cloning uses neighbor distance.', 'search_depth': 'fast', 'search_cutoff_date': '2023-01-01'})
    job = wait_for_job(client, created.json()['id'])
    assert job['search_manifest']['engine']['candidates'][0]['date_status'] == 'after_cutoff'
    assert not job['search_manifest']['reported']['candidates']


def test_invalid_depth_is_rejected(client):
    response = client.post('/api/jobs', json={'job_kind': 'similarity_search', 'provider': 'test-search',
                                            'claim_text': 'A sensor', 'search_depth': 'unlimited'})
    assert response.status_code == 422


def test_progressive_limits_can_be_configured_through_settings(client):
    from app.db import session_scope
    from app.models import AppSetting
    keys = ('progressive_search_enabled', 'progressive_search_web_enabled', 'progressive_search_limits')
    with session_scope() as session:
        previous = {key: session.get(AppSetting, key).value if session.get(AppSetting, key) else None for key in keys}
    try:
        response = client.put('/api/settings', json={'values': {
            'progressive_search_enabled': True, 'progressive_search_web_enabled': False,
            'progressive_search_limits': {'fast': {'seconds': 60, 'queries': 8}}}})
        assert response.status_code == 200, response.text
        ahead = client.post('/api/jobs/preflight', json={'job_kind': 'similarity_search',
            'provider': 'test-search', 'claim_text': 'A sensor.', 'search_depth': 'fast'})
        assert '60' in ahead.json()['message'] and '8' in ahead.json()['message']
        bad = client.put('/api/settings', json={'values': {'progressive_search_limits': {'fast': {'seconds': 99999}}}})
        assert bad.status_code == 400
    finally:
        with session_scope() as session:
            for key, value in previous.items():
                row = session.get(AppSetting, key)
                if row and value is None:
                    session.delete(row)
                elif row:
                    row.value = value
