"""Input-labelled coverage must work without a retrieval manifest."""
import json

import pytest

from app import analysis_completeness, claim_scope
from app.providers.base import ExecutionOutcome
from .conftest import wait_for_job
from .fake_provider import DeterministicTestProvider
from .test_followup import _upload


def item(claim='청구항 1', symbol='(A)', **extra):
    return {'claim': claim, 'symbol': symbol, 'feature': '센서 신호를 수신',
            'status': 'matched', 'similarity': 90, 'basis': 'direct', **extra}


def check(text, items, **extra):
    return analysis_completeness.check(claim_text=text, retrieval_manifest=None,
                                       analysis_manifest={'items': items}, **extra)


@pytest.mark.parametrize('heading', ['청구항 12.', '[청구항 12]', '【청구항 12】',
                                    '## 청구항 12', '**청구항 12**', 'Claim 12:'])
def test_explicit_components_retain_input_locations(heading):
    text = heading + ' (전제부) 통신 장치; (A) 신호 수신;\n(B) 조건에 따라 출력'
    scope = claim_scope.from_text(text)
    assert scope['claims'] == ['청구항 12']
    assert [c['symbol'] for c in scope['components']] == ['(전제부)', '(A)', '(B)']
    for c in scope['components']:
        assert text[c['source_start']:c['source_end']].strip() == c['feature']


def test_references_and_newlines_are_not_a_technical_decomposition():
    text = '청구항 1. 제2항에 있어서,\n센서(1)가 구성 (A)를 참조하고,\n조건에 따라 출력한다.'
    scope = claim_scope.from_text(text)
    assert not scope['components']
    assert not scope['component_scope_known']
    result = check(text, [item()])
    assert not result['complete']
    assert '자동 확인되지 않았습니다' in analysis_completeness.render(result)


def test_full_inline_reports_missing_component_even_when_every_label_was_changed():
    text = '청구항 1.\n(A) 수신;\n(B) 수신 전에 검증'
    result = check(text, [item(symbol='(X)')])
    assert result['missing_components'] == ['청구항 1 (A)', '청구항 1 (B)']
    assert not result['complete']
    assert '입력 청구항' in analysis_completeness.render(result)


def test_claims_use_their_own_labels_and_dependent_claim_cannot_disappear():
    text = '청구항 1.\n(A) 신호 수신\n청구항 2. 제1항에 있어서,\n(A) 수신 전에 검증'
    result = check(text, [item()])
    assert result['missing_claims'] == ['청구항 2']
    assert result['missing_components'] == ['청구항 2 (A)']


def test_symbol_and_claim_formatting_do_not_create_false_missing():
    result = check('청구항 01.\n(A) 수신\n(B) 출력',
                   [item(claim='Claim 1', symbol='[A]'), item(claim='청구항1', symbol='（B）')])
    assert not result['missing_components']
    assert result['complete']


def test_duplicate_or_failed_or_unparseable_analysis_is_not_complete():
    text = '청구항 1.\n(A) 수신'
    assert check(text, [item(), item()])['duplicate_components'] == ['청구항 1 (A)']
    assert not check(text, [item(), item()])['complete']
    assert not check(text, [item()], process_succeeded=False)['complete']
    assert not check(text, [item()], analysis_error='invalid')['complete']


def test_unnumbered_input_is_not_arbitrarily_assigned_to_multiple_claims():
    result = check('(A) 수신\n(B) 출력', [item(), item(claim='청구항 2', symbol='(B)')])
    assert not result['input_comparable']
    assert not result['complete']


def test_mixed_labelled_and_unlabelled_claims_do_not_claim_full_coverage():
    result = check('청구항 1.\n(A) 수신\n청구항 2. 제1항에 있어서 검증',
                   [item(), item(claim='청구항 2')])
    assert not result['missing_claims']
    assert not result['complete']


def test_full_inline_api_report_and_metadata_agree_on_missing_input_component(client, monkeypatch):
    requests = []
    async def execute(self, request, emit):
        requests.append(request)
        return ExecutionOutcome(exit_code=0, result_text='분석 결과\n[PRISM_COMPONENT_ANALYSIS_V1]\n'
            + json.dumps({'items': [item()]}, ensure_ascii=False)
            + '\n[/PRISM_COMPONENT_ANALYSIS_V1]\n[PRISM_CITATION_MAPPING_V1]\n'
            + '{"items":[{"citation_number":1,"attachment":"ATT-01","document_number":"문헌번호 확인 불가"}]}'
            + '\n[/PRISM_CITATION_MAPPING_V1]')
    monkeypatch.setattr(DeterministicTestProvider, 'execute', execute)
    created = client.post('/api/jobs', json={'provider': 'test',
        'claim_text': '청구항 1.\n(A) 신호 수신\n(B) 수신 전에 검증',
        'batch_id': _upload(client)}).json()
    job = wait_for_job(client, created['id'])
    assert job['status'] == 'SUCCEEDED', job['errors']
    assert job['retrieval_manifest'] is None
    assert job['analysis_completeness']['missing_components'] == ['청구항 1 (B)']
    assert '청구항 1 (B)' in job['result_text']
    assert requests
    assert '[PRISM 입력 구성 식별자]' in requests[-1].user_message
    assert job['analysis_manifest']['items'][0]['similarity'] == 90


def test_input_coverage_does_not_hide_report_issues():
    result = analysis_completeness.check(claim_text='청구항 1.\n(A) 수신',
        retrieval_manifest=None, analysis_manifest={'items': [item()],
            'report': {'issues': ['원문 근거 확인 필요']}})
    assert not result['missing_components']
    assert result['report_issues'] == ['원문 근거 확인 필요']
    assert not result['complete']
