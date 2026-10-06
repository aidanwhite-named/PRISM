"""Built-in search strategy reaches execution and is preserved in history."""
import json
from app.task_instructions import SEARCH
from .conftest import wait_for_job
from . import fake_provider

CLAIM = '청구항 1. 제1 센서와 제2 센서를 포함하는 장치.'

def run(client):
    response = client.post('/api/jobs', json={
        'job_kind': 'similarity_search', 'provider': 'test-search', 'claim_text': CLAIM,
    })
    assert response.status_code == 201, response.text
    return wait_for_job(client, response.json()['id'])

def test_builtin_strategy_is_delivered_and_snapshotted(client):
    fake_provider.RECEIVED.clear()
    job = run(client)
    assert job['status'] == 'SUCCEEDED', job['errors']
    assert job['prompt_id'] == SEARCH.id
    assert job['prompt_snapshot'] == SEARCH.body
    assert job['search_manifest']['prompt']['template_mode'] == 'structured_input'
    payload = json.loads(fake_provider.RECEIVED[0].user_message)
    assert payload['claim'] == CLAIM
    assert payload['search_strategy'] == SEARCH.body

def test_search_records_remain_auditable_with_builtin_strategy(client):
    fake_provider.RECEIVED.clear()
    job = run(client)
    assert job['status'] == 'SUCCEEDED', job['errors']
    assert job['search_manifest_error'] is None
    assert job['search_manifest']['observed']['search_queries']
    assert any('save_findings' in request.system_prompt for request in fake_provider.RECEIVED)
    saved = client.get(f"/api/history/{job['id']}").json()
    assert saved['prompt_snapshot'] == SEARCH.body
    assert saved['search_manifest'] == job['search_manifest']
