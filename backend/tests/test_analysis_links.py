import copy
import json
import time
from unittest.mock import AsyncMock

import pytest

from app import analysis_evidence as ae
from app.analysis_links import limitations_for
from app.providers.base import ExecutionOutcome, ExecutionRequest
from .test_analysis_evidence import assessment, component, source


FEATURE = 'A controller measures pressure and opens a valve only when pressure exceeds 10 kPa.'
FIRST = 'In embodiment 1, controller 10 measures pressure using sensor 12.'
SECOND = 'In embodiment 1, controller 10 opens valve 14 only when the measured pressure exceeds 10 kPa.'


@pytest.fixture
def linked():
    c = {**component(), 'feature': FEATURE}
    sources = {'ATT-01': source(text=FIRST)}
    sources['ATT-01']['pages'].append((2, SECOND))
    passages = [ae.passage('ATT-01', page, value, 0, len(value), 'draft')
                for page, value in sources['ATT-01']['pages']]
    a, b = [p['id'] for p in passages]
    supports = [
        {'limitation_id': 'L1', 'candidate_ids': [a], 'status': 'direct', 'reason': '제어기 10이 센서 12로 압력을 측정한다.'},
        {'limitation_id': 'L2', 'candidate_ids': [b], 'status': 'direct', 'reason': '측정 압력이 10 kPa를 초과할 때에만 밸브 14를 연다.'}]
    group = {'id': 'S1', 'candidate_ids': [a, b], 'verdict': 'direct', 'similarity': 95,
             'axes': {axis: 'same' for axis in ae.AXES}, 'relation': '동일 실시예의 제어기 10이 측정한 압력으로 밸브를 제어한다.',
             'difference': '', 'supports': supports,
             'coherence': {'status': 'same_embodiment', 'reason': '두 기재 모두 실시예 1의 제어기 10을 특정한다.',
                           'candidate_ids': [a, b]}}
    row = {'selected_id': 'S1', 'selection_reason': '한 구절만으로는 측정과 개방 조건 전체를 확인할 수 없다.',
           'limitations': [{'id': 'L1', 'text': 'A controller measures pressure'},
                           {'id': 'L2', 'text': 'and opens a valve only when pressure exceeds 10 kPa.'}],
           'assessments': [assessment(p, 'partial') for p in passages], 'evidence_sets': [group],
           'derivations': [], 'derivation_limitation': '같은 실시예의 복수 기재로 직접 대응하므로 결합 불필요.'}
    return c, sources, passages, row


def test_separate_pages_jointly_support_all_limitations_without_splicing_quotes(linked):
    c, sources, passages, row = linked
    result = ae.validate_review(c, passages, row)
    assert result['comparison_complete'], result['issues']
    assert ae.selected_evidence(result)['similarity'] == 95
    assert all(p['verdict'] == 'partial' for p in result['candidates'])
    mapping = ae.select_documents([result], sources, None)
    assert mapping['items'][0]['coverage'] == {'C001': 3}
    report = ae.render({'components': [result], 'documents': {'ATT-01': 'source.pdf'}, 'issues': []}, mapping)
    assert FIRST in report and SECOND in report
    assert 'PDF 1쪽' in report and 'PDF 2쪽' in report
    assert 'only when pressure exceeds 10 kPa.' in report
    assert '동일 실시예 연결 근거' in report


@pytest.mark.parametrize('mutation,issue', [
    ('different_document', 'unsupported_evidence_set'),
    ('different_embodiment', 'unsupported_evidence_set'),
    ('unknown_coherence', 'unsupported_evidence_set'),
    ('missing_limitation', 'unsupported_evidence_set'),
    ('invented_candidate', 'unsupported_evidence_set'),
    ('inferred_limitation', 'inconsistent_direct_evidence_set'),
    ('different_condition', 'inconsistent_direct_evidence_set'),
    ('remaining_gap', 'inconsistent_direct_evidence_set'),
    ('duplicate_set', 'invalid_evidence_set'),
])
def test_invalid_sets_cannot_promote_partial_passages_to_direct(linked, mutation, issue):
    c, _, passages, row = linked
    group = row['evidence_sets'][0]
    if mutation == 'different_document': passages[1]['attachment'] = 'ATT-02'
    if mutation == 'different_embodiment': group['coherence']['status'] = 'different_embodiments'
    if mutation == 'unknown_coherence': group['coherence']['status'] = 'unknown'
    if mutation == 'missing_limitation': group['supports'].pop()
    if mutation == 'invented_candidate': group['supports'][1]['candidate_ids'] = ['invented']
    if mutation == 'inferred_limitation': group['supports'][1]['status'] = 'inferred'
    if mutation == 'different_condition': group['axes']['condition'] = 'different'
    if mutation == 'remaining_gap': group['difference'] = '10 kPa 조건 미확인'
    if mutation == 'duplicate_set': row['evidence_sets'].append(copy.deepcopy(group))
    result = ae.validate_review(c, passages, row)
    assert result['selected_id'] is None
    assert issue in result['issues']


def test_omitted_only_condition_fails_limitation_coverage(linked):
    c, _, passages, row = linked
    row['limitations'][1]['text'] = 'and opens a valve'
    result = ae.validate_review(c, passages, row)
    assert not result['limitation_coverage_complete']
    assert not result['evidence_sets']
    assert result['selected_id'] is None
    assert not limitations_for(FEATURE, [{'id': 'L1', 'text': 'paraphrased feature'}])[1]


@pytest.mark.parametrize('symbol', ['<', '>', '≤', '≥', '%', '°', '-'])
def test_numeric_operators_cannot_disappear_from_limitation_coverage(symbol):
    feature = f'pressure {symbol} 10'
    assert not limitations_for(feature, [{'id': 'L1', 'text': 'pressure'}, {'id': 'L2', 'text': '10'}])[1]
    assert limitations_for(feature, [{'id': 'L1', 'text': feature}])[1]


def combination_row(linked):
    c, sources, passages, row = linked
    passages[1]['attachment'] = 'ATT-02'
    sources['ATT-02'] = source('ATT-02', SECOND)
    group = row['evidence_sets'].pop()
    # Keep single-document scores partial even if the route can fill all gaps.
    row['selected_id'] = 'S0'
    partial = copy.deepcopy(group)
    partial.update(id='S0', candidate_ids=[passages[0]['id']], verdict='partial', similarity=55,
                   difference='압력 조건에 따른 밸브 개방은 이 문헌에서 확인되지 않는다.')
    partial['coherence']['candidate_ids'] = [passages[0]['id']]
    partial['coherence']['reason'] = '측정 동작은 한 문장에 기재되어 있다.'
    partial['supports'][1].update(candidate_ids=[], status='missing', reason='개방 조건은 미확인이다.')
    row['evidence_sets'] = [partial]
    row['derivations'] = [{
        'kind': 'combination', 'candidate_ids': group['candidate_ids'], 'supports': group['supports'],
        'motivation': {'reason': '측정한 압력을 밸브의 제어 입력으로 활용하는 경로를 검토한다.', 'candidate_ids': group['candidate_ids']},
        'compatibility': {'status': 'compatible', 'reason': '두 기재에서 동일한 압력 물리량을 사용한다.', 'candidate_ids': group['candidate_ids']},
        'modification': '측정 제어기에 인용발명 2의 압력 조건에 따른 개방 제어를 적용한다.',
        'conclusion': 'supported', 'reason': '각 문헌에서 확인된 기재를 결합하는 경로에 대한 평가.',
        'remaining_difference': ''}]
    return c, sources, passages, row


def test_combination_is_rendered_with_sources_but_never_changes_similarity(linked):
    c, sources, passages, row = combination_row(linked)
    result = ae.validate_review(c, passages, row)
    assert len(result['derivations']) == 1
    assert ae.selected_evidence(result)['similarity'] == 55
    mapping = ae.select_documents([result], sources, None)
    assert {r['alias'] for r in mapping['items']} == {'ATT-01', 'ATT-02'}
    assert next(r for r in mapping['items'] if r['alias'] == 'ATT-01')['coverage'] == {'C001': 2}
    assert next(r for r in mapping['items'] if r['alias'] == 'ATT-02')['coverage'] == {}
    report = ae.render({'components': [result], 'documents': {a: a for a in sources}, 'issues': []}, mapping)
    for expected in (FIRST, SECOND, '문헌 간 결합', '변경·결합 동기', '기술적 양립 가능성', '도출·결합 후 남는 차이', '(55%)'):
        assert expected in report


@pytest.mark.parametrize('mutation', ['invented', 'missing_motivation', 'unsupported_gap', 'incompatible', 'same_document', 'missing_support'])
def test_unjustified_combination_cannot_be_reported_as_supported(linked, mutation):
    c, _, passages, row = combination_row(linked)
    route = row['derivations'][0]
    if mutation == 'invented': route['motivation']['candidate_ids'] = ['invented']
    if mutation == 'missing_motivation': route['motivation']['reason'] = ''
    if mutation == 'unsupported_gap': route['remaining_difference'] = '연결 방식이 확인되지 않았다.'
    if mutation == 'incompatible': route['compatibility']['status'] = 'incompatible'
    if mutation == 'same_document': passages[1]['attachment'] = 'ATT-01'
    if mutation == 'missing_support': route['supports'].pop()
    result = ae.validate_review(c, passages, row)
    assert not result['derivations']
    assert not result['comparison_complete']


def test_remaining_difference_and_inference_survive_rendering(linked):
    c, sources, passages, row = combination_row(linked)
    route = row['derivations'][0]
    route.update(conclusion='remaining_gap', remaining_difference='두 제어기의 연결 방식은 미확인.')
    route['supports'][1].update(status='inferred', reason='입출력 연결에는 추가 변경이 필요하다.')
    result = ae.validate_review(c, passages, row)
    mapping = ae.select_documents([result], sources, None)
    report = ae.render({'components': [result], 'documents': {a: a for a in sources}, 'issues': []}, mapping)
    assert '두 제어기의 연결 방식은 미확인.' in report
    assert '추론 필요' in report
    assert '차이점 잔존' in report


def test_single_document_modification_remains_inferred_and_preserves_prior_number(linked):
    c, sources, passages, row = combination_row(linked)
    passages[1]['attachment'] = 'ATT-01'
    sources.pop('ATT-02')
    route = row['derivations'][0]
    route['kind'] = 'single_document'
    route['supports'][1].update(status='inferred', reason='기존 제어기에 압력 조건을 적용하는 변경이 필요하다.')
    result = ae.validate_review(c, passages, row)
    prior = {'items': [{'attachment_id': 'ATT-01', 'citation_number': 7}]}
    mapping = ae.select_documents([result], sources, None, prior)
    report = ae.render({'components': [result], 'documents': {'ATT-01': 'source.pdf'}, 'issues': []}, mapping)
    assert '단일 문헌으로부터의 도출' in report and '추론 필요' in report
    assert '인용발명 7' in report
    assert ae.selected_evidence(result)['verdict'] == 'partial'


def test_derivation_sources_alone_do_not_select_a_primary_document(linked):
    c, sources, passages, row = combination_row(linked)
    row['selected_id'] = None
    result = ae.validate_review(c, passages, row)
    mapping = ae.select_documents([result], sources, None)
    assert len(mapping['items']) == 2
    assert mapping['primary_alias'] is None
    assert all(not item['coverage'] for item in mapping['items'])


def test_foreign_cjk_passage_without_translation_cannot_support_a_set(linked):
    c, _, passages, row = linked
    passages[1]['quote'] = '実施例１は圧力制御によって弁を開放する装置である。'
    row['assessments'][1]['translation'] = ''
    result = ae.validate_review(c, passages, row)
    assert result['selected_id'] is None
    assert 'translation_unreviewed' in result['issues']


def test_raw_direct_score_without_limitation_links_is_not_a_final_match(linked):
    c, sources, passages, row = linked
    row['assessments'] = [assessment(p) for p in passages]
    row['evidence_sets'] = []
    row['selected_id'] = passages[0]['id']
    result = ae.validate_review(c, passages, row)
    assert result['selected_id'] is None
    assert not result['comparison_complete']
    mapping = ae.select_documents([result], sources, None)
    assert not mapping['items']
    report = ae.render({'components': [result], 'documents': {'ATT-01': 'reference.pdf'}, 'issues': []}, mapping)
    assert '한정별 근거 미확인' in report
    assert '(95%)' not in report


def test_rejected_translation_or_source_quote_cannot_be_used_in_sets_or_routes(linked):
    c, _, passages, row = combination_row(linked)
    row['assessments'][1]['quote'] = 'Invented source support which does not exist.'
    result = ae.validate_review(c, passages, row)
    assert not result['derivations']
    assert 'review_quote_not_in_source' in result['issues']


@pytest.mark.parametrize('feature,query,quote', [
    ('압력 기준값', '10 kPa', 'The device responds at exactly 10 kPa.'),
    ('밸브 개방', '圧力制御', '実施例１は圧力制御によって弁を開放する装置である。'),
])
def test_numeric_and_cjk_queries_retrieve_source_alternatives(feature, query, quote):
    found, _ = ae.candidates_for({**component(), 'feature': feature}, {'queries': [query]}, {'ATT-01': source(text=quote)})
    assert found and found[0]['quote'] == quote


@pytest.mark.asyncio
async def test_pipeline_preserves_multi_passage_result_and_audit(tmp_path, linked):
    from types import SimpleNamespace
    from app.citation_mapping import AliasedAttachment
    c, _, _, row = linked
    path = tmp_path / 'source.txt'
    path.write_text('--- PAGE 1 ---\n' + FIRST + '\n--- PAGE 2 ---\n' + SECOND, encoding='utf-8')
    attachments = [SimpleNamespace(attachment_id='file', included=True, read_ok=True,
                                  role='CITATION', normalized_text_path=str(path))]
    aliases = {'ATT-01': AliasedAttachment('ATT-01', 'file', 'sha', 'reference.pdf')}
    proposal = {'components': [{**c, 'queries': ['pressure'], 'candidates': []}]}

    class Reviewer:
        max_input_bytes = None
        async def execute(self, request, emit):
            payload = json.loads(request.user_message)['components'][0]
            actual = payload['candidates']
            assert len(actual) == 2
            a = next(p['id'] for p in actual if FIRST in p['quote'])
            b = next(p['id'] for p in actual if SECOND in p['quote'])
            response = copy.deepcopy(row)
            response.update(id=c['id'], assessments=[assessment(p, 'partial') for p in actual])
            g = response['evidence_sets'][0]
            g['candidate_ids'] = g['coherence']['candidate_ids'] = [a, b]
            g['supports'][0]['candidate_ids'] = [a]
            g['supports'][1]['candidate_ids'] = [b]
            return ExecutionOutcome(result_text=json.dumps({'components': [response]}), exit_code=0)

    report, manifest, _, audit = await ae.run(Reviewer(), ExecutionRequest('job', tmp_path, '', ''),
        ExecutionOutcome(result_text=ae.OPEN + json.dumps(proposal) + ae.CLOSE),
        attachments=attachments, aliases=aliases, components={'items': [c]}, mapping=None,
        prior_mapping=None, claim_text=FEATURE, deadline=time.monotonic()+30, emit=AsyncMock(), cancelled=lambda: False)
    assert audit['status'] == 'reviewed', audit
    assert manifest['items'][0]['similarity'] == 95
    assert manifest['items'][0]['basis'] == 'direct'
    assert not manifest['items'][0]['search_eligible']
    assert FIRST in report and SECOND in report
    saved = json.loads((tmp_path / 'analysis_evidence/review.json').read_text(encoding='utf-8'))
    assert len(saved['components'][0]['evidence_sets'][0]['candidate_ids']) == 2
