"""V4 reports: multiple references, residual-only differences and concise quotes."""
from pathlib import Path

import pytest

from app import analysis_protocol, report_repair, structured_report
from app.task_instructions import ANALYSIS
from .test_structured_report import report_input, compile_input


@pytest.fixture
def multi_input(report_input):
    data, _, _ = report_input
    data['version'] = 4
    data['evidence'] = [
        {'id': 'E1', 'attachment': 'ATT-01', 'sentence_ids': ['ATT-01-P0-T1'],
         'language': 'foreign', 'translation': '제어기는 누적 소음에 따라 이득을 변경한다.'},
        {'id': 'E2', 'attachment': 'ATT-02', 'sentence_ids': ['ATT-02-P0-T1'],
         'language': 'foreign', 'translation': '다른 제어기는 초기 음성 구간을 유지한다.'},
    ]
    row = data['components'][0]
    row.pop('resolution')
    row.update(similarity=70, evidence=['E1', 'E2'],
               reference_roles={'ATT-01': 'support', 'ATT-02': 'support'},
               reasoning='{{ATT-01}}의 {{E1}}은 이득 변경에, {{ATT-02}}의 {{E2}}는 초기 구간 유지에 대응합니다.',
               difference='두 문헌에서도 유지 시간의 조건은 확인되지 않습니다.')
    return report_input


def test_multiple_documents_are_rendered_and_validated(multi_input):
    report, manifest, _ = compile_input(multi_input)
    comparison, difference = report.split('#### [구성요소]')[1].split('#### [차이점]')
    assert '"The controller' in comparison and '"Another controller' in comparison
    assert '**인용발명 1**' in comparison and '**인용발명 2**' in comparison
    assert '대응 이유:' in comparison and '근거 E2' in comparison
    assert '혼입' not in report and manifest['report']['issues'] == []
    assert manifest['report']['comparison_checks'][0]['excluded_evidence'] == []
    assert manifest['items'][0]['similarity'] == 70
    assert manifest['items'][0]['difference'] == multi_input[0]['components'][0]['difference']
    assert difference.split('## 3.')[0].strip() == '구성 (A): 두 문헌에서도 유지 시간의 조건은 확인되지 않습니다.'
    assert manifest['report']['resolution_checks'] == []


@pytest.mark.parametrize('score', [0, 60, 85, 94])
def test_primary_score_does_not_force_a_residual_difference(multi_input, score):
    row = multi_input[0]['components'][0]
    row.update(similarity=score, difference='')
    report, manifest, _ = compile_input(multi_input)
    assert '확인된 차이점이 없습니다.' in report
    assert manifest['report']['issues'] == []


@pytest.mark.parametrize('defect', ['missing', 'fabricated', 'wrong_document'])
def test_additional_document_cannot_bypass_source_validation(multi_input, defect):
    data = multi_input[0]
    if defect == 'missing':
        data['components'][0]['evidence'] = ['E1']
        data['components'][0]['reasoning'] = data['components'][0]['reasoning'].replace('{{E2}}', '기재')
    else:
        data['evidence'][1]['sentence_ids'] = ['ATT-02-P0-T999' if defect == 'fabricated' else 'ATT-01-P0-T1']
    report, manifest, _ = compile_input(multi_input)
    assert manifest['report']['issues']
    assert manifest['report']['comparison_checks'][0]['status'] == 'needs_review'
    assert '"Another controller' not in report


def test_same_quote_is_printed_once_across_ids_components_and_claims(multi_input):
    data = multi_input[0]
    data['evidence'].append({**data['evidence'][0], 'id': 'E3'})
    row = data['components'][0]
    row['evidence'].append('E3')
    data['components'].append({**row, 'symbol': '(B)'})
    data['components'].append({**row, 'claim': '청구항 2'})
    report, manifest, _ = compile_input(multi_input)
    assert report.count('"제어기는 누적 소음에 따라 이득을 변경한다."') == 1
    assert report.count('"Another controller retains') == 1
    assert '청구항 1 (A)의 근거 E1 참조.' in report
    assert len(manifest['items']) == 3 and len(manifest['report']['evidence']) == 3
    assert not manifest['report']['issues']


@pytest.mark.parametrize('foreign', [True, False])
def test_only_foreign_display_is_shortened_full_evidence_is_preserved(multi_input, foreign):
    body = ('The controller determines the gain using the accumulated noise and retains the initial speech segment '
            'until the configured time has elapsed while preserving the signal order and timing constraints.') if foreign else (
            '제어기는 누적 소음에 따라 이득을 정하고 초기 음성 구간을 유지하며 설정된 시간이 경과하기 전에는 그 구간을 삭제하지 않고 '
            '신호의 순서를 그대로 보존하며 정해진 시간 조건을 충족하도록 처리하고 추가적인 입력이 도착할 때에도 최초 입력의 상태를 보존하여 처리 순서와 연결 관계를 유지한다.')
    Path(multi_input[2][0].normalized_text_path).write_text('[0022] ' + body, encoding='utf-8')
    translation = '제어기는 누적 소음을 사용하여 이득을 결정하고 신호 순서와 시간 조건을 보존하면서 설정 시간이 경과할 때까지 초기 음성 구간을 유지한다.'
    multi_input[0]['evidence'][0].update(language='foreign' if foreign else 'ko', translation=translation if foreign else '')
    report, manifest, _ = compile_input(multi_input)
    item = manifest['report']['evidence']['E1']
    assert item['verified'] and body in item['quote']
    assert r'단락 \[0022\]' in report
    if foreign:
        assert translation in report and body not in report
        snippet = structured_report.original_preview(body)
        assert len(snippet) <= 120 and snippet.endswith('…') and snippet in report
        assert body.startswith(snippet[:-1])
    else:
        assert body in report
    assert manifest['report']['issues'] == []


def test_unknown_difference_is_not_silently_no_difference(multi_input):
    multi_input[0]['components'][0].pop('difference')
    report, manifest, _ = compile_input(multi_input)
    assert '잔존 차이 확인 필요' in report
    assert '확인된 차이점이 없습니다.' not in report
    assert manifest['report']['issues']


def test_difference_uses_one_line_and_never_expands_quotes(multi_input):
    multi_input[0]['components'][0]['difference'] = '시간 조건과\n\n입력의 연결 관계가 확인되지 않습니다.'
    report, _, _ = compile_input(multi_input)
    difference = report.split('#### [차이점]')[1].split('## 3.')[0].strip()
    assert difference == '구성 (A): 시간 조건과 입력의 연결 관계가 확인되지 않습니다.'


def test_repair_supports_v4_and_keeps_comparison_links(multi_input):
    multi_input[0]['evidence'][1]['sentence_ids'] = ['bad-id']
    compiled = compile_input(multi_input)
    assert report_repair._failed(compiled) == ['E2']
    patched, accepted, rejected, expansions = report_repair.apply_patches(compiled, [
        {'id': 'E2', 'sentence_ids': ['ATT-02-P0-T1'], 'language': 'foreign',
         'translation': '다른 제어기는 초기 음성 구간을 유지한다.'}], ['E2'],
        aliases=multi_input[1], attachments=multi_input[2])
    assert accepted == ['E2'] and rejected == {} and expansions == {'E2': ['E2']}
    assert report_repair._failed(patched) == []
    assert patched[1]['report']['data']['components'] == multi_input[0]['components']
    assert patched[1]['report']['issues'] == []
    assert '"Another controller' in patched[0]


@pytest.mark.parametrize('role', ['not_found', 'unavailable'])
def test_additional_negative_reference_does_not_change_primary_score(multi_input, role):
    row = multi_input[0]['components'][0]
    row.update(evidence=['E1'], reference_roles={'ATT-01': 'support', 'ATT-02': role},
               reasoning='{{ATT-01}}의 {{E1}}은 이득 변경에 대응하며 {{ATT-02}}의 추가 조건은 확인되지 않습니다.')
    _, manifest, _ = compile_input(multi_input)
    assert manifest['report']['issues'] == []
    assert manifest['items'][0]['similarity'] == 70


def test_additional_support_does_not_justify_primary_score(multi_input):
    row = multi_input[0]['components'][0]
    row.update(evidence=['E2'], reference_roles={'ATT-01': 'unavailable', 'ATT-02': 'support'},
               reasoning='{{ATT-01}}은 판독 불가이며 {{ATT-02}}의 {{E2}}만 초기 구간 유지에 대응합니다.')
    _, manifest, _ = compile_input(multi_input)
    assert any('주 인용발명 확인 불가' in s for s in manifest['report']['issues'])
    row['similarity'] = None
    _, manifest, _ = compile_input(multi_input)
    assert manifest['report']['issues'] == []


def test_reasoning_cannot_use_unlinked_evidence(multi_input):
    multi_input[0]['components'][0]['reasoning'] += ' {{E9}}도 대응합니다.'
    _, manifest, _ = compile_input(multi_input)
    assert any('E9: 대응 이유의 근거' in s for s in manifest['report']['issues'])


def test_active_contract_has_no_single_document_or_resolution_requirement():
    prompt = analysis_protocol.apply(ANALYSIS.body)
    assert '# PRISM 구조화 보고서 V5' in prompt
    assert 'reasoning에는 주 문헌만' not in prompt
    assert '다른 문헌은 resolution에 연결한다' not in prompt
    assert '80~94%이면 남는 구체적인 차이를 difference에 반드시' not in prompt
    assert '복수 인용발명' in prompt and '한 문장' in prompt
    retrieved = structured_report.instructions(retrieved=True)
    assert 'ATT-01-P3-T2' not in retrieved and 'ATT-02-P1-T1' not in retrieved
