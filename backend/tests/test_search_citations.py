import json
from types import SimpleNamespace

import pytest

from app.search_engine.engine import Engine
from app.search_engine.models import Candidate, Feature
from app.search_engine.sources import Sources, patent_references
from app.patent_search.artifacts import ArtifactStore


XML = b'''<world><exchange-document country="EP" doc-number="123" kind="A1">
<citation><patcit><document-id document-id-type="docdb"><country>US</country><doc-number>456</doc-number><kind>B2</kind></document-id></patcit></citation>
<application-reference><document-id document-id-type="docdb"><country>US</country><doc-number>999</doc-number><kind>A1</kind></document-id></application-reference>
</exchange-document><exchange-document country="EP" doc-number="789" kind="A1">
<citation><patcit><document-id document-id-type="docdb"><country>US</country><doc-number>777</doc-number><kind>A1</kind></document-id></patcit></citation>
</exchange-document></world>'''


def test_patent_edges_exclude_other_documents_and_application_ids():
    assert patent_references(XML, 'EP123A1') == ['US456B2']
    with pytest.raises(Exception, match='DOCTYPE'):
        patent_references(b'<!DOCTYPE world>' + XML, 'EP123A1')


def test_backward_patent_preserves_exact_cited_grant_not_search_equivalent(tmp_path, monkeypatch):
    monkeypatch.setattr('app.search_engine.sources.PATHS', SimpleNamespace(evidence_dir=tmp_path))
    aid = ArtifactStore(tmp_path).put(XML)
    sources = Sources({}, tmp_path)
    fetched = []
    def fetch(candidate, scope):
        fetched.append((candidate.document_number, scope))
        return {'raw_artifact_id': aid, 'records': [{'document_number': 'US456A1'}]}
    sources.fetch = fetch
    result = sources.citation_neighbors(Candidate('a', 'EP123A1', '', '', 'epo'), 'backward')
    assert fetched == [('EP123A1', 'biblio'), ('US456B2', 'biblio')]
    assert [r['document_number'] for r in result['records']] == ['US456B2']
    assert result['records'][0]['evidence_refs']['citation_reference']['artifact_id'] == aid


def test_forward_patent_uses_ct_not_keyword_search(tmp_path):
    sources = Sources({}, tmp_path)
    calls = []
    def call(source, args):
        calls.append((source, args))
        return {'records': []}
    sources.call = call
    sources.citation_neighbors(Candidate('a', 'EP123A1', '', '', 'epo'), 'forward')
    assert calls[0][1]['query'] == {'type': 'term', 'field': 'ct', 'value': 'EP123'}
    from app.patent_search.epo_cql import build, Term
    assert 'ct' in build(Term(field='ct', value='EP123'))


@pytest.mark.parametrize('direction,parameter', [('backward', 'cited_by'), ('forward', 'cites')])
def test_paper_citations_preserve_artifact_and_direction_without_keyword_filter(tmp_path, monkeypatch, direction, parameter):
    from app.patent_search import openalex_client
    monkeypatch.setattr('app.search_engine.sources.PATHS', SimpleNamespace(evidence_dir=tmp_path))
    urls = []
    def send(client, url, **kwargs):
        urls.append(url)
        return SimpleNamespace(body=json.dumps({'results': [{'id': 'https://openalex.org/W2', 'display_name': 'Sensor',
            'publication_date': '2026-01-01', 'abstract_inverted_index': {'Sensor': [0]}}]}).encode())
    monkeypatch.setattr(openalex_client.OpenAlexClient, '_send', send)
    seed = Candidate('a', 'openalex:W1', '', '', 'openalex')
    result = Sources({}, tmp_path).citation_neighbors(seed, direction)
    assert parameter + ':' in urls[0] and 'search=' not in urls[0]
    assert result['records'][0]['publication_date'] == '2026-01-01'
    assert result['records'][0]['document_number'] == 'openalex:W2'
    assert ArtifactStore(tmp_path).read(result['raw_artifact_id'])


class Inference:
    def usage(self): return {'stages': [], 'input_tokens': 0}


@pytest.mark.asyncio
@pytest.mark.parametrize('verified_x', [False, True])
async def test_citation_stage_only_without_verified_x_and_does_not_promote_edges(tmp_path, verified_x):
    calls, selected = [], []
    class Provider:
        def citation_neighbors(self, seed, direction):
            calls.append((seed.document_number, direction))
            return {'records': [{'document_number': 'US456A1', 'title': 'sensor distance', 'publication_date': '2024-01-01'},
                                {'document_number': 'US789A1', 'title': 'sensor distance', 'publication_date': '2026-01-01'}]}
    engine = Engine(claim='sensor distance', directory=tmp_path, inference=Inference(),
        values={'epo_integration_enabled': True}, sources=Provider(), cutoff='2025-01-01')
    engine.features = [Feature('A', 'sensor distance', ['sensor', 'distance'])]
    seed = engine.ledger.add({'document_number': 'EP123A1', 'title': 'sensor distance', 'publication_date': '2024-01-01'},
        source='epo', query_id='original', feature='A', rank=1)
    if verified_x:
        seed.document_classification = {'group': 'X'}
        seed.evidence = [{'feature': 'A', 'match': 'explicit', 'quote_verified': True, 'locator': {'scope': 'description'}}]
    async def verify(count, **kwargs):
        selected.extend(c.document_number for c in kwargs['selected'])
    engine.verify_candidates = verify
    await engine.search_citations()
    assert calls == ([] if verified_x else [('EP123A1', 'backward'), ('EP123A1', 'forward')])
    if not verified_x:
        assert selected == ['US456A1']  # future document preserved but never verified as eligible
        assert len(engine.ledger.candidates) == 3 and len(engine.citation_edges) == 4
        assert all(c.document_classification is None for c in engine.ledger.candidates.values())
        assert engine.stage_deadline is None and engine.route[-1]['outcome'] == 'no_verified_x'


@pytest.mark.asyncio
async def test_citations_skip_when_verification_budget_exhausted(tmp_path):
    engine = Engine(claim='sensor', directory=tmp_path, inference=Inference(), values={})
    engine.deadline = engine.started + 25
    await engine.search_citations()
    assert engine.route[-1]['reason'] == 'reserved_budget'
    assert not engine.queries


@pytest.mark.asyncio
async def test_run_merges_citations_before_shared_verification_without_extra_llm_round(tmp_path):
    order = []
    class Source:
        def citation_neighbors(self, seed, direction):
            order.append(direction)
            return {'records': [{'document_number': 'US456A1', 'title': 'sensor distance'}]}
    class Flow(Engine):
        async def plan(self): pass
        async def search_relation_seeds(self): pass
        async def discover(self):
            self.ledger.add({'document_number': 'EP123A1', 'title': 'sensor distance'},
                            source='epo', query_id='seed', feature='A', rank=1)
        async def triage(self): pass
        async def verify_candidates(self, count, **kwargs):
            order.append('verify')
            assert {c.document_number for c in self.ledger.candidates.values()} == {'EP123A1', 'US456A1'}
    engine = Flow(claim='sensor distance', directory=tmp_path, inference=Inference(),
                  sources=Source(), values={'epo_integration_enabled': True}, depth='fast')
    result = await engine.run()
    assert order == ['backward', 'forward', 'verify']
    assert result['route'][-1]['outcome'] == 'candidates_merged'
