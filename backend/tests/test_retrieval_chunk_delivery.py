"""선택한 구간만 확장하고, 같은 호출의 원문 참조와 이월을 검증한다."""
import asyncio
from dataclasses import replace

from app.retrieval.actions import ReadChunk, ReadPage, SearchDocument, parse_response
from app.retrieval.agent import ComponentState, RetrievalRun, json_size
from .test_retrieval_efficiency import agent


def _search(agent):
    state = ComponentState("R001", "센서", "센서")
    agent._components[state.id] = state
    run = RetrievalRun()
    results = asyncio.run(agent._execute_actions([
        SearchDocument(action="search_document", component_id=state.id, queries=["센서"])
    ], run, 1))
    hit = results[0]["documents"][0]["hits"][0]
    request = ReadChunk(action="read_chunk", component_id=state.id,
                        attachment="ATT-01", chunk_id=hit["chunk_id"])
    return run, request


def test_chunk_request_parses_and_does_not_expose_unseen_sources(agent):
    run, request = _search(agent)
    parsed = parse_response('{"actions":[' + request.model_dump_json() + ']}')
    assert parsed.actions == [request]
    before = set(run.exposed_chunks)
    for changed in ({"attachment": "*"}, {"component_id": "unknown"}, {"chunk_id": "P9999-001"}):
        result = asyncio.run(agent._execute_actions([request.model_copy(update=changed)], run, 2))
        assert result[0].get("error")
        assert run.exposed_chunks == before
    unseen_run = RetrievalRun()
    result = asyncio.run(agent._execute_actions([request], unseen_run, 3))
    assert result[0].get("error") and not unseen_run.exposed_chunks


def test_chunk_that_does_not_fit_is_deferred_without_dangling_sources(agent):
    run, request = _search(agent)
    before = set(run.exposed_chunks)
    agent.budget = replace(agent.budget, max_round_result_chars=100)
    result = asyncio.run(agent._execute_actions([request], run, 2))
    assert result == [] and run.deferred_pending and run.budget_exhausted
    assert not agent._round_sources and run.exposed_chunks == before
    agent.budget = replace(agent.budget, max_round_result_chars=56000)
    result = asyncio.run(agent._execute_actions([], run, 3))
    hit = result[0]["documents"][0]["hits"][0]
    assert hit["text"] == agent.corpus[0].index.chunk(request.chunk_id).text
    assert not run.deferred_pending and not run.budget_exhausted
    assert run.pages_read == 0


def test_shared_context_resolves_exactly_and_is_resent_next_call(agent, monkeypatch):
    run, request = _search(agent)
    context = "부정 조건: \n  서로 참조하지 않는다. " * 20
    monkeypatch.setattr(agent.corpus[0].index, "neighbours", lambda *a, **kw: (context, ""))
    agent._components["R002"] = ComponentState("R002", "다른 구성", "다른 구성")
    second = request.model_copy(update={"component_id": "R002"})
    results = asyncio.run(agent._execute_actions([request, second], run, 2))
    first, shared = [r["documents"][0]["hits"][0] for r in results]
    ref = shared["context_before_ref"]
    assert ref["attachment"] == "ATT-01" and ref["chunk_id"] == request.chunk_id
    assert ref["component_id"] == results[0]["component_id"]
    assert first[ref["field"]][ref["start"]:ref["end"]] == context[-500:]
    assert sum(json_size(r) for r in results) <= agent.budget.max_round_result_chars
    later = asyncio.run(agent._execute_actions([second], run, 3))[0]["documents"][0]["hits"][0]
    assert later["context_before"] == context[-500:]
    assert "context_before_ref" not in later


def test_source_sharing_never_crosses_document_or_page(agent):
    document = agent.corpus[0]
    value = "KEEP whitespace\n  and punctuation. " * 25
    row = {"pdf_page": 1, "text": value}
    ref = {"attachment": document.alias, "action": "read_page", "component_id": "R001"}
    agent._register_source_fields(row, document, ref)
    same = agent._share_source_fields(row, document)
    assert same["text_ref"]["start"] == 0 and same["text_ref"]["end"] == len(value)
    assert agent._share_source_fields({**row, "pdf_page": 2}, document)["text"] == value
    other = replace(document, attachment_id="other", alias="ATT-02")
    assert agent._share_source_fields(row, other)["text"] == value


def test_chunk_reference_can_locate_the_page_source_in_actual_results(agent, monkeypatch):
    run, request = _search(agent)
    index = agent.corpus[0].index
    target = replace(index.chunk(request.chunk_id), text="Original source\n  exact spacing. " * 50)
    monkeypatch.setattr(index, "chunk", lambda key: target if key == target.chunk_id else None)
    monkeypatch.setattr(index, "page_rows", lambda page: [target])
    monkeypatch.setattr(index, "neighbours", lambda *a, **kw: ("", ""))
    page = ReadPage(action="read_page", component_id=request.component_id,
                    attachment=request.attachment, page=target.page_number)
    results = asyncio.run(agent._execute_actions([page, request], run, 2))
    hit = next(r for r in results if r["action"] == "read_chunk")["documents"][0]["hits"][0]
    ref = hit["text_ref"]
    source_entry = next(r for r in results if r["action"] == ref["action"]
                        and r["component_id"] == ref["component_id"]
                        and r["attachment"] == ref["attachment"])
    source = next(p for p in source_entry["pages"] if p["pdf_page"] == ref["pdf_page"])
    assert source[ref["field"]][ref["start"]:ref["end"]] == target.text
