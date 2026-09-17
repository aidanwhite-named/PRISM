"""Classification is useful before, and survives failure of, evidence acquisition."""
import time
from types import SimpleNamespace

import pytest

from app.search_engine.engine import Engine
from app.search_engine.inference import decode_object
from app.search_engine.models import Feature
from app.search_engine.report import manifest, render
from .test_progressive_search import FakeInference


@pytest.fixture
def engine(monkeypatch, tmp_path):
    monkeypatch.setattr('app.search_engine.engine.PATHS', SimpleNamespace(data_dir=tmp_path, evidence_dir=tmp_path / 'evidence'))
    return Engine(claim='Gaussian cloning uses distance.', directory=tmp_path / 'run',
                  inference=FakeInference(), values={'progressive_search_web_enabled': False})


def add(engine, number='10.1234/a', abstract='Gaussian cloning uses the distance between neighboring Gaussians.'):
    return engine.ledger.add({'document_number': number, 'title': 'Gaussian cloning',
        'fields': {'abstract': abstract} if abstract else {},
        'evidence_refs': {'abstract': {'artifact_id': 'a' * 64}} if abstract else {}},
        source='openalex', query_id='q', feature='A', rank=1)


@pytest.mark.asyncio
async def test_single_candidate_classified_before_failed_acquisition_and_verifier(engine):
    candidate = add(engine)
    order = []
    class Inference(FakeInference):
        async def call(self, phase, *args, **kwargs):
            order.append(phase)
            return await super().call(phase, *args, **kwargs)
    engine.inference = Inference()
    engine.features = [Feature('A', engine.claim, ['gaussian', 'cloning', 'distance'])]
    candidate.url = 'https://example.org/paper'
    def fail(url):
        order.append('fetch')
        assert candidate.document_classification['group'] == 'Y'
        raise RuntimeError('access denied')
    engine.fetcher = SimpleNamespace(get=fail)
    await engine.triage()
    await engine.verify_candidates(3)
    assert order == ['triage', 'fetch', 'verify']
    assert candidate.document_classification['basis'] == 'abstract'
    assert candidate.document_classification['provisional']
    assert candidate.acquisitions[0]['status'] == 'failed'
    assert engine.classification_summary()['status'] == 'complete'
    assert not engine.sufficient()
    assert '초록 기준 · 잠정 판단' in render(engine.snapshot())


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['timeout', 'omitted', 'malformed', 'partial'])
async def test_missing_response_is_incomplete_and_keeps_partial_classification(engine, failure):
    first, second = add(engine), add(engine, '10.1234/b')
    class Inference(FakeInference):
        async def call(self, phase, system, payload, **kwargs):
            if failure == 'timeout':
                raise RuntimeError('inference_timeout')
            row = {'candidate_id': first.id, 'group': 'X', 'status': 'classified', 'reason': 'Relation in abstract'}
            if failure == 'malformed':
                row['status'] = 'low_relevance'  # contradictory group/status
            return {'candidate_ids': [first.id], 'classifications': [] if failure == 'omitted' else [row]}
    engine.inference = Inference()
    await engine.triage()
    assert second.document_classification is None
    assert engine.classification_summary()['status'] == 'incomplete'
    assert engine.classification_summary()['reviewed_count'] == (1 if failure == 'partial' else 0)
    engine.phase = 'complete'
    result = manifest(engine.snapshot(), claim=engine.claim, provider='fake')
    assert result['status'] == 'classification_incomplete'


@pytest.mark.asyncio
async def test_no_xyz_is_valid_when_all_target_candidates_are_assessed(engine):
    missing = add(engine, abstract='')
    unrelated = add(engine, '10.1234/b', 'This study measures the mineral content of human bones.')
    class Inference(FakeInference):
        async def call(self, phase, system, payload, **kwargs):
            return {'candidate_ids': [unrelated.id], 'classifications': [
                {'candidate_id': missing.id, 'group': None, 'status': 'insufficient_information', 'reason': 'No abstract'},
                {'candidate_id': unrelated.id, 'group': None, 'status': 'low_relevance', 'reason': 'Different technical subject'}]}
    engine.inference = Inference()
    await engine.triage()
    assert engine.classification_summary()['status'] == 'complete'
    assert missing.document_classification['status'] == 'insufficient_information'
    assert unrelated.document_classification['status'] == 'low_relevance'
    assert unrelated.triage_rank == 1000000
    text = render(engine.snapshot())
    assert '자료 부족' in text and '검토 결과 관련성 낮음' in text


@pytest.mark.asyncio
async def test_out_of_scope_ids_and_title_only_xyz_are_not_accepted(engine):
    candidate = add(engine, abstract='')
    future = add(engine, '10.1234/future')
    future.publication_date = '2026-01-01'
    engine.cutoff = '2025-01-01'
    class Inference(FakeInference):
        async def call(self, phase, system, payload, **kwargs):
            assert [r['id'] for r in payload['candidates']] == [candidate.id]
            return {'candidate_ids': ['invented', future.id, candidate.id], 'classifications': [
                {'candidate_id': cid, 'group': 'X', 'reason': 'Claimed match'} for cid in ['invented', future.id, candidate.id]]}
    engine.inference = Inference()
    await engine.triage()
    assert candidate.document_classification['group'] is None
    assert candidate.document_classification['status'] == 'insufficient_information'
    assert future.document_classification is None
    assert engine.classification_summary()['target_count'] == 1


@pytest.mark.asyncio
async def test_deep_search_after_slow_plan_keeps_full_relation_and_classification_window(engine):
    order = []
    relation = 'gaussian bone transformation parameter correction'
    async def plan():
        # Replay elapsed time without sleeping or using API/model quotas.
        engine.started = time.monotonic() - 28
        engine.deadline = engine.started + engine.limits.seconds
        engine.seed_queries = [relation]
        engine.features = [Feature('A', engine.claim, ['gaussian', 'cloning'])]
    engine.plan = plan
    engine.values['epo_integration_enabled'] = True
    def search(source, query, **kwargs):
        order.append((source, query))
        return {'records': [{'document_number': '10.1234/a', 'title': 'Gaussian cloning',
                             'fields': {'abstract': 'Gaussian cloning depends on neighbor distance.'}}]}
    engine.sources = SimpleNamespace(search=search)
    observed = []
    class Inference(FakeInference):
        async def call(self, phase, system, payload, **kwargs):
            if phase == 'triage':
                observed.append(kwargs['seconds'])
            return await super().call(phase, system, payload, **kwargs)
    engine.inference = Inference()
    result = await engine.run()
    assert order[0] == ('epo', relation)
    assert result['route'][0]['outcome'] == 'candidates_merged'
    assert observed == [45]
    assert result['classification']['status'] == 'complete'
    assert result['limits']['seconds'] == 120


@pytest.mark.asyncio
async def test_local_discovery_deadline_preserves_time_for_classification(engine):
    async def plan(): pass
    async def seeds():
        add(engine)
        # Simulate retrieval reaching its deadline.
        engine.deadline = time.monotonic() + 50
        engine.stage_deadline = time.monotonic() - 1
    async def discover():
        assert engine.remaining() == 0
    engine.plan, engine.search_relation_seeds, engine.discover = plan, seeds, discover
    result = await engine.run()
    assert result['classification']['status'] == 'complete'
    assert engine.stage_deadline is None


@pytest.mark.asyncio
async def test_legacy_continuation_counts_existing_judgments_without_reclassification(engine):
    candidate = add(engine)
    candidate.document_classification = {'group': 'X', 'reason': 'Existing reviewed evidence', 'basis': 'retrieved_passages'}
    class Inference(FakeInference):
        async def call(self, *args, **kwargs):
            pytest.fail('Do not repeat a completed judgment')
    engine.inference = Inference()
    await engine.triage()
    assert engine.classification_summary()['status'] == 'complete'


def test_truncated_classification_retains_only_complete_rows():
    value = decode_object('{"classifications":[{"candidate_id":"a","group":"Y","status":"classified","reason":"relation"},'
                          '{"candidate_id":"b","group":', 'triage')
    assert value['_partial'] and len(value['classifications']) == 1
    assert value['classifications'][0]['candidate_id'] == 'a'


@pytest.mark.parametrize('provider,model,expected', [('agy', 'gemini-3.8-flash-medium', 'medium'),
    ('agy', 'gemini-3.8-flash-high', 'high'), ('agy', 'gemini-flash', 'low'), ('codex', 'gpt-5.6-luna', 'low')])
def test_automatic_effort_respects_selected_agy_model(tmp_path, provider, model, expected):
    from app.search_engine.inference import Inference
    inference = Inference(SimpleNamespace(id=provider), job_id='test', directory=tmp_path, model=model)
    assert inference.provider_phase_effort('triage') == expected


@pytest.mark.asyncio
@pytest.mark.parametrize('status,group,expected', [('insufficient_information', None, 'X'),
                                                ('low_relevance', None, None), ('classified', 'Y', 'Y')])
async def test_evidence_can_revise_classification_but_missing_passage_cannot_erase_it(engine, status, group, expected):
    candidate = add(engine)
    candidate.document_classification = {'group': 'X', 'status': 'classified', 'reason': 'Abstract match', 'basis': 'abstract'}
    engine.passages = [{'id': 'p', 'candidate_id': candidate.id, 'feature': 'A',
                       'text': 'Gaussian cloning uses a distinct decision rule.', 'scope': 'description'}]
    class Inference(FakeInference):
        async def call(self, *args, **kwargs):
            return {'evidence': [], 'classifications': [{'candidate_id': candidate.id,
                'group': group, 'status': status, 'reason': 'Passage assessment'}]}
    engine.inference = Inference()
    await engine.verify_candidates(0, selected=[])
    assert candidate.document_classification['group'] == expected
    assert candidate.document_classification['basis'] == ('abstract' if status == 'insufficient_information' else 'retrieved_passages')
