from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app import settings_service, search_channels
from app.config import DEFAULTS, PATHS
from app.db import session_scope
from app.models import AppSetting
from app.patent_search import kipris_backend as kb, kipris_quota as quota
from app.patent_search.base import PatentSearchError, PatentSearchQuery, PatentSearchNotConfigured
from app.patent_search.artifacts import ArtifactStore
from app.patent_search.provenance import verify_excerpt
from app.search_mcp_server import SearchTools
from app.search_engine.sources import Sources
from app.search_engine.engine import Engine
from app.search_engine.planner import kipris_queries

XML = '''<response><header><resultCode>00</resultCode></header><body><items>
<TotalSearchCount>123</TotalSearchCount><SearchStartNumber>1</SearchStartNumber>
<PatentUtilityInfo><ApplicationNumber>1020190000123</ApplicationNumber>
<OpeningNumber>10-2020-0012345</OpeningNumber><OpeningDate>20200203</OpeningDate>
<InventionName>배터리 냉각 장치</InventionName><Abstract>유체 채널을 이용하여 배터리를 냉각한다.</Abstract>
<Applicant>연구소</Applicant><InternationalpatentclassificationNumber>H01M10/613</InternationalpatentclassificationNumber>
</PatentUtilityInfo></items></body></response>'''.encode()
VALUES = {**DEFAULTS, 'kipris_integration_enabled': True, 'kipris_api_key': 'test-access-key'}


@pytest.fixture(autouse=True)
def isolated_kipris(client, monkeypatch):
    keys = ('kipris_integration_enabled', 'kipris_api_key', quota.KEY)
    with session_scope() as session:
        previous = {key: session.get(AppSetting, key).value for key in keys if session.get(AppSetting, key)}
        for key in keys:
            row = session.get(AppSetting, key)
            if row:
                session.delete(row)
    def refuse(params):
        raise AssertionError('Tests must inject a KIPRIS transport')
    monkeypatch.setattr(kb, '_live_transport', refuse)
    yield
    with session_scope() as session:
        for key in keys:
            row = session.get(AppSetting, key)
            if row:
                session.delete(row)
        session.flush()
        for key, value in previous.items():
            session.add(AppSetting(key=key, value=value))


def state():
    with session_scope() as session:
        return settings_service.get_all(session)[quota.KEY]


def test_kst_month_boundary_and_december_rollover():
    before = datetime(2026, 12, 31, 14, 59, 59, tzinfo=timezone.utc)
    after = datetime(2026, 12, 31, 15, 0, 0, tzinfo=timezone.utc)
    quota.reserve(before)
    assert quota.snapshot(state(), before)['requests'] == 1
    assert quota.snapshot(state(), before)['reset_at'] == '2027-01-01T00:00:00+09:00'
    assert quota.snapshot(state(), after)['requests'] == 0
    quota.reserve(after)
    assert set(state()['months']) == {'2026-12', '2027-01'}


def test_concurrent_reservation_and_last_call_cannot_exceed_limit():
    month = quota.month_at()
    quota.reconcile(995, month)
    def request(_):
        try:
            quota.reserve()
            return True
        except quota.KiprisQuotaExceeded:
            return False
    with ThreadPoolExecutor(max_workers=8) as pool:
        outcomes = list(pool.map(request, range(20)))
    assert sum(outcomes) == 5
    result = quota.snapshot(state())
    assert result['used'] == 1000 and result['requests'] == 5 and result['blocked']
    with pytest.raises(ValueError):
        quota.reconcile(1, month)
    with pytest.raises(ValueError, match='월이 변경'):
        quota.reconcile(1000, '2025-01')


def test_quota_persists_across_backend_instances_and_failure():
    def fail(params):
        raise RuntimeError('https://example/?accessKey=test-access-key')
    backend = kb.KiprisBackend(transport=fail)
    backend.configure(VALUES)
    with pytest.raises(PatentSearchError) as exc:
        backend.search(PatentSearchQuery('냉각'))
    assert 'test-access-key' not in str(exc.value)
    assert quota.snapshot(state())['requests'] == 1
    other = kb.KiprisBackend(transport=lambda p: XML)
    other.configure(VALUES)
    other.search(PatentSearchQuery('냉각'))
    assert quota.snapshot(state())['requests'] == 2


def test_no_network_when_disabled_missing_key_or_quota_blocked():
    sent = []
    backend = kb.KiprisBackend(transport=lambda p: sent.append(p) or XML)
    for values in ({}, {**VALUES, 'kipris_api_key': ''}):
        backend.configure(values)
        with pytest.raises(PatentSearchNotConfigured):
            backend.search(PatentSearchQuery('냉각'))
    quota.reconcile(1000, quota.month_at())
    backend.configure(VALUES)
    with pytest.raises(quota.KiprisQuotaExceeded):
        backend.search(PatentSearchQuery('냉각'))
    assert not sent


def test_persistence_failure_prevents_transmission(monkeypatch):
    monkeypatch.setattr(quota, '_change', lambda **kw: (_ for _ in ()).throw(OSError('disk')))
    sent = []
    backend = kb.KiprisBackend(transport=lambda p: sent.append(p))
    backend.configure(VALUES)
    with pytest.raises(PatentSearchError, match='저장'):
        backend.search(PatentSearchQuery('냉각'))
    assert not sent


def test_official_fields_identity_date_and_reproducible_abstract():
    sent = []
    backend = kb.KiprisBackend(transport=lambda p: sent.append(p) or XML)
    backend.configure(VALUES)
    result = backend.search(PatentSearchQuery('배터리 냉각', 20), begin=2)
    assert sent[0]['docsStart'] == 2 and sent[0]['accessKey'] == 'test-access-key'
    assert sent[0]['patent'] == sent[0]['utility'] == 'true'
    assert 'accessKey' not in result.request_url
    assert result.total_found == 123
    record = result.records[0]
    assert record.doc_number == 'KR20200012345A'
    assert record.fields['publication_date'].value == '20200203'
    check = verify_excerpt(excerpt='유체 채널을 이용하여 배터리를 냉각한다.',
                           field=record.fields['abstract:ko'], store=ArtifactStore(PATHS.evidence_dir))
    assert check.verified
    assert not kb.parsers.get_profile(kb.PROFILE).raw_capable


@pytest.mark.parametrize('data', [b'<html>maintenance</html>', b'<response><resultCode>30</resultCode></response>',
    b'<!DOCTYPE x [<!ENTITY foo "bar">]><x>&foo;</x>', b'<broken'])
def test_faults_and_invalid_xml_are_not_zero_results(data):
    with pytest.raises(PatentSearchError):
        kb.parse(data)


def test_zero_results_and_application_number_not_a_publication():
    assert kb.parse(b'<response><TotalSearchCount>0</TotalSearchCount></response>') == ([], 0)
    assert kb.document({'applicationnumber': '1020251234567'}) is None
    number, fields = kb.document({'registrationnumber': '10-1234567-0000', 'registrationdate': '20200101'})
    assert number == 'KR101234567B1' and 'publication_date' not in fields


def test_settings_redaction_connection_count_and_external_reconciliation(client, monkeypatch):
    monkeypatch.setattr(kb, '_live_transport', lambda p: XML)
    response = client.put('/api/settings', json={'values': {
        'kipris_api_key': 'test-access-key', 'kipris_integration_enabled': True}})
    assert response.status_code == 200
    assert response.json()['values']['kipris_api_key'] == ''
    assert response.json()['secrets_set']['kipris_api_key']
    assert 'test-access-key' not in response.text
    assert client.post('/api/settings/kipris/check').json()['ok']
    usage = client.get('/api/settings').json()['kipris_quota']
    assert usage['requests'] == 1
    result = client.put('/api/settings/kipris/usage', json={'month': usage['month'], 'total_used': 850})
    assert result.json()['kipris_quota']['remaining'] == 150
    assert result.json()['kipris_quota']['warning']
    assert client.put('/api/settings', json={'values': {quota.KEY: {}}}).status_code == 400
    assert client.put('/api/settings', json={'values': {'kipris_integration_enabled': 'false'}}).status_code == 400


def test_tool_registration_artifacts_cache_and_no_secret_in_journal(tmp_path, monkeypatch):
    sent = []
    monkeypatch.setattr(kb, '_live_transport', lambda p: sent.append(p) or XML)
    tools = SearchTools(values=VALUES, work_dir=tmp_path / 'tools')
    assert 'kipris_search' in {t['name'] for t in tools.tool_definitions()}
    names = search_channels.available_mcp_names(tools.statuses())
    assert 'mcp__prism-search__kipris_search' in names
    assert 'mcp__prism-search__kipris_fetch' not in names
    result = tools.call('kipris_search', {'query': '고유 배터리', 'max_results': 20})
    assert result['records'][0]['document_number'] == 'KR20200012345A'
    assert 'test-access-key' not in tools.ledger_path.read_text(encoding='utf-8')
    sources = Sources(VALUES, tmp_path / 'sources')
    first = sources.search('kipris', tmp_path.name + ' 냉각')
    second = sources.search('kipris', tmp_path.name + ' 냉각')
    assert second['cache_hit'] and first['records'] == second['records']
    assert len(sent) == 2 and quota.snapshot(state())['requests'] == 2
    page = tools.call('kipris_search', {'query': '고유 배터리', 'max_results': 20, 'begin': 2})
    assert page['coverage']['result_range'] == '21-21'
    assert page['coverage']['next_page'] == 3


async def test_engine_domestic_search_runs_with_epo_and_literature_disabled(tmp_path):
    sources = SimpleNamespace(search=lambda *args, **kwargs: {'records': [
        {'document_number': 'KR20200012345A', 'title': '배터리 냉각', 'fields': {}}]})
    inference = SimpleNamespace(usage=lambda: {'stages': []})
    engine = Engine(claim='배터리 냉각 장치', directory=tmp_path, inference=inference,
        values={**VALUES, 'epo_integration_enabled': False, 'literature_integration_enabled': False}, sources=sources)
    await engine.query('kipris', '배터리 냉각', 'domestic')
    assert len(engine.ledger.candidates) == 1 and engine.queries[0]['status'] == 'completed'
    assert engine.queries[0]['source'] == 'kipris'


async def test_domestic_lane_precedes_early_seed_success(tmp_path):
    engine = Engine(claim='배터리 냉각', directory=tmp_path, inference=SimpleNamespace(usage=lambda: {}),
                    values=VALUES, depth='fast')
    engine.plan = AsyncMock()
    engine.query = AsyncMock()
    async def seeds():
        assert engine.query.await_count == 1
    engine.search_relation_seeds = seeds
    engine.sufficient = lambda: True
    engine.publish = AsyncMock()
    await engine.run()
    assert engine.query.call_args.args[0] == 'kipris'


def test_korean_query_plan_and_fallback():
    assert kipris_queries({'kipris_queries': ['배터리 냉각', '전지 방열', 'ignored']}, '') == ['배터리 냉각', '전지 방열']
    assert kipris_queries({}, '상기 배터리 냉각 장치') == ['배터리 냉각']


def test_korean_candidates_are_ranked_and_passages_retrieved(tmp_path):
    from app.search_engine.planner import parse_plan
    from app.search_engine.models import Candidate
    from app.search_engine.ranking import rank
    from app.search_engine.passages import retrieve
    from app.retrieval.extraction import PageRecord

    features, _, _ = parse_plan({'features': [{'text': '배터리 냉각',
        'terms': ['battery', 'cooling'], 'korean_terms': ['배터리', '냉각']} ]}, '배터리 냉각')
    domestic = Candidate('ko', 'KR20200012345A', '배터리 냉각', '', 'kipris')
    foreign = Candidate('en', 'US20200012345A1', 'battery cooling', '', 'epo')
    irrelevant = Candidate('other', 'KR20200012346A', '컴퓨터 화면', '', 'kipris')
    rank([domestic, foreign, irrelevant], features)
    assert domestic.lexical_score == foreign.lexical_score == 1
    assert irrelevant.lexical_score == 0
    passages = retrieve('ko', {'artifact_id': 'b' * 64, 'url': 'https://example.org',
        'scope': 'abstract', 'content_type': 'application/xml'},
        [PageRecord(1, '배터리 냉각 유체 채널을 구비한다. ' * 12)], features, tmp_path)
    assert passages and '배터리' in passages[0]['text']
