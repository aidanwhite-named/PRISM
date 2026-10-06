"""The active report contract works through upload, execution and persistence."""
import json

import pytest

from app.providers.base import ExecutionOutcome
from .fake_provider import DeterministicTestProvider
from .conftest import wait_for_job


@pytest.mark.parametrize('primary_absent', [False, True])
def test_v5_report_is_rendered_and_saved_without_redundant_positive_roles(client, monkeypatch, primary_absent):
    from app import comparison_review
    from .test_comparison_review import semantic_reply
    # This test isolates rendering/persistence. Stage-by-stage preparation has
    # separate integration coverage; the required final semantic check stays on.
    async def prepare(provider, request, **kwargs):
        return request, None, {'status': 'skipped', 'attempts': []}
    monkeypatch.setattr(comparison_review, 'prepare', prepare)
    calls = []
    claim = '청구항 1. 초기 음성 구간을 유지하는 제어기' if primary_absent else '청구항 1. 누적 소음에 따라 이득을 변경하는 제어기'
    async def execute(self, request, emit):
        calls.append(request)
        if request.system_prompt == comparison_review.SEMANTIC:
            reply = semantic_reply({'findings': []}, request)
            return ExecutionOutcome(result_text=json.dumps(reply, ensure_ascii=False), exit_code=0)
        assert '# PRISM 구조화 보고서 V5' in request.user_message
        data = {'version': 5,
            'documents': [{'attachment': 'ATT-01', 'document_number': 'US1', 'title': 'First'},
                          {'attachment': 'ATT-02', 'document_number': 'US2', 'title': 'Second'}],
            'evidence': [{'id': 'E1', 'attachment': 'ATT-01', 'sentence_ids': ['ATT-01-P0-T1'], 'language': 'ko', 'translation': ''},
                         {'id': 'E2', 'attachment': 'ATT-02', 'sentence_ids': ['ATT-02-P0-T1'], 'language': 'foreign',
                          'translation': '다른 제어기는 초기 음성 구간을 유지한다.'}],
            'components': [{'claim': '청구항 1', 'symbol': '(A)', 'feature': claim.split('. ', 1)[1],
                'similarity': 0 if primary_absent else 95, 'basis': 'direct', 'evidence': ['E1', 'E2'],
                'evidence_uses': {'E1': 'contrast' if primary_absent else 'support', 'E2': 'support' if primary_absent else 'contrast'},
                'reference_roles': {'ATT-01': 'not_found'} if primary_absent else {},
                'reasoning': '{{ATT-01}}의 {{E1}}은 이득 변경이며 {{ATT-02}}의 {{E2}}는 초기 구간 유지입니다. 각 처리의 대상과 동작을 구분합니다.',
                'difference': ''}],
            'summary': {'main_reason': '주 문헌의 제어기 처리를 기준으로 비교합니다.', 'relationships': '이득 변경과 구간 유지의 처리 차이를 구분합니다.'}}
        return ExecutionOutcome(result_text=json.dumps(data, ensure_ascii=False), exit_code=0, usage={'total_tokens': 10})
    monkeypatch.setattr(DeterministicTestProvider, 'execute', execute)
    uploaded = client.post('/api/uploads', data={'roles': json.dumps(['CITATION', 'CITATION'])}, files=[
        ('files', ('first.txt', '제어기는 누적 소음에 따라 이득을 변경한다.'.encode('utf-8'), 'text/plain')),
        ('files', ('second.txt', b'Another controller retains the initial speech segment.', 'text/plain'))])
    assert uploaded.status_code == 200, uploaded.text
    created = client.post('/api/jobs', json={'provider': 'test', 'claim_text': claim, 'batch_id': uploaded.json()['batch_id']})
    assert created.status_code == 201, created.text
    final = wait_for_job(client, created.json()['id'])
    assert final['status'] == 'SUCCEEDED', final.get('error_message')
    assert len(calls) == 2  # Report plus mandatory semantic review, no correction.
    report = final['analysis_manifest']['report']
    assert report['data']['version'] == 5 and report['issues'] == []
    assert '비교 설명:' in final['result_text']
    assert '보고서 항목 점검' not in final['result_text']
    assert final['analysis_manifest']['items'][0]['similarity'] == (0 if primary_absent else 95)
    reviews = report['comparison_checks'][0]['source_reviews']
    assert reviews['ATT-01']['role'] == ('not_found' if primary_absent else 'support')
    assert reviews['ATT-02']['role'] == ('support' if primary_absent else 'comparison')
