"""Server-owned target components for search assessments."""
import json

FILE = 'search_review_context.json'


def load(directory):
    path = directory / FILE
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else None


def components(focus):
    return focus.get('components', []) if focus and focus.get('mode') == 'gap' else []


def review_state(match):
    """Review happened independently of whether its evidence passed validation."""
    if match.get('review_status'):
        return match['review_status']
    if match.get('status') == 'unavailable':
        return 'unavailable'
    if match.get('status') == 'source_checked' or (match.get('verdict') and match.get('reason')):
        return 'reviewed'
    return 'unreviewed'


def component_label(match):
    state = review_state(match)
    if state == 'unavailable':
        return '검토 불가 · 원문 확인 불가'
    if state == 'unreviewed':
        return '미검토'
    if match.get('status') != 'source_checked':
        return '검토함 · 근거 검증 필요'
    return '검토 완료 · ' + {'strong': '강한 대응', 'partial': '부분 대응',
        'mismatch': '대응하지 않음', 'not_found': '검토 범위 내 대응 근거 없음'}.get(match.get('verdict'), '판정 확인 필요')


def pending_components(rows, focus):
    targets = components(focus)
    pending = []
    for row in rows:
        matches = {m['component_id']: m for m in (row.get('search_review') or {}).get('component_matches', [])}
        missing = [c['id'] for c in targets if matches.get(c['id'], {}).get('status') not in ('source_checked', 'unavailable')]
        if missing:
            pending.append({'url': row['url'], 'title': row['title'], 'component_ids': missing})
    return pending


def assessment_status(matches):
    if not matches or any(m.get('status') == 'needs_review' for m in matches):
        return 'needs_review'
    return 'unavailable' if all(m.get('status') == 'unavailable' for m in matches) else 'source_checked'


def assessment_verdict(matches):
    for verdict in ('strong', 'partial'):
        if any(m.get('status') == 'source_checked' and m.get('verdict') == verdict for m in matches):
            return verdict
    return 'unavailable' if matches and all(m.get('status') == 'unavailable' for m in matches) else 'mismatch'
