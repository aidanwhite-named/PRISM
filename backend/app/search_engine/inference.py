from __future__ import annotations

import asyncio
import json
import re
import time
from dataclasses import replace

from ..providers.base import ExecutionRequest, NO_TOOLS
from .models import write_json


class InferenceError(RuntimeError):
    pass


def decode_object(text, phase):
    """Accept fenced JSON or a native final JSON appended after a draft.

    Never repair/invent missing JSON. For interrupted web output, retain only
    fully decoded record objects and label the recovery explicitly.
    """
    key = {'plan': 'features', 'plan_recovery': 'features', 'triage': 'candidate_ids', 'web_seeds': 'records'}.get(phase, 'evidence')
    found = []
    for block in re.findall(r'```(?:json)?\s*([\s\S]*?)```', text):
        try:
            value = json.loads(block)
            if isinstance(value, dict) and isinstance(value.get(key), list):
                found.append(value)
        except ValueError:
            pass
    tail = re.sub(r'```(?:json)?\s*[\s\S]*?```', '', text).strip()
    try:
        value = json.loads(tail)
        if isinstance(value, dict) and isinstance(value.get(key), list):
            found.append(value)
    except ValueError:
        pass
    if found:
        return found[-1]
    if phase == 'web_seeds':
        match = re.search(r'"records"\s*:\s*\[', text)
        if match:
            tail = text[match.end():].lstrip()
            records = []
            decoder = json.JSONDecoder()
            while tail.startswith('{') and len(records) < 3:
                try:
                    value, end = decoder.raw_decode(tail)
                except ValueError:
                    break
                if isinstance(value, dict) and isinstance(value.get('url'), str) and isinstance(value.get('title'), str):
                    records.append(value)
                tail = tail[end:].lstrip().removeprefix(',').lstrip()
            if records:
                return {'records': records, '_partial': True}
    raise InferenceError('structured_output_unavailable')


def response_schema(phase):
    string = {'type': 'string'}
    strings = {'type': 'array', 'items': string}
    def obj(properties, required=None):
        return {'type': 'object', 'properties': properties, 'required': required or list(properties), 'additionalProperties': False}
    if phase == 'plan':
        feature = obj({'text': string, 'terms': strings, 'phrases': strings, 'queries': strings, 'relation': string})
        return obj({'features': {'type': 'array', 'items': feature, 'minItems': 1, 'maxItems': 8},
                    'context_query': string, 'seed_queries': {**strings, 'maxItems': 2}})
    if phase == 'triage':
        return obj({'candidate_ids': {**strings, 'maxItems': 3}})
    if phase == 'web_seeds':
        record = obj({k: string for k in ('title', 'url', 'document_number', 'feature', 'snippet')})
        return obj({'records': {'type': 'array', 'items': record, 'maxItems': 3}})
    row = obj({k: string for k in ('candidate_id', 'feature', 'match', 'passage_id', 'quote', 'relation', 'difference')})
    return obj({'evidence': {'type': 'array', 'items': row, 'maxItems': 24}})


class Inference:
    def __init__(self, provider, *, job_id, directory, model=None, reasoning_effort='', emit=None,
                 cancelled=lambda: False, max_calls=5, max_input_tokens=120000):
        self.provider, self.job_id, self.directory = provider, job_id, directory
        self.model, self.reasoning_effort = model, reasoning_effort
        self.emit, self.cancelled = emit, cancelled
        self.max_calls, self.max_input_tokens = max_calls, max_input_tokens
        self.calls = []
        self.last_outcome = None

    def usage(self):
        aliases = {'cache_read_tokens': 'cached_input_tokens', 'thinking_tokens': 'reasoning_output_tokens'}
        rows = [call.get('usage') or {} for call in self.calls]
        total = {key: sum(float(row.get(key) or row.get(aliases.get(key)) or 0) for row in rows)
                 for key in ('input_tokens', 'output_tokens', 'cache_read_tokens', 'thinking_tokens')}
        total['total_tokens'] = sum(float(row.get('total_tokens') or
            (float(row.get('input_tokens') or 0) + float(row.get('output_tokens') or 0))) for row in rows)
        return {**total, 'usage_complete': all(call.get('usage_complete') for call in self.calls),
                'stages': self.calls, 'cost_usd': None}

    async def call(self, phase, system, payload, *, seconds, web=False):
        if self.cancelled():
            raise InferenceError('cancelled')
        if len(self.calls) >= self.max_calls or self.usage()['input_tokens'] >= self.max_input_tokens:
            raise InferenceError('llm_budget')
        if seconds < 3:
            raise InferenceError('deadline_reserve')
        policy = NO_TOOLS
        if web:
            policy = self.provider.search_tool_policy
            if policy is None:
                raise InferenceError('web_unavailable')
            policy = replace(policy, max_tool_calls=3, required_tools=(), mcp_tools=(),
                             max_search_calls=3, max_content_read_calls=0, max_url_lookup_calls=0)
        work_dir = self.directory / ('%02d-' % len(self.calls) + phase)
        work_dir.mkdir(parents=True, exist_ok=True)
        text = json.dumps(payload, ensure_ascii=False)
        if self.provider.max_input_bytes and self.provider.payload_bytes(system, text) > self.provider.max_input_bytes:
            raise InferenceError('provider_input_budget')
        write_json(work_dir / 'input.json', {'system': system, 'payload': payload})
        effort = self.reasoning_effort or self.provider_phase_effort(phase)
        request = ExecutionRequest(self.job_id, work_dir, system, text, model=self.model,
            reasoning_effort=effort, timeout_seconds=max(1, int(seconds)),
            tool_policy=policy, mcp_servers={})
        start = time.monotonic()
        record = {'phase': phase, 'model': self.model, 'reasoning_effort': effort,
                  'input_chars': len(system) + len(text), 'usage_complete': False}
        self.calls.append(record)

        async def emit(kind, data):
            if kind == 'tool_use':
                allowed = ('search_web', 'WebSearch', 'web_search') if web else ()
                if data.get('name') not in allowed:
                    record['unexpected_tool'] = data.get('name')
                    await self.provider.cancel(self.job_id)
            # Intermediate JSON is audit data, not a user-facing final answer.
            if self.emit and kind not in ('result_stream', 'result', 'usage'):
                await self.emit('search_progress', {'phase': phase, 'message': phase,
                                                  'detail': kind})
        outcome = None
        try:
            outcome = await asyncio.wait_for(self.provider.execute(request, emit), timeout=seconds + .5)
            self.last_outcome = outcome
            record.update(usage=outcome.usage, seconds=time.monotonic() - start,
                          timed_out=outcome.timed_out, tool_calls=outcome.tool_calls,
                          usage_complete=bool(outcome.usage) and not outcome.timed_out and not outcome.cancelled
                          and (outcome.usage or {}).get('usage_complete', True))
            write_json(work_dir / 'outcome.json', {**record, 'result': outcome.result_text, 'errors': outcome.errors})
            if record.get('unexpected_tool') or (not web and outcome.tool_uses):
                raise InferenceError('unexpected_verifier_tool_use')
            if web and not any(c.get('name') in ('search_web', 'WebSearch', 'web_search') for c in outcome.tool_calls):
                raise InferenceError('web_search_not_observed')
            if outcome.cancelled or outcome.auth_required or outcome.rate_limited:
                raise InferenceError('provider_unavailable_or_cancelled')
            value = decode_object(outcome.result_text.strip(), phase)
            record['output_partial'] = bool(value.get('_partial')) or outcome.timed_out
            return value
        except asyncio.TimeoutError as exc:
            await self.provider.cancel(self.job_id)
            raise InferenceError('inference_timeout') from exc
        finally:
            record['seconds'] = time.monotonic() - start
            write_json(self.directory / 'usage.json', self.usage())

    @staticmethod
    def provider_phase_effort(phase):
        # Bounded passage comparisons must leave time for a complete response.
        # The caller's explicit effort still takes precedence.
        return 'low'
