import pytest

from app import report_references
from .test_structured_report import report_input, compile_input, resolution_input


def no_match(values):
    resolution = resolution_input(values)
    row = values[0]['components'][0]
    row.update(similarity=0, evidence=[],
        reasoning='{{ATT-01}}에는 중요도 산출 인공지능의 구성이 개시되어 있지 않습니다.')
    resolution.update(evidence=[], supplements=[],
        derivation='인용문헌({{ATT-01}}, {{ATT-02}})에는 중요도 기반 분기가 개시되어 있지 않아 기술상식으로 도출할 수 없습니다.',
        remaining_difference='중요도 산출 한정의 근거가 부족하여 차이로 남습니다.', conclusion='insufficient')
    return row, resolution


@pytest.mark.parametrize('declared', [False, True])
def test_no_match_and_insufficient_are_valid_findings_not_missing_quotes(report_input, declared):
    row, resolution = no_match(report_input)
    if declared:
        row['reference_roles'] = {'ATT-01': 'not_found'}
        resolution['reference_roles'] = {'ATT-01': 'not_found', 'ATT-02': 'not_found'}
    report, manifest, _ = compile_input(report_input)
    assert manifest['report']['issues'] == []
    assert '대응 근거 미발견' in report
    assert '제공된 자료 범위' in report
    assert '(0%)' in report
    assert row['reasoning'].replace('{{ATT-01}}', '**인용발명 1** (US1)') in report
    assert manifest['report']['resolution_checks'][0]['status'] == 'insufficient'
    review = manifest['report']['comparison_checks'][0]['source_reviews']['ATT-01']
    assert review['role'] == 'not_found' and review['assessment'] == 'model_reported'
    assert review['origin'] == ('declared' if declared else 'legacy_negative_context')
    assert manifest['report']['data'] == report_input[0]


@pytest.mark.parametrize('reasoning', [
    '{{ATT-01}}은 센서 제어를 개시합니다. 중요도 산출은 확인되지 않습니다.',
    '{{ATT-01}}은 센서 제어를 개시하지만 중요도 산출은 확인되지 않습니다.',
    '{{ATT-01}}의 센서 제어에 기초하여 확장할 수 있으나 중요도 산출은 확인되지 않습니다.',
])
def test_legacy_zero_score_does_not_excuse_positive_or_mixed_assertions(report_input, reasoning):
    row, _ = no_match(report_input)
    row['reasoning'] = reasoning
    _, manifest, _ = compile_input(report_input)
    assert manifest['report']['comparison_checks'][0]['status'] == 'needs_review'
    assert any('청구항 1 (A):' in p and '발췌가 연결되지' in p for p in manifest['report']['issues'])


@pytest.mark.parametrize('score', [1, 70, 95])
def test_positive_score_still_requires_real_support_even_if_declared_absent(report_input, score):
    row, _ = no_match(report_input)
    row.update(similarity=score, reference_roles={'ATT-01': 'not_found'})
    _, manifest, _ = compile_input(report_input)
    assert any('대응 판단을 뒷받침하는 근거가 누락' in p for p in manifest['report']['issues'])
    assert manifest['items'][0]['similarity'] == score


def test_resolution_can_mix_linked_support_and_explicit_negative_review(report_input):
    resolution = resolution_input(report_input)
    resolution.update(reference_roles={'ATT-01': 'support', 'ATT-02': 'not_found'},
        explanation='{{ATT-01}}의 이득 제어를 활용하나 {{ATT-02}}에서 시간 조건을 찾지 못했습니다.',
        conclusion='remaining_gap', remaining_difference='시간 조건은 남습니다.')
    _, manifest, _ = compile_input(report_input)
    assert not manifest['report']['issues']
    resolution['evidence'] = []
    _, manifest, _ = compile_input(report_input)
    assert any('ATT-01' in p and '발췌가 연결되지' in p for p in manifest['report']['issues'])


@pytest.mark.parametrize('defect', ['unknown_document', 'bad_role', 'bad_object', 'linked_negative', 'unavailable_score'])
def test_reference_roles_cannot_bypass_integrity_checks(report_input, defect):
    row, _ = no_match(report_input)
    row['reference_roles'] = {'ATT-01': 'not_found'}
    if defect == 'unknown_document':
        row['reasoning'] = '{{ATT-99}}에는 해당 구성이 개시되어 있지 않습니다.'
        row['reference_roles'] = {'ATT-99': 'not_found'}
    elif defect == 'bad_role':
        row['reference_roles'] = {'ATT-01': ['not_found']}
    elif defect == 'bad_object':
        row['reference_roles'] = ['ATT-01']
    elif defect == 'linked_negative':
        row['evidence'] = ['E1']
    else:
        row['reference_roles'] = {'ATT-01': 'unavailable'}
    _, manifest, _ = compile_input(report_input)
    assert manifest['report']['comparison_checks'][0]['status'] == 'needs_review'


def test_unavailable_with_null_score_is_not_absence_or_supported(report_input):
    row, _ = no_match(report_input)
    row.update(similarity=None, reference_roles={'ATT-01': 'unavailable'},
               reasoning='{{ATT-01}}의 해당 부분을 판독할 수 없어 판단할 수 없습니다.')
    _, manifest, _ = compile_input(report_input)
    assert not manifest['report']['issues']
    assert manifest['report']['comparison_checks'][0]['source_reviews']['ATT-01']['role'] == 'unavailable'


def test_not_found_requires_actual_delivered_source_scope():
    reviews, problems = report_references.assess({'ATT-01'}, ['미발견'], {'ATT-01': 'not_found'},
        known={'ATT-01'}, readable=set(), allow_absence=True)
    assert problems and reviews['ATT-01']['role'] == 'support'


def test_legacy_negative_detection_requires_all_statements_to_be_negative():
    assert report_references.negative_only(['{{ATT-01}}에는 해당 구성이 개시되어 있지 않습니다.'])
    assert report_references.negative_only(['두 문헌 전반에서 관련 근거를 확인할 수 없습니다.'])
    assert not report_references.negative_only(['{{ATT-01}}은 제어기를 개시합니다. 해당 구성은 확인되지 않습니다.'])
    assert not report_references.negative_only([''])


def test_legacy_negative_derivation_does_not_excuse_positive_supplement_in_remaining(report_input):
    _, resolution = no_match(report_input)
    resolution['remaining_difference'] = '{{ATT-02}}의 제어기가 시간 조건을 보완합니다.'
    _, manifest, _ = compile_input(report_input)
    assert any('ATT-02' in p and '발췌가 연결되지' in p for p in manifest['report']['issues'])


@pytest.mark.parametrize('retrieved', [False, True])
def test_negative_review_records_actual_input_scope_not_claimed_full_document(report_input, retrieved):
    no_match(report_input)
    kwargs = {'bundle': {'candidate_sources': [
        {'attachment': 'ATT-01', 'pdf_page': 2, 'source_text': 'A limited excerpt.'},
        {'attachment': 'ATT-02', 'pdf_page': 3, 'source_text': 'Another limited excerpt.'}]}} if retrieved else {}
    _, manifest, _ = compile_input(report_input, **kwargs)
    review = manifest['report']['comparison_checks'][0]['source_reviews']['ATT-01']
    assert review['role'] == 'not_found'
    assert review['scope'] == ('retrieved_passages' if retrieved else 'provided_document_text')
    assert review['source_ids']
    assert review['assessment'] == 'model_reported'
