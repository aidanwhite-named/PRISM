"""A no-match comparison may cite real text without asserting positive support."""
import copy
import json
from pathlib import Path

import pytest

from app import report_consistency, report_repair, structured_report
from .test_structured_report import report_input, compile_input
from .test_multi_document_report import multi_input
from .test_report_consistency import patch, Provider
from app.providers.base import ExecutionRequest, ExecutionOutcome


@pytest.fixture
def purpose_input(multi_input):
    multi_input[0]['version'] = 5
    row = multi_input[0]['components'][0]
    row.update(evidence_uses={'E1': 'support', 'E2': 'support'}, reference_roles={})
    return multi_input


def test_positive_document_roles_are_derived_from_linked_support(purpose_input):
    _, manifest, _ = compile_input(purpose_input)
    check = manifest['report']['comparison_checks'][0]
    assert not manifest['report']['issues']
    assert {a: r['role'] for a, r in check['source_reviews'].items()} == {
        'ATT-01': 'support', 'ATT-02': 'support'}
    assert check['evidence_uses'] == {'E1': 'support', 'E2': 'support'}
    assert all(r['origin'] == 'support_links' for r in check['source_reviews'].values())
    assert manifest['report']['data']['components'][0]['reference_roles'] == {}


def test_no_match_can_quote_a_different_process_without_becoming_support(purpose_input):
    row = purpose_input[0]['components'][0]
    row.update(similarity=0, evidence_uses={'E1': 'contrast', 'E2': 'support'},
               reference_roles={'ATT-01': 'not_found'},
               reasoning='{{ATT-01}}의 {{E1}}은 다른 입력에 대한 처리입니다. {{ATT-02}}의 {{E2}}는 초기 구간 유지에 대응합니다.')
    report, manifest, _ = compile_input(purpose_input)
    assert not manifest['report']['issues']
    assert manifest['items'][0]['similarity'] == 0
    assert '비교 설명:' in report
    assert manifest['report']['comparison_checks'][0]['source_reviews']['ATT-01']['role'] == 'not_found'


@pytest.mark.parametrize('defect', ['missing_use', 'invalid_use', 'orphan_use', 'unexplained_quote',
                                   'positive_score_on_contrast', 'positive_with_no_positive_quote',
                                   'unavailable_with_quote', 'negative_with_support'])
def test_purposes_do_not_hide_real_inconsistencies(purpose_input, defect):
    row = purpose_input[0]['components'][0]
    if defect == 'missing_use':
        row.pop('evidence_uses')
    elif defect == 'invalid_use':
        row['evidence_uses']['E2'] = 'background'
    elif defect == 'orphan_use':
        row['evidence_uses']['E99'] = 'support'
    elif defect == 'unexplained_quote':
        row['reasoning'] = row['reasoning'].replace('{{E2}}', '기재')
    elif defect == 'positive_score_on_contrast':
        row.update(evidence_uses={'E1': 'contrast', 'E2': 'support'}, reference_roles={'ATT-01': 'not_found'})
    elif defect == 'positive_with_no_positive_quote':
        row.update(similarity=0, evidence_uses={'E1': 'contrast', 'E2': 'support'}, reference_roles={'ATT-01': 'support'})
    elif defect == 'unavailable_with_quote':
        row.update(similarity=None, evidence_uses={'E1': 'contrast', 'E2': 'support'}, reference_roles={'ATT-01': 'unavailable'})
    else:
        row['reference_roles'] = {'ATT-01': 'not_found'}
    _, manifest, _ = compile_input(purpose_input)
    assert manifest['report']['issues']
    assert report_consistency.targets(compile_input(purpose_input)) == [1]


def test_comparison_quote_does_not_require_a_document_wide_absence_judgment(purpose_input):
    row = purpose_input[0]['components'][0]
    row.update(similarity=0, evidence_uses={'E1': 'contrast', 'E2': 'support'})
    _, manifest, _ = compile_input(purpose_input)
    assert not manifest['report']['issues']
    review = manifest['report']['comparison_checks'][0]['source_reviews']['ATT-01']
    assert review['role'] == 'comparison' and review['origin'] == 'comparison_links'
    assert row['reference_roles'] == {}


def test_mention_without_quote_or_review_stays_unresolved(purpose_input):
    row = purpose_input[0]['components'][0]
    row.update(evidence=['E1'], evidence_uses={'E1': 'support'},
               reasoning='{{ATT-01}}의 {{E1}}이 대응하며 {{ATT-02}}도 사용합니다.')
    _, manifest, _ = compile_input(purpose_input)
    assert any('검토 결과가 누락' in p for p in manifest['report']['issues'])


def test_no_implicit_comparison_exception_for_historical_reports(multi_input):
    row = multi_input[0]['components'][0]
    row.update(similarity=0, reference_roles={'ATT-01': 'not_found', 'ATT-02': 'support'})
    _, manifest, _ = compile_input(multi_input)
    assert any('긍정 근거로도' in p for p in manifest['report']['issues'])


def test_reused_quote_keeps_each_components_distinct_purpose(purpose_input):
    data = purpose_input[0]
    row = copy.deepcopy(data['components'][0])
    row.update(symbol='(B)', similarity=0, evidence_uses={'E1': 'contrast', 'E2': 'support'},
               reference_roles={'ATT-01': 'not_found'})
    data['components'].append(row)
    report, manifest, _ = compile_input(purpose_input)
    assert not manifest['report']['issues']
    assert report.count('"The controller changes') == 1
    assert '근거 E1 (비교 설명): 청구항 1 (A)의 근거 E1 참조.' in report


def test_sentence_split_preserves_purpose_on_every_new_evidence_id(purpose_input):
    row = purpose_input[0]['components'][0]
    expanded = report_repair._expand_links(row, {'E1': ['E1', 'E3']})
    assert expanded['evidence'] == ['E1', 'E3', 'E2']
    assert expanded['evidence_uses'] == {'E1': 'support', 'E3': 'support', 'E2': 'support'}
    assert '{{E3}}' in expanded['reasoning']
    assert row['evidence_uses'] == {'E1': 'support', 'E2': 'support'}


def missing_quote(values):
    row = values[0]['components'][0]
    row.update(evidence=['E1'], reasoning='{{ATT-01}}의 {{E1}}은 이득 변경에 대응합니다. {{ATT-02}}도 대응합니다.')
    return row


def test_missing_quote_review_reopens_affected_documents_delivered_text(multi_input):
    missing_quote(multi_input)
    compiled = compile_input(multi_input)
    sources = report_consistency.review_sources(compiled, [1], aliases=multi_input[1], attachments=multi_input[2])
    assert {s['attachment'] for s in sources} == {'ATT-01', 'ATT-02'}
    second = next(s for s in sources if s['attachment'] == 'ATT-02')
    assert second['sentences'][0]['id'] == 'ATT-02-P0-T1'
    assert 'retains the initial speech segment' in second['sentences'][0]['text']


@pytest.mark.parametrize('reviewed', [False, True])
def test_new_no_match_judgment_requires_reopened_source(multi_input, reviewed):
    row = missing_quote(multi_input)
    compiled = compile_input(multi_input)
    correction = patch(row, evidence=['E1', 'E2'], evidence_uses={'E1': 'support', 'E2': 'contrast'},
        reference_roles={'ATT-02': 'not_found'},
        reasoning='{{ATT-01}}의 {{E1}}은 이득 변경에 대응합니다. {{ATT-02}}의 {{E2}}는 다른 대상의 유지입니다.')
    corrected, accepted, rejected = report_consistency.apply_patches(compiled, [correction], [1],
        aliases=multi_input[1], attachments=multi_input[2], reviewed_attachments=['ATT-02'] if reviewed else [])
    assert bool(accepted) == reviewed
    assert bool(rejected) != reviewed
    if reviewed:
        assert not corrected[1]['report']['issues']


@pytest.mark.parametrize('defect', [None, 'not_reviewed', 'fabricated_sentence', 'wrong_document', 'existing_id'])
def test_repair_can_select_real_missing_excerpt_but_cannot_invent_it(multi_input, defect):
    path = Path(multi_input[2][1].normalized_text_path)
    path.write_text('Another controller retains the initial speech segment. A timer defines the retention period.', encoding='utf-8')
    row = missing_quote(multi_input)
    compiled = compile_input(multi_input)
    evidence = {'id': 'E3', 'attachment': 'ATT-02', 'sentence_ids': ['ATT-02-P0-T2'],
                'language': 'foreign', 'translation': '타이머는 유지 기간을 정의한다.'}
    correction = patch(row, evidence=['E1', 'E3'], evidence_uses={'E1': 'support', 'E3': 'support'},
        reference_roles={}, reasoning='{{ATT-01}}의 {{E1}}은 이득 변경에, {{ATT-02}}의 {{E3}}은 유지 기간에 대응합니다.')
    if defect == 'fabricated_sentence':
        evidence['sentence_ids'] = ['ATT-02-P0-T999']
    elif defect == 'wrong_document':
        evidence['sentence_ids'] = ['ATT-01-P0-T1']
    elif defect == 'existing_id':
        evidence['id'] = 'E2'
    if defect in ('not_reviewed', 'existing_id'):
        with pytest.raises(ValueError):
            report_consistency.apply_patches(compiled, [correction], [1], aliases=multi_input[1],
                attachments=multi_input[2], new_evidence=[evidence],
                reviewed_attachments=[] if defect == 'not_reviewed' else ['ATT-02'])
        return
    corrected, accepted, rejected = report_consistency.apply_patches(compiled, [correction], [1],
        aliases=multi_input[1], attachments=multi_input[2], new_evidence=[evidence], reviewed_attachments=['ATT-02'])
    assert bool(accepted) == (defect is None)
    assert bool(rejected) == (defect is not None)
    if defect is None:
        assert not corrected[1]['report']['issues']
        assert corrected[1]['report']['evidence']['E3']['verified']
        assert corrected[1]['report']['evidence']['E1'] == compiled[1]['report']['evidence']['E1']
        assert corrected[2] == compiled[2]
    else:
        assert corrected == compiled


@pytest.mark.asyncio
async def test_full_source_review_is_delivered_and_audited(multi_input, tmp_path):
    row = missing_quote(multi_input)
    correction = patch(row, evidence=['E1', 'E2'], evidence_uses={'E1': 'support', 'E2': 'support'},
        reference_roles={}, reasoning='{{ATT-01}}의 {{E1}}과 {{ATT-02}}의 {{E2}}가 각 한정에 대응합니다.')
    provider = Provider(ExecutionOutcome(result_text=json.dumps({'evidence': [], 'components': [correction]}), exit_code=0))
    request = ExecutionRequest(job_id='review', work_dir=tmp_path, system_prompt='', user_message='',
                               model='fake', timeout_seconds=30)
    async def emit(*args):
        pass
    corrected, audit = await report_consistency.repair(provider, request, compile_input(multi_input),
        aliases=multi_input[1], attachments=multi_input[2], emit=emit, cancelled=lambda: False)
    message = json.loads(provider.calls[0].user_message)
    assert message['reviewed_attachments'] == ['ATT-01', 'ATT-02']
    assert message['review_scope'] == 'provided_document_text'
    second = next(s for s in message['provided_sources'] if s['attachment'] == 'ATT-02')
    assert 'retains the initial speech segment' in second['sentences'][0]['text']
    assert audit['status'] == 'repaired'
    assert audit['attempts'][0]['reviewed_attachments'] == ['ATT-01', 'ATT-02']
    assert not corrected[1]['report']['issues']
