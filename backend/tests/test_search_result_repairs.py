"""Regression coverage for failed excerpts, misleading summaries and self hits."""
import asyncio
import json

import pytest

from app.search_engine import autonomous_store, input_documents
from app.search_engine.report import manifest, render
from app.search_engine.search_review import validate, verified_group
from app.search_engine.source_passages import options
from app.providers.base import ExecutionOutcome
from app.search_mcp_server import SearchTools
from app import search_manifest
from .test_autonomous_search import engine, Provider, finding
from .test_search_review import receipt, review, QUOTE

BODY = ('A camera detects light from active fiducial markers.\n\n'
        'Received synchronization time stamps set the schedule for emission of light.')
SPEC = ('--- PAGE 1 ---\nDigital Object Identifier 10.1 109/ACCESS.2024.0429000\n'
        'Active Fiducial Marker-Based Precise Underwater\n'
        'Positioning System for Industrial and Robotics Applications\n'
        'YOUNG-WOON SONG1, DONGSUB KIM1\n'
        'Citation information: DOI 10.1109/ACCESS.2026.3654109\n'
        '--- PAGE 2 ---\nReferences\n[1] DOI 10.1109/OTHER.2015.7271491\n')
SELF = {'title': 'Active Fiducial Marker-Based Precise Underwater Positioning System for Industrial and Robotics Applications',
        'url': 'https://doi.org/10.1109/ACCESS.2026.3654109',
        'document_number': '10.1109/ACCESS.2026.3654109', 'reason': 'same paper'}


def observed(tools, body=BODY, *, offset=0, url=None):
    tools._record({'id': 'read1', 'tool': 'source_fetch', 'state': 'completed', 'ok': True,
        'result': {'capture_artifact_id': 'capture1', 'verification_scope': 'claims', 'offset': offset,
            'records': [{'url': url or finding()['url'], 'fields': {'claims': body}}]}})


def test_failed_ellipsis_has_observed_repair_options_and_can_be_saved_without_retyping(tmp_path):
    tools = SearchTools(work_dir=tmp_path, values={})
    observed(tools)
    bad = review(group='Y')
    bad['passages'][0]['quote'] = 'A camera detects light ... time stamps set the schedule for emission of light.'
    result = tools.call('save_findings', {'records': [finding(review=bad)]})
    assert verified_group(autonomous_store.load(tmp_path)[0]) is None
    repair = result['pending_source_checks'][0]['passage_issues'][0]['repair_options'][0]
    assert repair['quote'] in BODY and '...' not in repair['quote']
    corrected = review(group='Y')
    corrected['passages'][0].pop('quote')
    corrected['passages'][0]['passage_id'] = repair['passage_id']
    corrected['passages'][0]['translation'] = '수신한 동기화 타임스탬프로 발광 일정을 설정합니다.'
    tools.call('save_findings', {'records': [finding(review=corrected)]})
    saved = autonomous_store.load(tmp_path)[0]
    assert verified_group(saved) == 'Y'
    assert saved['search_review']['passages'][0]['quote'] == repair['quote']
    assert len([c for c in search_manifest.read_tool_journal(tmp_path)
                if c.get('tool') == 'source_fetch']) == 1


@pytest.mark.parametrize('defect', ['unobserved_range', 'other_document', 'missing_translation', 'invented_quote'])
def test_selectors_never_bypass_source_identity_or_translation(tmp_path, defect):
    tools = SearchTools(work_dir=tmp_path, values={})
    observed(tools, offset=100, url='https://example.org/other' if defect == 'other_document' else None)
    assessment = review(group='X')
    passage = assessment['passages'][0]
    passage.pop('quote')
    passage['passage_id'] = options(BODY, 'claims', 100)[0]['passage_id']
    if defect == 'unobserved_range':
        passage['passage_id'] = 'claims:0:50'
    if defect == 'missing_translation':
        passage['translation'] = ''
    if defect == 'invented_quote':
        passage['quote'] = 'The camera computes every sensor offset exactly.'
    assert validate(tmp_path, finding(), assessment)['status'] == 'needs_review'


def test_one_valid_passage_does_not_silently_certify_an_invalid_second_passage(tmp_path):
    receipt(tmp_path)
    assessment = review(group='Y')
    assessment['passages'].append({**assessment['passages'][0], 'quote': 'The camera computes an offset.'})
    assert validate(tmp_path, finding(), assessment)['status'] == 'needs_review'


def test_model_completion_claim_is_audit_only_and_report_uses_saved_review(tmp_path):
    receipt(tmp_path)
    bad = review(group='Y', reason='최신 잠정 대응 판단')
    bad['passages'][0]['quote'] = 'The frame number ... initializes the generator.'
    autonomous_store.merge(tmp_path, [finding(reason='이전 설명', review=bad)])
    e = engine(tmp_path)
    e.summary = '검증 완료: X와 Y를 찾았습니다.'
    e.phase, e.stop_reason = 'complete', 'model_complete'
    e.refresh()
    snapshot = e.snapshot()
    assert snapshot['model_summary'] == e.summary
    assert 'Y 0건' in snapshot['summary'] and '미완료 1건' in snapshot['summary']
    report = render(snapshot)
    assert '검증 완료: X' not in report and '이전 설명' not in report
    assert report.count('최신 잠정 대응 판단') == 1
    assert '[미분류]' in report and '검증 미완료 사유' in report
    assert manifest(snapshot, claim=e.claim, provider='codex')['status'] == 'verification_incomplete'


def test_historical_api_corrects_presentation_without_rewriting_original_record(client, tmp_path):
    from app.db import session_scope
    from app.models import ExecutionJob
    e = engine(tmp_path)
    autonomous_store.merge(tmp_path, [finding()])
    e.refresh()
    e.phase, e.stop_reason = 'complete', 'model_complete'
    snapshot = {**e.snapshot(), 'summary': '검증 완료: Y 문헌 확보'}
    snapshot.pop('model_summary')
    with session_scope() as session:
        job = ExecutionJob(job_kind='similarity_search', provider='test-search', status='SUCCEEDED',
            search_manifest={'version': 15, 'status': 'complete', 'engine': snapshot}, result_text='Y 검증 완료')
        session.add(job)
        session.flush()
        job_id = job.id
    response = client.get('/api/jobs/' + job_id)
    assert response.status_code == 200
    result = response.json()
    assert result['search_manifest']['status'] == 'verification_incomplete'
    assert '근거 확인 미완료 1건' in result['search_manifest']['engine']['summary']
    assert result['search_manifest']['engine']['model_summary'] == snapshot['summary']
    assert 'Y 0건' in result['result_text'] and 'Y 검증 완료' not in result['result_text']
    with session_scope() as session:
        assert session.get(ExecutionJob, job_id).search_manifest['status'] == 'complete'


def test_model_repairs_rejected_excerpt_using_same_session_receipt(tmp_path):
    calls = []
    async def action(request, emit):
        calls.append(json.loads(request.user_message))
        tools = SearchTools(work_dir=tmp_path, values={})
        observed(tools)
        bad = review(group='Y')
        bad['passages'][0]['quote'] = 'A camera detects ... emission of light.'
        result = tools.call('save_findings', {'records': [finding(review=bad)]})
        repair = result['pending_source_checks'][0]['passage_issues'][0]['repair_options'][0]
        fixed = review(group='Y')
        fixed['passages'][0].pop('quote')
        fixed['passages'][0]['passage_id'] = repair['passage_id']
        tools.call('save_findings', {'records': [finding(review=fixed)]})
        return ExecutionOutcome(result_text='검증을 완료했습니다.', exit_code=0,
            tool_calls=[{'name': 'web_search'}])
    e = engine(tmp_path, Provider(action), specification='context')
    asyncio.run(e.run())
    assert len(calls) == 1
    assert verified_group(e.records[0]) == 'Y'
    assert e.snapshot()['review_summary']['Y'] == 1


def test_input_identity_uses_first_page_not_the_reference_list():
    context = input_documents.from_specification(SPEC)
    assert 'doi:10.1109/access.2026.3654109' in context['identifiers']
    assert 'doi:10.1109/access.2024.0429000' in context['identifiers']
    assert 'doi:10.1109/other.2015.7271491' not in context['identifiers']
    assert input_documents.matches(SELF, context)
    assert input_documents.matches({**SELF, 'document_number': '', 'url': 'https://publisher.org/article'}, context)
    assert not input_documents.matches({**SELF, 'title': SELF['title'] + ' II', 'document_number': '',
                                       'url': 'https://publisher.org/other'}, context)
    assert 'doi:10.1234/background' not in input_documents.from_specification(
        SPEC.replace('--- PAGE 2 ---', 'Related work: see 10.1234/background\n--- PAGE 2 ---'))['identifiers']


def test_excerpt_repair_can_fetch_additional_source_context(tmp_path, monkeypatch):
    tools = SearchTools(work_dir=tmp_path, values={})
    called = []
    monkeypatch.setattr(tools, '_execute', lambda name, args: called.append(name) or {})
    tools.call('source_fetch', {'url': finding()['url']})
    assert called == ['source_fetch']


@pytest.mark.parametrize('name,args', [('source_fetch', {'url': SELF['url']}),
    ('literature_fetch', {'doi': SELF['document_number']})])
def test_known_input_fetch_is_skipped_before_network_or_provider_backend(tmp_path, monkeypatch, name, args):
    e = engine(tmp_path, specification=SPEC)
    e.prepare_input_documents()
    tools = SearchTools(work_dir=tmp_path, values={})
    monkeypatch.setattr(tools, 'statuses', lambda: {s: {'status': 'available'} for s in ('epo', 'literature', 'kipris')})
    def unexpected(*_):
        raise AssertionError('Input document must not trigger a source request')
    monkeypatch.setattr(tools, '_execute', unexpected)
    assert tools.call(name, args)['excluded_input_document']
    assert len(input_documents.exclusions(tmp_path)) == 1


def test_search_filters_self_hit_and_learns_publisher_url_for_later_fetch(tmp_path, monkeypatch):
    e = engine(tmp_path, specification=SPEC)
    e.prepare_input_documents()
    tools = SearchTools(work_dir=tmp_path, values={})
    monkeypatch.setattr(tools, 'statuses', lambda: {s: {'status': 'available'} for s in ('epo', 'literature', 'kipris')})
    alias = {**SELF, 'url': 'https://publisher.org/article'}
    calls = []
    monkeypatch.setattr(tools, '_execute', lambda name, args: calls.append(name) or {'records': [alias, finding()]})
    result = tools.call('literature_search', {'query': 'active fiducial'})
    assert result['records'] == [finding()]
    assert tools.call('source_fetch', {'url': alias['url']})['excluded_input_document']
    assert calls == ['literature_search']


def test_streamed_self_hit_is_excluded_and_does_not_start_verification(tmp_path):
    async def action(request, emit):
        assert json.loads(request.user_message)['excluded_input_documents']['identifiers']
        await emit('result_stream', {'delta': json.dumps({'records': [SELF]})})
        return ExecutionOutcome(result_text='입력과 동일한 논문', exit_code=0, tool_calls=[{'name': 'web_search'}])
    e = engine(tmp_path, Provider(action), specification=SPEC)
    asyncio.run(e.run())
    assert e.snapshot()['candidates'] == []
    assert len(e.inference.provider.requests) == 1
    assert e.snapshot()['excluded_input_documents'][0]['document_number'] == SELF['document_number']
    assert '동일한 문헌 1건' in render(e.snapshot())


def test_one_input_paper_with_multiple_urls_has_one_exclusion_record(tmp_path):
    input_documents.record_exclusion(tmp_path, SELF)
    alias = {**SELF, 'document_number': '', 'url': 'https://publisher.org/alias'}
    input_documents.record_exclusion(tmp_path, alias)
    input_documents.record_exclusion(tmp_path, {'url': alias['url']})
    assert len(input_documents.exclusions(tmp_path)) == 1
    assert input_documents.is_input(tmp_path, {'url': alias['url']})
