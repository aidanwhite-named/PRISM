"""Distinguish supporting citations from scoped negative search findings.

The model declares a reference's role; this is not a semantic certification.
Legacy reports get a conservative compatibility path only for wholly negative
statements inside a no-match/insufficient assessment. Mixed prose stays checked.
"""
import re

ROLES = {'support', 'not_found', 'unavailable'}

_NEGATIVE = re.compile(
    r'(?:개시|기재|설명)(?:되어\s*있지|되지|되어\s*있지는)\s*않습니다$|'
    r'(?:(?:확인|도출)할|찾을)\s*수\s*없습니다$|'
    r'확인되지\s*않습니다$|'
    r'(?:근거|구성|기재|개시)(?:가|는|이)?\s*(?:없습니다|부족합니다|부재합니다)$'
)
_POSITIVE = re.compile(
    r'(?:개시|기재|설명|제시|입증|보완|대응|확인)(?:하|되)(?:고|며|지만|므로|여|였|었|는)|'
    r'(?:개시|기재|설명|제시|확인)되어\s*있(?:고|으며|으나|지만)|'
    r'(?:기초하여|바탕으로|적용하여|활용하여)'
)


def negative_only(values):
    statements = [part.strip() for value in values if isinstance(value, str)
                  for part in re.split(r'[.!?。;](?:\s+|$)|\n+', value) if part.strip()]
    return bool(statements) and all(_NEGATIVE.search(s) and not _POSITIVE.search(s) for s in statements)


def assess(mentions, values, declared, *, known, readable, allow_absence=False,
           allow_unavailable=False, legacy_negative=False, comparison_only=()):
    """Return explicit review records plus schema/context problems.

    Declaring a source absent never creates an excerpt, validates an absence,
    changes a score, or excuses nonexistent documents.
    """
    problems = []
    if declared is None:
        declared = {}
    elif not isinstance(declared, dict):
        problems.append('문헌별 참조 역할은 자료 번호와 support/not_found/unavailable의 객체여야 합니다.')
        declared = {}
    reviews = {}
    for alias in sorted(set(mentions) | set(declared)):
        comparison = alias in comparison_only and alias not in declared
        role = 'comparison' if comparison else declared.get(alias, 'support')
        origin = 'comparison_links' if comparison else 'declared' if alias in declared else 'default_support'
        if not isinstance(role, str) or role not in ROLES and not comparison:
            problems.append(f'{alias}: 문헌 참조 역할이 잘못되었습니다.')
            role = 'support'
        if alias not in known:
            problems.append(f'{alias}: 확인되지 않은 문헌을 검토 대상으로 표시했습니다.')
            role = 'support'
        elif alias not in declared and legacy_negative and alias in readable and negative_only(values):
            role, origin = 'not_found', 'legacy_negative_context'
        if role == 'not_found' and (not allow_absence or alias not in readable):
            problems.append(f'{alias}: 미발견 표시에 필요한 검토 범위 또는 무대응 판단을 확인해야 합니다.')
            role = 'support'
        if role == 'unavailable' and not allow_unavailable:
            problems.append(f'{alias}: 확인 불가 표시와 대응 판단이 일치하지 않습니다.')
            role = 'support'
        reviews[alias] = {'role': role, 'origin': origin,
                          'scope': 'provided_text' if alias in readable else 'unavailable',
                          'assessment': 'model_reported'}
    return reviews, problems
