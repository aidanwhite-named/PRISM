from __future__ import annotations

from ..search_channels import cell
from ..search_report import _link, category_label
from .. import search_manifest, search_dates
from .categories import DEFINITIONS, ASSESSMENTS, normalize, display_order

MATCHES = {'explicit': '명시적 대응', 'semantic': '의미상 대응', 'partial': '부분 대응',
           'absent': '검토 passage에 대응 없음', 'unknown': '미확인'}
STOPS = {'running': '검색 중', 'verified_feature_coverage': '구성별 원문 근거 확보',
         'verified_xy': '원문 근거가 확인된 X·Y 문헌 확보',
         'bounded_expansion_complete': '정해진 범위의 확장 완료', 'fast_budget_complete': '빠른 검색 범위 완료',
         'deadline_reserve': '시간 예산 종료 · 확보한 후보 보존', 'cancelled': '사용자 중단 · 확보한 후보 보존',
         'engine_error': '오류 · 확보한 후보 보존'}
STOPS['classification_incomplete'] = '분류 미완료 · 확보한 후보와 부분 분류 보존'


def manifest(snapshot, *, claim, provider, model=None, prompt_id='', prompt_name='', prompt_sha256=''):
    candidates = []
    for i, item in enumerate(display_order(snapshot['candidates']), 1):
        number = item['document_number']
        candidates.append({'index': i, 'rank': i, 'group': normalize((item.get('document_classification') or {}).get('group')), 'doc_number': number,
            'doi': number if number.startswith('10.') else '', 'title': item['title'], 'url': item['url'],
            'publication_date': item['publication_date'], 'family': item['family_id'], 'mapping': [],
            'note': '구조화 검색 후보; 구성별 근거는 engine.evidence에 기록',
            'evidence_level': 'search_snippet_only', 'verification_issues': [], 'verification_scope': {}})
    reported = {'candidates': candidates, 'term_expansions': [], 'rounds': [], 'access_failures': [],
                'search_review': {'stop_reason': snapshot['stop_reason'], 'remaining_gaps': snapshot['warnings']}}
    dates = search_dates.filter_candidates(reported, snapshot.get('cutoff'))
    result = search_manifest.build(claim_text=claim, provider=provider, model=model,
        prompt_id=prompt_id, prompt_name=prompt_name, prompt_sha256=prompt_sha256,
        reported=reported, date_filter=dates, usage=snapshot['usage'], search_depth=snapshot['depth'],
        max_tool_calls_total=snapshot['limits']['queries'], timeout_seconds=snapshot['limits']['seconds'])
    result['engine'] = snapshot
    result['group_definitions'] = dict(DEFINITIONS)
    result['status'] = ('in_progress' if snapshot['phase'] != 'complete' else
                        'incomplete' if snapshot['stop_reason'] in ('cancelled', 'engine_error') else
                        'classification_incomplete' if snapshot.get('classification', {}).get('status') == 'incomplete' else
                        'verification_incomplete' if any(not c['evidence'] for c in snapshot['candidates']) else 'complete')
    return result


def render(snapshot):
    lines = ['# 선행문헌 검색 결과', '',
        f"{cell(STOPS.get(snapshot['stop_reason'], snapshot['stop_reason']))} · {snapshot['elapsed_seconds']:.1f}초", '',
        '검색 범위의 완전성은 확인되지 않았습니다. 미확보 원문과 미검증 관계는 문헌 부재를 뜻하지 않습니다.', '',
        'X분류 · 전체 구조와 핵심 특징 유사 / Y분류 · 구조는 다르나 핵심 관계 유사 / Z분류 · 구조는 유사하나 핵심 대응 부분적.',
        '문헌 분류와 구성 번호는 별개이며, 분류만으로 원문 검증 완료를 의미하지 않습니다.', '', '## 검색 구성', '']
    summary = snapshot.get('classification')
    if summary:
        label = {'complete': '선별 대상 분류 응답 완료', 'incomplete': '분류 미완료', 'not_applicable': '분류할 후보 없음'}[summary['status']]
        lines[2:2] = [f"{label} · 검토 {summary['reviewed_count']}/{summary['target_count']}건 · 미평가 후보 {summary['unreviewed_count']}건", '']
    for feature in snapshot['features']:
        lines += [f"- **{feature['id']}**: {cell(feature['text'])}"]
    if snapshot.get('route'):
        lines += ['', '## 검색 경로', '']
        for step in snapshot['route']:
            label = {'relation_seed': '관계 중심 검색', 'citations': '인용·피인용 검색',
                     'continuation': '정밀 검색 이어서 진행'}.get(step['lane'], '기존 특허·논문 검색')
            outcome = {'verified_x': '구성별 원문 근거가 있는 X 후보 확인', 'no_verified_x': 'X 미확인',
                       'verified_xy': '원문 근거가 있는 X·Y 후보 확인', 'no_verified_xy': 'X·Y 미확인',
                       'resumed': '이전 후보·원문 근거와 사용량을 이어받음',
                       'candidates_merged': '발견한 후보를 합쳐 원문 검증 대상으로 전달',
                       'no_candidates': '반환된 후보 없음',
                       'skipped': '지원되는 후보·검색어 또는 잔여 예산 부족으로 생략'}.get(step.get('outcome'), 'X·Y 미확인으로 후속 검색')
            lines += [f'- {label}: {outcome}']
    candidates = display_order([c for c in snapshot['candidates'] if c['date_status'] != 'after_cutoff'])
    lines += ['', f'## 후보 {len(candidates)}건', '']
    families = set()
    for candidate in candidates:
        family = candidate['family_id'] or candidate['id']
        if family in families:
            continue
        families.add(family)
        if len(families) > 25:
            break
        lines += [f"### {len(families)}. {cell(candidate['title'] or candidate['document_number'])}", '',
                  f"{cell(candidate['document_number'])} · 공개일 {cell(candidate['publication_date'] or '미확인')} · {candidate['data_status']}",
                  '', _link(candidate['url']), '']
        classification = candidate.get('document_classification') or {}
        lines += [f"**{category_label(classification.get('group'))}** · {cell(classification.get('reason') or '아직 평가하지 않은 후보')}", '']
        status = ASSESSMENTS.get(classification.get('status'), '미평가' if not classification else '분류 검토됨')
        basis = {'abstract': '초록 기준 · 잠정 판단', 'search_metadata': '제목·검색 단서 기준',
                 'retrieved_passages': '확보한 근거 구간 기준'}.get(classification.get('basis'), '')
        lines += [cell(' · '.join(filter(None, [status, basis]))), '']
        members = [c['document_number'] for c in candidates if c['family_id'] and c['family_id'] == candidate['family_id']]
        if len(members) > 1:
            lines += ['동일 family: ' + cell(', '.join(members)), '']
        lines += ['| 구성 | 대응 | 관계·차이 | 원문 근거·위치 |', '| --- | --- | --- | --- |']
        for feature in snapshot['features']:
            evidence = next((e for e in candidate['evidence'] if e['feature'] == feature['id']), {})
            locator = evidence.get('locator') or {}
            where = 'p.' + str(locator['page']) if locator.get('page') else str(locator.get('section') or '')
            lines.append('| ' + ' | '.join(cell(value) for value in (feature['id'],
                MATCHES.get(evidence.get('match'), '미검증'),
                str(evidence.get('relation') or '') + ' / ' + str(evidence.get('difference') or ''),
                str(evidence.get('quote') or '') + (' [' + where + ']' if where else ''))) + ' |')
        failed = [a for a in candidate['acquisitions'] if a['status'] == 'failed']
        if failed:
            lines += ['', '확보 제약: ' + cell('; '.join(a.get('error', '') for a in failed))]
        lines += ['']
    if not candidates:
        lines += ['반환된 후보가 없습니다. 아래 채널 상태와 실제 질의를 확인하십시오.', '']
    lines += ['## 실행 기록', '', '| Source | Query | 결과 |', '| --- | --- | --- |']
    for query in snapshot['queries']:
        lines.append('| ' + ' | '.join(cell(x) for x in (query['source'], query['query'],
            query.get('error') or str(query.get('hits', '')) + ' ' + query['status'])) + ' |')
    lines += ['', *['- ' + cell(w) for w in snapshot['warnings']], '',
              '토큰·근거 위치·원문 해시·후보 순위 및 발견 경로는 검색 감사 기록에 보존했습니다.', '']
    return '\n'.join(lines)
