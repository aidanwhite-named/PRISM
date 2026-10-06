import asyncio
import copy
import json
from pathlib import Path

import pytest

from app import report_consistency, report_repair
from app.providers.base import ExecutionOutcome, ExecutionRequest
from .test_multi_document_report import multi_input
from .test_structured_report import report_input, compile_input


def inconsistent(values):
    row = values[0]['components'][0]
    row.update(evidence=['E2'], similarity=55, reference_roles={'ATT-01': 'not_found', 'ATT-02': 'support'},
               reasoning='{{ATT-01}}에는 해당 한정이 확인되지 않고 {{ATT-02}}의 {{E2}}가 초기 구간 유지에 대응합니다.')
    return row


def patch(row, **changes):
    return {'component_index': 1, **{k: row[k] for k in
        ('similarity', 'basis', 'evidence', 'reference_roles', 'reasoning', 'difference')}, **changes}


def test_blank_symbols_get_unique_labels_without_changing_user_symbols(multi_input):
    data = multi_input[0]
    row = data['components'][0]
    data['components'] = [{**row, 'symbol': ''}, {**row, 'symbol': '(구성 1)'}, row]
    original = copy.deepcopy(data)
    report, manifest, _ = compile_input(multi_input)
    assert [r['symbol'] for r in manifest['items']] == ['(구성 2)', '(구성 1)', '(A)']
    assert manifest['report']['data'] == original
    assert not manifest['report']['issues']
    assert manifest['report']['normalizations'][0]['field'] == 'symbol'
    assert '구성 기호가 비어' not in report


def test_blank_symbols_are_numbered_within_each_claim(multi_input):
    data = multi_input[0]
    row = data['components'][0]
    data['components'] = [{**row, 'claim': claim, 'symbol': ''}
                          for claim in ['청구항 1', '청구항 1', '청구항 1', '청구항 2']]
    report, manifest, _ = compile_input(multi_input)
    assert [item['symbol'] for item in manifest['items']] == [
        '(구성 1)', '(구성 2)', '(구성 3)', '(구성 1)']
    assert '**(구성 3)' in report
    assert not manifest['report']['issues']


def test_only_explicit_verified_evidence_reference_is_relinked(multi_input):
    row = multi_input[0]['components'][0]
    row['evidence'] = ['E1']  # reasoning explicitly references E2
    report, manifest, _ = compile_input(multi_input)
    assert manifest['report']['comparison_checks'][0]['evidence'] == ['E1', 'E2']
    assert not manifest['report']['issues']
    assert manifest['report']['data']['components'][0]['evidence'] == ['E1']
    assert manifest['report']['normalizations'][0]['added'] == 'E2'
    row['reasoning'] = row['reasoning'].replace('{{E2}}', '기재')
    _, manifest, _ = compile_input(multi_input)
    assert manifest['report']['issues']
    assert manifest['report']['comparison_checks'][0]['evidence'] == ['E1']


def test_score_conflict_is_reviewed_without_changing_other_content(multi_input):
    row = inconsistent(multi_input)
    original = compile_input(multi_input)
    assert report_consistency.targets(original) == [1]
    compiled, accepted, rejected = report_consistency.apply_patches(original, [patch(row, similarity=0)], [1],
        aliases=multi_input[1], attachments=multi_input[2])
    assert accepted == [1] and rejected == {}
    assert not compiled[1]['report']['issues']
    assert compiled[1]['items'][0]['similarity'] == 0
    assert original[1]['items'][0]['similarity'] == 55
    assert compiled[1]['report']['evidence'] == original[1]['report']['evidence']
    assert compiled[2] == original[2]
    assert compiled[1]['items'][0]['feature'] == original[1]['items'][0]['feature']


@pytest.mark.parametrize('value', ['missing', None, [], {}])
def test_missing_difference_is_included_in_review_targets(multi_input, value):
    row = multi_input[0]['components'][0]
    if value == 'missing':
        row.pop('difference')
    else:
        row['difference'] = value
    compiled = compile_input(multi_input)
    assert report_consistency.targets(compiled) == [1]
    assert compiled[1]['report']['comparison_checks'][0]['status'] == 'needs_review'
    assert compiled[1]['report']['comparison_checks'][0]['repair_scope'] == 'difference_only'
    correction = patch({**row, 'difference': ''})
    repaired, accepted, rejected = report_consistency.apply_patches(compiled, [correction], [1],
        aliases=multi_input[1], attachments=multi_input[2])
    assert accepted == [1] and not rejected and not repaired[1]['report']['issues']
    assert repaired[1]['items'][0]['difference'] == ''
    assert compiled[1]['report']['issues']  # The unresolved original remains intact.


def test_difference_only_review_cannot_rescore_or_drop_existing_support(multi_input):
    row = multi_input[0]['components'][0]
    row.pop('difference')
    compiled = compile_input(multi_input)
    correction = patch({**row, 'difference': ''}, similarity=0)
    repaired, accepted, rejected = report_consistency.apply_patches(compiled, [correction], [1],
        aliases=multi_input[1], attachments=multi_input[2])
    assert not accepted and rejected and repaired == compiled


def test_explicit_empty_difference_does_not_trigger_review(multi_input):
    multi_input[0]['components'][0]['difference'] = ''
    assert report_consistency.targets(compile_input(multi_input)) == []


def test_difference_only_accepts_minimal_patch_and_preserves_all_other_fields(multi_input):
    row = multi_input[0]['components'][0]
    original = copy.deepcopy(row)
    row.pop('difference')
    compiled = compile_input(multi_input)
    repaired, accepted, rejected = report_consistency.apply_patches(compiled,
        [{'component_index': 1, 'difference': '시간 조건은 여전히 확인되지 않습니다.',
          'review_basis': '두 문헌의 처리 기재만으로는 유지 시간을 입증하지 못합니다.'}], [1],
        aliases=multi_input[1], attachments=multi_input[2])
    assert accepted == [1] and not rejected and not repaired[1]['report']['issues']
    corrected = repaired[1]['report']['data']['components'][0]
    assert {k: v for k, v in corrected.items() if k != 'difference'} == {
        k: v for k, v in original.items() if k != 'difference'}


def test_repair_decoding_accepts_known_final_delimiter_but_rejects_extra_data():
    assert report_consistency.decode_response('{"components":[]}</final>') == {'components': []}
    with pytest.raises(ValueError):
        report_consistency.decode_response('{"components":[]} arbitrary trailing prose')


@pytest.mark.parametrize('defect', ['unknown_evidence', 'invalid_evidence', 'still_inconsistent', 'new_absence', 'empty_reasoning'])
def test_invalid_patches_preserve_the_original_report(multi_input, defect):
    row = inconsistent(multi_input)
    correction = patch(row, similarity=0)
    if defect == 'unknown_evidence':
        correction['evidence'] = ['E999']
    elif defect == 'invalid_evidence':
        multi_input[0]['evidence'][1]['sentence_ids'] = ['unknown']
    elif defect == 'still_inconsistent':
        correction['similarity'] = 55
    elif defect == 'new_absence':
        correction['reference_roles'] = {'ATT-01': 'not_found', 'ATT-02': 'not_found'}
    else:
        correction['reasoning'] = ''
    original = compile_input(multi_input)
    compiled, accepted, rejected = report_consistency.apply_patches(original, [correction], [1],
        aliases=multi_input[1], attachments=multi_input[2])
    assert compiled == original and not accepted and rejected


@pytest.mark.parametrize('defect', ['unrequested', 'duplicate', 'feature_change'])
def test_patches_cannot_change_unrequested_components_or_claims(multi_input, defect):
    row = inconsistent(multi_input)
    correction = patch(row, similarity=0)
    patches = [correction]
    if defect == 'unrequested':
        correction['component_index'] = 2
    elif defect == 'duplicate':
        patches.append(correction)
    else:
        correction['feature'] = 'changed'
    with pytest.raises(ValueError):
        report_consistency.apply_patches(compile_input(multi_input), patches, [1],
            aliases=multi_input[1], attachments=multi_input[2])


class Provider:
    max_input_bytes = None

    def __init__(self, response):
        self.response, self.calls, self.cancelled = response, [], []

    def payload_bytes(self, system, user):
        return len((system + user).encode('utf-8'))

    async def execute(self, request, emit):
        self.calls.append(request)
        if isinstance(self.response, BaseException):
            raise self.response
        return self.response

    async def cancel(self, job_id):
        self.cancelled.append(job_id)


def run(values, provider, tmp_path, **kwargs):
    async def emit(*args):
        pass
    return asyncio.run(report_consistency.repair(provider,
        ExecutionRequest(job_id='consistency-test', work_dir=tmp_path, system_prompt='', user_message=''),
        compile_input(values), aliases=values[1], attachments=values[2], emit=emit,
        cancelled=kwargs.pop('cancelled', lambda: False), **kwargs))


def test_one_review_call_uses_verified_full_quotes_and_saves_audit(multi_input, tmp_path):
    row = inconsistent(multi_input)
    provider = Provider(ExecutionOutcome(result_text=json.dumps({'components': [patch(row, similarity=0)]}),
                                          exit_code=0, usage={'total_tokens': 20}))
    compiled, audit = run(multi_input, provider, tmp_path)
    assert audit['status'] == 'repaired' and audit['repaired'] == [1]
    assert len(provider.calls) == 1 and not compiled[1]['report']['issues']
    request = provider.calls[0]
    assert request.tool_policy.tools_disabled and request.mcp_servers == {}
    payload = json.loads(request.user_message)
    assert payload['primary_attachment'] == 'ATT-01'
    assert payload['evidence_catalog'][0]['quote'] == compiled[1]['report']['evidence']['E1']['quote']
    assert (tmp_path / 'component-repair/audit.json').exists()
    assert (tmp_path / 'component-repair/repaired-analysis.json').exists()


@pytest.mark.parametrize('mode', ['not_needed', 'cancelled', 'input_limited', 'timeout', 'provider_error', 'malformed', 'tools'])
def test_review_limits_and_failures_preserve_findings(multi_input, tmp_path, mode):
    if mode != 'not_needed':
        inconsistent(multi_input)
    response = ExecutionOutcome(result_text='not json', exit_code=0)
    if mode == 'timeout':
        response = asyncio.TimeoutError()
    elif mode == 'provider_error':
        response = RuntimeError('unavailable')
    elif mode == 'tools':
        response.tool_uses = ['Read']
    provider = Provider(response)
    original = compile_input(multi_input)
    kwargs = {'max_chars': 1} if mode == 'input_limited' else {'cancelled': lambda: True} if mode == 'cancelled' else {}
    compiled, audit = run(multi_input, provider, tmp_path, **kwargs)
    assert compiled == original and not audit['repaired']
    assert len(provider.calls) <= 1
    if mode in ('not_needed', 'cancelled', 'input_limited'):
        assert not provider.calls and audit['status'] == mode
    if mode == 'timeout':
        assert provider.cancelled == ['consistency-test']


def test_review_usage_is_counted_separately_without_overwriting_generation():
    first = report_repair.merge_usage({'total_tokens': 100}, {'attempts': [{'usage': {'total_tokens': 10}}]})
    total = report_repair.merge_usage(first, {'attempts': [{'usage': {'total_tokens': 20}}]}, category='component_repair')
    assert total['total_tokens'] == 130 and total['report_generation'] == {'total_tokens': 100}
    assert total['sentence_repair'] == [{'total_tokens': 10}]
    assert total['component_repair'] == [{'total_tokens': 20}]


@pytest.mark.parametrize('mode', ['repaired', 'malformed', 'cancelled'])
@pytest.mark.parametrize('defect', ['score', 'difference'])
def test_runner_reviews_only_inconsistent_components_and_preserves_original(client, monkeypatch, multi_input, mode, defect):
    from .fake_provider import DeterministicTestProvider
    from .conftest import wait_for_job
    from app.db import session_scope
    from app.models import ExecutionJob
    original = copy.deepcopy(multi_input[0])
    if defect == 'score':
        inconsistent((original,))
    else:
        original['components'][0].pop('difference')
    original['components'][0]['symbol'] = ''
    calls = []

    async def execute(self, request, emit):
        # This fixture returns a legacy V4 report; staged V5 review is tested
        # independently in test_comparison_review.
        from app import comparison_review
        if request.system_prompt == comparison_review.CLAIMS:
            return ExecutionOutcome(result_text='{"components":[]}', exit_code=0)
        calls.append(request)
        if request.system_prompt == report_consistency.SYSTEM:
            if mode == 'cancelled':
                return ExecutionOutcome(cancelled=True, exit_code=0)
            correction = (patch(original['components'][0], similarity=0) if defect == 'score' else
                          patch({**original['components'][0], 'difference': ''}))
            payload = 'bad json' if mode == 'malformed' else json.dumps({'components': [correction]})
        else:
            payload = json.dumps(original)
        return ExecutionOutcome(result_text=payload, exit_code=0, usage={'total_tokens': 10})

    monkeypatch.setattr(DeterministicTestProvider, 'execute', execute)
    batch = client.post('/api/uploads', files=[
        ('files', ('first.txt', b'The controller changes the gain based on accumulated noise.', 'text/plain')),
        ('files', ('second.txt', b'Another controller retains the initial speech segment.', 'text/plain'))]).json()
    created = client.post('/api/jobs', json={'provider': 'test', 'claim_text': '초기 구간을 유지하는 제어기',
                                            'batch_id': batch['batch_id']})
    assert created.status_code == 201
    job = wait_for_job(client, created.json()['id'])
    assert len(calls) == 2
    assert job['status'] == ('CANCELLED' if mode == 'cancelled' else 'SUCCEEDED')
    manifest = job['analysis_manifest']
    assert manifest['items'][0]['symbol'] == '(구성 1)'
    if mode == 'repaired':
        assert not manifest['report']['issues']
        assert manifest['items'][0]['similarity'] == (0 if defect == 'score' else 70)
        if defect == 'difference':
            assert manifest['items'][0]['difference'] == ''
        assert job['usage']['total_tokens'] == 20
    else:
        assert manifest['report']['issues']
        assert manifest['items'][0]['similarity'] == (55 if defect == 'score' else 70)
    with session_scope() as session:
        stored = session.get(ExecutionJob, job['id'])
        directory = Path(stored.work_dir)
    assert json.loads((directory / 'analysis-response.txt').read_text(encoding='utf-8')) == original
    assert (directory / 'component-repair/audit.json').exists()
