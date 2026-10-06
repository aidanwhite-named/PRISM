"""Instructions are application code, not user-selectable templates."""
import pytest

from app.task_instructions import ANALYSIS, SEARCH
from app import settings_service
from app.db import session_scope
from app.models import AppSetting


@pytest.mark.parametrize('path', ['', '/catalog', '/export', '/search_prompt.md'])
def test_prompt_library_is_not_exposed(client, path):
    assert client.get('/api/prompts' + path).status_code == 404


@pytest.mark.parametrize('path', ['', '/import'])
def test_prompt_writes_are_not_exposed(client, path):
    assert client.post('/api/prompts' + path, json={'body': 'replace'}).status_code in (404, 405)


@pytest.mark.parametrize('endpoint', ['/api/jobs', '/api/jobs/preflight'])
def test_requests_cannot_replace_instructions(client, endpoint):
    response = client.post(endpoint, json={'prompt_id': 'custom.md'})
    assert response.status_code == 422
    assert '프롬프트 교체는 지원하지 않습니다' in response.text


def test_old_defaults_are_ignored_and_cannot_be_saved(client):
    keys = ('default_prompt_id', 'default_search_prompt_id')
    with session_scope() as session:
        for key in keys:
            session.merge(AppSetting(key=key, value='custom.md'))
        session.commit()
        values = settings_service.get_all(session)
        assert all(key not in values for key in keys)
    try:
        for key in keys:
            response = client.put('/api/settings', json={'values': {key: 'custom.md'}})
            assert response.status_code == 400
    finally:
        with session_scope() as session:
            session.query(AppSetting).filter(AppSetting.key.in_(keys)).delete(synchronize_session=False)
            session.commit()


def test_builtins_are_bound_to_execution():
    from app.api import jobs
    from app import analysis_protocol, structured_report
    assert jobs.ANALYSIS is ANALYSIS
    assert jobs.SEARCH is SEARCH
    assert structured_report.MARKER in analysis_protocol.apply(ANALYSIS.body)
