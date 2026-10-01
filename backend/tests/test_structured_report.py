import json
from pathlib import Path

import pytest

from app import citation_mapping, structured_report, report_sources
from app.enums import AttachmentRole
from app.ingestion.service import ingest_one, IngestionLimits


@pytest.fixture
def report_input(tmp_path):
    a = ingest_one('reference.txt', b'The controller changes the gain based on accumulated noise.', tmp_path,
                   True, IngestionLimits(), role=AttachmentRole.CITATION)
    b = ingest_one('other.txt', b'Another controller retains the initial speech segment.', tmp_path,
                   True, IngestionLimits(), role=AttachmentRole.CITATION)
    aliases = citation_mapping.assign_aliases([a, b])
    data = {'version': 1, 'documents': [
        {'attachment': 'ATT-01', 'document_number': 'US1', 'title': 'First source'},
        {'attachment': 'ATT-02', 'document_number': 'US2', 'title': 'Second source'}],
        'evidence': [{'id': 'E1', 'attachment': 'ATT-01', 'quote': 'The controller changes the gain based on accumulated noise.',
                      'language': 'foreign', 'translation': '제어기는 누적 소음에 따라 이득을 변경한다.', 'page': None}],
        'components': [{'claim': '청구항 1', 'symbol': '(A)', 'feature': '이득을 변경하는 제어기',
                        'similarity': 95, 'basis': 'direct', 'evidence': ['E1'], 'reasoning': '{{ATT-01}}의 이득 변경과 대응합니다.',
                        'difference': '', 'resolution': ''}],
        'summary': {'main_reason': '{{ATT-02}}가 가장 폭넓게 대응합니다.', 'relationships': '시간적 관계는 추가 확인이 필요합니다.'}}
    return data, aliases, [a, b]


def compile_input(values, **kwargs):
    data, aliases, attachments = values
    return structured_report.compile_report(json.dumps(data), aliases, attachments, **kwargs)


@pytest.mark.parametrize('score', [0, 60, 85, 94])
def test_missing_difference_is_not_reported_as_no_difference(report_input, score):
    report_input[0]['components'][0]['similarity'] = score
    report, manifest, _ = compile_input(report_input)
    assert '확인된 차이점이 없습니다' not in report
    assert f'{score}% 평가에 대한 구체적인 차이점 설명이 누락' in report
    assert manifest['items'][0]['similarity'] == score
    assert report_input[0]['components'][0]['feature'] in report
    assert '대응 이유:' in report


@pytest.mark.parametrize('foreign', [True, False])
def test_paragraph_markers_are_only_displayed_in_location(report_input, foreign):
    quote = '[0022] First statement.\n[0023] Second statement.'
    Path(report_input[2][0].normalized_text_path).write_text(quote, encoding='utf-8')
    report_input[0]['evidence'][0].update(quote=quote, language='foreign' if foreign else 'ko',
        translation='[0022] 첫 번째 문장. [0023] 두 번째 문장.' if foreign else '')
    report, manifest, _ = compile_input(report_input)
    assert report.count(r'\[0022\]') == 1 and report.count(r'\[0023\]') == 1
    assert manifest['report']['evidence']['E1']['quote'] == quote
    assert 'First statement.' in report and 'Second statement.' in report


def test_page_end_list_fragment_requires_translation_scope_review(report_input):
    quote = '[0060] Operations comprising: (a) obtaining a response; (f)\n14'
    Path(report_input[2][0].normalized_text_path).write_text(quote, encoding='utf-8')
    report_input[0]['evidence'][0].update(quote=quote, translation='응답을 구하고 최종 값을 생성합니다.')
    report, manifest, _ = compile_input(report_input)
    assert '발췌가 열거 항목 중간에서 끝납니다' in report
    assert manifest['report']['evidence']['E1']['verified']
    assert manifest['report']['evidence']['E1']['issues']


@pytest.mark.parametrize('retrieved', [False, True])
def test_sentence_selection_restores_original_without_model_transcription(report_input, retrieved):
    body = ('The controller controls the reproduction of video. '
            'The output of the sequence generator is used. '
            'An unrelated example follows.')
    Path(report_input[2][0].normalized_text_path).write_text(body, encoding='utf-8')
    data = report_input[0]
    data['version'] = 3
    prefix = 'S001' if retrieved else 'ATT-01-P0'
    data['evidence'][0] = {'id': 'E1', 'attachment': 'ATT-01', 'sentence_ids': [prefix + '-T1', prefix + '-T2'],
                           'language': 'foreign', 'translation': '제어기는 영상 재생을 제어하며 시퀀스 생성기의 출력을 사용합니다.'}
    kwargs = {'bundle': {'candidate_sources': [{'attachment': 'ATT-01', 'pdf_page': 3, 'source_text': body}]}} if retrieved else {}
    report, manifest, _ = compile_input(report_input, **kwargs)
    result = manifest['report']['evidence']['E1']
    assert result['verified'] and result['resolution_method'] == 'source_sentence_ids'
    assert result['quote'] == 'The controller controls the reproduction of video. The output of the sequence generator is used.'
    assert result['quote'] in report and 'An unrelated example' not in report
    assert result['page'] == (3 if retrieved else None)
    assert manifest['items'][0]['similarity'] == 95
    assert manifest['report']['issues'] == []


@pytest.mark.parametrize('defect', ['skip', 'reverse', 'duplicate', 'other_document', 'unknown', 'empty',
                                   'wrong_type', 'rewrite', 'offset', 'other_page'])
def test_sentence_selection_rejects_invalid_or_noncontiguous_choices(report_input, defect):
    body = 'The first sentence is used. An intervening formula is x = y. The third sentence is used.'
    Path(report_input[2][0].normalized_text_path).write_text(body, encoding='utf-8')
    data = report_input[0]
    data['version'] = 3
    row = {'id': 'E1', 'attachment': 'ATT-01', 'sentence_ids': ['ATT-01-P0-T1'],
           'language': 'foreign', 'translation': '첫 문장을 사용합니다.'}
    data['evidence'][0] = row
    row['sentence_ids'] = {'skip': ['ATT-01-P0-T1', 'ATT-01-P0-T3'],
        'reverse': ['ATT-01-P0-T2', 'ATT-01-P0-T1'], 'duplicate': ['ATT-01-P0-T1', 'ATT-01-P0-T1'],
        'other_document': ['ATT-02-P0-T1'], 'unknown': ['ATT-01-P0-T999'],
        'empty': [], 'wrong_type': 'ATT-01-P0-T1'}.get(defect, row['sentence_ids'])
    if defect == 'rewrite':
        row['quote'] = 'The first sentence is changed.'
    if defect == 'offset':
        row['start'] = 0
    if defect == 'other_page':
        Path(report_input[2][0].normalized_text_path).write_text(
            '--- PAGE 1 ---\nFirst sentence on the first page.\n--- PAGE 2 ---\nSecond sentence on the second page.', encoding='utf-8')
        row['sentence_ids'] = ['ATT-01-P1-T1', 'ATT-01-P2-T1']
    report, manifest, _ = compile_input(report_input)
    assert not manifest['report']['evidence']['E1']['verified']
    assert manifest['report']['issues']
    assert '"The first sentence is changed."' not in report
    assert manifest['items'][0]['similarity'] == 95


def test_sentence_ids_cannot_access_undelivered_full_text(report_input):
    data = report_input[0]
    data['version'] = 3
    data['evidence'][0] = {'id': 'E1', 'attachment': 'ATT-01', 'sentence_ids': ['ATT-01-P0-T1'],
                           'language': 'foreign', 'translation': '임의 번역'}
    _, manifest, _ = compile_input(report_input, bundle={'candidate_sources': []})
    assert not manifest['report']['evidence']['E1']['verified']


def test_program_numbers_sources_and_always_renders_translation_first(report_input):
    report, manifest, mapping = compile_input(report_input)
    assert '**인용발명 1** (US1)' in report
    assert report.index('"제어기는') < report.index('"The controller')
    assert '“' not in report and '{{ATT-' not in report
    assert manifest['items'][0]['status'] == 'matched'
    assert manifest['report']['issues'] == []
    assert mapping['items'][0]['attachment_id'] == report_input[2][0].attachment_id


def test_fixed_mapping_numbers_override_model_document_order(report_input):
    _, _, mapping = compile_input(report_input)
    report_input[0]['documents'].reverse()
    report, _, preserved = compile_input(report_input, prior_mapping=mapping)
    assert preserved == mapping
    assert '**인용발명 1** (US1)' in report


def test_evidence_is_stored_once_and_reused_for_components(report_input):
    report_input[0]['components'].append({**report_input[0]['components'][0], 'symbol': '(B)'})
    report, manifest, _ = compile_input(report_input)
    assert len(manifest['report']['evidence']) == 1
    assert len(manifest['items']) == 2
    assert report.count('"The controller') == 2


def test_program_resolves_shared_source_without_model_copying_quote(report_input):
    quote = report_input[0]['evidence'][0].pop('quote')
    report_input[0]['evidence'][0].update(source_id='S001', start=0, end=len(quote))
    bundle = {'candidate_sources': [{'attachment': 'ATT-01', 'pdf_page': 3, 'source_text': quote}]}
    report, manifest, _ = compile_input(report_input, bundle=bundle)
    assert quote in report
    assert 'PDF 페이지 3' in report
    assert manifest['report']['evidence']['E1']['verified']


@pytest.mark.parametrize('retrieved', [False, True])
def test_unit_selection_restores_quote_and_page_without_model_offsets(report_input, retrieved):
    row = report_input[0]['evidence'][0]
    quote = row.pop('quote')
    row.pop('page')
    row['source_id'] = 'S001-U1' if retrieved else 'ATT-01-P0-U1'
    kwargs = {'bundle': {'candidate_sources': [
        {'attachment': 'ATT-01', 'pdf_page': 7, 'source_text': quote}]}} if retrieved else {}
    report, manifest, _ = compile_input(report_input, **kwargs)
    verified = manifest['report']['evidence']['E1']
    assert verified['verified'] and verified['quote'].strip() == quote
    assert verified['page'] == (7 if retrieved else None)
    assert quote in report


@pytest.mark.parametrize('retrieved', [False, True])
def test_selected_quote_does_not_expand_to_untranslated_context(report_input, retrieved):
    row = report_input[0]['evidence'][0]
    quote = row['quote']
    body = '[0001] Unrelated introductory details. ' + quote + ' Untranslated closing details. [0002] Other context.'
    Path(report_input[2][0].normalized_text_path).write_text(body, encoding='utf-8')
    row['source_id'] = 'S001-U1' if retrieved else 'ATT-01-P0-U1'
    kwargs = {'bundle': {'candidate_sources': [
        {'attachment': 'ATT-01', 'pdf_page': 7, 'source_text': body}]}} if retrieved else {}
    report, manifest, _ = compile_input(report_input, **kwargs)
    item = manifest['report']['evidence']['E1']
    assert item['verified'] and item['quote'] == quote
    assert 'Unrelated introductory' not in report and 'Untranslated closing' not in report


@pytest.mark.parametrize('quote', ['', 'Another controller retains the initial speech segment.',
                                 'The controller changes the gain ... accumulated noise.'])
def test_explicit_bad_quote_cannot_fall_back_to_selected_unit(report_input, quote):
    report_input[0]['evidence'][0].update(source_id='ATT-01-P0-U1', quote=quote)
    report, manifest, _ = compile_input(report_input)
    assert not manifest['report']['evidence']['E1']['verified']
    assert '근거 확인 필요' in report


def test_v2_missing_quote_does_not_restore_entire_unit(report_input):
    report_input[0]['version'] = 2
    row = report_input[0]['evidence'][0]
    row.pop('quote')
    row['source_id'] = 'ATT-01-P0-U1'
    report, manifest, _ = compile_input(report_input)
    assert not manifest['report']['evidence']['E1']['verified']
    assert '구간 전체로 대체하지 않습니다' in report


def test_v2_quote_is_validated_in_selected_unit(report_input):
    report_input[0]['version'] = 2
    report_input[0]['evidence'][0]['source_id'] = 'ATT-01-P0-U1'
    _, manifest, _ = compile_input(report_input)
    assert manifest['report']['evidence']['E1']['verified']


@pytest.mark.parametrize('source_id,extra', [
    ('ATT-02-P0-U1', {}), ('ATT-01-P0-U999', {}), ('ATT-01-P0-U1', {'start': 0, 'end': 10})])
def test_wrong_document_unknown_or_modified_unit_is_rejected(report_input, source_id, extra):
    report_input[0]['evidence'][0].update(source_id=source_id, **extra)
    report, manifest, _ = compile_input(report_input)
    assert not manifest['report']['evidence']['E1']['verified']
    assert '**출력 점검: 근거·항목 연결 확인 필요**' in report
    assert manifest['items'][0]['similarity'] == 95


def test_empty_retrieval_bundle_never_falls_back_to_full_text(report_input):
    report_input[0]['evidence'][0]['source_id'] = 'ATT-01-P0-U1'
    _, manifest, _ = compile_input(report_input, bundle={})
    assert not manifest['report']['evidence']['E1']['verified']


def test_full_page_choice_restores_original_punctuation_and_pdf_location(report_input):
    from app.prompt_assembly import assemble
    body = '--- PAGE 9 ---\n[0158] The controller uses “gain” to reduce noise.\n'
    Path(report_input[2][0].normalized_text_path).write_text(body, encoding='utf-8')
    prompt = assemble('본문', report_input[2], '', False, None)
    assert 'ATT-01-P9-T1' in prompt.user_message
    row = report_input[0]['evidence'][0]
    row.pop('quote')
    row.pop('page')
    report_input[0]['version'] = 3
    row.update(sentence_ids=['ATT-01-P9-T1'], translation='제어기는 잡음을 줄이기 위해 이득을 사용합니다.')
    report, manifest, _ = compile_input(report_input)
    item = manifest['report']['evidence']['E1']
    assert item['verified'] and item['page'] == 9
    assert '“gain”' in report
    assert item['quote'].strip() == body.split('\n')[1]


@pytest.mark.parametrize('quote', [
    'the controller changes the gain based on accumulated noise.',
    'The controller changes the gain ... accumulated noise.',
    'The controller changes the gain accumulated noise.',
])
def test_legacy_changed_or_spliced_quote_is_not_fuzzy_matched(report_input, quote):
    report_input[0]['evidence'][0]['quote'] = quote
    _, manifest, _ = compile_input(report_input)
    assert not manifest['report']['evidence']['E1']['verified']


@pytest.mark.parametrize('duplicate', [False, True])
def test_legacy_wrong_page_is_corrected_only_when_exact_page_is_unique(report_input, duplicate):
    data, _, attachments = report_input
    quote = data['evidence'][0]['quote']
    Path(attachments[0].normalized_text_path).write_text(
        f'--- PAGE 9 ---\n{quote}\n--- PAGE 10 ---\nUnrelated page text.'
        + (f'\n--- PAGE 11 ---\n{quote}' if duplicate else ''), encoding='utf-8')
    data['evidence'][0]['page'] = 10
    _, manifest, _ = compile_input(report_input)
    item = manifest['report']['evidence']['E1']
    assert item['verified'] is not duplicate
    if not duplicate:
        assert item['page'] == 9 and item['requested_page'] == 10
        assert item['quote'] == quote


def resolution_input(values):
    row = values[0]['components'][0]
    row['difference'] = '추가 조건에 따른 이득 변경'
    row['resolution'] = {'gap': row['difference'], 'evidence': ['E1'],
        'explanation': '{{ATT-01}}의 누적 소음 기반 이득 변경을 적용하여 보완합니다.',
        'remaining_difference': '', 'conclusion': 'supported'}
    return row['resolution']


def test_resolution_does_not_repeat_main_document_quote(report_input):
    resolution_input(report_input)
    report, manifest, _ = compile_input(report_input)
    section = report.split('#### [차이점]')[1]
    assert '구성 (A)에 대해' in section
    assert '"제어기는' not in section and '"The controller' not in section
    assert report.count('"The controller') == 1
    assert manifest['report']['resolution_checks'][0]['status'] == 'supported'
    assert '해소 판단(모델 분석)' not in section


def test_resolution_does_not_implicitly_inherit_comparison_evidence(report_input):
    resolution_input(report_input)['evidence'] = []
    _, manifest, _ = compile_input(report_input)
    check = manifest['report']['resolution_checks'][0]
    assert check['status'] == 'needs_review' and check['evidence'] == []


def test_insufficient_with_no_evidence_is_not_a_broken_supported_claim(report_input):
    resolution = resolution_input(report_input)
    report_input[0]['components'][0]['evidence'] = []
    resolution.update(evidence=[], explanation='해당 한정의 보완 근거를 찾지 못했습니다.',
                      remaining_difference='추가 조건의 보완 근거 부족', conclusion='insufficient')
    _, manifest, _ = compile_input(report_input)
    assert manifest['report']['resolution_checks'][0]['status'] == 'insufficient'


@pytest.mark.parametrize('defect', ['missing_ref', 'missing_translation', 'bad_quote', 'unlinked_document',
                                    'no_evidence', 'malformed_evidence', 'contradiction', 'legacy', 'missing'])
def test_resolution_validation_preserves_model_conclusion_separately(report_input, defect):
    resolution = resolution_input(report_input)
    data = report_input[0]
    if defect == 'missing_ref':
        resolution['evidence'] = ['E404']
    elif defect == 'missing_translation':
        data['evidence'][0]['translation'] = ''
    elif defect == 'bad_quote':
        data['evidence'][0]['quote'] = 'Invented text not found in any source.'
    elif defect == 'unlinked_document':
        resolution['explanation'] += ' {{ATT-02}}의 다른 처리도 결합합니다.'
    elif defect == 'no_evidence':
        resolution['evidence'] = []
        data['components'][0]['evidence'] = []
    elif defect == 'malformed_evidence':
        resolution['evidence'] = 'E1,E2'
    elif defect == 'contradiction':
        resolution['remaining_difference'] = '시점 조건이 부족합니다.'
    elif defect == 'legacy':
        data['components'][0]['resolution'] = resolution['explanation']
    elif defect == 'missing':
        data['components'][0].pop('resolution')
    report, manifest, _ = compile_input(report_input)
    assert manifest['report']['resolution_checks'][0]['status'] == 'needs_review'
    assert '출력 점검: 근거·항목 연결 확인 필요' in report
    if defect not in {'legacy', 'missing'}:
        assert '해소 판단(모델 분석)' not in report
        assert manifest['report']['resolution_checks'][0]['model_conclusion'] == 'supported'
    else:
        assert '해소 판단(모델 분석): 해소 가능' not in report
    assert '분석 서술과 근거·항목 점검 결과를 구분하여 확인하십시오' in report


def test_korean_resolution_has_excerpt_without_translation(report_input):
    quote = '제어기는 누적 소음에 따라 이득을 변경합니다.'
    Path(report_input[2][0].normalized_text_path).write_text(quote, encoding='utf-8')
    report_input[0]['evidence'][0].update(quote=quote, language='ko', translation='')
    resolution_input(report_input)
    report, manifest, _ = compile_input(report_input)
    section = report.split('#### [차이점]')[1]
    assert '"' + quote + '"' not in section
    assert '[번역 미제공]' not in section
    assert manifest['report']['resolution_checks'][0]['status'] == 'supported'


@pytest.mark.parametrize('score,grade', [(98, '동일 🔵'), (85, '실질적 동일 🟢'),
                                       (40, '일부 차이 🟡'), (0, '대응 없음 ⚪')])
def test_comparison_layout_groups_components_before_differences(report_input, score, grade):
    data = report_input[0]
    data['components'][0]['similarity'] = score
    resolution_input(report_input)
    data['components'].append({**data['components'][0], 'symbol': '(B)'})
    report, _, _ = compile_input(report_input)
    comparison = report.split('#### [구성요소]')[1].split('#### [차이점]')[0]
    assert f'**(A) {grade} ({score}%)**\n\n이득을 변경하는 제어기\n\n"제어기는' in comparison
    assert f'**(B) {grade} ({score}%)**' in comparison
    assert comparison.index('텍스트 자료 · 페이지 구분 없음;') < comparison.index('"The controller')
    assert '구성 (A)에 대해' not in comparison
    assert report.index('**(B)') < report.index('구성 (A)에 대해')


@pytest.mark.parametrize('case', ['other_reference', 'remaining_gap', 'same_reference'])
def test_difference_paths_embed_verified_quotes_in_natural_paragraph(report_input, case):
    data = report_input[0]
    resolution = resolution_input(report_input)
    resolution['gap'] = '{{ATT-01}}에는 초기 구간 유지 조건의 기재가 부족합니다.'
    if case == 'same_reference':
        resolution['explanation'] = '다만 {{ATT-01}}의 {{E1}}에 기초하여 변경의 기술적 이유를 검토합니다.'
    else:
        data['evidence'].append({'id': 'E2', 'attachment': 'ATT-02',
            'quote': 'Another controller retains the initial speech segment.', 'language': 'foreign',
            'translation': '다른 제어기는 초기 음성 구간을 유지한다.'})
        resolution['evidence'].append('E2')
        resolution['explanation'] = '그러나 {{ATT-02}}에는 {{E2}}는 구성이 기재되어 있으며 초기 구간 유지에 대응합니다.'
        if case == 'remaining_gap':
            resolution.update(conclusion='remaining_gap', remaining_difference='유지 시간의 조건은 대응되지 않습니다.')
    report, manifest, _ = compile_input(report_input)
    section = report.split('#### [차이점]')[1]
    assert '{{E' not in report
    assert section.index('구성 (A)에 대해') < section.index('초기 구간 유지 조건의 기재가 부족합니다.')
    if case == 'same_reference':
        assert '"제어기는' not in section and '"The controller' not in section
        assert '인용발명 2' not in section.split('## 3.')[0]
    else:
        assert '\n\n**인용발명 2** (US2)에는 "다른 제어기는' in section
        assert section.index('"다른 제어기는') < section.index('페이지 구분 없음;', section.index('"다른 제어기는')) < section.index('"Another controller')
        assert section.count('"Another controller') == 1
    if case == 'remaining_gap':
        assert '유지 시간의 조건은 대응되지 않습니다.' in section
        assert '해소 판단(모델 분석): 해소 가능' not in section
    assert manifest['report']['resolution_checks'][0]['status'] == resolution['conclusion']


def test_unlinked_inline_evidence_cannot_appear_as_a_verified_quote(report_input):
    resolution = resolution_input(report_input)
    resolution['explanation'] = '{{ATT-01}}의 {{E404}}로 보완됩니다.'
    report, manifest, _ = compile_input(report_input)
    assert '{{E404}}' not in report
    assert '[근거 확인 필요]' in report
    assert manifest['report']['resolution_checks'][0]['status'] == 'needs_review'


@pytest.mark.parametrize('retrieved', [False, True])
def test_continuous_quote_can_cross_index_boundary_on_delivered_page(report_input, retrieved):
    first = report_input[0]['evidence'][0]['quote']
    quote = first + ' The output is sent to the receiver.'
    Path(report_input[2][0].normalized_text_path).write_text(quote, encoding='utf-8')
    report_input[0]['evidence'][0].update(quote=quote,
        source_id='S001-U1' if retrieved else 'ATT-01-P0-U1')
    kwargs = {'bundle': {'candidate_sources': [
        {'attachment': 'ATT-01', 'pdf_page': 3, 'source_text': quote}]}} if retrieved else {}
    _, manifest, _ = compile_input(report_input, **kwargs)
    item = manifest['report']['evidence']['E1']
    assert item['verified'] and item['quote'] == quote
    assert item['resolution_method'] == 'continuous_same_page'
    assert not item['issues']


@pytest.mark.parametrize('defect', ['ellipsis', 'header_removed', 'different_page'])
def test_boundary_recovery_does_not_accept_spliced_or_unrelated_quotes(report_input, defect):
    first = report_input[0]['evidence'][0]['quote']
    second = 'The output is sent to the receiver.'
    quote = first + ' ' + second
    body = quote
    if defect == 'different_start':
        quote = second
    elif defect == 'ellipsis':
        quote = first + ' ... ' + second
    elif defect == 'header_removed':
        body = first + '\nOct. 11, 2007\n' + second
    else:
        body = '--- PAGE 1 ---\n' + first + '\n--- PAGE 2 ---\n' + second
    Path(report_input[2][0].normalized_text_path).write_text(body, encoding='utf-8')
    report_input[0]['evidence'][0].update(quote=quote,
        source_id='ATT-01-P1-U1' if defect == 'different_page' else 'ATT-01-P0-U1')
    report, manifest, _ = compile_input(report_input)
    assert not manifest['report']['evidence']['E1']['verified']
    if defect == 'ellipsis':
        assert '생략부호가 포함되어' in report


@pytest.mark.parametrize('other_page', [False, True])
def test_wrong_existing_selection_recovers_only_unique_exact_quote(report_input, other_page):
    quote = report_input[0]['evidence'][0]['quote']
    body = '--- PAGE 1 ---\nAn unrelated introduction.\n'
    body += ('--- PAGE 2 ---\n' if other_page else '') + quote
    Path(report_input[2][0].normalized_text_path).write_text(body, encoding='utf-8')
    report_input[0]['evidence'][0]['source_id'] = 'ATT-01-P1-U1'
    _, manifest, _ = compile_input(report_input)
    item = manifest['report']['evidence']['E1']
    assert item['verified'] and item['quote'] == quote
    assert item['page'] == (2 if other_page else 1)
    assert item['resolution_method'] == 'unique_exact_quote'
    assert item['requested_source_id'] == 'ATT-01-P1-U1'
    assert item['issues'] == []


def test_wrong_selection_does_not_guess_between_duplicate_quote_locations(report_input):
    quote = report_input[0]['evidence'][0]['quote']
    Path(report_input[2][0].normalized_text_path).write_text(
        '--- PAGE 1 ---\nAn unrelated introduction.\n--- PAGE 2 ---\n' + quote +
        '\n--- PAGE 3 ---\n' + quote, encoding='utf-8')
    report_input[0]['evidence'][0]['source_id'] = 'ATT-01-P1-U1'
    _, manifest, _ = compile_input(report_input)
    assert not manifest['report']['evidence']['E1']['verified']


@pytest.mark.parametrize('in_evidence', [False, True])
def test_supplementary_reference_is_excluded_without_changing_model_score(report_input, in_evidence):
    data = report_input[0]
    data['components'][0]['similarity'] = 85
    data['evidence'].append({'id': 'E2', 'attachment': 'ATT-02',
        'quote': 'Another controller retains the initial speech segment.',
        'language': 'foreign', 'translation': '다른 제어기는 초기 음성 구간을 유지합니다.'})
    row = data['components'][0]
    if in_evidence:
        row['evidence'].append('E2')
    else:
        row['reasoning'] += ' ATT-02의 초기 구간 유지도 대응합니다.'
    report, manifest, _ = compile_input(report_input)
    comparison = report.split('#### [구성요소]')[1].split('#### [차이점]')[0]
    assert 'Another controller' not in comparison
    assert '**인용발명 2**' not in comparison
    assert '(85%)' in comparison
    assert manifest['items'][0]['similarity'] == 85
    assert manifest['report']['data']['components'][0]['similarity'] == 85
    assert manifest['report']['comparison_checks'][0]['status'] == 'needs_review'


@pytest.mark.parametrize('conclusion', ['supported', 'remaining_gap', 'insufficient'])
def test_format_checks_preserve_internal_conclusion_without_rendering_verdict(report_input, conclusion):
    resolution_input(report_input).update(conclusion=conclusion, evidence=['E404'],
        remaining_difference='' if conclusion == 'supported' else '추가 조건의 확인이 필요합니다.')
    report, manifest, _ = compile_input(report_input)
    assert '해소 판단(모델 분석)' not in report
    assert manifest['report']['resolution_checks'][0]['model_conclusion'] == conclusion
    assert manifest['report']['resolution_checks'][0]['status'] == 'needs_review'
    assert manifest['report']['data']['components'][0]['resolution']['conclusion'] == conclusion


def test_resolution_only_renders_its_own_evidence(report_input):
    data = report_input[0]
    data['evidence'].append({'id': 'E2', 'attachment': 'ATT-02',
        'quote': 'Another controller retains the initial speech segment.',
        'language': 'foreign', 'translation': '다른 제어기는 초기 음성 구간을 유지합니다.'})
    resolution_input(report_input).update(evidence=['E2'], gap='초기 구간 유지의 기재가 부족합니다.',
        explanation='{{ATT-02}}의 {{E2}}가 초기 구간 유지의 근거입니다.')
    report, manifest, _ = compile_input(report_input)
    section = report.split('#### [차이점]')[1]
    assert 'The controller changes' not in section
    assert 'Another controller' in section
    assert manifest['report']['resolution_checks'][0]['evidence'] == ['E2']


@pytest.mark.parametrize('korean', [False, True])
def test_structured_difference_starts_with_gap_and_only_quotes_supplement(report_input, korean):
    data = report_input[0]
    quote = '다른 제어기는 초기 음성 구간을 유지합니다.' if korean else 'Another controller retains the initial speech segment.'
    Path(report_input[2][1].normalized_text_path).write_text(quote, encoding='utf-8')
    data['evidence'].append({'id': 'E2', 'attachment': 'ATT-02', 'quote': quote,
        'language': 'ko' if korean else 'foreign', 'translation': '' if korean else '다른 제어기는 초기 음성 구간을 유지합니다.'})
    resolution_input(report_input).update(supplements=[{'attachment': 'ATT-02', 'evidence': ['E2'],
        'explanation': '이는 초기 구간 유지에 대응하지만 유지 시간은 확인되지 않습니다.'}],
        evidence=['E1', 'E2'], derivation='', remaining_difference='유지 시간 조건은 남습니다.', conclusion='remaining_gap')
    report, manifest, _ = compile_input(report_input)
    section = report.split('#### [차이점]')[1].split('## 3.')[0]
    assert '"The controller' not in section and '"제어기는 누적' not in section
    assert '**인용발명 1**' not in section
    assert '누적 소음 기반 이득 변경을 적용' not in section  # Legacy recap ignored.
    assert '"' + quote + '"' in section
    assert section.index('구성 (A)에 대해') < section.index('**인용발명 2**') < section.index('이는 초기 구간')
    assert manifest['report']['resolution_checks'][0]['status'] == 'remaining_gap'


def test_single_document_derivation_keeps_support_without_repeating_it(report_input):
    resolution_input(report_input).update(supplements=[], derivation='입력 조건을 추가하면 처리 순서를 유지할 수 있습니다.')
    report, manifest, _ = compile_input(report_input)
    section = report.split('#### [차이점]')[1].split('## 3.')[0]
    assert '입력 조건을 추가하면' in section and 'The controller' not in section
    assert '누적 소음 기반 이득 변경을 적용' not in section
    assert manifest['report']['resolution_checks'][0]['status'] == 'supported'


def test_main_document_cannot_be_registered_as_difference_supplement(report_input):
    resolution_input(report_input).update(supplements=[{'attachment': 'ATT-01', 'evidence': ['E1'],
        'explanation': '주 문헌의 기존 대응 내용을 다시 설명합니다.'}], derivation='')
    report, manifest, _ = compile_input(report_input)
    section = report.split('#### [차이점]')[1]
    assert '기존 대응 내용을 다시' not in section and '"The controller' not in section
    assert manifest['report']['resolution_checks'][0]['status'] == 'needs_review'


def test_no_supplementary_support_is_insufficient_not_a_format_failure(report_input):
    resolution_input(report_input).update(supplements=[], derivation='',
        remaining_difference='해당 한정을 보완할 구체적인 근거가 부족합니다.', conclusion='insufficient')
    report, manifest, _ = compile_input(report_input)
    assert manifest['report']['resolution_checks'][0]['status'] == 'insufficient'
    assert '해소 판단: 근거 확인 필요' not in report
    assert '해당 한정을 보완할 구체적인 근거가 부족합니다.' in report


def supplement_resolution(values):
    values[0]['evidence'].append({'id': 'E2', 'attachment': 'ATT-02',
        'quote': 'Another controller retains the initial speech segment.', 'language': 'foreign',
        'translation': '다른 제어기는 초기 음성 구간을 유지합니다.'})
    resolution = resolution_input(values)
    resolution.update(evidence=['E2'], supplements=[{'attachment': 'ATT-02', 'evidence': 'E2',
        'explanation': '이는 초기 음성 구간을 유지하는 조건에 대응합니다.'}],
        derivation='', remaining_difference='실질적 기술 구성상의 차이는 해소됩니다.', conclusion='supported')
    return resolution


def test_single_reference_and_no_gap_declaration_preserve_verified_supplement(report_input):
    resolution = supplement_resolution(report_input)
    report, manifest, _ = compile_input(report_input)
    assert manifest['report']['issues'] == []
    assert manifest['report']['resolution_checks'][0]['status'] == 'supported'
    assert '"Another controller retains the initial speech segment."' in report
    assert resolution['supplements'][0]['explanation'] in report
    assert resolution['remaining_difference'] in report
    # Normalization is a reading compatibility rule, not a rewrite of model judgments.
    assert manifest['report']['data'] == report_input[0]


@pytest.mark.parametrize('field', ['component', 'resolution', 'supplement'])
def test_single_id_accepted_at_each_reference_site(report_input, field):
    resolution = supplement_resolution(report_input)
    resolution['supplements'][0]['evidence'] = ['E2']
    if field == 'component':
        report_input[0]['components'][0]['evidence'] = ' E1 '
    elif field == 'resolution':
        resolution['evidence'] = ' E2 '
    else:
        resolution['supplements'][0]['evidence'] = ' E2 '
    _, manifest, _ = compile_input(report_input)
    assert not manifest['report']['issues']


@pytest.mark.parametrize('defect', ['unknown', 'wrong_document', 'unlinked', 'malformed', 'bad_quote', 'missing_detail'])
def test_single_reference_tolerance_never_bypasses_evidence_checks(report_input, defect):
    resolution = supplement_resolution(report_input)
    supplement = resolution['supplements'][0]
    detail = supplement['explanation']
    if defect == 'unknown':
        supplement['evidence'] = 'E404'
    elif defect == 'wrong_document':
        supplement['evidence'] = 'E1'
        resolution['evidence'] = ['E1', 'E2']
    elif defect == 'unlinked':
        resolution['evidence'] = ['E1']
    elif defect == 'malformed':
        supplement['evidence'] = 'E1,E2'
    elif defect == 'bad_quote':
        report_input[0]['evidence'][-1]['quote'] = 'An invented sentence.'
    else:
        supplement['explanation'] = ''
    report, manifest, _ = compile_input(report_input)
    assert manifest['report']['resolution_checks'][0]['status'] == 'needs_review'
    if defect != 'missing_detail':
        assert detail in report
        assert '보완 방법과 기술적 이유가 누락되었습니다.' not in report
    else:
        assert '보완 방법과 기술적 이유가 누락되었습니다.' in report
    if defect in ('unknown', 'wrong_document', 'unlinked', 'malformed'):
        assert '관련 설명 (근거 연결 확인 필요)' in report
        assert '"Another controller' not in report.split('#### [차이점]')[1]
    assert '"An invented sentence."' not in report


@pytest.mark.parametrize('remaining', ['없음', '잔존 차이가 없습니다.', '차이가 해소됩니다.',
    '실질적 기술 구성상의 차이는 해소됩니다.'])
def test_unambiguous_no_gap_statement_does_not_contradict_supported(report_input, remaining):
    resolution_input(report_input)['remaining_difference'] = remaining
    _, manifest, _ = compile_input(report_input)
    assert manifest['report']['resolution_checks'][0]['status'] == 'supported'


@pytest.mark.parametrize('remaining', ['유지 시간 조건은 남습니다.', '차이가 해소되지 않습니다.',
    '차이는 해소됩니다. 다만 유지 시간 조건은 남습니다.', '차이가 해소된다면 잔존 차이가 없습니다.',
    '실질적 기술 구성상의 차이는 해소됩니다. 추가 검증이 필요합니다.'])
def test_substantive_or_qualified_gap_is_still_flagged(report_input, remaining):
    resolution_input(report_input)['remaining_difference'] = remaining
    report, manifest, _ = compile_input(report_input)
    assert manifest['report']['resolution_checks'][0]['status'] == 'needs_review'
    assert '해소 판단과 잔존 차이가 모순됩니다.' in report
    assert remaining in report


@pytest.mark.parametrize('conclusion', ['remaining_gap', 'insufficient'])
def test_no_gap_statement_cannot_fill_missing_gap_or_insufficient_reason(report_input, conclusion):
    resolution_input(report_input).update(conclusion=conclusion, remaining_difference='없음')
    _, manifest, _ = compile_input(report_input)
    assert manifest['report']['resolution_checks'][0]['status'] == 'needs_review'


@pytest.mark.parametrize('positive_claim', [False, True])
def test_primary_reference_gap_does_not_require_a_positive_quote(report_input, positive_claim):
    resolution_input(report_input).update(gap='{{ATT-01}}에는 추가 조건이 기재되어 있지 않습니다.',
        evidence=[], supplements=[], derivation='{{ATT-01}}의 동작을 적용합니다.' if positive_claim else '',
        remaining_difference='{{ATT-01}}의 추가 조건은 확인되지 않습니다.', conclusion='insufficient')
    report, manifest, _ = compile_input(report_input)
    check = manifest['report']['resolution_checks'][0]
    assert check['status'] == ('needs_review' if positive_claim else 'insufficient')
    if positive_claim:
        assert '인용발명 1 (ATT-01): 설명의 근거로 사용했지만' in report
    else:
        assert manifest['report']['issues'] == []
        assert '출력 점검' not in report


def test_difference_separator_is_added_for_each_claim(report_input):
    row = report_input[0]['components'][0]
    report_input[0]['components'].append({**row, 'claim': '청구항 2'})
    report, _, _ = compile_input(report_input)
    assert report.count('\n\n---\n\n#### [차이점]') == 2


def test_each_supplement_gets_a_paragraph_and_connected_sentence(report_input, tmp_path):
    data, aliases, attachments = report_input
    quote = 'A third controller adjusts the duration of the initial segment.'
    third = ingest_one('third.txt', quote.encode(), tmp_path / 'third', True,
                       IngestionLimits(), role=AttachmentRole.CITATION)
    attachments.append(third)
    aliases.update(citation_mapping.assign_aliases(attachments))
    data['documents'].append({'attachment': 'ATT-03', 'document_number': 'US3', 'title': 'Third source'})
    for eid, alias, original, translation in [
        ('E2', 'ATT-02', 'Another controller retains the initial speech segment.', '다른 제어기는 초기 음성 구간을 유지한다.'),
        ('E3', 'ATT-03', quote, '제3 제어기는 초기 구간의 지속시간을 조정한다.')]:
        data['evidence'].append({'id': eid, 'attachment': alias, 'quote': original,
                                'language': 'foreign', 'translation': translation})
    resolution_input(report_input).update(gap='{{ATT-01}}에는 유지 시간 조건이 부족합니다.',
        evidence=['E2', 'E3'], supplements=[
            {'attachment': 'ATT-02', 'evidence': ['E2'], 'explanation': '이는 초기 구간의 유지에 대응합니다.'},
            {'attachment': 'ATT-03', 'evidence': ['E3'], 'explanation': '이는 유지 시간의 조정에 대응합니다.'}],
        derivation='', remaining_difference='')
    report, manifest, _ = compile_input(report_input)
    section = report.split('#### [차이점]')[1].split('## 3.')[0]
    for number in (1, 2, 3):
        assert f'\n\n**인용발명 {number}**' in section
    assert '라는 기재가 있습니다.' not in section
    assert '라는 기재가 있으며, 이는 초기 구간의 유지에 대응합니다.' in section
    assert '라는 기재가 있으며, 이는 유지 시간의 조정에 대응합니다.' in section
    assert quote in section  # Punctuation inside the original excerpt is unchanged.
    assert manifest['report']['issues'] == []


def test_nonexistent_unit_is_relocated_only_when_v2_quote_is_exact(report_input):
    report_input[0]['version'] = 2
    report_input[0]['evidence'][0]['source_id'] = 'ATT-01-P0-U999'
    _, manifest, _ = compile_input(report_input)
    item = manifest['report']['evidence']['E1']
    assert item['verified'] and item['resolution_method'] == 'unique_exact_quote'


@pytest.mark.parametrize('main_quote,supplement_quote,translation,feature', [
    ('The valve closes when pressure exceeds the threshold.',
     'The second valve uses a resilient sealing ring.', '제2 밸브는 탄성 밀봉 링을 사용한다.', '압력에 따라 닫히는 밀봉 밸브'),
    ('The reactor controls temperature during the reaction.',
     'The catalyst is recovered using a porous filter.', '촉매는 다공성 필터로 회수된다.', '반응 온도를 조절하고 촉매를 회수하는 장치'),
    ('수신기는 패킷의 순서를 검사하여 오류를 검출한다.',
     '송신기는 수신 확인이 없으면 패킷을 재전송한다.', '', '순서 검사와 재전송을 수행하는 통신 장치'),
])
def test_report_contract_is_independent_of_technology_and_upload_order(
        report_input, main_quote, supplement_quote, translation, feature):
    data, _, attachments = report_input
    # The second uploaded document is the primary reference. The first is
    # supplementary, irrespective of terminology and document language.
    Path(attachments[1].normalized_text_path).write_text(main_quote, encoding='utf-8')
    Path(attachments[0].normalized_text_path).write_text(supplement_quote, encoding='utf-8')
    data['version'] = 2
    data['documents'].reverse()
    data['evidence'] = [
        {'id': 'E1', 'attachment': 'ATT-02', 'source_id': 'ATT-02-P0-U1', 'quote': main_quote,
         'language': 'foreign' if translation else 'ko', 'translation': '주 문헌의 해당 동작에 관한 번역문입니다.' if translation else ''},
        {'id': 'E2', 'attachment': 'ATT-01', 'source_id': 'ATT-01-P0-U1', 'quote': supplement_quote,
         'language': 'foreign' if translation else 'ko', 'translation': translation},
    ]
    data['components'][0].update(feature=feature, reasoning='{{ATT-02}}의 동작과 부분 대응합니다.',
        difference='추가 조건이 확인되지 않습니다.', resolution={
            'gap': '{{ATT-02}}에는 추가 조건의 기재가 부족합니다.', 'evidence': ['E1', 'E2'],
            'supplements': [{'attachment': 'ATT-01', 'evidence': ['E2'],
                            'explanation': '해당 근거는 추가 조건의 일부를 보완합니다.'}],
            'derivation': '', 'remaining_difference': '전체 작동 관계는 추가 확인이 필요합니다.',
            'conclusion': 'remaining_gap'})
    report, manifest, _ = compile_input(report_input)
    comparison, difference = report.split('#### [구성요소]')[1].split('#### [차이점]')
    difference = difference.split('## 3.')[0]
    assert main_quote in comparison and supplement_quote not in comparison
    assert supplement_quote in difference and main_quote not in difference
    assert '**인용발명 2** (US1)' in difference
    assert manifest['report']['issues'] == []
    assert manifest['report']['comparison_checks'][0]['status'] == 'main_only'


@pytest.mark.parametrize('korean', [False, True])
@pytest.mark.parametrize('paragraph', [False, True])
def test_location_is_between_translation_and_original_or_after_korean(report_input, korean, paragraph):
    data = report_input[0]
    quote = '제어기는 이득을 변경합니다.' if korean else data['evidence'][0]['quote']
    marker = '[0123] ' if paragraph else ''
    Path(report_input[2][0].normalized_text_path).write_text(
        '--- PAGE 7 ---\n' + marker + quote, encoding='utf-8')
    data['evidence'][0].update(quote=quote, language='ko' if korean else 'foreign',
                                translation='' if korean else '제어기는 이득을 변경합니다.')
    report, _, _ = compile_input(report_input)
    location = r'단락 \[0123\]' if paragraph else 'PDF 페이지 7'
    if korean:
        assert f'"{quote}" ({location})' in report
        assert '[번역 미제공]' not in report
    else:
        assert f'"제어기는 이득을 변경합니다." ({location}; "{quote}")' in report


def test_missing_translation_is_visible_and_other_content_survives(report_input):
    report_input[0]['evidence'][0]['translation'] = ''
    report, manifest, _ = compile_input(report_input)
    assert '[번역 미제공]' in report
    assert manifest['report']['issues']
    assert len(manifest['items']) == 1


def test_fabricated_quote_never_appears_as_verified_excerpt(report_input):
    report_input[0]['evidence'][0]['quote'] = 'The controller does a different thing never found in the source.'
    report, manifest, _ = compile_input(report_input)
    assert 'different thing' not in report
    assert not manifest['report']['evidence']['E1']['verified']
    assert '원문과 일치하는 발췌를 확인하지 못했습니다' in report


def test_unknown_reference_is_explicit_not_guessed(report_input):
    report_input[0]['components'][0]['evidence'] = ['E404']
    report, manifest, _ = compile_input(report_input)
    assert 'E404 근거가 없습니다' in report
    assert manifest['report']['issues']


def test_application_attachment_cannot_be_a_citation(report_input):
    report_input[2][0].role = AttachmentRole.APPLICATION
    with pytest.raises(structured_report.ReportError, match='실제 인용문헌에 없는'):
        compile_input(report_input)


def test_non_json_and_duplicate_documents_are_rejected(report_input):
    with pytest.raises(structured_report.ReportError):
        structured_report.decode('Markdown only')
    report_input[0]['documents'].append(report_input[0]['documents'][0])
    with pytest.raises(structured_report.ReportError, match='중복 문헌'):
        compile_input(report_input)


def test_model_text_cannot_create_extra_markdown_structure(report_input):
    report_input[0]['components'][0]['reasoning'] = '<script>alert(1)</script>\n# Fake heading [link](https://example.org)'
    report, _, _ = compile_input(report_input)
    assert '<script>' not in report
    assert '\n# Fake heading' not in report


def test_bad_component_preserves_other_components_without_inventing_a_score(report_input):
    report_input[0]['components'].append({**report_input[0]['components'][0], 'symbol': '(B)', 'similarity': 'ninety'})
    report, manifest, _ = compile_input(report_input)
    assert len(manifest['items']) == 1
    assert '결과 항목 확인 필요' in report
    assert '(B)' in report
    assert manifest['report']['issues']


def test_retrieval_quote_is_limited_to_the_actual_delivered_sources(report_input):
    report, manifest, _ = compile_input(report_input, bundle={'candidate_sources': [
        {'attachment': 'ATT-02', 'pdf_page': 1, 'source_text': 'Only this source was supplied.'}]})
    assert not manifest['report']['evidence']['E1']['verified']
    assert '"The controller' not in report


@pytest.mark.parametrize('malformed', [False, True])
@pytest.mark.parametrize('version', [3, 4])
def test_runner_persists_legacy_report_without_formatting_retry(client, monkeypatch, malformed, version):
    from app.providers.base import ExecutionOutcome
    from .fake_provider import DeterministicTestProvider
    from .conftest import wait_for_job
    calls = []
    async def execute(self, request, emit):
        from app import comparison_review
        if request.system_prompt == comparison_review.CLAIMS:
            return ExecutionOutcome(result_text='{"components":[]}', exit_code=0)
        calls.append(request)
        payload = {'version': version, 'documents': [{'attachment': 'ATT-01', 'document_number': 'US1'}],
            'evidence': [{'id': 'E1', 'attachment': 'ATT-01', 'sentence_ids': ['ATT-01-P0-T1'],
                          'language': 'foreign', 'translation': '제어기는 이득을 변경한다.'}],
            'components': [{'claim': '청구항 1', 'symbol': '(A)', 'feature': '제어기', 'similarity': 90,
                            'basis': 'direct', 'evidence': ['E1'], 'reasoning': '이득을 변경하는 제어기로 대응합니다.',
                            'difference': '이득의 구체적인 변경 조건',
                            'resolution': {'gap': '변경 조건', 'evidence': ['E1'],
                                'explanation': '{{ATT-01}}의 제어기에 조건을 추가하는 변경을 검토합니다.',
                                'remaining_difference': '변경 조건의 근거 부족', 'conclusion': 'insufficient'}}],
            'summary': {'main_reason': '제어기에 대응합니다.', 'relationships': '동일한 순서입니다.'}}
        if version == 4:
            payload['components'][0].pop('resolution')
        return ExecutionOutcome(result_text='{"version":1' if malformed else json.dumps(payload), exit_code=0)
    monkeypatch.setattr(DeterministicTestProvider, 'execute', execute)
    batch = client.post('/api/uploads', files=[('files', ('source.txt', b'The controller changes gain.', 'text/plain'))]).json()
    created = client.post('/api/jobs', json={'provider': 'test',
        'claim_text': 'A controller changes gain.', 'batch_id': batch['batch_id']})
    assert created.status_code == 201, created.text
    job = wait_for_job(client, created.json()['id'])
    assert len(calls) == 1
    if malformed:
        from app.db import session_scope
        from app.models import ExecutionJob
        assert job['status'] == 'FAILED'
        assert job['error_code'] == 'INVALID_OUTPUT'
        assert '구조화된 분석 결과를 읽지 못했습니다' in job['result_text']
        with session_scope() as session:
            stored = session.get(ExecutionJob, job['id'])
            assert (Path(stored.work_dir) / 'analysis-response.txt').read_text(encoding='utf-8') == '{"version":1'
        return
    assert job['status'] == 'SUCCEEDED', job['errors']
    assert '**인용발명 1**' in job['result_text']
    assert job['analysis_manifest']['report']['version'] == 1
    assert job['analysis_manifest']['report']['evidence']['E1']['resolution_method'] == 'source_sentence_ids'
    if version == 3:
        assert job['analysis_manifest']['report']['resolution_checks'][0]['status'] == 'insufficient'
    else:
        assert job['analysis_manifest']['report']['resolution_checks'] == []
        assert '구성 (A): 이득의 구체적인 변경 조건' in job['result_text']
    assert job['citation_mapping']['items'][0]['document_number'] == 'US1'
    assert job['result_text'].index('"제어기는') < job['result_text'].index('"The controller')
