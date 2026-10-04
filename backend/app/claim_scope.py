"""Record explicit input labels without inventing a technical decomposition."""
from __future__ import annotations

import re
import unicodedata

_HEADER = re.compile(
    r"(?im)^[ \t]*(?:#{1,6}\s*)?(?:\*\*)?[\[【]?\s*"
    r"(?:청구항\s*|claim\s+)(\d+)(?=[\s\]】*.:：]|$)\s*[\]】]?(?:\*\*)?[.:：]?"
)
_SYMBOL = re.compile(
    r"(?m)(?:^[ \t]*(?:[-*]\s*)?|(?<=;)\s*)"
    r"[\(（\[]([A-Z]|전제부)[\)）\]]\s*"
)
_FIRST_SYMBOL = re.compile(r"^\s*[\(（\[]([A-Z]|전제부)[\)）\]]\s*")


def claim_key(label: str) -> str:
    text = unicodedata.normalize('NFKC', str(label or '')).strip()
    match = re.fullmatch(r"(?:청구항\s*|claim\s+)(\d+)[.:]?", text, re.I)
    return f"청구항 {int(match[1])}" if match else text.casefold()


def symbol_key(label: str) -> str:
    text = unicodedata.normalize('NFKC', str(label or '')).strip()
    return text.strip('()[] ').casefold()


def from_text(text: str) -> dict:
    """Only headers and line/semicolon labels are authoritative input facts.

    References inside a feature are not new components. Unlabelled clauses are
    deliberately not split on commas/newlines: that would be technical judgment.
    """
    text = text or ''
    headers = list(_HEADER.finditer(text))
    sections = [(f'청구항 {int(h[1])}', h.end(), headers[i + 1].start()
                 if i + 1 < len(headers) else len(text)) for i, h in enumerate(headers)]
    if not sections:
        sections = [('', 0, len(text))]
    components, unmarked = [], []
    for claim, start, end in sections:
        body = text[start:end]
        markers = list(_SYMBOL.finditer(body))
        first = _FIRST_SYMBOL.match(body)
        if first and not any(m.start(1) == first.start(1) for m in markers):
            markers.insert(0, first)
        if not markers and claim:
            unmarked.append(claim)
        for i, marker in enumerate(markers):
            feature_end = markers[i + 1].start() if i + 1 < len(markers) else len(body)
            symbol = f'({marker[1]})'
            components.append({'claim': claim, 'symbol': symbol,
                'label': f'{claim} {symbol}'.strip(),
                'feature': body[marker.end():feature_end].strip(),
                'source_start': start + marker.end(), 'source_end': start + feature_end})
    return {'claims': list(dict.fromkeys(c for c, _, _ in sections if c)),
            'components': components, 'unmarked_claims': list(dict.fromkeys(unmarked)),
            'component_scope_known': bool(components) and not unmarked}


def instructions(text: str) -> str:
    scope = from_text(text)
    if not scope['components']:
        return ''
    labels = ', '.join(dict.fromkeys(c['label'] for c in scope['components']))
    return ('[PRISM 입력 구성 식별자]\n'
            '현재 청구항에 명시된 구성 표식입니다. 보고서와 구성별 분석 블록에서 '
            '각 표식을 원문 청구항 번호에 연결해 유지하고, 확인하지 못한 구성도 '
            'unreadable로 기록하십시오. 표식 내부의 관계·조건·한정도 검토하십시오. '
            '이 목록은 기술적 분해의 완전성을 보장하지 않습니다.\n' + labels)
