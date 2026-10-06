"""Incremental evidence selection reduces resending, without inventing review."""
import asyncio
import copy
import json
from dataclasses import replace

import pytest

from app.providers.base import ExecutionOutcome
from app.retrieval.actions import AgentResponse, FinalizeEvidence, SearchDocument
from app.retrieval.agent import RetrievalRun, json_size
from app.retrieval.evidence import EvidenceBuilder
from .test_retrieval_efficiency import agent
from .test_reviewed_evidence_retention import setup_read


def selection(key, chunk_id):
    return {'component_id': key, 'status_claim': 'matched', 'searched_terms': ['센서'],
            'evidence': [{'attachment': 'ATT-01', 'chunk_id': chunk_id, 'relevance': '센서 후보'}]}


def prepared(agent):
    state, row, request = setup_read(agent)
    run = RetrievalRun()
    asyncio.run(agent._execute_actions([SearchDocument(action='search_document',
        component_id=state.id, queries=['센서']), request], run, 1))
    return run, state, row, request


def save(agent, run, rows, final=False):
    response = AgentResponse.model_validate({'selected_components': rows})
    return agent._save_selections(response, run, 2, final)


def test_saved_component_originals_leave_prompt_but_survive_in_final_evidence(agent):
    run, state, row, _ = prepared(agent)
    before = agent._round_payload(2, agent._retained_results(), '')
    assert not save(agent, run, [selection(state.id, row.chunk_id)])
    after = agent._round_payload(3, agent._retained_results(), '')
    assert not after['components'] and 'results' not in after
    assert after['saved_components'][0]['evidence_count'] == 1
    assert json_size(after) < json_size(before)
    run.finalize = FinalizeEvidence(action='finalize_evidence',
                                   components=list(agent._selected_components.values()))
    builder = EvidenceBuilder(corpus=agent.corpus, run=run, budget=agent.budget,
        claim_text='센서', semantic={}, capabilities={}, library_versions={})
    finding, error = builder._resolve(run.finalize.components[0].evidence[0], state)
    assert not error and finding['source_text'] == row.text


def test_shared_page_reference_is_rebuilt_after_first_owner_is_saved(agent):
    run, state, row, _ = prepared(agent)
    _, _, second = setup_read(agent, 'R002')
    asyncio.run(agent._execute_actions([second], run, 2))
    assert not save(agent, run, [selection(state.id, row.chunk_id)])
    result = asyncio.run(agent._execute_actions([], run, 3))
    assert {entry['component_id'] for entry in result} == {'R002'}
    hit = result[0]['documents'][0]['hits'][0]
    assert hit['text'] == row.text and 'text_ref' not in hit


@pytest.mark.parametrize('failure', ['unknown', 'duplicate', 'unseen', 'invented', 'page', 'empty_match'])
def test_invalid_selections_are_rejected_atomically(agent, failure):
    run, state, row, _ = prepared(agent)
    good = selection(state.id, row.chunk_id)
    bad = copy.deepcopy(good)
    if failure == 'unknown':
        bad['component_id'] = 'R099'
    elif failure == 'unseen':
        run.exposed_chunks.clear()
    elif failure == 'invented':
        bad['evidence'][0]['chunk_id'] = 'P9999-001'
    elif failure == 'page':
        bad['evidence'][0]['page'] = 999
    elif failure == 'empty_match':
        bad['evidence'] = []
    assert save(agent, run, [good, bad] if failure == 'duplicate' else [bad])
    assert not agent._selected_components and not run.selection_events


def test_unsearched_documents_cannot_be_silently_skipped(agent):
    run, state, row, _ = prepared(agent)
    state.searched.clear()
    assert save(agent, run, [selection(state.id, row.chunk_id)])
    # Last round can preserve actual evidence while the existing coverage
    # validator records the unsearched scope. It cannot invent an absence.
    assert not save(agent, run, [selection(state.id, row.chunk_id)], final=True)
    assert not state.searched


def test_saved_deferred_is_paused_and_remains_a_scope_limit(agent):
    run, state, row, _ = prepared(agent)
    pending = SearchDocument(action='search_document', component_id=state.id, queries=['추가 조건'])
    agent._enqueue_deferred(pending, run=run, round_no=1, reason='space')
    assert not save(agent, run, [selection(state.id, row.chunk_id)])
    before = len(state.search_attempts)
    asyncio.run(agent._execute_actions([], run, 3))
    assert len(state.search_attempts) == before
    assert run.deferred_pending and run.budget_exhausted
    assert agent._deferred_preview()['paused_for_selected_components'] == 1


def test_reopening_does_not_restore_an_oversized_set_of_old_reads(agent):
    run, state, row, _ = prepared(agent)
    assert not save(agent, run, [selection(state.id, row.chunk_id)])
    _, _, request = setup_read(agent, 'R002')
    asyncio.run(agent._execute_actions([request], run, 2))
    active_size = sum(json_size(entry) for entry in agent._retained_results())
    agent.budget = replace(agent.budget, max_round_result_chars=active_size)
    agent._reopen_selection(state.id)
    # Only explicit re-reads of reopened sources enter the budget again.
    assert sum(json_size(entry) for entry in agent._retained_results()) == active_size
    result = asyncio.run(agent._execute_actions([], run, 3))
    assert sum(json_size(entry) for entry in result) <= agent.budget.max_round_result_chars


def test_saved_selection_cannot_be_overwritten_without_reopening(agent):
    run, state, row, request = prepared(agent)
    original = selection(state.id, row.chunk_id)
    assert not save(agent, run, [original])
    changed = {**original, 'note': 'changed without re-reading'}
    assert save(agent, run, [changed])
    finalized = FinalizeEvidence.model_validate({'action': 'finalize_evidence', 'components': [changed]})
    assert agent._finalize_problem(finalized)
    response = AgentResponse.model_validate({'selected_components': [original],
                                             'actions': [request.model_dump()]})
    assert agent._save_selections(response, run, 3, False)


def test_invalid_selection_on_last_round_has_one_source_preserving_repair(agent, monkeypatch):
    agent.budget = replace(agent.budget, max_rounds=2)
    calls = []
    chosen = None

    async def model(request, emit):
        nonlocal chosen
        payload = json.loads(request.user_message.splitlines()[1])
        calls.append(payload)
        if len(calls) == 1:
            result = {'components': [{'label': 'A', 'feature': '센서'}], 'actions': [
                {'action': 'search_document', 'component_id': 'R001', 'queries': ['센서']}]}
        elif len(calls) == 2:
            chosen = selection('R001', payload['results'][0]['documents'][0]['hits'][0]['chunk_id'])
            result = {'selected_components': [selection('R001', 'invented')]}
        else:
            assert len(calls) == 3 and payload['finalization_repair']
            assert payload['results'] == calls[1]['results']
            result = {'selected_components': [chosen]}
        return ExecutionOutcome(exit_code=0, result_text=json.dumps(result))

    monkeypatch.setattr(agent.provider, 'execute', model)
    run = asyncio.run(agent.run())
    assert not run.error and len(run.finalize.components) == 1
    assert len(run.action_errors) == 1


def test_all_selections_finish_early_with_unreviewed_queue_still_visible(agent, monkeypatch):
    calls = []
    execute = agent._execute_actions

    async def actions(items, run, round_no):
        assert round_no == 1
        result = await execute(items, run, round_no)
        agent._enqueue_deferred(SearchDocument(action='search_document', component_id='R001',
            queries=['미확인 조건']), run=run, round_no=1, reason='space')
        return result

    async def model(request, emit):
        payload = json.loads(request.user_message.splitlines()[1])
        calls.append(payload)
        if len(calls) == 1:
            result = {'components': [{'label': 'A', 'feature': '센서', 'importance': 'high'}],
                      'actions': [{'action': 'search_document', 'component_id': 'R001',
                                   'queries': ['센서']}]}
        else:
            assert len(calls) == 2 and not payload['finalize_only']
            chosen = selection('R001', payload['results'][0]['documents'][0]['hits'][0]['chunk_id'])
            chosen['note'] = '미확인 조건은 검토 범위 밖'
            result = {'selected_components': [chosen]}
        return ExecutionOutcome(exit_code=0, result_text=json.dumps(result))

    monkeypatch.setattr(agent, '_execute_actions', actions)
    monkeypatch.setattr(agent.provider, 'execute', model)
    run = asyncio.run(agent.run())
    assert run.finalize and len(calls) == 2 and not run.error
    assert run.deferred_pending and run.budget_exhausted
    assert run.finalize.components[0].note == '미확인 조건은 검토 범위 밖'


@pytest.mark.parametrize('legacy_finish', [False, True])
def test_incremental_selection_finishes_without_extra_confirmation(agent, monkeypatch, legacy_finish):
    calls = []
    chosen = {}

    async def model(request, emit):
        payload = json.loads(request.user_message.splitlines()[1])
        calls.append(payload)
        if len(calls) == 1:
            result = {'components': [{'label': key, 'feature': '센서'} for key in ['A', 'B']],
                      'actions': [{'action': 'search_document', 'component_id': key,
                                   'queries': ['센서']} for key in ['R001', 'R002']]}
        elif len(calls) == 2:
            for entry in payload['results']:
                chosen[entry['component_id']] = selection(entry['component_id'],
                    entry['documents'][0]['hits'][0]['chunk_id'])
            result = {'selected_components': [chosen['R001']], 'actions': [
                {'action': 'read_chunk', 'component_id': 'R002', 'attachment': 'ATT-01',
                 'chunk_id': chosen['R002']['evidence'][0]['chunk_id']}]}
        else:
            assert len(calls) == 3
            assert [row['id'] for row in payload['components']] == ['R002']
            assert payload['saved_components'][0]['id'] == 'R001'
            assert all(row.get('component_id') != 'R001' for row in payload['results'])
            result = ({'actions': [{'action': 'finalize_evidence', 'components': [chosen['R002']]}]}
                      if legacy_finish else {'selected_components': [chosen['R002']]})
        return ExecutionOutcome(exit_code=0, result_text=json.dumps(result))

    monkeypatch.setattr(agent.provider, 'execute', model)
    run = asyncio.run(agent.run())
    assert not run.error and len(calls) == 3
    assert {row.component_id: row.model_dump() for row in run.finalize.components} == {
        key: AgentResponse.model_validate({'selected_components': [value]}).selected_components[0].model_dump()
        for key, value in chosen.items()}
    assert run.selection_events


def test_explicit_lookup_reopens_only_requested_saved_component(agent, monkeypatch):
    calls = []
    chosen = {}

    async def model(request, emit):
        payload = json.loads(request.user_message.splitlines()[1])
        calls.append(payload)
        if len(calls) == 1:
            result = {'components': [{'label': key, 'feature': '센서'} for key in ['A', 'B']],
                      'actions': [{'action': 'search_document', 'component_id': key,
                                   'queries': ['센서']} for key in ['R001', 'R002']]}
        elif len(calls) == 2:
            for entry in payload['results']:
                chosen[entry['component_id']] = selection(entry['component_id'],
                    entry['documents'][0]['hits'][0]['chunk_id'])
            result = {'selected_components': [chosen['R001']]}
        elif len(calls) == 3:
            result = {'actions': [{'action': 'read_chunk', 'component_id': 'R001',
                **{key: chosen['R001']['evidence'][0][key] for key in ['attachment', 'chunk_id']}}]}
        else:
            assert len(calls) == 4 and 'saved_components' not in payload
            assert {row['id'] for row in payload['components']} == {'R001', 'R002'}
            assert payload['results'][0]['documents'][0]['hits'][0]['text']
            result = {'selected_components': list(chosen.values())}
        return ExecutionOutcome(exit_code=0, result_text=json.dumps(result))

    monkeypatch.setattr(agent.provider, 'execute', model)
    run = asyncio.run(agent.run())
    assert not run.error and len(run.finalize.components) == 2
    assert any(event['event'] == 'reopened' and event['component_id'] == 'R001'
               for event in run.selection_events)
