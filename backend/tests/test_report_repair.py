import asyncio
import copy
import json
from pathlib import Path

import pytest

from app import citation_mapping, report_repair, structured_report, analysis_protocol
from app.enums import AttachmentRole
from app.ingestion.service import ingest_one, IngestionLimits
from app.providers.base import ExecutionOutcome, ExecutionRequest
from app.providers.model_limits import TokenBudget


@pytest.fixture
def case(tmp_path):
    attachments = [ingest_one(name, text, tmp_path, True, IngestionLimits(), role=AttachmentRole.CITATION)
                   for name, text in [('first.txt', b'The controller changes gain. The sensor measures noise.'),
                                      ('second.txt', b'The receiver stores the signal.')]]
    aliases = citation_mapping.assign_aliases(attachments)
    data = {'version': 3, 'documents': [{'attachment': a, 'document_number': a, 'title': a} for a in aliases],
            'evidence': [{'id': 'E1', 'attachment': 'ATT-01', 'sentence_ids': ['missing'],
                          'language': 'foreign', 'translation': '이득을 변경합니다.'},
                         {'id': 'E2', 'attachment': 'ATT-02', 'sentence_ids': ['ATT-02-P0-T1'],
                          'language': 'foreign', 'translation': '수신기는 신호를 저장합니다.'}],
            'components': [{'claim': '청구항 1', 'symbol': '(A)', 'feature': '이득을 변경하는 제어기',
                            'similarity': 95, 'basis': 'direct', 'evidence': ['E1'], 'reasoning': '이득을 변경합니다.',
                            'difference': '', 'resolution': None}],
            'summary': {'main_reason': '이득 변경', 'relationships': '같은 처리'}}
    return data, aliases, attachments, tmp_path


class Provider:
    max_input_bytes = None

    def __init__(self, response=None):
        self.response = response
        self.calls = []
        self.cancelled = []

    def payload_bytes(self, system, user):
        return len((system + user).encode('utf-8'))

    async def cancel(self, job_id):
        self.cancelled.append(job_id)
        return True

    async def execute(self, request, emit):
        self.calls.append(request)
        if isinstance(self.response, BaseException):
            raise self.response
        if isinstance(self.response, ExecutionOutcome):
            return self.response
        payload = json.loads(request.user_message)
        source = next(s for s in payload['sources'] if s['attachment'] == 'ATT-01')
        patch = {'id': 'E1', 'sentence_ids': [source['sentences'][0]['id']], 'language': 'foreign',
                 'translation': '제어기는 이득을 변경합니다.'}
        return ExecutionOutcome(result_text=json.dumps(self.response if self.response is not None else {'evidence': [patch]}),
                                exit_code=0, usage={'input_tokens': 15, 'output_tokens': 5, 'total_tokens': 20})


def run(case, provider, *, bundle=None, cancelled=lambda: False, **kwargs):
    data, aliases, attachments, path = case
    compiled = structured_report.compile_report(json.dumps(data), aliases, attachments, bundle=bundle)
    request = ExecutionRequest(job_id='repair-job', work_dir=path, system_prompt='', user_message='', timeout_seconds=10)

    async def emit(*args):
        pass

    return asyncio.run(report_repair.repair(provider, request, compiled, aliases=aliases, attachments=attachments,
                       bundle=bundle, emit=emit, cancelled=cancelled, **kwargs))


@pytest.mark.parametrize('retrieved', [False, True])
def test_repair_uses_delivered_ids_preserves_scores_mapping_and_valid_evidence(case, retrieved):
    data, _, _, path = case
    bundle = None
    if retrieved:
        bundle = {'candidate_sources': [
            {'attachment': 'ATT-01', 'pdf_page': 4, 'source_text': 'The controller changes gain.'},
            {'attachment': 'ATT-01', 'pdf_page': 4, 'source_text': 'The sensor measures noise.'},
            {'attachment': 'ATT-02', 'pdf_page': 2, 'source_text': 'The receiver stores the signal.'}]}
        data['evidence'][1]['sentence_ids'] = ['S003-T1']
    original = copy.deepcopy(data)
    provider = Provider()
    compiled, audit = run(case, provider, bundle=bundle)
    report = compiled[1]['report']
    assert audit['status'] == 'repaired' and audit['repaired'] == ['E1']
    assert len(provider.calls) == 1
    assert report['evidence']['E1']['verified']
    assert report['data']['components'] == original['components']
    assert report['data']['documents'] == original['documents']
    assert report['data']['evidence'][1] == original['evidence'][1]
    assert data == original
    assert (path / 'sentence-repair/repaired-analysis.json').exists()
    assert json.loads((path / 'sentence-repair/audit.json').read_text(encoding='utf-8')) == audit
    payload = json.loads(provider.calls[0].user_message)
    assert {s['attachment'] for s in payload['sources']} == {'ATT-01'}
    if retrieved:
        assert len(payload['sources']) == 2  # Same page, independent S numbers.


@pytest.mark.parametrize('patch', [
    {'id': 'E1', 'sentence_ids': ['ATT-02-P0-T1']},
    {'id': 'E1', 'sentence_ids': ['ATT-01-P0-T2', 'ATT-01-P0-T1']},
    {'id': 'E1', 'sentence_ids': ['ATT-01-P0-T999']},
    {'id': 'E2', 'sentence_ids': ['ATT-01-P0-T1']},
    {'id': 'E1', 'sentence_ids': ['ATT-01-P0-T1'], 'similarity': 100},
])
def test_invalid_patch_cannot_bypass_validation_or_edit_good_evidence(case, patch):
    patch.update(language='foreign', translation='변경합니다.')
    provider = Provider({'evidence': [patch]})
    compiled, audit = run(case, provider)
    assert audit['status'] == 'unresolved'
    assert len(provider.calls) == report_repair.MAX_CALLS
    assert compiled[1]['report']['data'] == case[0]
    assert not compiled[1]['report']['evidence']['E1']['verified']


@pytest.mark.parametrize('gate', ['bytes', 'chars', 'tokens', 'cancelled'])
def test_limits_and_cancellation_prevent_call(case, gate):
    provider, kwargs = Provider(), {}
    if gate == 'bytes':
        provider.max_input_bytes = 10
    elif gate == 'chars':
        kwargs['max_chars'] = 10
    elif gate == 'tokens':
        kwargs['token_budget'] = TokenBudget(10, 9, 'configured')
    else:
        kwargs['cancelled'] = lambda: True
    _, audit = run(case, provider, **kwargs)
    assert provider.calls == []
    assert audit['unresolved'] == ['E1']


@pytest.mark.parametrize('response', [RuntimeError('offline'), asyncio.TimeoutError(),
    ExecutionOutcome(result_text='{}', exit_code=0, rate_limited=True),
    ExecutionOutcome(result_text='{}', exit_code=0, tools_uncontrollable=True, tool_calls=[{'name': 'shell'}]),
    ExecutionOutcome(result_text='{}', exit_code=0, cancelled=True)])
def test_provider_failures_preserve_report_and_stop(case, response):
    provider = Provider(response)
    compiled, audit = run(case, provider)
    assert len(provider.calls) == 1
    assert compiled[1]['report']['data'] == case[0]
    assert audit['unresolved'] == ['E1']
    if isinstance(response, asyncio.TimeoutError):
        assert provider.cancelled == ['repair-job']


def test_valid_report_needs_no_call_and_usage_is_accounted(case):
    case[0]['evidence'][0]['sentence_ids'] = ['ATT-01-P0-T1']
    provider = Provider()
    _, audit = run(case, provider)
    assert audit['status'] == 'not_needed' and not provider.calls
    case[0]['evidence'][0]['sentence_ids'] = ['missing']
    _, audit = run(case, provider)
    usage = report_repair.merge_usage({'input_tokens': 100, 'output_tokens': 10, 'total_tokens': 110}, audit)
    assert usage['total_tokens'] == 130 and usage['input_tokens'] == 115
    assert usage['report_generation']['total_tokens'] == 110
    assert usage['sentence_repair'][0]['total_tokens'] == 20


def test_instructions_show_only_current_delivery_id_format():
    full = analysis_protocol.apply('분석')
    retrieved = analysis_protocol.apply('분석', retrieved=True)
    assert 'ATT-01-P3-T2' in full and 'S001-T2' not in full
    assert 'S001-T2' in retrieved and 'ATT-01-P3-T2' not in retrieved


def split_case(case):
    data, _, attachments, _ = case
    Path(attachments[0].normalized_text_path).write_text(
        'The controller changes gain. A separate heading. The sensor measures noise. Unselected source.', encoding='utf-8')
    data['evidence'][0]['sentence_ids'] = ['ATT-01-P0-T1', 'ATT-01-P0-T3']
    data['components'][0]['resolution'] = {
        'gap': '차이', 'evidence': ['E1', 'E2'], 'explanation': '{{E1}}와 {{E2}}가 있다.',
        'conclusion': 'remaining_gap', 'remaining_difference': '관계가 미확인'}
    return [
        {'sentence_ids': ['ATT-01-P0-T1'], 'language': 'foreign', 'translation': '제어기는 이득을 변경합니다.'},
        {'sentence_ids': ['ATT-01-P0-T3'], 'language': 'foreign', 'translation': '센서는 잡음을 측정합니다.'}]


@pytest.mark.parametrize('shape', ['segments', 'repeated', 'suffix'])
def test_split_repair_preserves_all_spans_and_expands_every_link(case, shape):
    parts = split_case(case)
    before = copy.deepcopy(case[0])
    if shape == 'segments':
        patches = [{'id': 'E1', 'segments': parts}]
    else:
        patches = [{'id': f'E1-{i+1}' if shape == 'suffix' else 'E1', **part} for i, part in enumerate(parts)]
    provider = Provider({'evidence': patches})
    compiled, audit = run(case, provider)
    report = compiled[1]['report']
    assert audit['status'] == 'repaired' and len(provider.calls) == 1
    assert audit['attempts'][0]['expanded_evidence'] == {'E1': ['E1', 'E3']}
    assert report['data']['components'][0]['evidence'] == ['E1', 'E3']
    assert report['data']['components'][0]['resolution']['evidence'] == ['E1', 'E3', 'E2']
    assert report['data']['components'][0]['resolution']['explanation'] == '{{E1}}, {{E3}}와 {{E2}}가 있다.'
    assert report['data']['components'][0]['similarity'] == before['components'][0]['similarity']
    assert report['data']['components'][0]['reasoning'] == before['components'][0]['reasoning']
    assert next(row for row in report['data']['evidence'] if row['id'] == 'E2') == before['evidence'][1]
    assert all(row['verified'] for row in report['evidence'].values())
    payload = json.loads(provider.calls[0].user_message)
    assert payload['evidence'][0]['expected_segments'] == [part['sentence_ids'] for part in parts]
    assert [row['id'] for s in payload['sources'] for row in s['sentences']] == ['ATT-01-P0-T1', 'ATT-01-P0-T3']
    assert 'Unselected source.' not in provider.calls[0].user_message
    assert not report['issues']


@pytest.mark.parametrize('fault', ['missing', 'duplicate', 'reversed', 'added', 'no_translation', 'other_document'])
def test_split_repair_never_loses_adds_or_reorders_original_selection(case, fault):
    parts = split_case(case)
    if fault == 'missing':
        parts = parts[:1]
    elif fault == 'duplicate':
        parts = [parts[0], parts[0]]
    elif fault == 'reversed':
        parts.reverse()
    elif fault == 'added':
        parts[0]['sentence_ids'].append('ATT-01-P0-T2')
    elif fault == 'no_translation':
        parts[1]['translation'] = ''
    else:
        parts[1]['sentence_ids'] = ['ATT-02-P0-T1']
    provider = Provider({'evidence': [{'id': 'E1', 'segments': parts}]})
    compiled, audit = run(case, provider)
    assert compiled[1]['report']['data'] == case[0]
    assert audit['status'] == 'unresolved'
    assert json.loads(provider.calls[1].user_message)['previous_repair_errors']


def test_split_repair_updates_nested_supplement_links(case):
    parts = split_case(case)
    data = case[0]
    # Reorder primary document so E1 is the supplement for this comparison.
    data['documents'].reverse()
    data['components'][0]['evidence'] = ['E2']
    data['components'][0]['resolution'] = {
        'gap': '주 문헌의 차이', 'evidence': ['E1'],
        'supplements': [{'attachment': 'ATT-01', 'evidence': ['E1'], 'explanation': '두 처리를 보완합니다.'}],
        'derivation': '', 'conclusion': 'remaining_gap', 'remaining_difference': '관계 미확인'}
    compiled, audit = run(case, Provider({'evidence': [{'id': 'E1', 'segments': parts}]}))
    resolution = compiled[1]['report']['data']['components'][0]['resolution']
    assert resolution['evidence'] == resolution['supplements'][0]['evidence'] == ['E1', 'E3']
    assert not compiled[1]['report']['issues']


@pytest.mark.parametrize('mode', ['repaired', 'unresolved', 'cancelled'])
def test_runner_saves_original_repair_and_usage_separately(client, monkeypatch, mode):
    from .conftest import wait_for_job
    from .fake_provider import DeterministicTestProvider
    from app.db import session_scope
    from app.models import ExecutionJob
    calls = []
    original = {'version': 3, 'documents': [{'attachment': 'ATT-01', 'document_number': 'US1', 'title': 'Source'}],
        'evidence': [{'id': 'E1', 'attachment': 'ATT-01', 'sentence_ids': ['nonexistent'],
                      'language': 'foreign', 'translation': '제어기는 이득을 변경합니다.'}],
        'components': [{'claim': '청구항 1', 'symbol': '(A)', 'feature': '이득 변경',
                        'similarity': 95, 'basis': 'direct', 'evidence': ['E1'],
                        'reasoning': '이득 변경에 대응합니다.', 'difference': '', 'resolution': None}],
        'summary': {'main_reason': '이득 변경', 'relationships': ''}}

    async def execute(self, request, emit):
        from app import comparison_review
        if request.system_prompt == comparison_review.CLAIMS:
            return ExecutionOutcome(result_text='{"components":[]}', exit_code=0)
        calls.append(request)
        if request.system_prompt == report_repair.SYSTEM:
            if mode == 'cancelled':
                return ExecutionOutcome(cancelled=True, exit_code=0)
            payload = {'evidence': [{'id': 'E1', 'sentence_ids': ['ATT-01-P0-T1' if mode == 'repaired' else 'missing'],
                                    'language': 'foreign', 'translation': '제어기는 이득을 변경합니다.'}]}
        else:
            payload = original
        return ExecutionOutcome(result_text=json.dumps(payload), exit_code=0,
                                usage={'input_tokens': 100, 'output_tokens': 10, 'total_tokens': 110})

    monkeypatch.setattr(DeterministicTestProvider, 'execute', execute)
    batch = client.post('/api/uploads', files=[('files', ('source.txt', b'The controller changes gain.', 'text/plain'))]).json()
    created = client.post('/api/jobs', json={'provider': 'test', 'claim_text': '이득 변경', 'batch_id': batch['batch_id']})
    assert created.status_code == 201, created.text
    job = wait_for_job(client, created.json()['id'])
    assert job['status'] == ('CANCELLED' if mode == 'cancelled' else 'SUCCEEDED'), job['errors']
    assert len(calls) == (3 if mode == 'unresolved' else 2)
    with session_scope() as session:
        stored = session.get(ExecutionJob, job['id'])
        path = Path(stored.work_dir)
        assert json.loads((path / 'analysis-response.txt').read_text(encoding='utf-8')) == original
        audit = json.loads((path / 'sentence-repair/audit.json').read_text(encoding='utf-8'))
        assert audit['status'] == mode
        assert stored.analysis_manifest['report']['sentence_repair'] == audit
        assert stored.analysis_manifest['report']['data']['components'] == original['components']
        if mode != 'cancelled':
            assert stored.usage['total_tokens'] == 110 * len(calls)
        assert stored.analysis_manifest['report']['evidence']['E1']['verified'] == (mode == 'repaired')
        assert ('근거 확인 미완료' in stored.result_text) == (mode != 'repaired')
