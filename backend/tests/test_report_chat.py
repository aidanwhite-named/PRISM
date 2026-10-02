import asyncio
import json
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError

from app import report_chat, settings_service
from app.api import report_chat as chat_api
from app.config import PATHS
from app.db import session_scope
from app.models import Attachment, ExecutionJob, ReportChatTurn
from app.providers.base import ExecutionOutcome, NO_TOOLS


class ChatProvider:
    id = 'test'
    max_input_bytes = None

    def __init__(self, outcome=None):
        self.requests = []
        self.cancelled = []
        self.outcome = outcome or ExecutionOutcome(result_text='인용발명 1의 E1이 해당 한정에 대응합니다.',
            exit_code=0, terminal_reason='completed', tool_policy=NO_TOOLS)

    def supports_tool_policy(self, policy):
        return policy == NO_TOOLS

    def payload_bytes(self, system, message):
        return len((system + message).encode())

    async def execute(self, request, emit):
        self.requests.append(request)
        await emit('stage', {'message': 'checking'})
        return self.outcome

    async def cancel(self, turn_id):
        self.cancelled.append(turn_id)


@pytest.fixture
def chat_job(client, monkeypatch):
    provider = ChatProvider()
    monkeypatch.setattr(report_chat, 'build_provider', lambda *_: provider)
    async def resolve(*_):
        return 'test', 'test-model'
    monkeypatch.setattr(chat_api, '_resolve_provider', resolve)
    monkeypatch.setattr(report_chat, 'start', lambda _: None)
    job_id = str(uuid4())
    root = PATHS.run_dir(job_id)
    root.mkdir(parents=True)
    source = root / 'source.txt'
    source.write_text('--- PAGE 1 ---\n[0001] A controller changes gain based on noise.', encoding='utf-8')
    excluded = root / 'excluded.txt'
    excluded.write_text('EXCLUDED SECRET CONTENT', encoding='utf-8')
    with session_scope() as session:
        job = ExecutionJob(id=job_id, provider='test', model='test-model', status='SUCCEEDED',
            claim_text='청구항 1\n(A) 소음에 따라 이득을 변경하는 제어기', result_text='현재 보고서 인용발명 1 근거 E1',
            work_dir=str(root), citation_mapping={'items': [{'citation_number': 1, 'attachment': 'ATT-01'}]},
            analysis_manifest={'report': {'data': {'documents': [{'attachment': 'ATT-01'}]}, 'evidence': {
                'E1': {'id': 'E1', 'attachment': 'ATT-01', 'quote': 'A controller changes gain based on noise.',
                       'translation': '소음에 따라 이득을 변경합니다.', 'verified': True, 'issues': [], 'page': 1},
                'E2': {'id': 'E2', 'quote': 'UNVERIFIED SECRET CONTENT', 'verified': False, 'issues': ['bad']}}}})
        session.add(job)
        session.add_all([Attachment(job_id=job_id, original_filename='citation.pdf', internal_filename='source',
            mime_type='application/pdf', size_bytes=1, stored_path=str(source), normalized_text_path=str(source),
            read_ok=True, included=True, role='CITATION', delivery_mode='INLINE_CONTEXT'),
            Attachment(job_id=job_id, original_filename='excluded.pdf', internal_filename='excluded',
                mime_type='application/pdf', size_bytes=1, stored_path=str(excluded), normalized_text_path=str(excluded),
                read_ok=True, included=False, role='CITATION')])
    return job_id, provider


def ask(client, job_id, question='주 인용발명에서 어떤 한정이 빠졌나요?', request_id=None):
    return client.post(f'/api/jobs/{job_id}/chat', json={'question': question, 'request_id': request_id or str(uuid4())})


def test_context_is_report_scoped_and_original_report_is_unchanged(client, chat_job):
    job_id, provider = chat_job
    response = ask(client, job_id)
    assert response.status_code == 202, response.text
    turn_id = response.json()['id']
    asyncio.run(report_chat._run(turn_id))
    history = client.get(f'/api/jobs/{job_id}/chat').json()
    assert history[0]['status'] == 'succeeded' and history[0]['answer']
    request = provider.requests[0]
    payload = json.loads(request.user_message)
    assert payload['report'] == '현재 보고서 인용발명 1 근거 E1'
    assert payload['claim_text'].startswith('청구항 1')
    assert payload['verified_excerpts'][0]['id'] == 'E1'
    assert len(payload['sources']) == 1 and 'controller' in payload['sources'][0]['text']
    assert 'EXCLUDED SECRET CONTENT' not in request.user_message
    assert 'UNVERIFIED SECRET CONTENT' not in request.user_message
    assert request.tool_policy == NO_TOOLS and request.mcp_servers == {}
    assert request.model == 'test-model'
    with session_scope() as session:
        assert session.get(ExecutionJob, job_id).result_text == payload['report']


def test_followup_question_receives_prior_successful_conversation(client, chat_job):
    job_id, provider = chat_job
    first = ask(client, job_id).json()
    asyncio.run(report_chat._run(first['id']))
    second = ask(client, job_id, '그 부분을 어떤 문헌으로 보완할 수 있어?').json()
    asyncio.run(report_chat._run(second['id']))
    dialogue = json.loads(provider.requests[-1].user_message)['conversation']
    assert dialogue == [{'question': first['question'], 'answer': provider.outcome.result_text}]


def test_request_retry_is_idempotent_and_pending_questions_are_serialized(client, chat_job):
    job_id, _ = chat_job
    request_id = str(uuid4())
    first = ask(client, job_id, request_id=request_id)
    retry = ask(client, job_id, request_id=request_id)
    assert retry.json()['id'] == first.json()['id']
    assert ask(client, job_id).status_code in (409, 422)
    assert len(client.get(f'/api/jobs/{job_id}/chat').json()) == 1


def test_database_pending_guard_works_for_another_writer(client, chat_job):
    job_id, _ = chat_job
    ask(client, job_id)
    with pytest.raises(IntegrityError), session_scope() as session:
        session.add(ReportChatTurn(job_id=job_id, request_id=str(uuid4()), question='동시 질문', provider='test',
            context_scope='report_evidence'))
        session.flush()


def test_source_scope_narrows_explicitly_without_truncating_report_or_history(client, chat_job):
    job_id, provider = chat_job
    with session_scope() as session:
        job = session.get(ExecutionJob, job_id)
        payload = report_chat.context(job, [], '질문')
        provider.max_input_bytes = provider.payload_bytes(report_chat.SYSTEM, json.dumps(payload, ensure_ascii=False)) + 30
        turn = report_chat.create_turn(session, job, '질문', str(uuid4()), 'test', 'test-model', settings_service.get_all(session))
        stored = json.loads((report_chat.directory(job, turn.id) / 'input.json').read_text(encoding='utf-8'))
        assert turn.context_scope == 'report_evidence' and stored['sources'] == []
        assert stored['report'] == job.result_text and stored['verified_excerpts'] == payload['verified_excerpts']


def test_report_over_budget_rejects_without_calling_ai(client, chat_job):
    job_id, provider = chat_job
    provider.max_input_bytes = 20
    response = ask(client, job_id)
    assert response.status_code == 422
    assert not provider.requests and client.get(f'/api/jobs/{job_id}/chat').json() == []


@pytest.mark.parametrize('change', ['running', 'no_report', 'search'])
def test_chat_requires_a_completed_analysis_report(client, chat_job, change):
    job_id, provider = chat_job
    with session_scope() as session:
        job = session.get(ExecutionJob, job_id)
        if change == 'running': job.status = 'RUNNING'
        if change == 'no_report': job.result_text = ''
        if change == 'search': job.job_kind = 'similarity_search'
    assert ask(client, job_id).status_code in (400, 409)
    assert not provider.requests


def test_blank_question_and_cross_report_cancel_are_rejected(client, chat_job):
    job_id, _ = chat_job
    assert ask(client, job_id, ' ').status_code == 422
    assert ask(client, str(uuid4())).status_code == 404
    assert client.post(f'/api/jobs/{job_id}/chat/{uuid4()}/cancel').status_code == 404


def test_cancelled_queued_turn_does_not_execute_and_can_be_retried(client, chat_job):
    job_id, provider = chat_job
    turn = ask(client, job_id).json()
    response = client.post(f"/api/jobs/{job_id}/chat/{turn['id']}/cancel")
    assert response.json()['status'] == 'cancelled'
    asyncio.run(report_chat._run(turn['id']))
    assert not provider.requests
    assert ask(client, job_id).status_code == 202


def test_tool_failure_is_not_saved_as_answer(client, chat_job):
    job_id, provider = chat_job
    provider.outcome.tool_uses = ['shell']
    provider.outcome.tools_uncontrollable = True
    turn = ask(client, job_id).json()
    asyncio.run(report_chat._run(turn['id']))
    result = client.get(f'/api/jobs/{job_id}/chat').json()[0]
    assert result['status'] == 'failed' and result['answer'] is None and result['error']


def test_restart_marks_unfinished_answers_and_preserves_completed_history(client, chat_job):
    job_id, _ = chat_job
    turn = ask(client, job_id).json()
    asyncio.run(report_chat._run(turn['id']))
    ask(client, job_id)
    report_chat.recover()
    assert [t['status'] for t in client.get(f'/api/jobs/{job_id}/chat').json()] == ['succeeded', 'failed']


def test_deleting_report_also_deletes_its_conversation(client, chat_job):
    job_id, _ = chat_job
    ask(client, job_id)
    assert client.delete(f'/api/history/{job_id}').status_code == 204
    with session_scope() as session:
        assert not report_chat.turns(session, job_id)


def test_another_report_does_not_receive_existing_dialogue(client, chat_job):
    job_id, _ = chat_job
    turn = ask(client, job_id).json()
    asyncio.run(report_chat._run(turn['id']))
    other_id = str(uuid4())
    with session_scope() as session:
        session.add(ExecutionJob(id=other_id, provider='test', status='SUCCEEDED',
            claim_text='다른 청구항', result_text='다른 보고서', work_dir=str(PATHS.run_dir(other_id))))
    assert client.get(f'/api/jobs/{other_id}/chat').json() == []
    other_turn = ask(client, other_id).json()
    with session_scope() as session:
        job = session.get(ExecutionJob, other_id)
        payload = json.loads((report_chat.directory(job, other_turn['id']) / 'input.json').read_text(encoding='utf-8'))
        assert payload['report'] == '다른 보고서' and payload['conversation'] == []


def test_running_answer_cancellation_stops_provider_and_preserves_question(client, chat_job, monkeypatch):
    job_id, provider = chat_job
    turn = ask(client, job_id).json()
    async def scenario():
        entered = asyncio.Event()
        async def waiting(request, emit):
            entered.set()
            await asyncio.Event().wait()
        monkeypatch.setattr(provider, 'execute', waiting)
        task = asyncio.create_task(report_chat._run(turn['id']))
        report_chat._tasks[turn['id']] = task
        await entered.wait()
        await report_chat.cancel(turn['id'])
        report_chat._tasks.pop(turn['id'], None)
    asyncio.run(scenario())
    stored = client.get(f'/api/jobs/{job_id}/chat').json()[0]
    assert stored['status'] == 'cancelled' and stored['question'] == turn['question']
    assert provider.cancelled == [turn['id']]


def test_timeout_stops_provider_without_losing_report(client, chat_job, monkeypatch):
    job_id, provider = chat_job
    turn = ask(client, job_id).json()
    async def waiting(*_):
        await asyncio.Event().wait()
    monkeypatch.setattr(provider, 'execute', waiting)
    original_values = settings_service.get_all
    monkeypatch.setattr(settings_service, 'get_all', lambda s: {**original_values(s), 'default_timeout_seconds': 1})
    asyncio.run(report_chat._run(turn['id']))
    assert client.get(f'/api/jobs/{job_id}/chat').json()[0]['status'] == 'failed'
    assert provider.cancelled == [turn['id']]
    with session_scope() as session:
        assert session.get(ExecutionJob, job_id).result_text == '현재 보고서 인용발명 1 근거 E1'
