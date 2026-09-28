"""Render model-ordered findings and their observed sources."""
from ..search_channels import cell
from ..search_format import _link
from .. import search_manifest, search_dates
from .source_observations import with_observations
STOPS = {'running': '검색 중', 'deadline': '시간 종료 · 확보한 후보 보존',
         'cancelled': '사용자 중단 · 확보한 후보 보존', 'engine_error': '오류 · 확보한 후보 보존',
         'model_complete': '검색 완료',
         'deadline_reserve': '시간 종료 · 확보한 후보 보존'}


def manifest(snapshot, *, claim, provider, model=None, prompt_id='', prompt_name='', prompt_sha256=''):
    candidates = [{'index': i, 'rank': i, 'doc_number': c['document_number'],
        'title': c['title'], 'url': c['url'], 'publication_date': c['publication_date'],
        'note': c.get('reason', ''), 'reported_scope': c.get('reported_scope', ''),
        'triage_status': c.get('triage_status', 'unreviewed'), 'review_stage': c.get('review_stage', 'metadata'),
        'triage_reason': c.get('triage_reason', ''), 'core_matches': c.get('core_matches', '')}
        for i, c in enumerate(snapshot['candidates'], 1)]
    reported = {'candidates': candidates}
    dates = search_dates.filter_candidates(reported, snapshot.get('cutoff'))
    return {'version': 15, 'execution_mode': 'autonomous',
        'status': ('in_progress' if snapshot['phase'] != 'complete' else
                   'incomplete' if snapshot['stop_reason'] in ('cancelled', 'engine_error', 'deadline') else 'complete'),
        'provider': provider, 'model': model,
        'input': {'claim_text': claim, 'search_focus': snapshot.get('search_focus')},
        'prompt': {'id': prompt_id, 'name': prompt_name, 'sha256': prompt_sha256, 'template_mode': 'structured_input'},
        'policy': snapshot.get('policy', {}), 'limits': snapshot['limits'],
        'tool_journal': snapshot.get('source_calls', []),
        'observed': search_manifest.observed(snapshot.get('native_calls', [])),
        'reported': reported, 'date_filter': dates, 'usage': snapshot['usage'], 'engine': snapshot}


def render(snapshot):
    snapshot = with_observations(snapshot)
    lines = ['# 유사 문헌 검색 결과', '',
             f"{STOPS.get(snapshot['stop_reason'], snapshot['stop_reason'])} · {snapshot['elapsed_seconds']:.1f}초", '']
    candidates = [c for c in snapshot['candidates'] if c['date_status'] != 'after_cutoff']
    for i, c in enumerate(candidates, 1):
        lines += [f"## {i}. {cell(c['title'])}", '', _link(c['url']), '',
                  cell(c['document_number']) + ' · 공개일 ' + cell(c['publication_date'] or '미확인'), '',
                  cell(c.get('reason', '')), '']
        if c.get('difference'):
            lines += ['남은 차이·확인 사항: ' + cell(c['difference']), '']
        lines += ['선별 상태: ' + cell(c.get('triage_status') or 'unreviewed') +
                  ' · 검토 단계: ' + cell(c.get('review_stage') or 'metadata'), '']
        if c.get('triage_reason'):
            lines += ['선별 근거: ' + cell(c['triage_reason']), '']
        if c.get('core_matches'):
            lines += ['확인된 핵심 구성 관계: ' + cell(c['core_matches']), '']
        if c.get('observed_scope'):
            lines += ['프로그램이 확보한 자료: ' + cell(c['observed_scope']), '']
        lines += ['LLM이 보고한 확인 범위(작성 당시): ' + cell(c.get('reported_scope') or '미기재'), '']
        if c.get('source_receipts'):
            lines += ['프로그램에 보존된 출처 응답: ' + cell(', '.join(dict.fromkeys(
                r['tool'] + ' (' + r['scope'] + ')' for r in c['source_receipts']))), '']
    if snapshot.get('summary'):
        lines += ['## 검색 설명', '', '아래는 AI가 작성한 설명입니다. 자료 확보 범위는 위 문헌별 출처 응답 기록을 기준으로 확인하세요.', '', snapshot['summary'], '']
    if not candidates:
        lines += ['저장된 문헌 후보가 없습니다.', '']
    if snapshot['warnings']:
        lines += ['## 실행 중 확인 사항', '', *['- ' + cell(w) for w in snapshot['warnings']], '']
    return '\n'.join(lines)
