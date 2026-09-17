import json
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
    assert audit['status'] == 'reviewed'
    assert 'invented narrative quote' not in text
    assert QUOTE in text and 'PDF 1쪽' in text
    assert all(c['similarity'] == 95 for c in manifest['items'])
    assert len(mapping['items']) == 1
    assert (tmp_path / 'analysis_evidence/review.json').exists()
    assert (tmp_path / 'analysis_evidence/draft.md').read_text(encoding='utf-8') == draft


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


def test_final_report_api_uses_independent_review_and_new_document_order(client, monkeypatch):
    from .fake_provider import DeterministicTestProvider
    from .pdf_fixture import build_pdf
    from .conftest import wait_for_job
    from app.config import PATHS
    calls = []
    reviewer = Reviewer(); reviewer.calls = []
    async def execute(self, request, emit):
        calls.append(request)
        if request.system_prompt == ae.REVIEW_SYSTEM:
            return await reviewer.execute(request, emit)
        c = component()
        draft = 'Unverified draft claims 99 percent.\n'
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
    assert job['analysis_manifest']['items'][0]['similarity'] == 95
    assert job['analysis_manifest']['evidence_review']['status'] == 'reviewed'
    assert job['citation_mapping']['items'][0]['filename'] == 'good.pdf'
    assert job['citation_mapping']['items'][0]['citation_number'] == 1
    assert 'Unverified draft' not in job['result_text']
    assert QUOTE in job['result_text']
    assert job['usage']['input_tokens'] == 110
    assert len(calls) == 2
