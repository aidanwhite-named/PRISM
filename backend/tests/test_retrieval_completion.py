"""The final bounded call reviews sources; it must not produce unseen fetches."""
import asyncio
import json
from dataclasses import replace

import pytest

from app.providers.base import ExecutionOutcome
from app.retrieval.actions import ActionError, SearchDocument, parse_response
from app.enums import ErrorCode
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
    assert run.error_code == ErrorCode.RETRIEVAL_FAILED
    assert len(run.rounds) == 2  # One correction attempt, with no more searches.
    assert run.budget_exhausted and len(run.deferred_pending) == 1
    assert not run.exposed_chunks


def test_misplaced_finalization_is_not_silently_parsed_as_declaration():
    response = {'components': [{'component_id': 'R001', 'status_claim': 'matched',
                               'evidence': [{'attachment': 'ATT-01', 'chunk_id': 'P0001-001'}]}]}
    with pytest.raises(ActionError, match='finalize_evidence'):
        parse_response(json.dumps(response))


@pytest.mark.parametrize('failure', ['misplaced', 'missing', 'bad_json', 'incomplete'])
@pytest.mark.parametrize('repaired', [True, False])
def test_finalization_repair_preserves_sources_and_is_bounded(agent, monkeypatch, failure, repaired):
    agent.budget = replace(agent.budget, max_rounds=2)
    calls = []
    execute = agent._execute_actions
    finalization = None

    async def actions(items, run, round_no):
        assert round_no == 1  # Neither finalization call can execute new searches.
        return await execute(items, run, round_no)

    async def model(request, emit):
        nonlocal finalization
        payload = json.loads(request.user_message.splitlines()[1])
        calls.append(payload)
        if len(calls) == 1:
            response = {'components': [{'label': '청구항 2 (A)', 'feature': '센서'}],
                        'actions': [{'action': 'search_document', 'component_id': 'R001',
                                     'queries': ['센서', 'sensor', '제어부']}]}
        elif len(calls) == 2:
            hit = payload['results'][0]['documents'][0]['hits'][0]
            finalization = {'action': 'finalize_evidence', 'components': [
                {'component_id': 'R001', 'status_claim': 'matched', 'evidence': [
                    {'attachment': 'ATT-01', 'chunk_id': hit['chunk_id'], 'relevance': '센서'}]}]}
            response = {
                'misplaced': {'components': finalization['components']},
                'missing': {'notes': '검토 완료'},
                'incomplete': {'actions': [{'action': 'finalize_evidence', 'components': []}]},
                'bad_json': None,
            }[failure]
        else:
            assert len(calls) == 3
            assert payload['finalize_only'] and payload['finalization_repair']
            assert payload['budget']['rounds_remaining'] == 0
            assert payload['previous_error'] and payload['previous_response']
            assert payload['results'] == calls[1]['results']
            response = {'actions': [finalization]} if repaired else {'notes': '검토 완료'}
        return ExecutionOutcome(exit_code=0, result_text=json.dumps(response) if response else 'bad json')

    monkeypatch.setattr(agent, '_execute_actions', actions)
    monkeypatch.setattr(agent.provider, 'execute', model)
    run = asyncio.run(agent.run())
    assert len(calls) == 3
    if repaired:
        assert not run.error_code
        assert run.finalize.components[0].evidence[0].chunk_id == finalization['components'][0]['evidence'][0]['chunk_id']
    else:
        assert run.error_code == ErrorCode.RETRIEVAL_FAILED
        assert run.finalize is None
    assert all(record.attempts for record in run.rounds)


def test_limited_component_precedes_sufficient_component_with_same_importance(agent):
    for ident, completeness in [('R001', 'sufficient'), ('R002', 'limited')]:
        state = ComponentState(ident, ident, '센서', current_priority='high', search_completeness=completeness)
        state.searched[agent.corpus[0].attachment_id] = object()
        state.hit_chunks['candidate'] = {}
        agent._components[ident] = state
    items = [SearchDocument(action='search_document', component_id=ident, queries=['센서'])
             for ident in agent._components]
    assert [item.component_id for item, _ in agent._scheduled_actions(items)] == ['R002', 'R001']
