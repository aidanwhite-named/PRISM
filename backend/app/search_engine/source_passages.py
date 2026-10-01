"""Selectable, exact passages inside an already observed source window."""
import re


def options(body, scope, offset=0):
    result = []
    # Keep clauses/paragraphs separate; never join omitted sections into a quote.
    for block in re.finditer(r'\S[^\n]*(?:\n(?!\s*\n)[^\n]+)*', body):
        start, stop = block.span()
        while start < stop:
            end = min(stop, start + 1200)
            if end < stop:
                boundary = body.rfind(' ', start + 600, end)
                if boundary > start:
                    end = boundary
            left, right = start, end
            while left < right and body[left].isspace():
                left += 1
            while right > left and body[right - 1].isspace():
                right -= 1
            if right - left >= 40:
                result.append({'passage_id': f'{scope}:{offset + left}:{offset + right}',
                    'start': offset + left, 'end': offset + right,
                    'preview': re.sub(r'\s+', ' ', body[left:right])[:100]})
            start = end
    return result


def select(body, scope, offset, passage_id):
    item = next((p for p in options(body, scope, offset) if p['passage_id'] == passage_id), None)
    if item:
        return item['start'] - offset, item['end'] - offset
    return None


def repair_options(body, scope, offset, quote):
    words = set(re.findall(r'\w{3,}', quote.casefold()))
    choices = options(body, scope, offset)
    def score(item):
        text = body[item['start'] - offset:item['end'] - offset]
        return len(words.intersection(re.findall(r'\w{3,}', text.casefold())))
    choices = sorted(choices, key=score, reverse=True)
    return [{**item, 'quote': body[item['start'] - offset:item['end'] - offset]}
            for item in choices[:2] if score(item) > 0]
