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

def category_label(group):
    category = {'A': 'X', 'B': 'Y', 'C': 'Z', 'X': 'X', 'Y': 'Y', 'Z': 'Z'}.get(group)
    return category + '분류' if category else '미분류'

