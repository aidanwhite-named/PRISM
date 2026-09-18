"""Malformed model arguments must not masquerade as disk checkpoint failure."""
import asyncio
import json

import pytest

from app import search_agent_tools, search_manifest, search_session
from app.providers.base import ExecutionOutcome, ExecutionRequest
from app.providers.codex_stream import CodexStreamParser
from app.search_mcp_server import SearchTools, error_response


def _error_events(exc, arguments, *, structured=True):
    detail = error_response(exc, 'save_candidates', arguments)
    result = {'content': [{'type': 'text', 'text': json.dumps(detail, ensure_ascii=False)}]}
    if structured:
        result['structured_content'] = detail
    return CodexStreamParser().feed(json.dumps({'type': 'item.completed', 'item': {
        'id': 'save-error', 'type': 'mcp_tool_call', 'server': 'prism-search',
        'tool': 'save_candidates', 'error': None, 'status': 'failed', 'result': result,
    }}))


@pytest.mark.parametrize('structured', [True, False])
def test_codex_reports_mcp_validation_detail_instead_of_failed(structured):
    # Exact historical shape: only the result contains the error, not item.error.
    exc = search_manifest.SearchLogError('후보 5: group은 A/B/C/null이어야 합니다.')
    events = _error_events(exc, {}, structured=structured)
    error = next(payload for kind, payload in events if kind == 'tool_error')
    assert error['detail'].startswith('후보 5:')
    assert error['error_code'] == 'SearchLogError'
    assert error['recoverable'] is True


def test_nullable_group_schema_and_failed_save_preserve_checkpoint(tmp_path):
    tools = SearchTools(values={}, work_dir=tmp_path)
    schema = next(x for x in tools.tool_definitions() if x['name'] == 'save_candidates')['inputSchema']
    group = schema['properties']['report']['properties']['candidates']['items']['properties']['group']
    assert group['enum'] == ['A', 'B', 'C', None]
    original = {'report': {'candidates': [{'doc_number': 'JP7475618B1', 'group': 'A'}]}}
    tools.call('save_candidates', original)
    before = (tmp_path / search_agent_tools.CHECKPOINT).read_bytes()
    invalid = {'report': {'candidates': [{'doc_number': 'JP7475618B1', 'group': 'null'}]}}
    with pytest.raises(search_manifest.SearchLogError) as caught:
        tools.call('save_candidates', invalid)
    assert (tmp_path / search_agent_tools.CHECKPOINT).read_bytes() == before
    response = error_response(caught.value, 'save_candidates', invalid)
    assert response['recoverable']
    assert 'JSON null' in response['recovery']['next_step']
    invalid['report']['candidates'][0]['group'] = None
    tools.call('save_candidates', invalid)
    assert search_agent_tools.load_checkpoint(tmp_path)['candidates'][0]['group'] is None


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['corrected', 'unfinished', 'repeated'])
async def test_validation_can_be_corrected_but_never_silently_succeeds(tmp_path, mode):
    tools = SearchTools(values={}, work_dir=tmp_path)
    valid = {'report': {'candidates': [{'doc_number': 'JP7475618B1', 'group': None}]}}
    tools.call('save_candidates', valid)
    checkpoint_before = (tmp_path / search_agent_tools.CHECKPOINT).read_bytes()
    events = []
    done = asyncio.Event()

    class Provider:
        stopped = False

        async def execute(self, request, emit):
            invalid = {'report': {'candidates': [{'doc_number': 'WO2024144877A1', 'group': 'null'}]}}
            for _ in range(3 if mode == 'repeated' else 1):
                with pytest.raises(search_manifest.SearchLogError) as caught:
                    tools.call('save_candidates', invalid)
                for kind, payload in _error_events(caught.value, invalid):
                    await emit(kind, payload)
            if mode == 'repeated':
                await asyncio.wait_for(done.wait(), timeout=2)
            else:
                # Let the watchdog observe the invalid save before correction.
                await asyncio.sleep(0.35)
                assert not self.stopped
                if mode == 'corrected':
                    invalid['report']['candidates'][0]['group'] = None
                    tools.call('save_candidates', invalid)
            return ExecutionOutcome(exit_code=0, result_text='done')

        async def cancel(self, job_id):
            self.stopped = True
            done.set()
            return True

    async def emit(kind, payload):
        events.append((kind, payload))

    provider = Provider()
    outcome = await search_session.execute(provider, ExecutionRequest(
        job_id='validation', work_dir=tmp_path, system_prompt='', user_message=''),
        emit, cancelled=lambda: False)
    if mode == 'corrected':
        assert not outcome.is_error and not provider.stopped
        assert len(search_agent_tools.load_checkpoint(tmp_path)['candidates']) == 2
        assert not any(p.get('stage') == 'checkpoint_failed' for _, p in events)
    else:
        assert outcome.is_error and outcome.terminal_reason == 'search_checkpoint_failed'
        assert '입력 형식' in outcome.error_message and 'null' in outcome.error_message
        assert (tmp_path / search_agent_tools.CHECKPOINT).read_bytes() == checkpoint_before
        assert provider.stopped == (mode == 'repeated')


def test_disk_error_is_not_an_argument_correction():
    error = error_response(OSError('disk full'), 'save_candidates', {})
    assert not error.get('recoverable')
