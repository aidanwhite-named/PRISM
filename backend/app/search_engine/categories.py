"""Document categories, independent of claim feature IDs and retrieval ranking."""

DEFINITIONS = {
    'X': '전체 구조와 핵심 특징이 모두 강하게 유사',
    'Y': '전체 구조는 다르지만 핵심 특징 또는 핵심 관계가 강하게 유사',
    'Z': '전체 구조는 유사하지만 핵심 대응은 부분적',
}


def normalize(value):
    # Read historical results and existing user strategies without rewriting them.
    value = {'A': 'X', 'B': 'Y', 'C': 'Z'}.get(value, value)
    return value if value in DEFINITIONS else None


def display_order(candidates):
    """Stable category order; retain relevance order within each category."""
    return sorted(candidates, key=lambda c: {'X': 0, 'Y': 1, 'Z': 2}.get(
        normalize((c.get('document_classification') or {}).get('group')), 3))


ASSESSMENTS = {'classified': '분류됨', 'insufficient_information': '자료 부족',
               'low_relevance': '검토 결과 관련성 낮음', 'not_evaluated': '미평가'}


def assessment(value):
    """Validate a model judgment; an omitted row is never a negative judgment."""
    if not isinstance(value, dict) or not isinstance(value.get('reason'), str) or not value['reason'].strip():
        return None
    group = value.get('group')
    if group is not None and (not isinstance(group, str) or normalize(group) is None):
        return None
    group = normalize(group)
    status = value.get('status', 'classified' if group else 'insufficient_information')
    if not isinstance(status, str) or status not in ASSESSMENTS or status == 'not_evaluated' or (status == 'classified') != bool(group):
        return None
    return {'group': group, 'status': status, 'reason': value['reason'].strip()[:1000]}
