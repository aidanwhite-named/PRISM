import asyncio
import json

import pytest

from app.search_engine import autonomous_store
from app.search_engine.search_review import validate, prioritize
from app.search_mcp_server import SearchTools
from app.providers.base import ExecutionOutcome
from .test_autonomous_search import engine, Provider, finding

QUOTE = 'The frame number initializes the key stream generator.'


def receipt(tmp_path, *, scope='claims', url=None):
    tools = SearchTools(work_dir=tmp_path, values={})
    tools._record({'id': 'read1', 'tool': 'source_fetch', 'state': 'completed', 'ok': True,
        'result': {'capture_artifact_id': 'capture1', 'verification_scope': scope, 'offset': 10,
            'records': [{'url': url or finding()['url'], 'fields': {scope: QUOTE}}]}})
    return tools


def review(verdict='strong', **extra):
    return {'verdict': verdict, 'reason': '번호가 생성기의 입력으로 사용됩니다.',
        'gaps': '' if verdict == 'strong' else '샘플별 가산 마스크는 확인되지 않습니다.',
        'queries': ['frame number additive mask'], 'passages': [{
            'capture_artifact_id': 'capture1', 'quote': QUOTE,
            'feature': '번호에 따른 키스트림 생성', 'relation': '생성기의 초기값으로 번호를 사용합니다.',
            'translation': '프레임 번호는 키스트림 생성기를 초기화합니다.'}], **extra}


def test_save_tool_validates_source_and_returns_gap_feedback(tmp_path):
    tools = receipt(tmp_path)
    reply = tools.call('save_findings', {'records': [finding(review=review('partial'))]})
    saved = autonomous_store.load(tmp_path)[0]['search_review']
    assert saved['status'] == 'source_checked'
    assert saved['passages'][0]['start'] == 10
    assert reply['search_gaps'][0]['queries'] == ['frame number additive mask']
    assert not reply['pending_source_checks']


@pytest.mark.parametrize('defect', ['abstract', 'page_text', 'other_document', 'wrong_quote', 'missing_translation'])
def test_snippets_wrong_identity_and_invented_quotes_never_pass(tmp_path, defect):
    receipt(tmp_path, scope=defect if defect in ('abstract', 'page_text') else 'claims',
            url='https://example.org/other' if defect == 'other_document' else None)
    item = review()
    if defect == 'wrong_quote':
        item['passages'][0]['quote'] = 'The frame number is transmitted to a receiver.'
    if defect == 'missing_translation':
        item['passages'][0]['translation'] = ''
    assert validate(tmp_path, finding(), item)['status'] == 'needs_review'


def test_unavailable_is_not_mismatch_and_partial_updates_keep_review(tmp_path):
    autonomous_store.merge(tmp_path, [finding(review=review('unavailable', passages=[]))])
    autonomous_store.merge(tmp_path, [finding(reason='updated')])
    row = autonomous_store.load(tmp_path)[0]
    assert row['search_review']['status'] == 'unavailable'
    assert row['search_review']['verdict'] == 'unavailable'


def test_checked_partial_precedes_unchecked_and_mismatch(tmp_path):
    tools = receipt(tmp_path)
    tools.call('save_findings', {'records': [finding(review=review('partial'))]})
    partial = autonomous_store.load(tmp_path)[0]
    mismatch = {**finding(3), 'search_review': {'status': 'source_checked', 'verdict': 'mismatch'}}
    assert prioritize([mismatch, finding(2), partial]) == [partial, finding(2), mismatch]


def test_mismatch_replaces_optimistic_reason_and_is_not_recommended(tmp_path):
    tools = receipt(tmp_path)
    tools.call('save_findings', {'records': [finding(triage_status='candidate',
        reason='Everything matches.', review=review('mismatch'))]})
    e = engine(tmp_path)
    e.refresh()
    candidate = e.snapshot()['candidates'][0]
    assert candidate['triage_status'] == 'rejected'
    assert candidate['reason'] == review('mismatch')['reason']
    assert candidate['difference'] == review('mismatch')['gaps']


def test_search_checks_and_refines_sources_in_one_provider_session(tmp_path):
    calls = []
    async def action(request, emit):
        calls.append(json.loads(request.user_message))
        tools = receipt(tmp_path)
        partial = tools.call('save_findings', {'records': [finding(review=review('partial'))]})
        assert partial['search_gaps'][0]['queries']
        tools.call('save_findings', {'records': [finding(review=review('strong'))]})
        return ExecutionOutcome(result_text='원문 확인 결과', exit_code=0,
            tool_calls=[{'name': 'web_search'}], usage={'input_tokens': 12, 'output_tokens': 3})
    e = engine(tmp_path, Provider(action))
    e.seconds = 60
    asyncio.run(e.run())
    assert len(calls) == 1
    assert e.inference.usage()['input_tokens'] == 12
    assert e.snapshot()['candidates'][0]['search_review']['status'] == 'source_checked'
    assert e.inference.provider.requests[0].timeout_seconds <= 60


def test_model_completion_keeps_pending_candidates_without_forced_retry(tmp_path):
    e = engine(tmp_path)
    e.seconds = 60
    asyncio.run(e.run())
    assert len(e.inference.provider.requests) == 1
    assert e.warnings
    assert e.stop_reason == 'model_complete'


def test_continuation_preserves_validated_review(tmp_path):
    previous = tmp_path / 'old'
    previous.mkdir()
    tools = receipt(previous)
    tools.call('save_findings', {'records': [finding(review=review())]})
    old = engine(previous)
    old.refresh()
    new = engine(tmp_path / 'new')
    new.restore(old.checkpoint())
    new.refresh()
    assert new.snapshot()['candidates'][0]['search_review']['status'] == 'source_checked'
