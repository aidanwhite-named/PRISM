import asyncio
import copy
import json

import pytest

from app import comparison_review as review, structured_report
from app.providers.base import ExecutionOutcome, ExecutionRequest, NO_TOOLS
from .test_multi_document_report import multi_input
from .test_structured_report import report_input, compile_input


@pytest.fixture
def review_input(multi_input):
    data, aliases, attachments = multi_input
    data['version'] = 5
    row = data['components'][0]
    row['evidence_uses'] = {'E1': 'support', 'E2': 'contrast'}
    row['reference_roles'] = {}
    return data, aliases, attachments


def requirements(values):
    row = values[0]['components'][0]
    return [{k: row[k] for k in ('claim', 'symbol', 'feature')} | {
        'id': 'C001', 'requirements': [row['feature']],
        'functional_interpretation': {'input': '영상 또는 신호', 'operation': '이득을 변경', 'output': '변경한 이득',
            'conditions': [], 'term_roles': [], 'unspecified': [], 'uncertainties': []}}]


def document(components, alias, status='matched', refs=None):
    return {'attachment': alias, 'processes': [{'input': '신호', 'operation': '원문의 제어 동작',
        'output': '제어 결과', 'sentence_ids': [f'{alias}-P0-T1']}], 'components': [
        {'id': c['id'], 'limitations': [{'requirement_index': n, 'status': status,
            'sentence_ids': refs if refs is not None else [f'{alias}-P0-T1'],
            'reason': '원문의 처리 대상과 동작을 비교했습니다.',
            'missing': '' if status == 'matched' else req, 'naming_only': False}
            for n, req in enumerate(c['requirements'], 1)]} for c in components]}


async def emit(*_):
    pass


class ScriptedProvider:
    def __init__(self, replies):
        self.replies = list(replies)
        self.requests = []
        self.cancelled = False

    async def execute(self, request, _emit):
        self.requests.append(request)
        reply = self.replies.pop(0)
        if callable(reply):
            reply = reply(request)
        if isinstance(reply, Exception):
            raise reply
        if request.system_prompt == review.SEMANTIC and isinstance(reply, dict):
            reply = semantic_reply(reply, request)
        return ExecutionOutcome(result_text=json.dumps(reply, ensure_ascii=False), exit_code=0,
            terminal_reason='completed', tool_policy=NO_TOOLS, usage={'input_tokens': 10, 'output_tokens': 5})

    async def cancel(self, _):
        self.cancelled = True


def semantic_reply(reply, request):
    payload = json.loads(request.user_message)
    affected = {cid for f in reply.get('findings', []) for cid in f.get('affected_components', [])}
    reply.setdefault('component_reviews', [{'id': c['id'],
        'status': 'needs_correction' if c['id'] in affected else 'consistent',
        'sentence_ids': ['ATT-01-P0-T1'], 'reason': '입력·처리·결과를 원문과 대조했습니다.'}
        for c in payload['canonical_components']])
    report = reply.get('report') or payload['report']
    reply.setdefault('selection_review', {'primary_attachment': report['documents'][0]['attachment'],
                                         'reason': '모든 구성의 실제 한정을 비교했습니다.'})
    return reply


def run_prepare(tmp_path, values, replies, **kwargs):
    provider = ScriptedProvider(replies)
    request = ExecutionRequest(job_id='test', work_dir=tmp_path, system_prompt='system',
        user_message='original source corpus', model='existing-model', reasoning_effort='medium')
    result = asyncio.run(review.prepare(provider, request,
        claim_text='청구항 1\n(A) ' + values[0]['components'][0]['feature'],
        aliases=values[1], attachments=values[2], emit=emit, cancelled=lambda: False, **kwargs))
    return provider, request, result


def test_document_comparison_and_absence_review_precede_selection(tmp_path, review_input):
    components = requirements(review_input)
    first = document(components, 'ATT-01')
    second = document(components, 'ATT-02', 'not_found', [])
    rechecked = document(components, 'ATT-02', 'not_found', [])
    provider, original, (request, context, audit) = run_prepare(tmp_path, review_input, [
        {'components': components}, first, second, {'documents': [rechecked]},
        {'primary_attachment': 'ATT-01', 'document_order': ['ATT-01', 'ATT-02'],
         'reason': '첫 문헌이 실제 이득 변경에 대응합니다.'}])
    assert audit['status'] == 'validated'
    assert [r.work_dir.name for r in provider.requests] == ['claims', 'document-ATT-01', 'document-ATT-02', 'absence-ATT-02', 'selection']
    assert all(r.model == original.model and r.reasoning_effort == 'medium' and r.tool_policy.tools_disabled
               for r in provider.requests)
    for request_i, alias in zip(provider.requests[1:3], ('ATT-01', 'ATT-02')):
        sources = json.loads(request_i.user_message)['sources']
        assert sources and {s['attachment'] for s in sources} == {alias}
        assert request_i.response_schema['properties']['attachment']['enum'] == [alias]
    recheck_input = json.loads(provider.requests[3].user_message)
    assert [d['attachment'] for d in recheck_input['documents']] == ['ATT-02']
    assert recheck_input['documents'][0]['components'] == [{'id': 'C001'}]
    assert 'not_found' not in provider.requests[3].user_message  # no previous negative judgment to anchor on
    assert 'original source corpus' in request.user_message
    assert context['selection']['primary_attachment'] == 'ATT-01'
    assert (tmp_path / 'comparison-review' / 'audit.json').exists()


def test_primary_is_not_selected_by_attachment_order(tmp_path, review_input):
    components = requirements(review_input)
    _, _, (_, context, audit) = run_prepare(tmp_path, review_input, [
        {'components': components}, document(components, 'ATT-01'), document(components, 'ATT-02'),
        {'primary_attachment': 'ATT-02', 'document_order': ['ATT-02', 'ATT-01'], 'reason': '두 번째 문헌의 처리 연결이 더 넓습니다.'}])
    assert audit['status'] == 'validated' and context['selection']['primary_attachment'] == 'ATT-02'


def test_primary_order_normalization_preserves_the_selection():
    selected = review.validate_selection({'primary_attachment': 'ATT-02', 'document_order': ['ATT-01', 'ATT-02'],
        'reason': '두 번째 문헌의 대응 범위가 넓습니다.'}, [{'attachment': 'ATT-01'}, {'attachment': 'ATT-02'}])
    assert selected['document_order'] == ['ATT-02', 'ATT-01']
    assert selected['order_normalization']['before'] == ['ATT-01', 'ATT-02']


def test_schema_identifiers_do_not_restrict_source_text_or_reasoning(review_input):
    schema = review.stage_schema('document-ATT-01', {'attachment': 'ATT-01', 'components': requirements(review_input)})
    props = schema['properties']
    assert props['attachment']['enum'] == ['ATT-01']
    row = props['components']['items']['properties']
    assert row['id']['enum'] == ['C001']
    assert row['limitations']['items']['properties']['reason'] == {'type': 'string'}
    assert row['limitations']['items']['properties']['sentence_ids']['items'] == {'type': 'string'}
    assert props['processes']['items']['properties']['operation'] == {'type': 'string'}
    assert review.STAGE_SCHEMAS['document']['properties']['attachment'] == {'type': 'string'}


def test_whole_device_purpose_cannot_disappear_from_claim_split(review_input):
    components = requirements(review_input)
    claim = '청구항 1\n(A) ' + components[0]['feature'] + '\n를 포함하는 것을 특징으로 하는 시간 동기화 장치'
    assert review.explicit_features(claim)[('청구항 1', '전제부')] == '시간 동기화 장치'
    with pytest.raises(ValueError, match='구성이 누락'):
        review.validate_claims({'components': components}, claim)


def test_internal_component_numbers_are_assigned_without_changing_user_symbols(review_input):
    components = requirements(review_input)
    components[0]['id'] = 'C000'
    data = {'components': components}
    review.validate_claims(data, '청구항 1\n(A) ' + components[0]['feature'])
    assert components[0]['id'] == 'C001' and components[0]['symbol'] == '(A)'
    assert data['normalizations'][0]['before'] == 'C000'


@pytest.mark.parametrize('defect', ['unknown_sentence', 'cross_document', 'missing_component', 'missing_requirement', 'no_positive_evidence'])
def test_unverified_document_review_cannot_enter_the_report_plan(tmp_path, review_input, defect):
    components = requirements(review_input)
    bad = document(components, 'ATT-01')
    if defect == 'unknown_sentence':
        bad['components'][0]['limitations'][0]['sentence_ids'] = ['ATT-01-P0-T999']
    elif defect == 'cross_document':
        bad['components'][0]['limitations'][0]['sentence_ids'] = ['ATT-02-P0-T1']
    elif defect == 'missing_component':
        bad['components'] = []
    elif defect == 'missing_requirement':
        bad['components'][0]['limitations'] = []
    else:
        bad['components'][0]['limitations'][0]['sentence_ids'] = []
    _, original, (request, context, audit) = run_prepare(tmp_path, review_input, [{'components': components}, bad])
    assert context is None and request == original and audit['status'] == 'incomplete' and audit['issues']


def test_claim_split_cannot_omit_relationships_or_user_symbols(review_input):
    components = requirements(review_input)
    claim = '청구항 1\n(A) ' + components[0]['feature']
    bad = copy.deepcopy(components)
    bad[0]['requirements'] = [bad[0]['feature'][:2]]
    repaired = review.validate_claims({'components': bad}, claim)
    assert repaired[0]['requirements'] == [components[0]['feature']]
    with pytest.raises(ValueError, match='구성이 누락'):
        review.validate_claims({'components': components}, claim + '\n(B) 추가 구성')


def test_fixed_primary_and_excluded_sources(review_input):
    _, aliases, attachments = review_input
    mapping = {'items': [{'citation_number': 1, 'attachment_sha256': aliases['ATT-02'].sha256}]}
    assert review.fixed_primary(mapping, aliases) == 'ATT-02'
    with pytest.raises(ValueError, match='고정 주 인용발명'):
        review.validate_selection({'primary_attachment': 'ATT-01', 'document_order': ['ATT-01', 'ATT-02'], 'reason': 'wrong'},
            [{'attachment': 'ATT-01'}, {'attachment': 'ATT-02'}], 'ATT-02')
    attachments[1].included = False
    assert {s['attachment'] for s in review.corpus(aliases, attachments)} == {'ATT-01'}


def test_claim_interpretation_receives_user_clarification_and_application(tmp_path, review_input):
    from app.enums import AttachmentRole
    _, aliases, attachments = review_input
    attachments[0].role = AttachmentRole.APPLICATION
    clarification = '오프셋은 촬영 시각의 시간 차이를 뜻합니다.'
    provider, _, (_, _, audit) = run_prepare(tmp_path, review_input, [{'components': []}],
        interpretation_instruction=clarification)
    payload = json.loads(provider.requests[0].user_message)
    assert payload['interpretation_instruction'] == clarification
    assert {r['attachment'] for r in payload['application_sources']} == {'ATT-01'}
    assert {r['attachment'] for r in review.corpus(aliases, attachments)} == {'ATT-02'}
    assert audit['status'] == 'incomplete'


@pytest.mark.parametrize('field', ['functional_interpretation', 'term_roles', 'processes'])
def test_interpretation_and_source_processes_are_required(review_input, field):
    components = requirements(review_input)
    if field == 'processes':
        value = document(components, 'ATT-01')
        value['processes'] = []
        with pytest.raises(ValueError):
            review.validate_document(value, components, 'ATT-01', {'ATT-01-P0-T1': 'ATT-01'})
    else:
        if field == 'functional_interpretation':
            components[0].pop(field)
        else:
            components[0]['functional_interpretation'][field] = [{'term': '추가 제한', 'role': '구성에 없음'}]
        with pytest.raises(ValueError):
            review.validate_claims({'components': components}, '청구항 1\n(A) ' + components[0]['feature'])


@pytest.mark.parametrize('valid_retry', [True, False])
def test_malformed_json_has_one_audited_retry(tmp_path, valid_retry):
    class MalformedProvider(ScriptedProvider):
        async def execute(self, request, _):
            self.requests.append(request)
            raw = '{"components":[]}' if valid_retry and len(self.requests) == 2 else '{"components":['
            return ExecutionOutcome(result_text=raw, exit_code=0, terminal_reason='completed', tool_policy=NO_TOOLS,
                                    usage={'total_tokens': 7})
    provider = MalformedProvider([])
    request = ExecutionRequest(job_id='test', work_dir=tmp_path, system_prompt='system', user_message='sources')
    audit = {'attempts': []}
    call = review._call(provider, request, 'claims', review.CLAIMS, {'claim_text': '동작'}, audit,
        token_budget=None, max_chars=None, emit=emit, cancelled=lambda: False)
    if valid_retry:
        assert asyncio.run(call) == {'components': []}
    else:
        with pytest.raises(ValueError, match='형식'):
            asyncio.run(call)
    assert len(provider.requests) == 2 and audit['attempts'][0]['status'] == 'invalid_response'
    assert sum(a['usage']['total_tokens'] for a in audit['attempts']) == 14
    assert provider.requests[0].user_message == provider.requests[1].user_message
    assert all(r.response_schema == review.STAGE_SCHEMAS['claims'] for r in provider.requests)


def run_review(tmp_path, values, reply, context=None):
    compiled = compile_input(values)
    provider = ScriptedProvider(reply if isinstance(reply, list) else [reply])
    request = ExecutionRequest(job_id='test', work_dir=tmp_path, system_prompt='system', user_message='report')
    result = asyncio.run(review.review(provider, request, compiled, context=context,
        aliases=values[1], attachments=values[2], emit=emit, cancelled=lambda: False))
    return provider, compiled, result


def test_semantic_review_runs_even_when_all_structural_checks_pass(tmp_path, review_input):
    provider, original, (compiled, audit) = run_review(tmp_path, review_input, {'findings': [], 'report': None})
    assert not original[1]['report']['issues']
    assert compiled == original and audit['status'] == 'validated' and len(provider.requests) == 1
    assert json.loads(provider.requests[0].user_message)['sources']


def test_semantic_review_cannot_skip_structurally_clean_components(tmp_path, review_input):
    _, original, (compiled, audit) = run_review(tmp_path, review_input,
        {'findings': [], 'component_reviews': [], 'report': None})
    assert audit['status'] == 'incomplete' and compiled == original


def test_findings_trigger_a_separate_report_correction(tmp_path, review_input):
    corrected = copy.deepcopy(review_input[0])
    corrected['components'][0]['difference'] = ''
    provider, _, (compiled, audit) = run_review(tmp_path, review_input, [
        {'findings': [{'affected_components': ['C001'], 'attachment': 'ATT-01',
                      'sentence_ids': ['ATT-01-P0-T1'], 'reason': '청구항에 없는 한정을 차이점에 추가했습니다.'}]}, corrected])
    assert audit['status'] == 'corrected' and compiled[1]['items'][0]['difference'] == ''
    assert [r.work_dir.name for r in provider.requests] == ['semantic', 'correction']


def test_semantic_reanalysis_can_reuse_verified_passages_without_duplicates(tmp_path, review_input):
    corrected = copy.deepcopy(review_input[0])
    row = corrected['components'][0]
    row.update(difference='', reasoning='{{ATT-01}}의 {{E1}}이 이득 변경에 대응합니다.')
    row['evidence'] = ['E1']
    row['evidence_uses'] = {'E1': 'support'}
    row['reference_roles'] = {'ATT-02': 'not_found'}
    _, original, (compiled, audit) = run_review(tmp_path, review_input, {
        'findings': [{'affected_components': ['C001'], 'attachment': 'ATT-01',
                      'sentence_ids': ['ATT-01-P0-T1'], 'reason': '청구항에 없는 유지 시간 조건을 차이점으로 추가했습니다.'}],
        'report': corrected})
    assert audit['status'] == 'corrected' and compiled[1]['items'][0]['difference'] == ''
    assert original[1]['items'][0]['difference']  # original remains recoverable


def test_semantic_review_can_correct_an_unfixed_primary(tmp_path, review_input):
    corrected = copy.deepcopy(review_input[0])
    corrected['documents'].reverse()
    corrected['components'][0]['similarity'] = 0
    components = requirements(review_input)
    context = {'components': components, 'documents': [document(components, 'ATT-01'), document(components, 'ATT-02')],
               'selection': {'primary_attachment': 'ATT-01'}}
    _, original, (compiled, audit) = run_review(tmp_path, review_input, {
        'findings': [{'affected_components': ['C001'], 'attachment': 'ATT-02',
                      'sentence_ids': ['ATT-02-P0-T1'], 'reason': '주 문헌 선정과 점수 기준을 원문에서 다시 확인했습니다.'}],
        'report': corrected}, context)
    assert audit['status'] == 'corrected', audit
    assert compiled[1]['report']['data']['documents'][0]['attachment'] == 'ATT-02'
    assert audit['selection_correction']['before'] == 'ATT-01'
    assert original[1]['report']['data']['documents'][0]['attachment'] == 'ATT-01'


@pytest.mark.parametrize('defect', ['fabricated_evidence', 'changed_claim', 'changed_symbol', 'unlinked_evidence', 'missing_correction', 'unknown_finding_document'])
def test_invalid_semantic_reanalysis_preserves_original(tmp_path, review_input, defect):
    corrected = copy.deepcopy(review_input[0])
    if defect == 'fabricated_evidence':
        corrected['evidence'][0]['sentence_ids'] = ['ATT-01-P0-T999']
    elif defect == 'changed_claim':
        corrected['components'][0]['feature'] = '추가된 별도 장치'
    elif defect == 'changed_symbol':
        corrected['components'][0]['symbol'] = '(B)'
    elif defect == 'unlinked_evidence':
        corrected['components'][0]['evidence'] = []
        corrected['components'][0]['reasoning'] = '원문 근거 없이 대응한다고 판단했습니다.'
    elif defect == 'missing_correction':
        corrected = None
    _, original, (compiled, audit) = run_review(tmp_path, review_input, {
        'findings': [{'affected_components': ['C001'], 'attachment': 'ATT-99' if defect == 'unknown_finding_document' else 'ATT-01',
                      'sentence_ids': [] if defect == 'unknown_finding_document' else ['ATT-01-P0-T1'],
                      'reason': '대응 판단을 재검토했습니다.'}], 'report': corrected})
    assert audit['status'] == 'incomplete' and compiled == original and audit['issues']


def test_partial_matching_cannot_be_cleared_by_no_findings(tmp_path, review_input):
    components = requirements(review_input)
    context = {'components': components, 'documents': [document(components, 'ATT-01'), document(components, 'ATT-02')],
               'selection': {'primary_attachment': 'ATT-01'}}
    review_input[0]['components'][0]['reference_roles'] = {'ATT-02': 'not_found'}
    _, original, (compiled, audit) = run_review(tmp_path, review_input,
        {'findings': [], 'report': None}, context)
    assert audit['status'] == 'incomplete' and compiled == original


def test_budget_exhaustion_does_not_make_paid_call(tmp_path, review_input):
    provider, original, (request, context, audit) = run_prepare(tmp_path, review_input, [], max_chars=1)
    assert not provider.requests and request == original and context is None
    assert audit['status'] == 'incomplete'


def test_cancellation_stops_before_call_and_saves_audit(tmp_path, review_input):
    provider = ScriptedProvider([])
    request = ExecutionRequest(job_id='test', work_dir=tmp_path, system_prompt='system', user_message='report')
    _, context, audit = asyncio.run(review.prepare(provider, request, claim_text='청구항 1',
        aliases=review_input[1], attachments=review_input[2], emit=emit, cancelled=lambda: True))
    assert not provider.requests and context is None and audit['status'] == 'cancelled'
    assert (tmp_path / 'comparison-review' / 'audit.json').exists()


@pytest.mark.parametrize('semantic_failure', [False, True])
def test_runner_executes_and_persists_all_stages(client, monkeypatch, review_input, semantic_failure):
    from .fake_provider import DeterministicTestProvider
    from .conftest import wait_for_job
    from app.db import session_scope
    from app.models import ExecutionJob
    from pathlib import Path

    calls = []
    components = requirements(review_input)
    first = document(components, 'ATT-01')
    second = document(components, 'ATT-02', 'not_found', [])
    main = copy.deepcopy(review_input[0])
    main['components'][0]['difference'] = ''

    async def execute(self, request, _):
        calls.append(request)
        if request.system_prompt == review.CLAIMS:
            value = {'components': components}
        elif request.system_prompt == review.DOCUMENT:
            value = first if json.loads(request.user_message)['attachment'] == 'ATT-01' else second
        elif request.system_prompt == review.ABSENCE:
            value = {'documents': [second]}
        elif request.system_prompt == review.SELECT:
            value = {'primary_attachment': 'ATT-01', 'document_order': ['ATT-01', 'ATT-02'], 'reason': '직접 이득 변경을 개시합니다.'}
        elif request.system_prompt == review.SEMANTIC:
            value = {} if semantic_failure else {'findings': [], 'report': None}
            if not semantic_failure:
                value = semantic_reply(value, request)
        else:
            value = main
        return ExecutionOutcome(result_text=json.dumps(value, ensure_ascii=False), exit_code=0,
                                tool_policy=NO_TOOLS, usage={'total_tokens': 10})

    monkeypatch.setattr(DeterministicTestProvider, 'execute', execute)
    batch = client.post('/api/uploads', files=[
        ('files', ('first.txt', b'The controller changes the gain based on accumulated noise.', 'text/plain')),
        ('files', ('second.txt', b'Another controller retains the initial speech segment.', 'text/plain'))]).json()
    created = client.post('/api/jobs', json={'provider': 'test', 'claim_text':
        '청구항 1\n(A) ' + components[0]['feature'], 'batch_id': batch['batch_id']})
    assert created.status_code == 201, created.text
    job = wait_for_job(client, created.json()['id'])
    assert job['status'] == 'SUCCEEDED'
    assert len(calls) == 7 and calls[-1].system_prompt == review.SEMANTIC
    assert job['usage']['total_tokens'] == 70 and job['usage']['report_generation']['total_tokens'] == 10
    report = job['analysis_manifest']['report']
    assert report['comparison_preparation']['status'] == 'validated'
    assert report['semantic_review']['status'] == ('incomplete' if semantic_failure else 'validated')
    assert ('구성 판단 검토 상태' in job['result_text']) == semantic_failure
    with session_scope() as session:
        row = session.get(ExecutionJob, job['id'])
        directory = Path(row.work_dir)
        prompt = Path(row.final_prompt_path).read_text(encoding='utf-8')
        assert calls[-2].user_message in prompt
        assert row.final_prompt_chars == len(calls[-2].system_prompt) + len(calls[-2].user_message)
    assert json.loads((directory / 'analysis-response.txt').read_text(encoding='utf-8')) == main
    assert (directory / 'comparison-review' / 'audit.json').exists()
    assert (directory / 'comparison-review' / 'semantic-audit.json').exists()
