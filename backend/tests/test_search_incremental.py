import asyncio
import json
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.providers.base import AGY_WEB_SEARCH, ExecutionOutcome
from app.search_engine.engine import Engine
from app.search_engine.inference import Inference, InferenceError
from app.search_engine.models import Feature
from app.search_engine.passages import retrieve, source_from_fetch, verification_package
from app.search_engine.fetcher import Fetched
from app.search_engine.refinement import revisions
from app.search_engine.sources import Sources
from app.search_engine.web_progress import WebProgress
from app.retrieval.extraction import PageRecord
from .test_progressive_search import FakeInference


RECORD = {'title': 'Coupled rotation bounds', 'url': 'https://example.org/limits',
          'snippet': 'One coordinate constrains the other two and invalid configurations are projected to the boundary.'}


def test_partial_objects_repeated_batches_and_longer_evidence_are_retained():
    progress = WebProgress()
    text = json.dumps({'records': [RECORD]}, ensure_ascii=False)
    updates = []
    for char in text:
        updates.extend(progress.feed(char))
    assert len(updates) == 1
    assert progress.feed('\n' + text) == []
    second = {**RECORD, 'url': 'https://example.org/second'}
    assert len(progress.feed('\n{"records":[' + json.dumps(second) + ',{"title":"unfinished')) == 1
    assert len(progress.records) == 2
    assert not progress.feed('\n' + json.dumps({'title': 'No URL', 'document_number': 'JP1234567B1'}))


@pytest.mark.parametrize('url', ['file:///private', 'https://user:password@example.org/a',
                               'javascript:alert(1)', 'https://example.org:9999/a'])
def test_unusable_reported_urls_are_not_candidates(url):
    assert not WebProgress().feed(json.dumps({'records': [{**RECORD, 'url': url}]}))


def test_publication_url_identifies_exact_publication_and_rejects_mismatch():
    progress = WebProgress()
    row = {**RECORD, 'url': 'https://patents.google.com/patent/JP1234567B1/en'}
    assert progress.feed(json.dumps(row))[0]['document_number'] == 'JP1234567B1'
    assert not progress.feed(json.dumps({**row, 'document_number': 'US1234567B2'}))


@pytest.mark.asyncio
async def test_engine_publishes_before_provider_finishes_and_keeps_findings_on_timeout(tmp_path):
    async def execute(request, emit):
        await emit('tool_use', {'name': 'search_web', 'id': 's', 'input': {'query': 'rotation boundaries'}})
        await emit('result_stream', {'delta': json.dumps({'records': [RECORD]})})
        # The candidate must be on disk while the provider is still running.
        saved = json.loads((tmp_path / 'candidates.json').read_text())
        assert len(saved) == 1 and saved[0]['source'] == 'web_reported'
        assert not saved[0]['evidence']
        raise asyncio.TimeoutError()
    provider = SimpleNamespace(id='agy', max_input_bytes=None, search_tool_policy=AGY_WEB_SEARCH,
                               execute=execute, cancel=AsyncMock())
    inference = Inference(provider, job_id='test', directory=tmp_path / 'inference')
    engine = Engine(claim='coupled rotations', directory=tmp_path, inference=inference, values={})
    await engine.web_seeds([])
    assert len(engine.ledger.candidates) == 1
    assert engine.queries[0]['status'] == 'partial'
    assert inference.usage()['stages'][0]['retained_records'] == 1
    assert inference.usage()['stages'][0]['timed_out']
    assert len(next(iter(engine.ledger.candidates.values())).discoveries) == 1


@pytest.mark.asyncio
async def test_query_inputs_and_unsearched_model_output_do_not_become_findings(tmp_path):
    async def execute(request, emit):
        await emit('tool_use', {'name': 'read_url_content', 'input': RECORD})
        await emit('result_stream', {'delta': json.dumps({'records': [RECORD]})})
        return ExecutionOutcome(result_text=json.dumps({'records': [RECORD]}), tool_calls=[{'name': 'read_url_content'}])
    provider = SimpleNamespace(id='agy', max_input_bytes=None, search_tool_policy=AGY_WEB_SEARCH,
                               execute=execute, cancel=AsyncMock())
    inference = Inference(provider, job_id='test', directory=tmp_path)
    callback = AsyncMock()
    with pytest.raises(InferenceError, match='web_search_not_observed'):
        await inference.call('web_seeds', 'search', {}, seconds=5, web=True, on_records=callback)
    callback.assert_not_called()


@pytest.mark.asyncio
async def test_observed_empty_and_noisy_results_drive_additive_source_specific_revision(tmp_path):
    observed = []
    class Model(FakeInference):
        async def call(self, phase, system, payload, **kwargs):
            assert phase == 'revise_queries'
            assert payload['outcomes'][0]['hits'] == 0 and payload['claim'] == 'Coupled rotation bounds'
            return {'queries': [{'source': 'openalex', 'query': 'rotation coupled bounds',
                                'mode': 'title_and_abstract', 'reason': 'Disambiguate noisy results'}]}
    def search(source, query, **kwargs):
        observed.append((source, query, kwargs))
        return {'records': []}
    engine = Engine(claim='Coupled rotation bounds', directory=tmp_path, inference=Model(),
                    sources=SimpleNamespace(search=search), values={})
    engine.queries = [{'id': 'old', 'source': 'epo', 'query': 'overly restrictive original terms', 'hits': 0, 'status': 'completed'}]
    engine.ledger.add({'document_number': '10.1234/original', 'title': 'Existing candidate'},
                     source='openalex', query_id='old', feature='A', rank=1)
    await engine.revise_queries()
    assert observed == [('openalex', 'rotation coupled bounds', {'openalex_mode': 'title_and_abstract'})]
    assert len(engine.ledger.candidates) == 1 and len(engine.queries) == 2
    assert engine.checkpoint()['snapshot']['query_revisions']


def test_revision_rejects_invented_ids_unknown_sources_and_missing_reasons():
    rows = [{'source': 'epo', 'query': 'JP1234567B1 rotation', 'reason': 'memorized', 'mode': 'search'},
            {'source': 'unknown', 'query': 'joint limits', 'reason': 'invalid'},
            {'source': 'openalex', 'query': 'joint limits'}]
    assert revisions({'queries': rows}, ['epo', 'openalex']) == []


def test_revised_scope_reaches_the_adapter(tmp_path):
    sources = Sources({}, tmp_path)
    sources.call = lambda source, args: (source, args)
    source, args = sources.search('openalex', 'joint limits', openalex_mode='title_and_abstract')
    assert args['openalex_mode'] == 'title_and_abstract'


@pytest.mark.asyncio
async def test_collection_deadline_reserves_source_verification_after_classification(tmp_path):
    engine = Engine(claim='rotation limits', directory=tmp_path, inference=FakeInference(),
                    values={'progressive_search_web_enabled': False}, depth='deep')
    async def plan(): pass
    async def collect():
        # Spend all collection time without sleeping.
        spent = engine.stage_deadline - time.monotonic()
        engine.deadline -= spent
        engine.stage_deadline -= spent
        engine.ledger.add({'document_number': '10.1234/a', 'title': 'rotation limits'},
                         source='openalex', query_id='q', feature='A', rank=1)
    async def triage():
        # Spend the classification allowance; evidence time must still exist.
        engine.deadline -= max(0, engine.stage_deadline - time.monotonic())
        engine.classification_targets = list(engine.ledger.candidates)
        for c in engine.ordered():
            c.document_classification = {'group': 'X', 'reason': 'Potential full relation'}
    async def verify(count):
        assert engine.remaining() >= 25
        engine.ledger.event('verification_time_reserved')
    engine.plan, engine.search_relation_seeds, engine.triage, engine.verify_candidates = plan, collect, triage, verify
    await engine.run()
    assert any(e['event'] == 'verification_time_reserved' for e in engine.ledger.events)


def test_non_english_claims_are_available_even_without_lexical_matches(tmp_path):
    text = '親骨と子骨が関節で接続され、パラメータの範囲を判定し、制限内に補正する。' * 8
    found = retrieve('jp', {'artifact_id': 'a' * 64, 'url': 'https://example.org/jp', 'scope': 'claims'},
                     [PageRecord(1, text)], [Feature('A', 'coupled bone rotation', ['bone', 'rotation'])], tmp_path)
    assert found and found[0]['text'] == text and found[0]['offset'] == 0


@pytest.mark.parametrize('host,number,expected', [
    ('patents.google.com', 'JP1234567B1', 'claims'),
    ('example.org', 'JP1234567B1', 'page_text'),
    ('patents.google.com', 'US1234567B2', 'page_text'),
])
def test_labelled_patent_claims_require_matching_publication(host, number, expected):
    claim = 'A parent and child bone with coupled parameter bounds and correction to the allowed region. ' * 3
    html = f'<title>Pose</title><dd itemprop="publicationNumber">{number}</dd><section itemprop="claims"><claim num="1">{claim}</claim></section><section>Unrelated citations</section>'
    source, pages = source_from_fetch(Fetched(f'https://{host}/patent/JP1234567B1/en', html.encode(), 'text/html', 'a' * 64))
    assert source['scope'] == expected
    if expected == 'claims':
        assert 'Unrelated citations' not in pages[0].text and claim.strip() == pages[0].text


def test_claim_context_is_not_repeated_or_cut_to_a_small_feature_window():
    rows = []
    for cid in ('one', 'two', 'three'):
        rows.append({'id': cid, 'candidate_id': cid, 'feature': 'A', 'chunk_id': 'claims',
                     'text': 'early context ' * 180 + 'LATE COUPLED LIMIT RELATION ' + 'tail ' * 1000})
        for feature in 'ABCDE':
            rows.append({'id': cid + feature, 'candidate_id': cid, 'feature': feature,
                         'chunk_id': feature, 'text': 'lexical excerpt ' * 40})
    package = verification_package(rows)
    assert sum(len(p['text']) for p in package) <= 12000
    assert {p['candidate_id'] for p in package} == {'one', 'two', 'three'}
    assert all('LATE COUPLED LIMIT RELATION' in p['text'] for p in package if p['chunk_id'] == 'claims')


@pytest.mark.asyncio
async def test_empty_revision_is_reconsidered_once_using_its_actual_results(tmp_path):
    payloads = []
    class Model(FakeInference):
        async def call(self, phase, system, payload, **kwargs):
            payloads.append(payload)
            return {'queries': [{'source': 'openalex', 'query': 'joint limits pose' if len(payloads) == 1 else 'joint constraints',
                                  'mode': 'title_and_abstract', 'reason': 'Earlier query had zero results'}]}
    class EmptySources:
        def search(self, *args, **kwargs):
            return {'records': []}
    engine = Engine(claim='coupled rotations', directory=tmp_path, inference=Model(), sources=EmptySources(),
                    values={}, depth='exhaustive')
    engine.queries = [{'id': 'seed', 'source': 'openalex', 'query': 'too many constraints', 'status': 'completed', 'hits': 0}]
    await engine.revise_queries()
    assert len(payloads) == 2 and len(engine.query_revisions) == 2
    assert any(q['query'] == 'joint limits pose' and q['hits'] == 0 for q in payloads[1]['outcomes'])
    assert len(engine.queries) == 3
