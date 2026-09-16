import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.search_engine.models import Candidate, Feature, Ledger, Limits
from app.search_engine.planner import parse_plan
from app.search_engine.ranking import rank, diverse_top
from app.search_engine.passages import validate_evidence, retrieve
from app.search_engine.fetcher import public_target, FetchError, SafeFetcher
from app.search_engine.engine import Engine
from app.retrieval.extraction import PageRecord
from app.patent_search.artifacts import ArtifactStore


def test_feature_plan_requires_original_claim_and_separate_tokens():
    claim = 'Gaussian covariance is computed using SVD.'
    features, _, _ = parse_plan({'features': [{'text': claim, 'terms': ['Gaussian covariance', 'SVD'],
        'queries': ['Gaussian covariance SVD'], 'phrases': ['singular value decomposition']}]}, claim)
    assert features[0].terms == ['gaussian', 'covariance', 'svd']
    with pytest.raises(ValueError, match='not_in_claim'):
        parse_plan({'features': [{'text': 'The processor requires cloning.'}]}, claim)


def test_candidate_union_persists_discoveries_and_family(tmp_path):
    ledger = Ledger(tmp_path, 2)
    record = {'document_number': 'WO2025000001A1', 'title': 'Gaussian covariance',
              'fields': {'family_id': '123'}, 'url': 'https://example.org/a'}
    first = ledger.add(record, source='epo', query_id='q1', feature='A', rank=8)
    second = ledger.add(record, source='web', query_id='q2', feature='B', rank=1)
    ledger.save()
    assert first is second and first.family_id == '123'
    saved = json.loads((tmp_path / 'candidates.json').read_text(encoding='utf-8'))
    assert len(saved) == 1 and len(saved[0]['discoveries']) == 2


def test_rank_preserves_feature_and_family_diversity_and_dates():
    features = [Feature('A', 'svd', ['svd']), Feature('B', 'cloning', ['cloning'])]
    a = Candidate('a', 'WO2025000001A1', 'svd', 'https://a.org', 'epo', '2025-01-01', 'family')
    a.discoveries = [{'source': 'epo', 'query_id': 'q', 'feature': 'A', 'rank': 1}]
    duplicate = Candidate('a2', 'US2025000001A1', 'svd', 'https://a.org/us', 'epo', '2025-01-01', 'family')
    duplicate.discoveries = a.discoveries
    b = Candidate('b', '10.1/b', 'cloning', 'https://b.org', 'openalex', '2024-01-01')
    b.discoveries = [{'source': 'openalex', 'query_id': 'b', 'feature': 'B', 'rank': 20}]
    future = Candidate('c', '10.1/c', 'svd cloning', 'https://c.org', 'openalex', '2026-01-01')
    ordered = rank([a, duplicate, b, future], features, '2025-06-01')
    top = diverse_top(ordered, features, 2)
    assert {c.id for c in top} == {'a', 'b'}
    assert future.date_status == 'after_cutoff'


def test_quote_cannot_cross_documents_or_be_fabricated():
    features = [Feature('A', 'distance determines clone')]
    passages = [{'id': 'p', 'candidate_id': 'doc1', 'feature': 'A',
                 'text': 'Center distance determines Gaussian cloning.', 'scope': 'full_text'}]
    good = {'candidate_id': 'doc1', 'feature': 'A', 'match': 'explicit', 'passage_id': 'p',
            'quote': 'Center distance determines Gaussian cloning.'}
    assert validate_evidence({'evidence': [good]}, passages, features)[0]['quote_verified']
    bad = {**good, 'quote': 'Gradient determines Gaussian cloning.'}
    assert validate_evidence({'evidence': [bad]}, passages, features)[0]['match'] == 'unknown'
    wrong = {**good, 'candidate_id': 'doc2'}
    assert validate_evidence({'evidence': [wrong]}, passages, features) == []


def test_same_document_passage_can_support_a_different_search_feature():
    features = [Feature('A', 'input'), Feature('B', 'cloning')]
    passages = [{'id': 'p', 'candidate_id': 'doc', 'feature': 'A',
                 'text': 'Center distance determines Gaussian cloning.', 'scope': 'full_text'}]
    row = {'candidate_id': 'doc', 'feature': 'B', 'match': 'explicit', 'passage_id': 'p',
           'quote': 'Center distance determines Gaussian cloning.'}
    result = validate_evidence({'evidence': [row]}, passages, features)
    assert result[0]['quote_verified'] and result[0]['feature'] == 'B'


def test_existing_fts_receives_terms_and_selects_relation_passage(tmp_path):
    feature = Feature('B', 'clone using neighbor distance', ['gaussian', 'distance', 'cloning'], ['neighbor distance'])
    text = 'Gaussian cloning is determined by neighbor distance exceeding the threshold. ' * 8
    selected = retrieve('doc', {'artifact_id': 'a' * 64, 'url': 'https://example.org/paper',
        'scope': 'full_text', 'content_type': 'application/pdf'}, [PageRecord(1, text)], [feature], tmp_path)
    assert selected and 'neighbor distance' in selected[0]['text']
    assert selected[0]['page'] == 1 and selected[0]['feature'] == 'B'


@pytest.mark.parametrize('url', ['file:///etc/passwd', 'http://example.org/', 'https://user:pass@example.org/', 'https://example.org:8765/'])
def test_unsafe_url_rejected_before_network(url):
    with pytest.raises(FetchError):
        public_target(url)


@pytest.mark.parametrize('address', ['127.0.0.1', '10.1.2.3', '169.254.169.254', '::1', 'fc00::1'])
def test_private_dns_blocked(monkeypatch, address):
    monkeypatch.setattr('socket.getaddrinfo', lambda *a, **kw: [(2, 1, 6, '', (address, 443))])
    with pytest.raises(FetchError, match='non_public'):
        public_target('https://public-name.example/')


def test_fetch_pins_address_and_checks_redirects(monkeypatch, tmp_path):
    monkeypatch.setattr('socket.getaddrinfo', lambda host, *a, **kw: [(2, 1, 6, '',
        ('127.0.0.1' if host == 'internal.example' else '93.184.216.34', 443))])
    connections = []
    class Pool:
        def __init__(self, address, **kwargs):
            connections.append((address, kwargs))
        def urlopen(self, *args, **kwargs):
            return SimpleNamespace(status=302, headers={'Location': 'https://internal.example/'}, close=lambda: None)
        def close(self): pass
    monkeypatch.setattr('urllib3.HTTPSConnectionPool', Pool)
    with pytest.raises(FetchError, match='non_public'):
        SafeFetcher(ArtifactStore(tmp_path)).get('https://public.example/')
    assert len(connections) == 1
    assert connections[0][0] == '93.184.216.34'
    assert connections[0][1]['assert_hostname'] == 'public.example'


class FakeInference:
    provider = SimpleNamespace(id='fake')
    model = 'fake'
    def usage(self): return {'input_tokens': 1, 'stages': [], 'usage_complete': True}
    async def call(self, phase, system, payload, **kwargs):
        if phase == 'plan':
            return {'features': [{'text': payload['claim'], 'terms': ['Gaussian', 'distance', 'cloning'],
                                  'queries': ['Gaussian distance cloning']}], 'context_query': ''}
        if phase == 'verify':
            raise RuntimeError('simulated verifier failure')
        return {'records': []}


@pytest.mark.asyncio
async def test_verifier_failure_keeps_candidates_and_publishes_before_verification(monkeypatch, tmp_path):
    monkeypatch.setattr('app.search_engine.engine.PATHS', SimpleNamespace(data_dir=tmp_path, evidence_dir=tmp_path / 'evidence'))
    class FakeSources:
        def search(self, source, query, **kwargs):
            return {'records': [{'document_number': '10.1234/test', 'title': 'Gaussian distance cloning',
                    'url': '', 'fields': {'abstract': 'Gaussian cloning uses neighbor distance as a decision input.' * 6},
                    'evidence_refs': {'abstract': {'artifact_id': 'a' * 64}}}]}
    snapshots = []
    async def emit(snapshot): snapshots.append(snapshot)
    engine = Engine(claim='Gaussian cloning uses distance.', directory=tmp_path / 'run', inference=FakeInference(),
        values={'epo_integration_enabled': True, 'progressive_search_web_enabled': False}, depth='fast',
        sources=FakeSources(), emit=emit)
    result = await engine.run()
    assert len(result['candidates']) == 1
    assert any(snapshot['candidates'] and snapshot['phase'] == 'fast' for snapshot in snapshots)
    assert any('simulated verifier failure' in warning for warning in result['warnings'])
    assert (tmp_path / 'run/candidates.json').exists()
    assert result['stop_reason'] == 'fast_budget_complete'


@pytest.mark.asyncio
async def test_cancellation_does_not_start_retrieval(monkeypatch, tmp_path):
    monkeypatch.setattr('app.search_engine.engine.PATHS', SimpleNamespace(data_dir=tmp_path, evidence_dir=tmp_path / 'evidence'))
    source = SimpleNamespace(search=lambda *a, **kw: pytest.fail('search must not start'))
    engine = Engine(claim='Gaussian cloning uses distance.', directory=tmp_path, inference=FakeInference(),
                    values={}, sources=source, cancelled=lambda: True)
    await engine.run()
    assert engine.stop_reason == 'cancelled' and not engine.queries


def test_new_depth_limits_are_bounded_and_configurable():
    assert Limits.for_depth('fast').seconds == 45
    assert Limits.for_depth('deep').queries == 12
    assert Limits.for_depth('exhaustive', {'progressive_search_limits': {'exhaustive': {'seconds': 999999}}}).seconds == 900


def test_agy_uses_native_effort_and_json_schema(tmp_path):
    from app.providers.agy_cli import AgyCliProvider
    from app.providers.base import ExecutionRequest
    from app.search_engine.inference import response_schema
    request = ExecutionRequest('test', tmp_path, 'system', 'user', reasoning_effort='low',
                               response_schema=response_schema('triage'))
    args = AgyCliProvider().build_args(request)
    assert args[args.index('--effort') + 1] == 'low'
    schema = json.loads(args[args.index('--json-schema') + 1])
    assert schema['properties']['candidate_ids']['maxItems'] == 3


def test_agy_partial_usage_is_accumulated_without_duplicate_steps():
    from app.providers.agy_stream import AgyStreamParser
    parser = AgyStreamParser()
    parser._on_step({'step_index': 1, 'usage': {'input_tokens': 10, 'output_tokens': 2}})
    parser._on_step({'step_index': 1, 'usage': {'input_tokens': 10, 'output_tokens': 3}})
    parser._on_step({'step_index': 2, 'usage': {'input_tokens': 7, 'output_tokens': 1}})
    assert parser.state.usage['input_tokens'] == 17
    assert parser.state.usage['output_tokens'] == 4
    assert not parser.state.usage['usage_complete']
    parser._on_result({'status': 'SUCCESS', 'usage': {'input_tokens': 18, 'output_tokens': 5}})
    assert parser.state.usage == {'input_tokens': 18, 'output_tokens': 5}


def test_structured_response_accepts_final_schema_output_without_merging_drafts():
    from app.search_engine.inference import decode_object
    text = '```json\n{"candidate_ids":["draft"]}\n```\n{"candidate_ids":["final"],"toolAction":"done"}'
    assert decode_object(text, 'triage')['candidate_ids'] == ['final']


def test_interrupted_web_json_keeps_only_complete_records():
    from app.search_engine.inference import decode_object
    text = '{"records":[{"title":"Saved","url":"https://example.org"},{"title":"incomplete'
    result = decode_object(text, 'web_seeds')
    assert result['_partial'] and len(result['records']) == 1
    assert result['records'][0]['title'] == 'Saved'


def test_partial_decomposition_keeps_useful_search_queries():
    features, _, warnings = parse_plan({'features': [
        {'text': 'First feature.', 'queries': ['useful technical query']},
        {'text': 'Invented limitation.'}]}, 'A: First feature.\nB: Second feature.')
    assert features[0].queries == ['useful technical query']
    assert warnings == ['plan_feature_skipped: feature_text_not_in_claim']


def test_real_immersive_plan_keeps_four_english_queries_despite_preamble_and_reference_numbers():
    from app.search_engine.planner import initial_queries, fallback_plan
    fixture = json.loads((Path(__file__).parent / 'fixtures/immersive_search_plan.json').read_text(encoding='utf-8'))
    features, context, warnings = parse_plan(fixture['plan'], fixture['claim'])
    assert [f.id for f in features] == ['A', 'B', 'C', 'D']
    assert len(initial_queries(features, context)) == 5
    assert all(f.queries and f.queries[0].isascii() for f in features)
    assert not warnings
    fallback, _, _ = fallback_plan(fixture['claim'])
    assert len(fallback) == 4


@pytest.mark.asyncio
async def test_planner_recovers_english_queries_once_on_invalid_output(monkeypatch, tmp_path):
    monkeypatch.setattr('app.search_engine.engine.PATHS', SimpleNamespace(data_dir=tmp_path, evidence_dir=tmp_path / 'evidence'))
    calls = []
    class Recover(FakeInference):
        async def call(self, phase, system, payload, **kwargs):
            calls.append(phase)
            if phase == 'plan':
                raise ValueError('invalid JSON')
            assert phase == 'plan_recovery'
            return {'features': [{'text': payload['claim'], 'queries': ['point cloud connectivity interpolation']}],
                    'context_query': 'immersive video'}
    engine = Engine(claim='포인트 클라우드 연결성으로 기하구조를 보간한다.', directory=tmp_path / 'run',
                    inference=Recover(), values={}, depth='deep')
    await engine.plan()
    assert calls == ['plan', 'plan_recovery']
    assert engine.features[0].queries == ['point cloud connectivity interpolation']
    assert engine.warnings[0].startswith('plan_recovered')


def test_document_classification_is_separate_from_feature_and_quote_status():
    from app.search_engine.passages import classifications
    result = classifications({'classifications': [{'candidate_id': 'doc', 'group': 'B', 'reason': 'core relation'}]},
                             [{'candidate_id': 'doc', 'feature': 'A'}], [])
    assert result['doc']['group'] == 'Y'
    assert result['doc']['evidence_status'] == 'unverified'
    assert classifications({'classifications': None}, [], []) == {}


def test_document_display_order_migrates_legacy_categories_without_changing_retrieval_order():
    from dataclasses import asdict
    from app.search_engine.categories import display_order
    from app.search_engine.report import render
    candidates = []
    for cid, group in [('z', 'C'), ('y1', 'Y'), ('x', 'A'), ('y2', 'B'), ('none', None)]:
        c = Candidate(cid, cid, cid, '', 'epo')
        c.document_classification = {'group': group, 'reason': 'test'}
        candidates.append(asdict(c))
    assert [c['id'] for c in display_order(candidates)] == ['x', 'y1', 'y2', 'z', 'none']
    assert [c['id'] for c in candidates] == ['z', 'y1', 'x', 'y2', 'none']
    text = render({'candidates': candidates, 'features': [{'id': 'A', 'text': 'original feature'}],
                   'elapsed_seconds': 0, 'stop_reason': 'bounded_expansion_complete', 'queries': [], 'warnings': []})
    assert text.index('**문헌 분류 X**') < text.index('**문헌 분류 Y**') < text.index('**문헌 분류 Z**')
    assert '**A**: original feature' in text


def test_codex_usage_normalizes_cache_without_double_counting(tmp_path):
    from app.search_engine.inference import Inference
    inference = Inference(SimpleNamespace(), job_id='test', directory=tmp_path)
    inference.calls = [{'usage_complete': True, 'usage': {'input_tokens': 100, 'output_tokens': 20,
                        'cached_input_tokens': 80, 'reasoning_output_tokens': 10}}]
    usage = inference.usage()
    assert usage['cache_read_tokens'] == 80 and usage['thinking_tokens'] == 10
    assert usage['total_tokens'] == 120


def test_shortlist_keeps_discriminative_sentence_after_long_intro():
    from app.search_engine.ranking import metadata_excerpt
    abstract = ('We present a rendering system with fast training and real time speed. ' * 15
                + 'Splitting and cloning use KL divergence guidance.')
    snippet = metadata_excerpt(abstract, [Feature('B', 'cloning decision', ['cloning', 'divergence'])], 600)
    assert len(snippet) <= 600 and 'KL divergence' in snippet


def test_malformed_optional_limits_use_defaults():
    assert Limits.for_depth('deep', {'progressive_search_limits': 'bad'}).seconds == 120
    assert Limits.for_depth('deep', {'progressive_search_limits': {'deep': {'seconds': None}}}).seconds == 120


def test_evaluation_does_not_treat_unjudged_candidates_as_irrelevant():
    from app.search_engine.evaluation import metrics
    case = {'id': 'test', 'known_relevant': [{'label': 'paper', 'identifiers': ['arxiv:2312.02973'], 'judgment': 'partial'}]}
    result = metrics({'engine': {'candidates': [
        {'document_number': '10.1234/unjudged'}, {'url': 'https://arxiv.org/abs/2312.02973v2'}]}}, case, 2)
    assert result['known_relevant_recall_at_k'] == 1
    assert result['known_relevant_ranks'][0]['rank'] == 2
    assert 'precision_at_k' not in result


def test_pdf_quote_whitespace_recovery_preserves_original_and_rejects_changed_words():
    from app.search_engine.passages import quote_span
    raw = 'After obtaining the KL divergence of nearby 3D Gaussian\npairs, we select Gaussians.'
    quote = 'KL divergence of nearby 3D Gaussian pairs,'
    start, end, method = quote_span(raw, quote)
    assert raw[start:end] == 'KL divergence of nearby 3D Gaussian\npairs,'
    assert method == 'whitespace_normalized'
    assert quote_span(raw, quote.replace('KL divergence', 'center distance')) is None


def test_source_cache_does_not_reuse_results_from_a_different_cutoff(monkeypatch, tmp_path):
    from app.search_engine.sources import Sources
    calls = []
    class Tools:
        def __init__(self, **kwargs):
            self.cutoff = kwargs['cutoff']
        def call(self, name, arguments):
            calls.append(self.cutoff)
            return {'records': [], 'cutoff': self.cutoff}
    monkeypatch.setattr('app.search_engine.sources.PATHS', SimpleNamespace(data_dir=tmp_path, evidence_dir=tmp_path / 'evidence'))
    monkeypatch.setattr('app.search_engine.sources.SearchTools', Tools)
    old = Sources({}, tmp_path / 'old', '2020-01-01')
    new = Sources({}, tmp_path / 'new', '')
    old.search('arxiv', 'sensor')
    new.search('arxiv', 'sensor')
    assert new.search('arxiv', 'sensor')['cache_hit']
    assert calls == ['2020-01-01', '']


def test_progressive_startup_does_not_change_agy_global_permissions(monkeypatch):
    from contextlib import nullcontext
    from app import settings_service
    monkeypatch.setattr('app.db.session_scope', lambda: nullcontext(object()))
    monkeypatch.setattr(settings_service, 'get_all', lambda session: {'progressive_search_enabled': True})
    monkeypatch.setattr(settings_service, 'apply_agy_allowlist', lambda *args, **kwargs: pytest.fail('global permissions must not change'))
    settings_service.run_agy_allowlist_migration()


@pytest.mark.asyncio
@pytest.mark.parametrize('group,scope,date,expected_fallback', [
    ('X', 'description', '2024-01-01', False),
    ('Y', 'description', '2024-01-01', True),
    ('X', 'abstract', '2024-01-01', True),
    ('X', 'description', '2026-01-01', True),
])
async def test_relation_seed_requires_dated_fulltext_x_before_skipping_existing_search(
        monkeypatch, tmp_path, group, scope, date, expected_fallback):
    monkeypatch.setattr('app.search_engine.engine.PATHS', SimpleNamespace(data_dir=tmp_path, evidence_dir=tmp_path / 'evidence'))
    class Planner(FakeInference):
        async def call(self, phase, system, payload, **kwargs):
            return {**await super().call(phase, system, payload, **kwargs),
                    'seed_queries': ['gaussian neighbor covariance', 'gaussian neighbor cloning']}
    calls = []
    class Sources:
        def search(self, source, query, **kwargs):
            calls.append(query)
            return {'records': [{'document_number': 'EP1234567A1', 'title': 'Gaussian neighbor cloning',
                                 'publication_date': date}]}
    class RouteEngine(Engine):
        async def verify_candidates(self, count, *, selected=None):
            for c in self.ordered():
                c.document_classification = {'group': group}
                c.evidence = [{'feature': f.id, 'match': 'explicit', 'quote_verified': True,
                               'locator': {'scope': scope}} for f in self.features]
        async def triage(self): pass
        async def expand(self): pass
    engine = RouteEngine(claim='Gaussian cloning uses distance.', directory=tmp_path / 'run',
        inference=Planner(), sources=Sources(), depth='deep', cutoff='2025-01-01',
        values={'epo_integration_enabled': True, 'progressive_search_web_enabled': False})
    result = await engine.run()
    assert (any(step['lane'] == 'existing_search' for step in result['route'])) == expected_fallback
    assert calls[:2] == ['gaussian neighbor covariance', 'gaussian neighbor cloning']
    assert len(result['candidates']) == 1  # union survives transition, not a reset
    assert result['route'][0]['outcome'] == ('no_verified_x' if expected_fallback else 'verified_x')


@pytest.mark.asyncio
async def test_seed_stage_reserves_fallback_time_and_restores_deadline(monkeypatch, tmp_path):
    monkeypatch.setattr('app.search_engine.engine.PATHS', SimpleNamespace(data_dir=tmp_path, evidence_dir=tmp_path / 'evidence'))
    engine = Engine(claim='test', directory=tmp_path, inference=FakeInference(), values={}, depth='deep')
    engine.seed_queries = ['sensor distance covariance']
    observed = []
    async def web(*args, **kwargs):
        observed.append(engine.remaining(0))
    engine.web_seeds = web
    await engine.search_relation_seeds()
    assert 0 < observed[0] <= 60
    assert engine.stage_deadline is None and engine.remaining() > 100
    assert engine.route[0]['outcome'] == 'no_verified_x'


def test_seed_shortlist_spans_features_without_promoting_classification():
    from app.search_engine.ranking import seed_shortlist
    features = [Feature('A', 'input', ['image', 'viewpoint']), Feature('B', 'relation', ['connectivity', 'geometry'])]
    topic = Candidate('topic', 'EP1A1', 'image viewpoint', '', 'epo')
    relation = Candidate('relation', 'EP2A1', 'image viewpoint connectivity geometry', '', 'epo')
    result = seed_shortlist([topic, relation], features, 1)
    assert result == [relation] and relation.document_classification is None


def test_seed_plan_queries_are_optional_and_bounded():
    from app.search_engine.planner import seed_queries
    assert seed_queries({}) == []
    assert seed_queries({'seed_queries': 'bad'}) == []
    assert seed_queries({'seed_queries': [None, 'point cloud connectivity geometry',
        'point cloud connectivity geometry', 'sensor adjacency topology', 'extra unused query']}) == [
            'point cloud connectivity geometry', 'sensor adjacency topology']


@pytest.mark.asyncio
async def test_seed_failure_keeps_global_deadline_available_for_fallback(monkeypatch, tmp_path):
    monkeypatch.setattr('app.search_engine.engine.PATHS', SimpleNamespace(data_dir=tmp_path, evidence_dir=tmp_path / 'evidence'))
    engine = Engine(claim='test', directory=tmp_path, inference=FakeInference(), values={}, depth='deep')
    engine.seed_queries = ['sensor distance covariance']
    async def fail(*args, **kwargs):
        raise RuntimeError('source unavailable')
    engine.web_seeds = fail
    await engine.search_relation_seeds()
    assert engine.stage_deadline is None and engine.remaining() > 100
    assert engine.route[0]['outcome'] == 'no_verified_x'
    assert 'source unavailable' in engine.warnings[-1]
