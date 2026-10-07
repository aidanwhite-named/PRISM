"""Source-grounded search assessments and compact feedback, not patentability."""
from .. import search_manifest
from ..analysis_evidence import anchor, needs_translation
from .source_passages import select, repair_options
from .document_identity import DocumentIndex

VERDICTS = {'strong', 'partial', 'mismatch', 'unavailable'}
GROUPS = {
    'X': '전체 구조와 핵심 특징이 모두 강하게 유사',
    'Y': '전체 구조는 다르지만 핵심 특징 또는 핵심 관계가 강하게 유사',
    'Z': '전체 구조는 유사하지만 핵심 대응은 부분적',
}


def verified_group(row):
    review = row.get('search_review') or {}
    group = review.get('group')
    return group if review.get('status') == 'source_checked' and group in GROUPS else None


def has_verified_x(rows, cutoff=''):
    from .. import search_dates
    return any(verified_group(row) == 'X' and
               search_dates.evaluate(row.get('publication_date', ''), cutoff).status in
               (search_dates.STATUS_NO_LIMIT, search_dates.STATUS_WITHIN) for row in rows)


def shortlist(rows, cutoff='', limit=10):
    from .. import search_dates
    return [row for row in prioritize(rows)
            if row.get('triage_status') != 'rejected'
            and (row.get('search_review') or {}).get('verdict') != 'mismatch'
            and search_dates.evaluate(row.get('publication_date', ''), cutoff).status != search_dates.STATUS_AFTER][:limit]


def validate(directory, candidate, review):
    from .review_context import load, components, assessment_status, assessment_verdict
    targets = components(load(directory))
    if not targets:
        return _validate_review(directory, candidate, review)
    review = review if isinstance(review, dict) else {}
    submitted = review.get('component_matches', [])
    submitted = submitted if isinstance(submitted, list) else []
    passages = review.get('passages', [])
    passages = passages if isinstance(passages, list) else []
    matches, issues = [], []
    known = {c['id'] for c in targets}
    for item in submitted:
        if not isinstance(item, dict) or item.get('component_id') not in known:
            issues.append('검색 대상으로 선택하지 않은 구성 ID입니다.')
    for component in targets:
        entries = [m for m in submitted if isinstance(m, dict) and m.get('component_id') == component['id']]
        match = {'component_id': component['id'], 'symbol': component.get('symbol', ''),
                 'feature': component['feature'], 'status': 'needs_review', 'review_status': 'unreviewed', 'passages': []}
        if len(entries) != 1:
            match['issues'] = ['이 구성은 아직 검토하지 않았습니다.' if not entries else '이 구성의 검토 결과가 중복되어 판정을 확인할 수 없습니다.']
            if any(item.get('verdict') in (*VERDICTS, 'not_found') and isinstance(item.get('reason'), str)
                   and item['reason'].strip() for item in entries):
                match['review_status'] = 'reviewed'
        else:
            item = entries[0]
            if item.get('verdict') in (*VERDICTS, 'not_found') and isinstance(item.get('reason'), str) and item['reason'].strip():
                match.update(review_status='reviewed', verdict=item['verdict'], reason=item['reason'], gaps=item.get('gaps', ''))
            refs = item.get('passage_indices', [])
            valid_refs = isinstance(refs, list) and all(type(n) is int and 0 <= n < len(passages) for n in refs)
            if not valid_refs:
                match['issues'] = ['구성과 원문 근거의 참조가 올바르지 않습니다.']
            else:
                assessment = _validate_review(directory, candidate, {
                    'verdict': item.get('verdict'), 'reason': item.get('reason'),
                    'gaps': item.get('gaps', ''), 'queries': [],
                    'passages': [passages[n] for n in dict.fromkeys(refs)]})
                if item.get('verdict') == 'not_found':
                    assessment = _validate_absence(directory, candidate, item)
                match.update(assessment)
                if item.get('verdict') in (*VERDICTS, 'not_found') and isinstance(item.get('reason'), str) and item['reason'].strip():
                    match['review_status'] = 'unavailable' if assessment.get('status') == 'unavailable' else 'reviewed'
        matches.append(match)
    result = {key: review.get(key, '') if isinstance(review.get(key), str) else ''
              for key in ('reason', 'gaps')}
    result.update(component_matches=matches, status=assessment_status(matches),
        verdict=assessment_verdict(matches), context_issues=issues,
        queries=review.get('queries', []) if isinstance(review.get('queries'), list) else [],
        issues=[*issues, *(issue for m in matches for issue in m.get('issues', []))], passages=[])
    if issues:
        result['status'] = 'needs_review'
    return result


def _validate_absence(directory, candidate, item):
    """Validate the inspected scope, never infer document-wide absence."""
    result = {'verdict': 'not_found', 'reason': item.get('reason', ''), 'gaps': item.get('gaps', ''),
              'passages': [], 'issues': [], 'reviewed_sources': []}
    journal = search_manifest.read_tool_journal(directory)
    index = DocumentIndex(journal)
    refs = item.get('reviewed_capture_ids', [])
    if not isinstance(refs, list) or not refs or any(not isinstance(ref, str) for ref in refs):
        result['issues'].append('대응 근거 없음 판단에는 실제 검토한 본문 수집 번호가 필요합니다.')
        refs = []
    for ref in dict.fromkeys(refs):
        sources = []
        for call in journal:
            response = call.get('result') or {}
            if (not call.get('ok') or call.get('tool') not in ('source_fetch', 'epo_fetch')
                    or response.get('identifier_matched') is False or response.get('capture_artifact_id') != ref
                    or response.get('verification_scope') not in ('claims', 'description', 'full_text')):
                continue
            for row in response.get('records', []):
                if index.matches(candidate, row):
                    sources.extend({'capture_artifact_id': ref, 'url': row['url'], 'scope': field,
                                    'start': response.get('offset', 0), 'end': response.get('offset', 0) + len(body)}
                        for field, body in row.get('fields', {}).items()
                        if field in ('claims', 'description', 'full_text') and isinstance(body, str) and body.strip())
        if not sources:
            result['issues'].append('대응 근거 없음 판단의 검토 범위를 후보 문헌의 실제 본문과 연결하지 못했습니다.')
        result['reviewed_sources'].extend(sources)
    for key in ('reason', 'gaps'):
        if not isinstance(result[key], str) or not result[key].strip():
            result['issues'].append('검토 범위와 대응하지 않는 한정의 설명이 필요합니다.')
    result['status'] = 'needs_review' if result['issues'] else 'source_checked'
    return result


def _validate_review(directory, candidate, review):
    if not isinstance(review, dict) or review.get('verdict') not in VERDICTS:
        return {'status': 'needs_review', 'issues': ['원문 검토 형식 오류']}
    result = {key: review.get(key, '') if isinstance(review.get(key, ''), str) else '' for key in ('verdict', 'reason', 'gaps')}
    queries = review.get('queries', [])
    result['queries'] = [q for q in queries if isinstance(q, str) and q.strip()][:4] if isinstance(queries, list) else []
    result['passages'], result['issues'], result['passage_issues'] = [], [], []
    group = review.get('group')
    if group:
        if group not in GROUPS or result['verdict'] != ('partial' if group == 'Z' else 'strong'):
            result['issues'].append('X/Y는 강한 핵심 대응, Z는 부분 대응으로 구분해야 합니다.')
        else:
            result['group'] = group
    journal = search_manifest.read_tool_journal(directory)
    calls = [c for c in journal
             if c.get('ok') and c.get('tool') in ('source_fetch', 'epo_fetch')
             and (c.get('result') or {}).get('identifier_matched') is not False]
    document_index = DocumentIndex(journal)
    passages = review.get('passages', [])
    if not isinstance(passages, list):
        passages = []
    for index, passage in enumerate(passages[:12]):
        if not isinstance(passage, dict):
            result['issues'].append('원문 근거 형식 오류')
            continue
        match = None
        repairs = []
        failure = 'capture_missing'
        for call in calls:
            response = call.get('result', {})
            if response.get('capture_artifact_id') != passage.get('capture_artifact_id'):
                continue
            failure = 'body_unavailable'
            if response.get('verification_scope') not in ('claims', 'description', 'full_text'):
                continue
            failure = 'document_link_failed'
            for row in response.get('records', []):
                if not document_index.matches(candidate, row):
                    continue
                failure = 'quote_mismatch'
                for field, body in row.get('fields', {}).items():
                    if field not in ('claims', 'description', 'full_text') or not isinstance(body, str):
                        continue
                    quote = passage.get('quote')
                    passage_id = passage.get('passage_id')
                    offset = response.get('offset', 0)
                    hit = select(body, field, offset, passage_id) if passage_id else (
                        anchor(quote, body) if isinstance(quote, str) else None)
                    # A selector proves the selected source only, never a different
                    # model-supplied quotation attached to that selector.
                    if hit and passage_id and quote and anchor(quote, body[hit[0]:hit[1]]) != (0, hit[1] - hit[0]):
                        hit = None
                    if hit:
                        start, end = response.get('offset', 0) + hit[0], response.get('offset', 0) + hit[1]
                        pages = [p['page'] for p in response.get('page_spans', [])
                                 if p['start'] < end and p['end'] > start]
                        match = {key: passage.get(key, '') if isinstance(passage.get(key, ''), str) else ''
                                 for key in ('feature', 'relation', 'translation')}
                        match.update(quote=body[hit[0]:hit[1]], url=row['url'], scope=field,
                                     start=start, end=end, pages=pages,
                                     capture_artifact_id=response['capture_artifact_id'])
                        failure = 'relation_missing'
                    else:
                        repairs.extend({**p, 'capture_artifact_id': response['capture_artifact_id']}
                            for p in repair_options(body, field, offset, quote or passage.get('feature', '')))
        if match and needs_translation(match['quote']) and not (isinstance(match['translation'], str) and match['translation'].strip()):
            result['issues'].append('외국어 발췌의 한국어 번역이 누락되었습니다.')
        if match and all(isinstance(match[k], str) and match[k].strip() for k in ('feature', 'relation')):
            result['passages'].append(match)
        else:
            messages = {
                'capture_missing': '저장된 원문 수집 기록을 찾지 못했습니다.',
                'body_unavailable': '확보한 자료가 초록·일반 웹페이지여서 본문 근거로 사용할 수 없습니다.',
                'document_link_failed': '읽은 원문을 후보 문헌의 식별번호·출처 주소에 연결하지 못했습니다.',
                'quote_mismatch': '발췌가 실제 읽은 원문의 연속 구간과 일치하지 않습니다.',
                'relation_missing': '발췌에 청구항 특징·구성 관계의 설명이 누락되었습니다.',
            }
            result['issues'].append(messages[failure])
            repair_hint = ('기존 원문의 passage_id와 해당 구간 전체의 번역으로 교정할 수 있습니다. '
                           '직접 quote를 쓰는 경우 생략·변경 없는 연속 원문이 필요합니다.')
            if failure == 'document_link_failed':
                repair_hint = ('DOI·arXiv 식별번호와 서지 페이지의 원문 연결 정보로 문헌을 대조할 수 있습니다. '
                               '제목 유사성만으로는 다른 문헌의 발췌를 연결할 수 없습니다.')
            elif failure == 'body_unavailable':
                repair_hint = '연결된 PDF 또는 arXiv HTML의 본문 영역은 근거 확보에 사용할 수 있는 대체 경로입니다.'
            elif failure == 'capture_missing':
                repair_hint = '실제 source_fetch 응답의 capture_artifact_id와 passage_id로 교정할 수 있습니다.'
            result['passage_issues'].append({'index': index, 'code': failure, 'repair_options': repairs[:2],
                'repair_hint': repair_hint})
    if result['verdict'] != 'unavailable' and not result['passages']:
        result['issues'].append('원문 근거가 필요합니다. 초록·검색 스니펫은 본문 검증이 아닙니다.')
    if not isinstance(result['reason'], str) or not result['reason'].strip():
        result['issues'].append('유사성 판단 이유가 없습니다.')
    if result['verdict'] in ('partial', 'mismatch') and not result['gaps'].strip():
        result['issues'].append('재검색에 반영할 구체적 차이가 없습니다.')
    result['status'] = 'needs_review' if result['issues'] else ('unavailable' if result['verdict'] == 'unavailable' else 'source_checked')
    return result


def feedback(rows, *, verification=False, cutoff='', focus=None):
    pending, gaps = [], []
    for row in rows:
        if not (focus and focus.get('mode') == 'gap') and row.get('triage_status') == 'rejected' and not row.get('search_review'):
            continue
        review = row.get('search_review') or {}
        item = {'url': row['url'], 'title': row['title']}
        if review.get('status') not in ('source_checked', 'unavailable'):
            pending.append({**item, 'issues': review.get('issues', []),
                            'passage_issues': review.get('passage_issues', []),
                            'component_matches': review.get('component_matches', [])})
        if review.get('verdict') in ('partial', 'mismatch', 'unavailable'):
            gaps.append({**item, 'gaps': review.get('gaps') or review.get('reason'),
                         'queries': review.get('queries', [])})
    found_x = has_verified_x(rows, cutoff)
    return {'pending_source_checks': pending[:10], 'search_gaps': gaps[:5],
            'review_summary': summary_counts(rows, focus),
            'verified_x_found': found_x}


def summary_counts(rows, focus=None):
    counts = {group: 0 for group in GROUPS}
    pending = unavailable = checked = 0
    for row in rows:
        review = row.get('search_review') or {}
        group = verified_group(row)
        if group:
            counts[group] += 1
        elif review.get('status') == 'source_checked':
            checked += 1
        elif review.get('status') == 'unavailable':
            unavailable += 1
        elif row.get('triage_status') == 'rejected':
            checked += 1
        else:
            pending += 1
    result = {**counts, 'pending': pending, 'unavailable': unavailable,
            'checked_without_group': checked, 'candidate_count': len(rows),
            'status': 'incomplete' if pending else 'complete'}
    from .review_context import components
    targets = components(focus)
    if targets:
        result.update(X=0, Y=0, Z=0)
        result['pending'] = sum(any(not any(m.get('component_id') == c['id'] and
            m.get('status') in ('source_checked', 'unavailable') for m in
            (row.get('search_review') or {}).get('component_matches', [])) for c in targets)
            for row in rows)
        result['status'] = 'incomplete' if result['pending'] else 'complete'
        result['components'] = []
        for c in targets:
            from .review_context import review_state
            totals = {key: 0 for key in ('strong', 'partial', 'mismatch', 'not_found', 'unavailable', 'pending', 'unreviewed', 'reviewed_pending')}
            for row in rows:
                match = next((m for m in (row.get('search_review') or {}).get('component_matches', [])
                              if m['component_id'] == c['id']), {})
                key = match.get('verdict') if match.get('status') == 'source_checked' else (
                    'unavailable' if match.get('status') == 'unavailable' else 'pending')
                totals[key] += 1
                if key == 'pending':
                    totals['unreviewed' if review_state(match) == 'unreviewed' else 'reviewed_pending'] += 1
            result['components'].append({'component_id': c['id'], 'symbol': c.get('symbol', ''),
                                         'feature': c['feature'], **totals})
    return result


def promising_candidate(row):
    """Explicit preliminary relevance, never an inferred or verified X/Y/Z."""
    review = row.get('search_review') or {}
    return (row.get('triage_status') == 'promising'
            and bool(str(row.get('triage_reason') or '').strip())
            and review.get('status') != 'source_checked'
            and review.get('verdict') != 'mismatch')


def summary_text(rows, focus=None):
    counts = summary_counts(rows, focus)
    if 'components' in counts:
        return f"저장된 후보 {counts['candidate_count']}건 · " + ' · '.join(
            f"{c['symbol'] or c['component_id']}: 강한 대응 {c['strong']}건 / 부분 대응 {c['partial']}건 / "
            f"비대응 {c['mismatch']}건 / 검토 범위 내 대응 근거 없음 {c['not_found']}건 / "
            f"미검토 {c['unreviewed']}건 / 검토함·검증 보완 {c['reviewed_pending']}건 / 검토 불가 {c['unavailable']}건"
            for c in counts['components'])
    return (f"저장된 후보 {counts['candidate_count']}건 · 원문 근거 검증을 통과한 분류: "
            f"X {counts['X']}건, Y {counts['Y']}건, Z {counts['Z']}건 · "
            f"근거 확인 미완료 {counts['pending']}건 · 원문 확인 불가 {counts['unavailable']}건" +
            (f" · 유력 후보 · 분류 보류 {sum(promising_candidate(row) for row in rows)}건"
             if any(promising_candidate(row) for row in rows) else ''))


def prioritize(rows):
    def rank(row):
        review = row.get('search_review') or {}
        group = verified_group(row)
        if group:
            return {'X': 0, 'Y': 1, 'Z': 2}[group]
        matches = review.get('component_matches', [])
        if any(m.get('status') == 'source_checked' and m.get('verdict') == 'strong' for m in matches):
            return 0
        if any(m.get('status') == 'source_checked' and m.get('verdict') == 'partial' for m in matches):
            return 1
        if review.get('status') == 'source_checked':
            return {'strong': 3, 'partial': 3, 'mismatch': 6}.get(review.get('verdict'), 5)
        if promising_candidate(row):
            return 3
        return 6 if row.get('triage_status') == 'rejected' else 4 if row.get('triage_status') == 'candidate' else 5
    return sorted(rows, key=rank)
