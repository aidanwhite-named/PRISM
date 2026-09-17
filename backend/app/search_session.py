"""Bounded model-selected shortlists, overlapping API collection and evidence-gated completion."""
from __future__ import annotations

import asyncio
import copy
import json
from concurrent.futures import ThreadPoolExecutor

from . import search_manifest as sm, search_verification, search_dates
from .search_agent_tools import MAX_CANDIDATES
from .search_engine.models import write_json

STOP_FILE = 'search_x_complete.json'


def bound_report(report):
    """Keep model order; retain overflow in the audit without inventing scores."""
    report = copy.deepcopy(report)
    unique = {}
    from .search_deadline import identity
    for candidate in report['candidates']:
        unique.setdefault(identity(candidate), candidate)
    candidates = list(unique.values())
    report['candidates'] = candidates[:MAX_CANDIDATES]
    report.setdefault('candidate_dispositions', []).extend(
        {**c, 'reason': '모델 제시 순서에서 후보 15건 상한 밖. 원시 기록에 보존.'}
        for c in candidates[MAX_CANDIDATES:])
    return report


def start_collection(tools, arguments):
    """Return immediately so the native model can search the web concurrently."""
    if getattr(tools, 'collection', None) and any(not f.done() for f in tools.collection.values()):
        raise ValueError('collection_running: collect existing results before starting another round')
    size = arguments.get('max_results', 3)
    requests = []
    statuses = tools.statuses()
    skipped = []
    for source, field, tool in [('epo', 'epo_query', 'epo_search'),
                                ('kipris', 'kipris_query', 'kipris_search'),
                                ('literature', 'openalex_query', 'literature_search')]:
        if field not in arguments:
            continue
        if statuses.get(source, {}).get('status') != 'available':
            skipped.append({'source': source, 'reason': 'not_available'})
            continue
        args = {'query': arguments[field], 'max_results': size}
        if source == 'literature':
            args.update(source='openalex', openalex_mode='title_and_abstract')
        requests.append((tool, args))
    if not requests:
        return {'started': [], 'skipped': skipped}
    if not getattr(tools, 'collection_pool', None):
        tools.collection_pool = ThreadPoolExecutor(max_workers=3, thread_name_prefix='search-api')
    tools.collection = {name: tools.collection_pool.submit(tools.call, name, args) for name, args in requests}
    return {'started': [name for name, _ in requests], 'skipped': skipped,
            'next_action': 'Run native web search now while APIs run; then call collect_results. Screen results and save at most 15 candidates.'}


def collect_results(tools, arguments):
    from .search_mcp_server import error_response
    completed, pending = {}, []
    for name, future in getattr(tools, 'collection', {}).items():
        if not future.done():
            pending.append(name)
            continue
        try:
            completed[name] = future.result()
        except Exception as exc:
            completed[name] = {'ok': False, **error_response(exc, name, {}, tools.secrets)}
    return {'completed': completed, 'pending': pending,
            'next_action': 'Review completed results now. If pending, do useful web/source work before polling again.'}


def review_x(tools, report, review):
    """The model judges coverage; code checks each declared feature's source evidence."""
    from .search_deadline import identity
    verified = search_verification.verify(report, {}, sm.read_tool_journal(tools.work_dir))
    candidate = next((c for c in verified['candidates'] if identity(c) == review['candidate_id']), None)
    reason = ''
    if not candidate or candidate.get('group') != 'A':
        reason = 'X 후보가 아닙니다.'
    elif not review['core_features'] or not review['rationale'].strip():
        reason = '청구항 전체의 핵심 구성과 관계에 대한 판단이 필요합니다.'
    else:
        refs = [value['evidence_ref'] for source in candidate.get('evidence_sources', [])
                for field, value in source['fields'].items()
                if field.split(':')[0] in ('claims', 'description', 'full_text')]
        for feature in review['core_features']:
            if not any(row.get('feature') == feature and row.get('support_verified')
                       and row.get('evidence_ref') in refs for row in candidate['mapping']):
                reason = '핵심 구성의 청구항·명세서·전문 근거 대조가 부족합니다: ' + feature
                break
        if tools.cutoff:
            dated = copy.deepcopy({'candidates': [candidate]})
            audit = search_dates.filter_candidates(dated, tools.cutoff)
            if audit.get('excluded') or audit.get('unknown_publication_date') or audit.get('status_counts', {}).get('ambiguous'):
                reason = '검색 기준일 이전 공개 여부를 확인해야 합니다.'
    if reason:
        return {'accepted': False, 'reason': reason}
    report = bound_report(report)
    for item in report['candidates']:
        if identity(item) == review['candidate_id']:
            item['note'] = item.get('note') or review['rationale']
        elif not item.get('note'):
            item['note'] = 'X 후보의 원문 검토로 탐색을 조기 종료했습니다. 이 후보의 추가 검토는 미완료입니다.'
    report.setdefault('search_review', {}).update(stop_reason='원문 근거가 대조된 X 후보를 검토하여 조기 종료: ' + review['rationale'])
    # Written atomically. Runner rechecks this gate before accepting cancellation.
    write_json(tools.work_dir / STOP_FILE, {'report': report, 'review': review})
    for future in getattr(tools, 'collection', {}).values():
        future.cancel()
    return {'accepted': True, 'reason': 'X 원문 근거 대조 완료. 추가 검색을 중단합니다.'}


async def execute(provider, request, emit, *, cancelled):
    """Watch independently of model progress so a stalled CLI can still finish."""
    from types import SimpleNamespace
    task = asyncio.create_task(provider.execute(request, emit))
    stop = None
    try:
        while not task.done():
            await asyncio.wait({task}, timeout=0.25)
            path = request.work_dir / STOP_FILE
            if cancelled() or not path.exists():
                continue
            try:
                value = json.loads(path.read_text(encoding='utf-8'))
                cutoff = request.mcp_servers.get('prism-search', {}).get('env', {}).get('PRISM_SEARCH_CUTOFF', '')
                gate = review_x(SimpleNamespace(work_dir=request.work_dir, cutoff=cutoff), value['report'], value['review'])
                if gate['accepted']:
                    stop = value['report']
                    if not task.done():
                        await provider.cancel(request.job_id)
                    await emit('stage', {'stage': 'finalizing', 'message': '원문 근거를 확인한 X 후보가 있어 탐색을 조기 종료합니다.'})
                    break
            except (OSError, ValueError, KeyError, TypeError):
                continue
        outcome = await task
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    if stop is not None and not cancelled() and not (outcome.is_error or outcome.auth_required or outcome.rate_limited
            or outcome.tool_budget_exceeded or outcome.content_read_budget_exceeded or outcome.permission_denials):
        outcome.result_text = json.dumps(stop, ensure_ascii=False)
        outcome.cancelled = outcome.timed_out = False
        outcome.exit_code = 0
        outcome.terminal_reason = 'verified_x_early_stop'
    return outcome
