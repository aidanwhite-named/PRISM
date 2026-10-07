"""Render model-ordered findings and their observed sources."""
from ..search_channels import cell
from ..search_format import _link
from .. import search_manifest, search_dates
from .source_observations import with_observations
from .search_review import GROUPS, verified_group, summary_counts, summary_text, promising_candidate
from .review_context import component_label
STOPS = {'running': '검색 중', 'deadline': '시간 종료 · 확보한 후보 보존',
         'cancelled': '사용자 중단 · 확보한 후보 보존', 'engine_error': '오류 · 확보한 후보 보존',
         'model_complete': '검색 완료',
         'component_review_incomplete': '검색 종료 · 구성 검토 미완료',
         'x_found': '원문 근거가 확인된 X 후보 확보 · 추가 검증 종료',
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
    review_summary = summary_counts([c for c in snapshot['candidates'] if c['date_status'] != 'after_cutoff'], snapshot.get('search_focus'))
    return {'version': 15, 'execution_mode': 'autonomous',
        'status': ('in_progress' if snapshot['phase'] != 'complete' else
                   'incomplete' if snapshot['stop_reason'] in ('cancelled', 'engine_error', 'deadline') else
                   'verification_incomplete' if review_summary['pending'] else 'complete'),
        'review_summary': review_summary,
        'provider': provider, 'model': model,
        'input': {'claim_text': claim, 'search_focus': snapshot.get('search_focus')},
        'prompt': {'id': prompt_id, 'name': prompt_name, 'sha256': prompt_sha256, 'template_mode': 'structured_input'},
        'policy': snapshot.get('policy', {}), 'limits': snapshot['limits'],
        'tool_journal': snapshot.get('source_calls', []),
        'observed': search_manifest.observed(snapshot.get('native_calls', [])),
        'reported': reported, 'date_filter': dates, 'usage': snapshot['usage'], 'engine': snapshot}


def render(snapshot):
    snapshot = with_observations(snapshot)
    focus = snapshot.get('search_focus')
    gap = bool(focus and focus.get('mode') == 'gap')
    candidates = [c for c in snapshot['candidates'] if c['date_status'] != 'after_cutoff']
    promising = [c for c in candidates if promising_candidate(c)] if not gap else []
    if promising:
        classified = [c for c in candidates if verified_group(c)]
        candidates = [*classified, *promising,
                      *[c for c in candidates if not verified_group(c) and not promising_candidate(c)]]
    dependent = bool(focus and focus.get('origin') == 'dependent_claims')
    lines = ['# 종속항만 따로 검색 결과' if dependent else '# 미대응 구성 검색 결과' if gap else '# 유사 문헌 검색 결과', '',
             f"{STOPS.get(snapshot['stop_reason'], snapshot['stop_reason'])} · {snapshot['elapsed_seconds']:.1f}초", '',
             summary_text(candidates, focus), '']
    if snapshot.get('phase', 'complete') == 'complete' and snapshot['stop_reason'] == 'model_complete' and not candidates:
        lines += ['이번 검색 범위에서 제시할 유사 문헌을 확보하지 못했습니다. 유사 문헌이 존재하지 않는다는 뜻은 아닙니다.', '']
    if dependent:
        automatic = focus.get('target_source') == 'dependent_claim'
        lines += ['## 검색할 종속항 원문' if automatic else '## 사용자가 확인한 검색 대상', '',
            *[cell(c['feature']) + '\n' for c in focus.get('components', [])],
            *(['종속항 원문에서 모델이 추가·한정된 특징을 파악하여 검색합니다.', ''] if automatic else []),
            '선택한 추가 특징의 대응 문헌을 찾은 결과입니다. 종속항 전체의 대응 여부와 구분합니다.', '']
    previous_section = None
    for i, c in enumerate(candidates, 1):
        section = ('promising' if promising_candidate(c) else 'classified' if verified_group(c) else 'other')
        if promising and section != previous_section:
            if section == 'promising':
                lines += ['## 유력 후보 · 분류 보류', '',
                    '초기 자료에서 전체 구조와 핵심 관계가 매우 가까운 후보입니다. 본문 검증이 미완료이며 X/Y/Z로 확정한 문헌은 아닙니다.', '']
            elif section == 'other':
                lines += ['## 기타 미분류 후보', '']
        previous_section = section
        group = None if gap else verified_group(c)
        review = c.get('search_review') or {}
        reason = review.get('reason') or c.get('reason', '')
        difference = review.get('gaps') or c.get('difference', '')
        provisional = 'AI 잠정 판단 (원문 근거 검증 미완료): ' if not gap and review.get('status') == 'needs_review' else ''
        label = '유력 후보 · 분류 보류' if c in promising else group or '미분류'
        lines += [f"## {i}. " + ('' if gap else f"[{label}] ") + cell(c['title']), '', _link(c['url']), '',
                  cell(c['document_number']) + ' · 공개일 ' + cell(c['publication_date'] or '미확인'), '',
                  provisional + cell(reason), '']
        if difference:
            lines += ['남은 차이·확인 사항: ' + cell(difference), '']
        triage = {'candidate': '우선 검토', 'promising': '유력 후보 · 분류 보류', 'hold': '자료 부족', 'rejected': '관련성 낮음',
                  'detailed': '본문 검토 후보'}.get(c.get('triage_status'), '미검토')
        scope = {'core_components': '일부 구성', 'full_text': '본문'}.get(c.get('review_stage'), '서지·검색 결과')
        lines += ['잠정 선별: ' + triage + ' · 검색 단계 확인 범위: ' + scope + ' (AI 보고)', '']
        if c.get('triage_reason'):
            lines += ['선별 근거: ' + cell(c['triage_reason']), '']
        if c.get('core_matches'):
            lines += ['확인된 핵심 구성 관계: ' + cell(c['core_matches']), '']
        review = c.get('search_review') or {}
        group = None if gap else verified_group(c)
        if group:
            lines += [f'{group} · {GROUPS[group]}', '']
        if gap:
            saved = {m['component_id']: m for m in review.get('component_matches', [])}
            for component in focus.get('components', []):
                match = saved.get(component['id'], {})
                degree = component_label(match)
                lines += ['### ' + cell(component.get('symbol') or component['id']) + ' · ' + degree,
                    '', cell(component['feature']), '', cell(match.get('reason') or '이 구성은 아직 검토하지 않았습니다.'), '']
                if match.get('gaps'):
                    lines += ['남은 차이: ' + cell(match['gaps']), '']
                if match.get('issues'):
                    lines += ['확인 미완료 사유: ' + cell(' '.join(dict.fromkeys(match['issues']))), '']
                for source in match.get('reviewed_sources', []):
                    lines += [f"검토 범위: {cell(source['scope'])} · 문자 {source['start']}–{source['end']}", '']
                for passage in match.get('passages', []):
                    location = ('PDF ' + ', '.join(map(str, passage['pages'])) + '쪽' if passage['pages'] else
                                f"{passage['scope']} · 문자 {passage['start']}–{passage['end']}")
                    lines += [cell(passage['relation']), '', '> ' + cell(passage['quote']), '',
                              cell(passage['translation']), '', cell(location), '']
        label = {'strong': '강한 유사성', 'partial': '일부 핵심 대응', 'mismatch': '기대한 대응과 다름'}.get(review.get('verdict'), '원문 미확인')
        if not gap:
            lines += ['검색 중 원문 대조: ' + (label + ' (AI 판단 · 발췌 일치 확인)' if review.get('status') == 'source_checked' else
                '원문 확보·확인 불가' if review.get('status') == 'unavailable' else '근거 확인 미완료'), '']
        if review.get('issues'):
            lines += ['검증 미완료 사유: ' + cell(' '.join(dict.fromkeys(review['issues']))), '']
        elif c.get('review_pending_reason'):
            lines += ['분류 미완료 사유: ' + cell(c['review_pending_reason']), '']
        for passage in review.get('passages', []):
            location = 'PDF ' + ', '.join(map(str, passage['pages'])) + '쪽' if passage['pages'] else f"{passage['scope']} · 문자 {passage['start']}–{passage['end']}"
            lines += [cell(passage['feature']) + ': ' + cell(passage['relation']), '',
                      '> ' + cell(passage['quote']), '', cell(passage['translation']), '', cell(location), '']
        if c.get('observed_scope'):
            lines += ['프로그램이 확보한 자료: ' + cell(c['observed_scope']), '']
        lines += ['LLM이 보고한 확인 범위(작성 당시): ' + cell(c.get('reported_scope') or '미기재'), '']
        if c.get('source_receipts'):
            lines += ['프로그램에 보존된 출처 응답: ' + cell(', '.join(dict.fromkeys(
                r['tool'] + ' (' + r['scope'] + ')' for r in c['source_receipts']))), '']
    if not candidates:
        lines += ['저장된 문헌 후보가 없습니다.', '']
    excluded = snapshot.get('excluded_input_documents', [])
    if excluded:
        lines += [f'입력과 동일한 문헌 {len(excluded)}건은 후보에서 제외하고 추가 검증을 생략했습니다.', '']
    if snapshot['warnings']:
        lines += ['## 실행 중 확인 사항', '', *['- ' + cell(w) for w in snapshot['warnings']], '']
    return '\n'.join(lines)
