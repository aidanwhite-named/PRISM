"""Preserve original text while avoiding pathological overlap expansion."""
import asyncio
from types import SimpleNamespace

from app.retrieval import chunking, evidence, extraction, index as index_module, pages
from app.retrieval.actions import ReadPage
from app.retrieval.agent import ComponentState, RetrievalRun
from .test_retrieval_efficiency import agent


def body_with_early_break():
    return 'Header ' * 12 + '\n' + ' '.join(f'word{i:04d}' for i in range(400))


def test_boundary_inside_overlap_does_not_produce_repeated_slivers():
    body = body_with_early_break()
    pieces = chunking._split_long(body)
    assert 3 <= len(pieces) <= 5
    assert all(len(p) > chunking.MAX_CHUNK_CHARS // 2 for p in pieces[:-1])
    assert sum(map(len, pieces)) < len(body) * 1.2
    covered, previous = set(), -1
    for piece in pieces:
        start = body.find(piece, previous + 1)
        assert start >= 0
        covered.update(range(start, start + len(piece)))
        previous = start
    assert all(i in covered for i, char in enumerate(body) if not char.isspace())


def test_page_delivery_uses_original_spacing_without_search_overlap(tmp_path):
    body = body_with_early_break() + '\n  Exact spacing.  \n'
    extracted = extraction.DocumentExtraction('doc', 'doc.pdf', 'sha', source_page_count=1,
        pages=[extraction.PageRecord(page_number=1, text=body, status='ok', extraction_method='raw_text')])
    path = tmp_path / 'index.sqlite3'
    index_module.build_index(path, extracted)
    with index_module.open_index(path) as index:
        joined = '\n'.join(row.text for row in index.page_rows(1))
        assert len(joined) > len(body) and joined != body
        assert index.page_text(1) == body
        document = SimpleNamespace(index=index, attachment_id='doc', alias='ATT-01', filename='doc.pdf')
        built = pages.build(corpus=[document], finding_pages={'doc': {1}}, neighbours=0, char_budget=100000)
        assert built[0]['pages'][0]['text'] == body
        document.page_count = 1
        assert evidence.identity_excerpt_location(document) == (1, body.strip()[:1200])


def test_agent_page_read_and_retention_keep_exact_page_text(agent, monkeypatch):
    state = ComponentState('R001', '센서', '센서')
    agent._components[state.id] = state
    body = body_with_early_break()
    index = agent.corpus[0].index
    monkeypatch.setattr(index, 'page_text', lambda page: body)
    request = ReadPage(action='read_page', attachment='ATT-01', component_id=state.id, page=1)
    run = RetrievalRun()
    first = asyncio.run(agent._execute_actions([request], run, 1))
    assert first[0]['pages'][0]['text'] == body
    second = asyncio.run(agent._execute_actions([], run, 2))
    assert second[0]['pages'][0]['text'] == body


def test_old_chunking_index_is_rebuilt(tmp_path):
    extracted = extraction.DocumentExtraction('doc', 'doc.pdf', 'sha', source_page_count=1,
        pages=[extraction.PageRecord(page_number=1, text=body_with_early_break(), status='ok', extraction_method='raw_text')])
    path = tmp_path / 'index.sqlite3'
    index_module.build_index(path, extracted)
    with index_module.open_index(path) as index:
        index._connection.execute("UPDATE meta SET value='1' WHERE key='index_version'")
        index._connection.commit()
    calls = []
    def factory():
        calls.append(True)
        return extracted
    index, _, rebuilt = index_module.ensure_index(path, factory, sha256='sha')
    try:
        assert calls == [True] and rebuilt
        assert index.fingerprint()['index_version'] == 2
    finally:
        index.close()
