"""Independent model calls must retain reviewed originals, not just search rank."""
import asyncio
import copy
from dataclasses import replace

from app.retrieval.actions import ReadChunk, ReadPage, SearchDocument
from app.retrieval.agent import ComponentState, RetrievalRun, json_size
from .test_retrieval_efficiency import agent


def setup_read(agent, component_id='R001'):
    state = ComponentState(component_id, '구성', '센서')
    agent._components[state.id] = state
    agent._order.append(state.id)
    row = agent.corpus[0].index.page_rows(1)[0]
    request = ReadChunk(action='read_chunk', component_id=state.id,
                        attachment='ATT-01', chunk_id=row.chunk_id)
    return state, row, request


def test_reviewed_chunk_survives_new_search_and_empty_final_actions(agent):
    state, row, request = setup_read(agent)
    run = RetrievalRun()
    run.exposed_chunks.add((agent.corpus[0].attachment_id, row.chunk_id))
    first = asyncio.run(agent._execute_actions([request], run, 1))
    original = copy.deepcopy(first[0]['documents'][0]['hits'][0])
    search = SearchDocument(action='search_document', component_id=state.id, queries=['제어'])
    asyncio.run(agent._execute_actions([search], run, 2))
    final = asyncio.run(agent._execute_actions([], run, 3))
    payload = agent._round_payload(4, final, '')
    retained = payload['results'][0]
    assert retained['component_id'] == state.id
    assert retained['previously_reviewed'] is True
    assert retained['documents'][0]['hits'][0] == original
    assert run.finalize is None  # Reading is never a relevance verdict.
    assert run.pages_read == 0
    assert sum(json_size(x) for x in final) <= agent.budget.max_round_result_chars


def test_shared_original_keeps_each_component_and_self_contained_refs(agent):
    state, row, first = setup_read(agent)
    _, _, second = setup_read(agent, 'R002')
    run = RetrievalRun()
    run.exposed_chunks.add((agent.corpus[0].attachment_id, row.chunk_id))
    asyncio.run(agent._execute_actions([first, second], run, 1))
    retained = asyncio.run(agent._execute_actions([], run, 2))
    assert {x['component_id'] for x in retained} == {'R001', 'R002'}
    first_hit = retained[0]['documents'][0]['hits'][0]
    second_hit = retained[1]['documents'][0]['hits'][0]
    if 'text_ref' in second_hit:
        ref = second_hit['text_ref']
        assert ref['component_id'] == 'R001'
        assert first_hit[ref['field']][ref['start']:ref['end']] == row.text
    else:
        assert second_hit['text'] == row.text  # Short text can be cheaper than a pointer.
    assert first_hit['text'] == row.text


def test_transient_search_reference_does_not_escape_into_later_read(agent):
    state, row, request = setup_read(agent)
    run = RetrievalRun()
    search = SearchDocument(action='search_document', component_id=state.id, queries=['센서'])
    asyncio.run(agent._execute_actions([search], run, 1))
    run.exposed_chunks.add((agent.corpus[0].attachment_id, row.chunk_id))
    # In the same scope the read can reference a search result. Persistence must
    # still rebuild from original text once that transient search is gone.
    asyncio.run(agent._execute_actions([request], run, 1))
    retained = asyncio.run(agent._execute_actions([], run, 2))
    assert retained[0]['documents'][0]['hits'][0]['text'] == row.text


def test_page_read_survives_without_another_read_or_page_charge(agent):
    state, row, _ = setup_read(agent)
    run = RetrievalRun()
    request = ReadPage(action='read_page', component_id=state.id, attachment='ATT-01', page=1)
    first = asyncio.run(agent._execute_actions([request], run, 1))
    later = asyncio.run(agent._execute_actions([], run, 2))
    assert later[0]['pages'][0]['text'] == first[0]['pages'][0]['text']
    assert later[0]['pages'][0]['chunks'] == first[0]['pages'][0]['chunks']
    assert run.pages_read == 1 and run.repeat_page_reads == 0
    assert (agent.corpus[0].attachment_id, row.chunk_id) in run.exposed_chunks


def test_fresh_search_cannot_evict_reviewed_text_or_exceed_budget(agent):
    state, row, request = setup_read(agent)
    run = RetrievalRun()
    run.exposed_chunks.add((agent.corpus[0].attachment_id, row.chunk_id))
    asyncio.run(agent._execute_actions([request], run, 1))
    reserved = sum(json_size(x) for x in agent._retained_results())
    agent.budget = replace(agent.budget, max_round_result_chars=reserved)
    search = SearchDocument(action='search_document', component_id=state.id, queries=['센서'])
    result = asyncio.run(agent._execute_actions([search], run, 2))
    assert sum(json_size(x) for x in result) == reserved
    assert result[0]['documents'][0]['hits'][0]['text'] == row.text
    assert run.deferred_pending and run.budget_limited


def test_undeliverable_read_is_never_remembered(agent):
    state, row, request = setup_read(agent)
    run = RetrievalRun()
    run.exposed_chunks.add((agent.corpus[0].attachment_id, row.chunk_id))
    agent.budget = replace(agent.budget, max_round_result_chars=1)
    asyncio.run(agent._execute_actions([request], run, 1))
    assert not agent._retained_reads
    assert not state.reviewed_chunks
    assert run.deferred_pending


def test_read_that_cannot_be_retained_is_deferred_without_false_review(agent):
    state, row, request = setup_read(agent)
    run = RetrievalRun()
    run.exposed_chunks.add((agent.corpus[0].attachment_id, row.chunk_id))
    first = asyncio.run(agent._execute_actions([request], run, 1))
    # The immediate read fits, but its persistent provenance also needs space.
    agent.budget = replace(agent.budget, max_round_result_chars=json_size(first[0]))
    agent._retained_reads.clear()
    state.reviewed_chunks.clear()
    later = asyncio.run(agent._execute_actions([request], run, 2))
    assert later[0]['deferred']
    assert not agent._retained_reads and not state.reviewed_chunks
    assert run.deferred_pending and run.budget_limited
