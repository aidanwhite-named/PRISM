"""Recover complete reported findings from incremental JSON, never from query text."""
from __future__ import annotations

import json
import re
from urllib.parse import urlsplit


def finding(value):
    if not isinstance(value, dict):
        return None
    title, url = value.get('title'), value.get('url')
    if not isinstance(title, str) or not title.strip() or not isinstance(url, str):
        return None
    try:
        parts = urlsplit(url)
        if parts.scheme != 'https' or not parts.hostname or parts.username or parts.password or parts.port not in (None, 443):
            return None
    except ValueError:
        return None
    if any(ord(c) < 33 for c in url):
        return None
    number = value.get('document_number')
    number = number.strip() if isinstance(number, str) else ''
    # A publication URL identifies that publication, not its family members.
    if parts.hostname == 'patents.google.com':
        match = re.fullmatch(r'/patent/([A-Z]{2}\d+[A-Z]\d?)(?:/[a-z-]+)?/?', parts.path, re.I)
        if match:
            url_number = match[1].upper()
            if number and number.replace(' ', '').upper() != url_number:
                return None
            number = url_number
    return {'title': title.strip(), 'url': url, 'document_number': number,
            'snippet': value.get('snippet') if isinstance(value.get('snippet'), str) else '',
            'feature': value.get('feature') if isinstance(value.get('feature'), str) else 'context'}


class WebProgress:
    def __init__(self):
        self.text = ''
        self.records = {}

    def feed(self, text):
        self.text += text
        # Bound parsing work independently of the provider's output-token limit.
        self.text = self.text[-1_000_000:]
        decoder, offset, updates = json.JSONDecoder(), 0, []
        while (start := self.text.find('{', offset)) >= 0:
            try:
                value, end = decoder.raw_decode(self.text, start)
            except ValueError:
                # The outer array may be unfinished; its complete rows still count.
                offset = start + 1
                continue
            offset = end
            rows = value.get('records', [value]) if isinstance(value, dict) else []
            for raw in rows if isinstance(rows, list) else []:
                row = finding(raw)
                if not row:
                    continue
                key = row['document_number'].lower() or row['url']
                previous = self.records.get(key)
                if previous and len(previous['snippet']) > len(row['snippet']):
                    row['snippet'] = previous['snippet']
                if previous != row:
                    self.records[key] = row
                    updates.append(row)
        return updates
