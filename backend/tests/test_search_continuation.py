import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.search_engine.engine import Engine
from app.search_engine.models import Candidate, Feature
from .test_progressive_search import FakeInference


def engine_at(tmp_path, **kwargs):
    return Engine(claim='A mechanical valve controls pressure.', directory=tmp_path,
                  inference=FakeInference(), values={'progressive_search_web_enabled': False}, **kwargs)


@pytest.mark.parametrize('group,scope,date,relation,expected', [
    ('Y', 'full_text', '2020-01-01', 'pressure controls valve', True),
    ('Y', 'abstract', '2020-01-01', 'pressure controls valve', False),
    ('Y', 'full_text', '2026-01-01', 'pressure controls valve', False),
    ('Y', 'full_text', '2020-01-01', '', False),
    ('X', 'full_text', '2020-01-01', '', False),  # only one of two features
])
def test_only_evidenced_xy_can_stop(tmp_path, group, scope, date, relation, expected):
    engine = engine_at(tmp_path, cutoff='2025-01-01')
    engine.features = [Feature('A', 'pressure'), Feature('B', 'valve')]
    candidate = Candidate('c', '10.1234/a', 'pressure', '', 'openalex', date)
    candidate.document_classification = {'group': group}
    candidate.evidence = [{'feature': 'A', 'match': 'semantic', 'quote_verified': True,
                          'relation': relation, 'locator': {'scope': scope}}]
    engine.ledger.candidates['c'] = candidate
    assert engine.sufficient() is expected


@pytest.mark.asyncio
async def test_arxiv_is_parallel_only_for_relevant_fields_then_single_supplement(tmp_path):
    engine = engine_at(tmp_path)
    engine.features = [Feature('A', 'valve', ['valve'], queries=['valve pressure'])]
    engine.query = AsyncMock()
    await engine.discover()
    assert [c.args[0] for c in engine.query.call_args_list] == ['openalex']
    await engine.supplement_arxiv()
    assert engine.query.call_args.args[0] == 'arxiv'
    engine.queries.append({'source': 'arxiv'})
    engine.query.reset_mock()
    await engine.supplement_arxiv()
    engine.query.assert_not_called()
    engine.claim = 'A neural network renders Gaussian splatting.'
    await engine.discover()
    assert [c.args[0] for c in engine.query.call_args_list] == ['openalex', 'arxiv']


@pytest.mark.asyncio
async def test_continuation_reuses_state_and_does_not_restart_plan_or_discovery(tmp_path):
    original = engine_at(tmp_path / 'old')
    original.started = time.monotonic() - 100
    original.features = [Feature('A', 'valve', ['valve'])]
    original.ledger.candidates['c'] = Candidate('c', '10.1234/a', 'valve', '', 'openalex')
    original.passages = [{'candidate_id': 'c', 'id': 'p', 'text': 'retained evidence'}]
    original.attempted_documents = {'c'}
    original.queries = [{'id': 'q', 'source': 'openalex', 'query': 'valve', 'feature': 'A'}]
    continued = engine_at(tmp_path / 'new', depth='exhaustive')
    continued.restore(original.checkpoint())
    assert 190 < continued.remaining() < 200
    assert continued.passages == original.passages
    assert continued.attempted_documents == {'c'}
    assert continued.queries == original.queries
    continued.plan = AsyncMock()
    continued.discover = AsyncMock()
    continued.expand = AsyncMock()
    continued.verify_candidates = AsyncMock()
    await continued.run()
    continued.plan.assert_not_called()
    continued.discover.assert_not_called()
    continued.expand.assert_awaited_once()
    assert 'c' in continued.ledger.candidates
