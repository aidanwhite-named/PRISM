import asyncio
import copy
import json

import pytest

from app.providers.base import ExecutionOutcome
from app.search_engine import autonomous_store
from app.search_engine.report import manifest, render
from app.search_engine.review_context import FILE, component_label
from app.search_engine.search_review import validate, summary_counts
from app.search_engine.storage import write_json
from .test_autonomous_search import Provider, engine, finding
from .test_search_review import receipt, review


FOCUS = {'mode': 'gap', 'components': [
    {'id': 'C001', 'symbol': '(A)', 'feature': '번호로 생성기를 초기화'},
    {'id': 'C002', 'symbol': '(B)', 'feature': '샘플마다 가산 마스크 생성'},
]}


def match(cid='C001', verdict='strong', **extra):
    return {'component_id': cid, 'verdict': verdict, 'reason': '검토한 생성기 원문과 대조했습니다.',
            'gaps': '' if verdict == 'strong' else '검토한 생성기 본문 범위에 샘플별 가산 마스크가 없습니다.',
            'passage_indices': [0] if verdict == 'strong' else [], **extra}


def saved_review(*matches):
    return review(component_matches=list(matches))


def test_reviewed_but_invalid_evidence_is_distinct_from_skipped_review(tmp_path):
    write_json(tmp_path / FILE, FOCUS)
    receipt(tmp_path)
    value = saved_review(match())
    value['passages'][0]['quote'] = 'invented quote'
    result = validate(tmp_path, finding(), value)
    checked, skipped = result['component_matches']
    assert checked['status'] == skipped['status'] == 'needs_review'
    assert checked['review_status'] == 'reviewed' and skipped['review_status'] == 'unreviewed'
    assert component_label(checked) == '검토함 · 근거 검증 필요'
    assert component_label(skipped) == '미검토'
    counts = summary_counts([{**finding(), 'search_review': result}], FOCUS)['components']
    assert counts[0]['reviewed_pending'] == 1 and counts[1]['unreviewed'] == 1


def test_duplicate_judgments_are_attempted_review_not_skipped_review(tmp_path):
    write_json(tmp_path / FILE, FOCUS)
    result = validate(tmp_path, finding(), saved_review(match(), match(verdict='partial')))
    checked, skipped = result['component_matches']
    assert checked['review_status'] == 'reviewed' and checked['status'] == 'needs_review'
    assert '중복' in checked['issues'][0]
    assert skipped['review_status'] == 'unreviewed'


@pytest.mark.parametrize('defect', ['', 'abstract', 'other_document', 'unknown_capture', 'missing_reason'])
def test_absence_requires_real_candidate_body_and_scope(tmp_path, defect):
    write_json(tmp_path / FILE, FOCUS)
    receipt(tmp_path, scope='abstract' if defect == 'abstract' else 'claims',
            url='https://example.org/other' if defect == 'other_document' else None)
    absent = match('C002', 'not_found', reviewed_capture_ids=['wrong' if defect == 'unknown_capture' else 'capture1'])
    if defect == 'missing_reason':
        absent['reason'] = ''
    result = validate(tmp_path, finding(), saved_review(match(), absent))
    m = result['component_matches'][1]
    assert m['status'] == ('needs_review' if defect else 'source_checked')
    if not defect:
        assert m['reviewed_sources'][0]['start'] == 10
        assert component_label(m) == '검토 완료 · 검토 범위 내 대응 근거 없음'


def test_partial_updates_preserve_checked_and_attempted_components(tmp_path):
    write_json(tmp_path / FILE, FOCUS)
    tools = receipt(tmp_path)
    tools.call('save_findings', {'records': [finding(review=saved_review(match(), match('C002', passage_indices=[2])))]})
    tools.call('save_findings', {'records': [finding(review=saved_review(match()))]})
    matches = autonomous_store.load(tmp_path)[0]['search_review']['component_matches']
    assert matches[0]['status'] == 'source_checked'
    assert matches[1]['review_status'] == 'reviewed'
    assert matches[1]['reason'] and matches[1]['issues']


def test_gap_search_can_deepen_review_within_the_same_session(tmp_path):
    requests = []

    async def action(request, emit):
        requests.append(request)
        tools = receipt(tmp_path)
        first = tools.call('save_findings', {'records': [finding(review=saved_review(match()))]})
        assert first['pending_source_checks']
        assert 'next_action' not in first
        tools.call('save_findings', {'records': [finding(review=saved_review(
            match('C002', 'not_found', reviewed_capture_ids=['capture1'])))]})
        return ExecutionOutcome(result_text='완료', exit_code=0, usage={'input_tokens': 10, 'output_tokens': 2},
                                tool_calls=[{'name': 'web_search'}])

    e = engine(tmp_path, Provider(action), focus=copy.deepcopy(FOCUS))
    e.seconds = 60
    asyncio.run(e.run())
    assert len(requests) == 1
    snapshot = e.snapshot()
    assert snapshot['stop_reason'] == 'model_complete'
    assert snapshot['review_summary']['pending'] == 0
    assert snapshot['usage']['input_tokens'] == 10
    assert len(snapshot['usage']['stages']) == 1
    assert manifest(snapshot, claim=e.claim, provider='codex')['status'] == 'complete'
    output = render(snapshot)
    assert '검토 완료 · 강한 대응' in output
    assert '검토 완료 · 검토 범위 내 대응 근거 없음' in output
    assert (tmp_path / 'search-round-1-response.txt').exists()
    assert not (tmp_path / 'search-round-2-response.txt').exists()


def test_gap_search_honors_model_stop_without_claiming_all_reviews_complete(tmp_path):
    async def action(request, emit):
        tools = receipt(tmp_path)
        tools.call('save_findings', {'records': [finding(review=saved_review(match()))]})
        return ExecutionOutcome(result_text='전부 완료했습니다.', exit_code=0, tool_calls=[{'name': 'web_search'}])

    e = engine(tmp_path, Provider(action), focus=copy.deepcopy(FOCUS))
    e.seconds = 60
    asyncio.run(e.run())
    assert len(e.inference.provider.requests) == 1
    snapshot = e.snapshot()
    assert snapshot['stop_reason'] == 'model_complete'
    assert not e.inference.provider.cancelled
    assert manifest(snapshot, claim=e.claim, provider='codex')['status'] == 'verification_incomplete'
    assert '### (B) · 미검토' in render(snapshot)
    assert e.warnings


def test_deadline_preserves_missing_review_without_starting_more_calls(tmp_path):
    async def action(request, emit):
        tools = receipt(tmp_path)
        tools.call('save_findings', {'records': [finding(review=saved_review(match()))]})
        return ExecutionOutcome(result_text='완료', exit_code=0, timed_out=True,
                                tool_calls=[{'name': 'web_search'}])

    e = engine(tmp_path, Provider(action), focus=copy.deepcopy(FOCUS))
    asyncio.run(e.run())
    assert len(e.inference.provider.requests) == 1
    assert e.stop_reason == 'deadline'
    assert e.snapshot()['review_summary']['components'][1]['unreviewed'] == 1


def test_incomplete_job_keeps_results_and_continuation_can_complete_reviews(client, monkeypatch):
    from app.db import session_scope
    from app.models import ExecutionJob
    from .fake_provider import DeterministicSearchProvider
    from .conftest import wait_for_job

    finish_reviews = False

    async def execute(self, request, emit):
        payload = json.loads(request.user_message)
        if payload.get('focus'):
            tools = receipt(request.work_dir)
            matches = [match()]
            if finish_reviews:
                matches.append(match('C002', 'not_found', reviewed_capture_ids=['capture1']))
            tools.call('save_findings', {'records': [finding(review=saved_review(*matches))]})
        else:
            autonomous_store.merge(request.work_dir, [finding()])
        return ExecutionOutcome(result_text='완료', exit_code=0, tool_calls=[{'name': 'WebSearch'}])

    monkeypatch.setattr(DeterministicSearchProvider, 'execute', execute)
    initial = client.post('/api/jobs', json={'job_kind': 'similarity_search', 'provider': 'test-search',
                                          'claim_text': 'A controls B'}).json()
    source = wait_for_job(client, initial['id'])
    assert source['status'] == 'SUCCEEDED'
    with session_scope() as session:
        session.get(ExecutionJob, source['id']).search_focus = copy.deepcopy(FOCUS)
    response = client.post(f"/api/jobs/{source['id']}/continue-search")
    assert response.status_code == 201, response.text
    partial = wait_for_job(client, response.json()['id'])
    assert partial['status'] == 'SUCCEEDED' and partial['error_code'] is None
    assert partial['errors'] == [] and partial['search_manifest_error'] is None
    assert partial['search_manifest']['status'] == 'verification_incomplete'
    assert partial['search_manifest']['engine']['stop_reason'] == 'model_complete'
    assert partial['usage']['input_tokens'] == 0
    assert len(partial['usage']['stages']) == 2  # Initial search plus this explicit continuation.
    assert '검토 완료 · 강한 대응' in partial['result_text'] and '### (B) · 미검토' in partial['result_text']
    finish_reviews = True
    response = client.post(f"/api/jobs/{partial['id']}/continue-search")
    assert response.status_code == 201, response.text
    completed = wait_for_job(client, response.json()['id'])
    assert completed['status'] == 'SUCCEEDED' and completed['error_code'] is None
    assert completed['search_manifest']['review_summary']['pending'] == 0
    assert completed['search_manifest']['status'] == 'complete'
    assert client.post(f"/api/jobs/{partial['id']}/continue-search").json()['id'] == completed['id']
