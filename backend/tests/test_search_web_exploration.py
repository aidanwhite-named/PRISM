import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.providers.base import AGY_WEB_SEARCH, WEB_SEARCH, CODEX_WEB_SEARCH, ExecutionOutcome
from app.search_engine.engine import Engine
from app.search_engine.inference import Inference, InferenceError, decode_object
from .test_progressive_search import FakeInference


@pytest.mark.asyncio
@pytest.mark.parametrize('policy,calls', [
    (AGY_WEB_SEARCH, [{'name': 'search_web'}] * 4 + [{'name': 'read_url_content'}, {'name': 'view_file', 'scope_ok': True}]),
    (WEB_SEARCH, [{'name': 'WebSearch'}] * 4 + [{'name': 'WebFetch'}]),
    (CODEX_WEB_SEARCH, [{'name': 'web_search'}] * 4),
])
async def test_web_invocation_uses_existing_provider_search_and_read_policy(tmp_path, policy, calls):
    async def execute(request, emit):
        assert request.tool_policy is policy
        for call in calls:
            await emit('tool_use', call)
        return ExecutionOutcome(result_text='{"records":[]}', tool_calls=calls,
                                tool_uses=[call['name'] for call in calls])
    provider = SimpleNamespace(id='fake', search_tool_policy=policy, max_input_bytes=None,
                               execute=execute, cancel=AsyncMock())
    inference = Inference(provider, job_id='test', directory=tmp_path)
    assert await inference.call('web_seeds', 'search', {}, seconds=20, web=True) == {'records': []}
    provider.cancel.assert_not_called()
    assert len(inference.usage()['stages'][0]['tool_events']) == len(calls)


@pytest.mark.asyncio
async def test_source_read_policy_still_checks_the_actual_artifact_scope(tmp_path):
    async def execute(request, emit):
        return ExecutionOutcome(result_text='{"records":[]}', tool_calls=[
            {'name': 'search_web'}, {'name': 'view_file', 'scope_ok': False}])
    provider = SimpleNamespace(id='agy', search_tool_policy=AGY_WEB_SEARCH, max_input_bytes=None,
                               execute=execute, cancel=AsyncMock())
    inference = Inference(provider, job_id='test', directory=tmp_path)
    with pytest.raises(InferenceError, match='unexpected_search_tool_use'):
        await inference.call('web_seeds', 'search', {}, seconds=20, web=True)


@pytest.mark.asyncio
async def test_web_search_runs_with_existing_api_candidates_and_receives_claim_and_strategy(tmp_path):
    received = []
    class Model(FakeInference):
        async def call(self, phase, system, payload, **kwargs):
            received.append(payload)
            return {'records': [{'title': f'Joint limits paper {i}', 'url': f'https://example.org/{i}',
                                 'document_number': f'10.1234/{i}', 'snippet': 'Relation discussion. ' * 100}
                                for i in range(10)]}
    class Sources:
        def search(self, *args, **kwargs):
            return {'records': [{'document_number': 'EP123A1', 'title': 'Existing candidate',
                                 'fields': {'abstract': 'Coupled joint constraints'}}]}
    engine = Engine(claim='One parameter controls limits on two others.', strategy='Read promising sources and follow references.',
                    directory=tmp_path, inference=Model(), sources=Sources(), values={})
    await engine.discover()
    assert len(received) == 1
    assert received[0]['claim'] == engine.claim and received[0]['search_strategy'] == engine.strategy
    assert received[0]['candidates'][0]['document_number'] == 'EP123A1'
    assert len(engine.ledger.candidates) == 11
    assert len(next(c for c in engine.ordered() if c.document_number == '10.1234/0').fields['web_snippet']) > 1200
    # Preserve reported excerpts, without upgrading them to independently verified quotes.
    assert all(not c.evidence for c in engine.ordered())


def test_interrupted_web_output_does_not_discard_complete_findings_after_the_third():
    records = [{'title': f'Paper {i}', 'url': f'https://example.org/{i}'} for i in range(5)]
    text = '{"records":[' + ','.join(json.dumps(r) for r in records) + ',{"title":"unfinished'
    result = decode_object(text, 'web_seeds')
    assert result['_partial'] and result['records'] == records
