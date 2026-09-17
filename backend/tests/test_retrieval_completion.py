"""The final bounded call reviews sources; it must not produce unseen fetches."""
import asyncio
import json
from dataclasses import replace

import pytest

from app.providers.base import ExecutionOutcome
from app.retrieval.actions import SearchDocument
from app.retrieval.agent import ComponentState
from .test_retrieval_efficiency import agent


@pytest.mark.parametrize('pending', [False, True])
def test_last_round_reviews_returned_sources_without_draining_more(agent, monkeypatch, pending):
    agent.budget = replace(agent.budget, max_rounds=2)
    calls = []
    execute = agent._execute_actions

    async def actions(items, run, round_no):
        assert round_no == 1  # No retrieval may happen after the final model call.
        result = await execute(items, run, round_no)
        if pending:
            agent._enqueue_deferred(SearchDocument(action='search_document', component_id='R001',
                queries=['아직 확인하지 않은 조건']), run=run, round_no=round_no, reason='space')
        return result

    async def model(request, emit):
        payload = json.loads(request.user_message.splitlines()[1])
        calls.append(payload)
        if len(calls) == 1:
            response = {'components': [{'label': '청구항 1 (A)', 'feature': '센서', 'importance': 'high'}],
                        'actions': [{'action': 'search_document', 'component_id': 'R001',
                                     'queries': ['센서', 'sensor', '제어부']}]}
        else:
            assert payload['finalize_only']
            hit = payload['results'][0]['documents'][0]['hits'][0]
            assert hit['text']
            response = {'actions': [{'action': 'finalize_evidence', 'components': [
                {'component_id': 'R001', 'status_claim': 'matched', 'evidence': [
                    {'attachment': 'ATT-01', 'chunk_id': hit['chunk_id'], 'relevance': '센서의 제어 관계'}]}]}]}
        return ExecutionOutcome(result_text=json.dumps(response), exit_code=0)

    monkeypatch.setattr(agent, '_execute_actions', actions)
    monkeypatch.setattr(agent.provider, 'execute', model)
    run = asyncio.run(agent.run())
    assert len(calls) == 2 and run.finalize
    assert bool(run.deferred_pending) == pending
    assert run.budget_exhausted == pending
    assert not run.action_errors


def test_last_round_search_request_is_recorded_but_never_executed(agent, monkeypatch):
    agent.budget = replace(agent.budget, max_rounds=1)

    async def model(request, emit):
        assert json.loads(request.user_message.splitlines()[1])['finalize_only']
        return ExecutionOutcome(exit_code=0, result_text=json.dumps({
            'components': [{'label': '청구항 1 (A)', 'feature': '센서'}],
            'actions': [{'action': 'search_document', 'component_id': 'R001', 'queries': ['센서']}]}))

    async def no_search(*args):
        pytest.fail('There is no remaining model call to inspect this search.')

    monkeypatch.setattr(agent.provider, 'execute', model)
    monkeypatch.setattr(agent, '_execute_actions', no_search)
    run = asyncio.run(agent.run())
    assert run.finalize is None
    assert run.budget_exhausted and len(run.deferred_pending) == 1
    assert not run.exposed_chunks


def test_limited_component_precedes_sufficient_component_with_same_importance(agent):
    for ident, completeness in [('R001', 'sufficient'), ('R002', 'limited')]:
        state = ComponentState(ident, ident, '센서', current_priority='high', search_completeness=completeness)
        state.searched[agent.corpus[0].attachment_id] = object()
        state.hit_chunks['candidate'] = {}
        agent._components[ident] = state
    items = [SearchDocument(action='search_document', component_id=ident, queries=['센서'])
             for ident in agent._components]
    assert [item.component_id for item, _ in agent._scheduled_actions(items)] == ['R002', 'R001']
