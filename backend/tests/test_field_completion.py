import json
import time
from dataclasses import replace

import pytest

from app import search_followup as followup, search_verification as sv, search_manifest as sm
from app.providers.base import ExecutionOutcome, ExecutionRequest, WEB_SEARCH
from app.patent_search import literature_parser as lp
from .test_search_quality import candidate


@pytest.mark.asyncio
async def test_metadata_completion_uses_backend_once_and_does_not_launch_model(tmp_path, monkeypatch):
    c = {**candidate(), 'doi': '10.1234/test', 'doc_number': '', 'mapping': []}
    payload = json.dumps({'candidates': [c]})
    seen = []
    class Tools:
        def __init__(self, **kwargs):
            assert kwargs['max_calls'] == 1
        def call(self, name, args):
            seen.append((name, args))
            raise ValueError('metadata absent')
    monkeypatch.setattr(followup, 'SearchTools', Tools)
    class Provider:
        async def execute(self, *args):
            pytest.fail('Missing metadata must not trigger a model')
    async def emit(*args): pass
    policy = replace(WEB_SEARCH, max_tool_calls=1, mcp_tools=('mcp__prism-search__literature_fetch',))
    request = ExecutionRequest(job_id='x', work_dir=tmp_path, user_message='', system_prompt='', tool_policy=policy)
    initial = ExecutionOutcome(result_text=payload, usage={'input_tokens': 10}, tool_policy=policy)
    result, audit = await followup.run(Provider(), request, initial, emit, attachments=[], fail_on_tool_use=True,
        deadline=time.monotonic()+100, availability={'literature': {'status': 'available'}}, cancelled=lambda: False)
    assert len(seen) == audit['metadata_fetches'] == 1
    assert not audit['attempted'] and result.usage == initial.usage


def test_metadata_plan_ignores_available_but_exhausted_and_unrelated_scopes():
    c = {**candidate(), 'doi': '10.1234/test', 'doc_number': '',
         'verification_issues': ['publication_date_unverified', 'applicant_unverified'],
         'evidence_sources': [{'fields': {}}]}
    availability = {'literature': {'status': 'available'}}
    allowed = ('mcp__prism-search__literature_fetch',)
    assert followup.retrieval_plan({'candidates': [c]}, [], availability, allowed) == [
        ('literature_fetch', {'doi': '10.1234/test', 'constituent': 'biblio'})]
    # A successful empty response, failed request, or still-running request all count as tried.
    for state, ok in [('completed', True), ('completed', False), ('started', None)]:
        journal = [{'tool': 'literature_fetch', 'arguments': {'doi': c['doi'], 'constituent': 'biblio'}, 'state': state, 'ok': ok}]
        assert followup.retrieval_plan({'candidates': [c]}, journal, availability, allowed) == []
    assert followup.retrieval_plan({'candidates': [c]}, [], availability, ()) == []


@pytest.mark.parametrize('parts,expected', [([2020], '2020'), ([2020, 3], '2020-03'), ([2020, 3, 2], '2020-03-02'), ([2020, 2, 31], '')])
def test_crossref_computed_metadata_reextracts_from_original_bytes(parts, expected):
    body = json.dumps({'message': {'DOI': '10.1234/a', 'author': [{'given': 'Ada', 'family': 'Lovelace'}, {'name': 'Research Group'}],
                                    'issued': {'date-parts': [parts]}}}).encode()
    work = lp.read_crossref_work(body)
    assert work.publication_date == expected
    assert lp._extract(body, work.paths['authors']) == work.authors == 'Ada Lovelace; Research Group'
    if expected:
        assert lp._extract(body, work.paths['publication_date']) == expected
    else:
        assert 'publication_date' not in work.paths


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['exact', 'invented', 'wrong_identity', 'timeout', 'tool_use'])
async def test_quote_repair_is_small_tool_free_and_cannot_rewrite_candidates(tmp_path, monkeypatch, mode):
    ref = {'artifact_id': 'saved', 'field_path': 'claims', 'profile_id': 'test'}
    c = {**candidate(), 'mapping': [{'feature': 'F', 'counterpart': 'original', 'support_text': 'summary', 'evidence_ref': ref}]}
    payload = json.dumps({'candidates': [c]})
    def verify(data, *args):
        import copy
        data = copy.deepcopy(data)
        for c in data['candidates']:
            c['evidence_sources'] = [{'fields': {'claims': {'text': 'The sensor measures temperature.', 'evidence_ref': ref}}}]
            for row in c['mapping']:
                row['support_verified'] = row['support_text'] == 'The sensor measures temperature.'
        return data
    monkeypatch.setattr(sv, 'verify', verify)
    initial = ExecutionOutcome(result_text=payload, usage={'input_tokens': 10}, tool_policy=WEB_SEARCH)
    class Provider:
        async def execute(self, request, emit):
            assert request.tool_policy.tools_disabled and not request.mcp_servers
            assert len(request.user_message) < 2000
            assert 'HUGE_SEARCH_PROMPT' not in request.system_prompt + request.user_message
            assert request.timeout_seconds <= 30
            return ExecutionOutcome(result_text=json.dumps({'repairs': [{'candidate': 9 if mode == 'wrong_identity' else 0,
                'mapping': 0, 'support_text': 'invented' if mode == 'invented' else 'The sensor measures temperature.', 'group': 'C'}]}),
                timed_out=mode == 'timeout', exit_code=0, terminal_reason='completed', usage={'input_tokens': 20},
                tool_policy=request.tool_policy, tool_calls=[{'name': 'WebSearch', 'ok': True}] if mode == 'tool_use' else [])
    async def emit(*args): pass
    request = ExecutionRequest(job_id='x', work_dir=tmp_path, system_prompt='HUGE_SEARCH_PROMPT', user_message='HUGE_SEARCH_PROMPT', tool_policy=WEB_SEARCH)
    result, audit = await followup.run(Provider(), request, initial, emit, attachments=[], fail_on_tool_use=True,
        deadline=time.monotonic()+100, availability={}, cancelled=lambda: False)
    assert result.usage['input_tokens'] == 30
    after = sm.parse(result.result_text)[0]['candidates'][0]
    assert after['group'] == 'A' and after['doc_number'] == c['doc_number']
    assert after['mapping'][0]['counterpart'] == 'original'
    assert audit['quote_repairs'] == (1 if mode == 'exact' else 0)
