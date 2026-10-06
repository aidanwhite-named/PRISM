import asyncio

import pytest

from app.search_engine import autonomous_store
from app.search_engine.autonomous import time_limit
from app.search_engine.search_review import has_verified_x, shortlist, verified_group
from app.search_engine.report import render
from app.providers.base import ExecutionOutcome
from .test_autonomous_search import engine, Provider, finding
from .test_search_review import receipt, review


def test_legacy_settings_migrate_to_a_single_bounded_budget():
    assert time_limit({}) == 360
    assert time_limit({'search_timeout_seconds': 300, 'search_verification_seconds': 900}) == 360
    assert time_limit({'search_total_seconds': 75}) == 75
    assert time_limit({'search_total_seconds': 900}) == 900
    assert time_limit({'search_total_seconds': 1200}) == 1200
    assert time_limit({'search_total_seconds': 1800}) == 1200


def test_settings_reject_values_above_stage_caps(client):
    assert client.put('/api/settings', json={'values': {'search_total_seconds': 1201}}).status_code == 400
    assert client.put('/api/settings', json={'values': {'search_timeout_seconds': 241}}).status_code == 400
    assert client.put('/api/settings', json={'values': {'search_verification_seconds': 121}}).status_code == 400


def test_settings_read_clamps_legacy_value_without_rewriting_it(client):
    from app.db import session_scope
    from app.models import AppSetting
    from app import settings_service
    with session_scope() as session:
        row = session.get(AppSetting, 'search_timeout_seconds')
        previous = row.value if row else None
        session.merge(AppSetting(key='search_timeout_seconds', value=300))
        session.commit()
        try:
            assert settings_service.get_all(session)['search_timeout_seconds'] == 240
            assert session.get(AppSetting, 'search_timeout_seconds').value == 300
        finally:
            row = session.get(AppSetting, 'search_timeout_seconds')
            if previous is None:
                session.delete(row)
            else:
                row.value = previous
            session.commit()


def test_search_and_source_review_share_one_run(tmp_path):
    e = engine(tmp_path)
    stages = []
    async def run_round():
        stages.append(e.phase)
        autonomous_store.merge(tmp_path, [finding()])
        e.stop_reason = 'model_complete'
    e._run_round = run_round
    asyncio.run(e.run())
    assert stages == ['searching']
    assert e.phase == 'complete'
    assert e.records


def test_verified_x_does_not_force_cancellation_and_keeps_unreviewed_leads(tmp_path):
    async def action(request, emit):
        tools = receipt(tmp_path)
        tools.call('save_findings', {'records': [finding(review=review(group='X')), finding(2)]})
        return ExecutionOutcome(result_text='후보 확보', exit_code=0)
    provider = Provider(action)
    e = engine(tmp_path, provider)
    asyncio.run(e.run())
    assert e.stop_reason == 'model_complete' and not provider.cancelled
    assert len(provider.requests) == 1
    assert verified_group(e.records[0]) == 'X'
    assert verified_group(e.records[1]) is None
    assert '[X]' in render(e.snapshot())


@pytest.mark.parametrize('defect', ['quote', 'verdict', 'after_cutoff', 'date_unknown'])
def test_invalid_or_date_unconfirmed_x_cannot_trigger_early_stop(tmp_path, defect):
    tools = receipt(tmp_path)
    assessment = review(group='X')
    if defect == 'quote':
        assessment['passages'][0]['quote'] = 'invented quotation'
    if defect == 'verdict':
        assessment['verdict'] = 'partial'
    date = '2025-01-01' if defect == 'after_cutoff' else '' if defect == 'date_unknown' else '2023-01-01'
    tools.call('save_findings', {'records': [finding(publication_date=date, review=assessment)]})
    assert not has_verified_x(autonomous_store.load(tmp_path), '2024-01-01')


def test_shortlist_is_bounded_but_all_leads_are_preserved(tmp_path):
    rows = [finding(i, triage_status='candidate') for i in range(20)]
    autonomous_store.merge(tmp_path, [finding(99, triage_status='rejected'), *rows])
    assert len(shortlist(autonomous_store.load(tmp_path))) == 10
    assert len(autonomous_store.load(tmp_path)) == 21


def test_source_review_allows_gap_driven_search_in_same_run(tmp_path, monkeypatch):
    tools = receipt(tmp_path)
    called = []
    monkeypatch.setattr(tools, '_execute', lambda name, args: called.append(name) or {'records': []})
    tools.call('citation_search', {'identifier': 'US1234567A', 'direction': 'backward'})
    tools.call('source_fetch', {'url': finding()['url']})
    assert called == ['citation_search', 'source_fetch']


def test_group_order_is_xyz_with_unreviewed_separate(tmp_path):
    from app.search_engine.search_review import prioritize
    rows = []
    for number, group in enumerate(('Z', 'Y', 'X'), 1):
        tools = receipt(tmp_path, url=finding(number)['url'])
        tools.call('save_findings', {'records': [finding(number, review=review(
            'partial' if group == 'Z' else 'strong', group=group))]})
    rows = prioritize([finding(99), *autonomous_store.load(tmp_path)])
    assert [verified_group(row) for row in rows] == ['X', 'Y', 'Z', None]
