import json
import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app import analysis_evidence as ae
from app.citation_mapping import AliasedAttachment
from app.providers.base import ExecutionRequest, ExecutionOutcome, NO_TOOLS

QUOTE = 'The controller opens the valve when pressure exceeds a threshold.'
WRONG = 'The controller closes the valve when temperature exceeds a threshold.'


def component(index=1):
    return {'id': f'C{index:03d}', 'claim': '청구항 1', 'symbol': f'({chr(64+index)})',
            'feature': QUOTE, 'similarity': 99, 'status': 'matched', 'basis': 'direct',
            'difference': '', 'search_eligible': False}


def source(alias='ATT-01', text=QUOTE):
    return {'identity': AliasedAttachment(alias, alias, alias, alias + '.pdf'), 'pages': [(1, text)]}


def assessment(candidate, verdict='direct', **extra):
    return {'id': candidate['id'], 'verdict': verdict,
            'axes': {axis: 'same' for axis in ae.AXES},
            'similarity': 95 if verdict == 'direct' else 55 if verdict == 'partial' else None if verdict == 'unknown' else 0,
            'relation': '압력이 임계값을 넘으면 밸브를 여는 관계가 일치함',
            'difference': '' if verdict == 'direct' else '제어 조건 또는 동작이 다름',
            'translation': '제어기는 압력이 임계값을 초과하면 밸브를 연다.', **extra}


def single_set(candidate, ident='S1'):
    return {**assessment(candidate), 'id': ident, 'candidate_ids': [candidate['id']],
            'coherence': {'status': 'same_embodiment', 'reason': '하나의 조건-동작 문장', 'candidate_ids': [candidate['id']]},
            'supports': [{'limitation_id': 'L1', 'candidate_ids': [candidate['id']], 'status': 'direct', 'reason': '조건과 동작이 모두 명시됨'}]}


def test_quotes_require_exact_document_and_page_but_tolerate_whitespace():
    sources = {'ATT-01': source()}
    proposal = {'candidates': [
        {'attachment': 'ATT-01', 'page': 1, 'quote': QUOTE.replace(' ', '\n')},
        {'attachment': 'ATT-01', 'page': 2, 'quote': QUOTE},
        {'attachment': 'ATT-02', 'page': 1, 'quote': QUOTE},
        {'attachment': 'ATT-01', 'page': 1, 'quote': WRONG}]}
    candidates, issues = ae.candidates_for(component(), proposal, sources)
    assert candidates[0]['quote'] == QUOTE
    assert len(candidates) == 1
    assert len(issues) == 3
    assert ae.anchor(WRONG, QUOTE) is None
    assert ae.anchor('valve', QUOTE) is None


def test_candidate_discovery_searches_unselected_documents():
    sources = {'ATT-01': source(text=WRONG), 'ATT-02': source('ATT-02')}
    found, _ = ae.candidates_for(component(), {'candidates': [], 'queries': ['pressure valve']}, sources)
    assert {c['attachment'] for c in found} == set(sources)


def test_source_number_uses_first_page_not_filename_or_cited_patents():
    assert ae.source_number(source(text='(10) US 2025/0148648 A1\n(12) Patent Application Publication')) == 'US 2025/0148648 A1'
    assert ae.source_number(source(text='(11) 공개번호 10-2018-0107192')) == '10-2018-0107192'
    assert ae.source_number(source(text='References: US 2025/0148648 A1 and US 2024/0104783 A1')) == '문헌번호 확인 불가'


def test_korean_passage_with_english_terms_does_not_need_duplicate_translation():
    assert not ae.needs_translation('버텍스 버퍼에 저장된 정점의 위치를 GPU에서 계산하고 변환하는 단계이다.')
    assert ae.needs_translation(QUOTE)
    assert ae.needs_translation('実施例１は圧力制御によって弁を開放する装置である。')


def test_review_cannot_replace_source_with_invented_quote():
    candidates, _ = ae.candidates_for(component(), {}, {'ATT-01': source()})
    raw = assessment(candidates[0], quote=WRONG)
    result = ae.validate_review(component(), candidates, {'selected_id': raw['id'],
        'selection_reason': 'best', 'assessments': [raw]})
    assert result['selected_id'] is None
    assert not result['comparison_complete']
    assert 'review_quote_not_in_source' in result['issues']


def test_missing_translation_is_not_a_completed_review():
    candidates, _ = ae.candidates_for(component(), {}, {'ATT-01': source()})
    raw = assessment(candidates[0], translation='')
    result = ae.validate_review(component(), candidates, {'selected_id': raw['id'],
        'selection_reason': 'best', 'assessments': [raw]})
    assert result['selected_id'] is None
    assert result['candidates'][0]['similarity'] is None
    assert 'translation_unreviewed' in result['issues']


def test_unfinished_internal_block_is_hidden():
    assert ae.strip('report\n' + ae.OPEN + '\n{"components":[') == 'report'


def test_direct_label_with_different_condition_is_not_accepted():
    candidates, _ = ae.candidates_for(component(), {}, {'ATT-01': source()})
    raw = assessment(candidates[0], axes={**{a: 'same' for a in ae.AXES}, 'condition': 'different'})
    result = ae.validate_review(component(), candidates, {'selected_id': raw['id'],
        'selection_reason': 'best', 'assessments': [raw]})
    assert result['selected_id'] is None
    assert result['candidates'][0]['similarity'] is None
    assert 'inconsistent_direct_match' in result['issues']


def test_lexical_match_cannot_beat_semantic_candidate_and_invented_ids_rejected():
    candidates, _ = ae.candidates_for(component(), {}, {'ATT-01': source(text=WRONG), 'ATT-02': source('ATT-02')})
    a, b = candidates
    result = ae.validate_review(component(), candidates, {'selected_id': a['id'], 'selection_reason': 'keywords',
        'assessments': [assessment(a, 'lexical_only'), assessment(b), {'id': 'invented'}]})
    assert result['selected_id'] is None
    assert 'selection_not_supported' in result['issues']
    assert 'unknown_candidate' in result['issues']


def test_document_selection_is_coverage_based_and_preserves_prior_numbers():
    sources = {'ATT-01': source(text=WRONG), 'ATT-02': source('ATT-02')}
    candidates, _ = ae.candidates_for(component(), {}, sources)
    a, b = candidates
    c = ae.validate_review(component(), candidates, {'selected_id': 'S1', 'selection_reason': '조건과 동작 일치',
        'assessments': [assessment(a, 'partial'), assessment(b)],
        'limitations': [{'id': 'L1', 'text': component()['feature']}], 'evidence_sets': [single_set(b)],
        'derivations': [], 'derivation_limitation': '직접 대응하므로 결합 불필요.'})
    result = ae.select_documents([c], sources, None)
    assert result['primary_alias'] == 'ATT-02'
    assert result['items'][0]['alias'] == 'ATT-02'
    assert result['items'][0]['citation_number'] == 1
    fixed = {'items': [{'attachment_id': 'ATT-02', 'citation_number': 7}]}
    result = ae.select_documents([c], sources, None, fixed)
    assert result['items'][0]['citation_number'] == 7


def test_independent_claim_priority_does_not_confuse_claim_one_with_ten():
    rows = [{**component(), 'claim': '청구항 1'}, {**component(2), 'claim': '청구항 2'},
            {**component(3), 'claim': '청구항 10'}]
    claim = '청구항 1. 밸브를 제어하는 장치.\n청구항 2. 제1항에 있어서 센서를 포함하는 장치.'
    assert ae.independent_components(claim, rows) == {'C001'}


class Reviewer:
    max_input_bytes = None
    calls = []
    def payload_bytes(self, system, user): return len((system + user).encode())
    async def cancel(self, job_id): return True
    async def execute(self, request, emit):
        self.calls.append(request)
        data = json.loads(request.user_message)
        results = []
        for c in data['components']:
            candidates = c['candidates']
            rows = [assessment(p, 'direct' if QUOTE in p['quote'] else 'lexical_only') for p in candidates]
            best = next((r['id'] for r in rows if r['verdict'] == 'direct'), None)
            sets = [{**assessment(next(p for p in candidates if p['id'] == best)),
                     'id': 'S1', 'candidate_ids': [best],
                     'coherence': {'status': 'same_embodiment', 'reason': '동일 제어기의 조건과 동작을 한 문장에서 개시',
                                   'candidate_ids': [best]},
                     'supports': [{'limitation_id': 'L1', 'candidate_ids': [best], 'status': 'direct',
                                   'reason': '압력 임계값 초과 조건과 밸브 개방의 관계가 모두 명시됨'}]}] if best else []
            results.append({'id': c['id'], 'selected_id': 'S1' if best else None,
                            'selection_reason': '다른 후보의 온도·닫힘과 달리 압력·열림 관계가 일치한다.', 'assessments': rows,
                            'limitations': [{'id': 'L1', 'text': c['feature']}], 'evidence_sets': sets,
                            'derivations': [], 'derivation_limitation': '직접 대응하는 단일 문헌이 있으므로 결합 불필요.'})
        return ExecutionOutcome(result_text=json.dumps({'components': results}), exit_code=0,
                                usage={'input_tokens': 100, 'output_tokens': 100})


@pytest.fixture
def inputs(tmp_path):
    normalized = tmp_path / 'source.txt'
    normalized.write_text('--- PAGE 1 ---\n' + QUOTE, encoding='utf-8')
    item = SimpleNamespace(attachment_id='file', included=True, read_ok=True, role='CITATION', normalized_text_path=str(normalized))
    aliases = {'ATT-01': AliasedAttachment('ATT-01', 'file', 'abc', 'source.pdf')}
    return [item], aliases


@pytest.mark.asyncio
async def test_complete_pipeline_batches_components_and_renders_only_source_quotes(tmp_path, inputs):
    items = [component(i) for i in range(1, 7)]
    proposal = {'components': [{**c, 'queries': ['pressure valve'], 'candidates': []} for c in items]}
    draft = 'invented narrative quote\n' + ae.OPEN + json.dumps(proposal) + ae.CLOSE
    provider = Reviewer(); provider.calls = []
    request = ExecutionRequest('job', tmp_path, 'system', 'original input')
    text, manifest, mapping, audit = await ae.run(provider, request, ExecutionOutcome(result_text=draft),
        attachments=inputs[0], aliases=inputs[1], components={'version': 1, 'items': items}, mapping=None,
        prior_mapping=None, claim_text=QUOTE, deadline=time.monotonic()+60, emit=AsyncMock(), cancelled=lambda: False)
    assert len(provider.calls) == 2  # six components share two bounded calls
    assert all(call.tool_policy == NO_TOOLS for call in provider.calls)
    assert all(call.timeout_seconds > 45 for call in provider.calls)
    assert audit['status'] == 'reviewed'
    assert 'invented narrative quote' not in text
    assert QUOTE in text and 'PDF 1쪽' in text
    assert all(c['similarity'] == 95 for c in manifest['items'])
    assert len(mapping['items']) == 1
    assert (tmp_path / 'analysis_evidence/review.json').exists()
    assert (tmp_path / 'analysis_evidence/draft.md').read_text(encoding='utf-8') == draft


@pytest.mark.asyncio
@pytest.mark.parametrize('provider_timeout', [False, True])
async def test_timeout_preserves_draft_and_reports_failure(tmp_path, inputs, provider_timeout):
    provider = Reviewer()
    provider.cancel = AsyncMock()
    provider.execute = AsyncMock(return_value=ExecutionOutcome(timed_out=True)) if provider_timeout else AsyncMock(side_effect=asyncio.TimeoutError)
    draft = 'Original readable draft\n' + ae.OPEN + json.dumps({'components': [component()]}) + ae.CLOSE
    original_mapping = {'items': [{'alias': 'ATT-01', 'attachment_id': 'file', 'citation_number': 1}]}
    text, manifest, mapping, audit = await ae.run(provider, ExecutionRequest('job', tmp_path, '', ''),
        ExecutionOutcome(result_text=draft), attachments=inputs[0], aliases=inputs[1],
        components={'items': [component()]}, mapping=original_mapping, prior_mapping=None, claim_text=QUOTE,
        deadline=time.monotonic()+100, emit=AsyncMock(), cancelled=lambda: False)
    assert audit['error_code'] == 'TIMED_OUT'
    assert audit['issues'] == ['1차 근거 재검토 실패: TimeoutError']
    assert 'Original readable draft' in text and '미검증 초안' in text and ae.OPEN not in text
    assert mapping == original_mapping
    assert manifest['items'][0]['similarity'] is None
    assert manifest['evidence_review']['status'] == 'incomplete'
    provider.cancel.assert_awaited_once()
    assert (tmp_path / 'analysis_evidence/review-01/error.json').exists()


def test_retrieval_does_not_repeat_lexical_discovery():
    sources = {'ATT-01': source(text=QUOTE * 40), 'ATT-02': source('ATT-02', WRONG * 40)}
    retrieved = [{'attachment': 'ATT-01', 'pdf_page': 1, 'source_text': QUOTE}]
    candidates, _ = ae.candidates_for(component(), {}, sources, retrieved)
    assert [(c['attachment'], c['origin']) for c in candidates] == [
        ('ATT-01', 'retrieval')]


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['cancelled', 'deadline', 'oversize', 'malformed'])
async def test_limits_and_failure_never_promote_unreviewed_evidence(tmp_path, inputs, mode):
    provider = Reviewer(); provider.calls = []
    if mode == 'oversize': provider.max_input_bytes = 1
    if mode == 'malformed':
        provider.execute = AsyncMock(return_value=ExecutionOutcome(result_text='{}', exit_code=0))
    c = component()
    draft = ae.OPEN + json.dumps({'components': [{**c, 'candidates': []}]}) + ae.CLOSE
    _, manifest, _, audit = await ae.run(provider, ExecutionRequest('job', tmp_path, '', ''),
        ExecutionOutcome(result_text=draft), attachments=inputs[0], aliases=inputs[1],
        components={'items': [c]}, mapping=None, prior_mapping=None, claim_text=QUOTE,
        deadline=time.monotonic() + (0 if mode == 'deadline' else 60), emit=AsyncMock(), cancelled=lambda: mode == 'cancelled')
    assert manifest['items'][0]['status'] == 'unreadable'
    assert manifest['items'][0]['similarity'] is None
    assert audit['status'] == 'incomplete'


@pytest.mark.asyncio
async def test_missing_protocol_is_explicitly_unverified_without_extra_model_call(tmp_path, inputs):
    provider = Reviewer(); provider.calls = []
    text, manifest, _, audit = await ae.run(provider, ExecutionRequest('job', tmp_path, '', ''),
        ExecutionOutcome(result_text='old draft'), attachments=inputs[0], aliases=inputs[1],
        components={'items': [component()]}, mapping=None, prior_mapping=None, claim_text=QUOTE,
        deadline=time.monotonic()+60, emit=AsyncMock(), cancelled=lambda: False)
    assert '미검증 초안' in text and 'old draft' in text
    assert not provider.calls
    assert manifest['evidence_review']['status'] == 'incomplete'


def test_usage_includes_review_and_marks_missing_call_accounting():
    total = ae.merge_usage({'input_tokens': 100, 'output_tokens': 10},
                          {'calls': 2, 'usage': [{'input_tokens': 20, 'output_tokens': 2}]})
    assert total['input_tokens'] == 120 and total['output_tokens'] == 12
    assert not total['usage_complete']


def test_discarded_candidates_need_only_ids_and_are_absent_from_report():
    sources = {'ATT-01': source(text=WRONG)}
    candidates, _ = ae.candidates_for(component(), {}, sources)
    row = {'selected_id': None, 'assessments': [], 'rejected_ids': [c['id'] for c in candidates],
           'limitations': [{'id': 'L1', 'text': component()['feature']}], 'evidence_sets': [],
           'derivations': [], 'derivation_limitation': '채택 근거 없음'}
    result = ae.validate_review(component(), candidates, row)
    assert result['comparison_complete'] and not result['candidates']
    assert ae.negative_scope(result)
    mapping = ae.select_documents([result], sources, None)
    report = ae.render({'components': [result], 'documents': {'ATT-01': 'discarded.pdf'}, 'issues': []}, mapping)
    assert WRONG not in report and 'discarded.pdf' not in report and '비교 후보' not in report
    row['rejected_ids'].append('invented')
    assert not ae.validate_review(component(), candidates, row)['comparison_complete']


@pytest.mark.asyncio
async def test_additional_review_only_targets_incomplete_component_once(tmp_path, inputs):
    class RepairReviewer(Reviewer):
        async def execute(self, request, emit):
            outcome = await super().execute(request, emit)
            if len(self.calls) == 1:
                result = json.loads(outcome.result_text)
                result['components'][1]['limitations'] = []
                outcome.result_text = json.dumps(result)
            else:
                repaired = json.loads(outcome.result_text)['components'][0]
                outcome.result_text = json.dumps({'components': [{'id': repaired['id'], 'limitations': repaired['limitations']}]})
            return outcome
    provider = RepairReviewer(); provider.calls = []
    items = [component(1), component(2)]
    draft = ae.OPEN + json.dumps({'components': items}) + ae.CLOSE
    _, _, _, audit = await ae.run(provider, ExecutionRequest('job', tmp_path, '', ''),
        ExecutionOutcome(result_text=draft), attachments=inputs[0], aliases=inputs[1],
        components={'items': items}, mapping=None, prior_mapping=None, claim_text=QUOTE,
        deadline=time.monotonic()+100, emit=AsyncMock(), cancelled=lambda: False)
    assert [[c['id'] for c in json.loads(r.user_message)['components']] for r in provider.calls] == [['C001', 'C002'], ['C002']]
    assert audit['status'] == 'reviewed'
    assert [c['id'] for c in audit['additional_review']] == ['C002']
    repaired_input = json.loads(provider.calls[1].user_message)['components'][0]
    assert 'translation' not in repaired_input['previous_result']['assessments'][0]


def test_valid_partial_difference_alone_does_not_trigger_additional_review():
    result = {'issues': [], 'selected_id': 'S1', 'candidates': [], 'evidence_sets': [
        {'id': 'S1', 'verdict': 'partial', 'axes': {axis: 'different' for axis in ae.AXES},
         'supports': [{'status': 'inferred'}]}]}
    assert ae.review_reasons(result) == []
    result['evidence_sets'][0]['supports'] = [{'status': 'missing'}]
    assert ae.review_reasons(result) == []  # A documented gap is not omitted evaluation.
    result['evidence_sets'][0]['supports'] = [{'status': 'contradicted'}]
    assert ae.review_reasons(result) == ['selected_limitation_conflicting']


def test_invalid_optional_derivation_does_not_repeat_sound_comparison():
    result = {'issues': ['unsupported_derivation'], 'selected_id': None, 'candidates': [], 'evidence_sets': []}
    assert ae.review_reasons(result) == []


@pytest.mark.asyncio
async def test_retrieval_sources_are_reviewed_without_draft_quotes_or_global_truncation(tmp_path, inputs):
    # More than the draft protocol's 1,200-character quote cap. Keep the actual
    # retrieved span, including its tail, for the separate component review.
    original = (' ' + QUOTE) * 25
    from pathlib import Path
    Path(inputs[0][0].normalized_text_path).write_text('--- PAGE 1 ---\n' + original, encoding='utf-8')
    provider = Reviewer(); provider.calls = []
    c = component()
    retrieved = [{'id': 'R001', 'label': c['claim'] + ' ' + c['symbol'], 'feature': c['feature'],
                  'depends_on': [], 'queries': ['pressure'], 'findings': [
                      {'attachment': 'ATT-01', 'pdf_page': 1, 'source_text': original}]}]
    report, manifest, _, audit = await ae.run(provider, ExecutionRequest('job', tmp_path, '', ''),
        ExecutionOutcome(result_text='draft without source quotes'), attachments=inputs[0], aliases=inputs[1],
        components={'items': [c]}, mapping=None, prior_mapping=None, claim_text=QUOTE,
        deadline=time.monotonic()+60, emit=AsyncMock(), cancelled=lambda: False,
        retrieval_components=retrieved)
    sent = json.loads(provider.calls[0].user_message)
    found = next(p for p in sent['components'][0]['candidates'] if p['origin'] == 'retrieval')
    assert ae.compact(found['quote']) == ae.compact(original)
    assert sent['claim'] == QUOTE
    assert sent['component_context'][0]['feature'] == c['feature']
    assert audit['status'] == 'reviewed', audit['issues']
    assert manifest['items'][0]['similarity'] == 95
    assert 'draft without source quotes' not in report


@pytest.mark.asyncio
async def test_component_batches_fit_actual_provider_envelope_without_dropping_sources(tmp_path, inputs):
    class BoundedReviewer(Reviewer):
        # Force separate component batches with an envelope that the raw prompt
        # length alone would miss.
        max_input_bytes = len(ae.REVIEW_SYSTEM.encode()) + 2200
        def payload_bytes(self, system, user):
            return len((system + user).encode()) + 1200
        async def execute(self, request, emit):
            assert self.payload_bytes(request.system_prompt, request.user_message) <= self.max_input_bytes
            return await super().execute(request, emit)
    provider = BoundedReviewer(); provider.calls = []
    items = [component(i) for i in range(1, 5)]
    draft = ae.OPEN + json.dumps({'components': [{**c, 'candidates': []} for c in items]}) + ae.CLOSE
    _, manifest, _, audit = await ae.run(provider, ExecutionRequest('job', tmp_path, '', ''),
        ExecutionOutcome(result_text=draft), attachments=inputs[0], aliases=inputs[1],
        components={'items': items}, mapping=None, prior_mapping=None, claim_text=QUOTE,
        deadline=time.monotonic()+60, emit=AsyncMock(), cancelled=lambda: False)
    assert len(provider.calls) > 1
    assert audit['status'] == 'reviewed'
    assert [c['id'] for c in manifest['items']] == [c['id'] for c in items]
    for call in provider.calls:
        assert all(c['candidates'][0]['quote'].strip() == QUOTE for c in json.loads(call.user_message)['components'])


@pytest.fixture(params=[False, True])
def local_retrieval(client, request):
    before = client.get('/api/settings').json()['values']['retrieval_mode']
    client.put('/api/settings', json={'values': {'retrieval_mode': 'retrieval' if request.param else 'full'}})
    yield request.param
    client.put('/api/settings', json={'values': {'retrieval_mode': before}})


def test_report_rollback_preserves_model_output_without_review(client, monkeypatch, local_retrieval):
    from .fake_provider import DeterministicTestProvider
    from .pdf_fixture import build_pdf
    from .conftest import wait_for_job
    from app.config import PATHS
    calls = []
    review = AsyncMock(side_effect=AssertionError('Automatic review must remain disabled'))
    monkeypatch.setattr(ae, 'run', review)
    async def execute(self, request, emit):
        calls.append(request)
        from app.retrieval.prompts import AGENT_SYSTEM_PROMPT
        if request.system_prompt == AGENT_SYSTEM_PROMPT:
            payload = json.loads(request.user_message.splitlines()[1])
            if payload['round'] == 1:
                response = {'components': [{'label': '청구항 1 (A)', 'feature': QUOTE}], 'actions': [
                    {'action': 'search_document', 'component_id': 'R001', 'queries': ['pressure', 'valve', 'controller']}]}
            else:
                hits = [h for r in payload['results'] for d in r.get('documents', []) for h in d.get('hits', [])]
                hit = next(h for h in hits if QUOTE in h.get('text', ''))
                response = {'actions': [{'action': 'finalize_evidence', 'components': [
                    {'component_id': 'R001', 'evidence': [{'attachment': hit['alias'], 'chunk_id': hit['chunk_id']}]}]}]}
            return ExecutionOutcome(result_text=json.dumps(response), exit_code=0)
        assert request.system_prompt != ae.REVIEW_SYSTEM
        assert not request.system_prompt.startswith('Prepare source candidates, not a report')
        assert ae.OPEN not in request.user_message
        c = component()
        draft = 'Model report body: 99 percent.\n'
        draft += '[PRISM_COMPONENT_ANALYSIS_V1]' + json.dumps({'items': [c]}) + '[/PRISM_COMPONENT_ANALYSIS_V1]\n'
        draft += '[PRISM_CITATION_MAPPING_V1]' + json.dumps({'items': [
            {'citation_number': 1, 'attachment': 'ATT-01', 'document_number': 'WRONG'},
            {'citation_number': 2, 'attachment': 'ATT-02', 'document_number': 'GOOD'}]}) + '[/PRISM_CITATION_MAPPING_V1]\n'
        draft += ae.OPEN + json.dumps({'components': [{**c, 'queries': ['pressure valve'], 'candidates': []}]}) + ae.CLOSE
        return ExecutionOutcome(result_text=draft, exit_code=0, usage={'input_tokens': 10, 'output_tokens': 10})
    monkeypatch.setattr(DeterministicTestProvider, 'execute', execute)
    prompt = client.post('/api/prompts', json={'name': '근거 검토', 'body': '청구항을 구성별로 비교한다.'}).json()
    uploaded = client.post('/api/uploads', files=[
        ('files', ('wrong.pdf', build_pdf([WRONG]), 'application/pdf')),
        ('files', ('good.pdf', build_pdf([QUOTE]), 'application/pdf'))],
        data={'roles': json.dumps(['CITATION', 'CITATION'])})
    assert uploaded.status_code == 200, uploaded.text
    response = client.post('/api/jobs', json={'provider': 'test', 'prompt_id': prompt['id'],
        'claim_text': QUOTE, 'batch_id': uploaded.json()['batch_id']})
    assert response.status_code == 201, response.text
    job = wait_for_job(client, response.json()['id'])
    assert job['status'] == 'SUCCEEDED', job['errors']
    assert job['analysis_manifest_error'] is None
    assert job['analysis_manifest']['items'][0]['similarity'] == 99
    assert 'evidence_review' not in job['analysis_manifest']
    assert [r['filename'] for r in job['citation_mapping']['items']] == ['wrong.pdf', 'good.pdf']
    assert 'Model report body: 99 percent.' in job['result_text']
    assert ae.OPEN not in job['result_text']
    assert job['usage']['input_tokens'] == 10
    assert len(calls) == (3 if local_retrieval else 1)
    review.assert_not_awaited()
    assert not (PATHS.runs_dir / uploaded.json()['batch_id'] / 'analysis_evidence').exists()
