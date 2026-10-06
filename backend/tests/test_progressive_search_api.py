"""Legacy depth selections route to the same autonomous session."""
import pytest
from app.config import DEFAULTS
from .conftest import wait_for_job
from .test_autonomous_search_api import autonomous_runtime

@pytest.fixture(autouse=True)
def configured_total_time(client):
    from app.db import session_scope
    from app.models import AppSetting
    with session_scope() as session:
        row = session.get(AppSetting, 'search_total_seconds')
        old = row.value if row else None
    assert client.put('/api/settings', json={'values': {'search_total_seconds': 75}}).status_code == 200
    yield
    with session_scope() as session:
        row = session.get(AppSetting, 'search_total_seconds')
        if old is None:
            session.delete(row)
        else:
            row.value = old

@pytest.mark.parametrize('depth', ['fast', 'deep', 'exhaustive'])
def test_legacy_depth_uses_configured_total_time_only(client, monkeypatch, autonomous_runtime, depth):
    monkeypatch.setitem(DEFAULTS, 'search_total_seconds', 75)
    payload = {'job_kind': 'similarity_search', 'provider': 'test-search',
               'claim_text': 'Gaussian cloning uses neighbor distance.', 'search_depth': depth}
    ahead = client.post('/api/jobs/preflight', json=payload)
    assert ahead.status_code == 200
    assert '75초' in ahead.json()['message']
    created = client.post('/api/jobs', json=payload)
    assert created.status_code == 201
    job = wait_for_job(client, created.json()['id'])
    assert job['status'] == 'SUCCEEDED', job['errors']
    assert job['search_manifest']['engine']['limits'] == {'seconds': 75}
    assert len(autonomous_runtime) == 1
    assert not hasattr(autonomous_runtime[0].tool_policy, 'max_tool_calls')

def test_invalid_depth_is_rejected(client):
    response = client.post('/api/jobs', json={'job_kind': 'similarity_search', 'provider': 'test-search',
                                            'claim_text': 'A sensor', 'search_depth': 'unlimited'})
    assert response.status_code == 422
