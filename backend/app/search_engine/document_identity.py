"""Exact document identifiers and source-owned links; never fuzzy title matching."""
import re
from urllib.parse import unquote, urlsplit, urlunsplit

from .. import search_manifest


def url_key(value):
    try:
        parts = urlsplit(value or '')
        if parts.scheme not in ('http', 'https') or not parts.hostname:
            return ''
        return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path, parts.query, ''))
    except ValueError:
        return ''


def identities(record):
    result = set()
    values = [record.get('document_number', ''), record.get('doi', ''), record.get('url', '')]
    # This field is emitted by our capture parser, not accepted by save_findings.
    values.extend(record.get('document_identifiers') or [])
    for value in values:
        value = unquote(str(value))
        for doi in re.findall(r'10\.\d{4,9}/[^\s;,<>"]+', value, re.I):
            result.add(search_manifest.identity_key(doi=doi.rstrip('.,;)]}')))
        for arxiv in re.findall(
                r'(?:arxiv\s*:\s*|arxiv\.org/(?:abs|pdf|html)/)(\d{4}\.\d{4,5}(?:v\d+)?|[a-z.-]+/\d{7}(?:v\d+)?)', value, re.I):
            result.add(search_manifest.identity_key(doi='arxiv:' + re.sub(r'v\d+$', '', arxiv, flags=re.I)))
        for item in re.split(r'[;,\s]+', value):
            if re.fullmatch(r'[A-Z]{2}\d+[A-Z]\d?', item, re.I):
                result.add(search_manifest.identity_key(item))
        try:
            parsed = urlsplit(value)
            if parsed.hostname == 'patents.google.com':
                publication = re.fullmatch(r'/patent/([A-Z]{2}\d+[A-Z]\d?)(?:/[a-z-]+)?/?', parsed.path, re.I)
                if publication:
                    result.add(search_manifest.identity_key(publication[1]))
        except ValueError:
            pass
    return result


def primary_identity(record):
    keys = identities(record)
    return next(iter(sorted(keys, key=lambda k: (k.startswith('doi:10.48550/arxiv.'), k))), '')


def tokens(record):
    urls = [record.get('url', ''), *(record.get('source_urls') or [])]
    return identities(record) | {'url:' + key for url in urls if (key := url_key(url))}


class DocumentIndex:
    def __init__(self, calls=()):
        self.parent = {}
        for call in calls:
            response = call.get('result') or {}
            if call.get('ok') is not True or response.get('identifier_matched') is False:
                continue
            for row in response.get('records', []):
                keys = tokens(row)
                # Only metadata/canonical/own-PDF links emitted by the capture
                # parser connect sources. Reference-list URLs are not aliases.
                if call.get('tool') == 'source_fetch':
                    keys.update('url:' + key for url in response.get('document_links', [])
                                if (key := url_key(url)))
                    requested = (call.get('arguments') or {}).get('url')
                    if requested and response.get('raw_artifact_id'):
                        keys.add('url:' + url_key(requested))
                if (call.get('tool') == 'literature_fetch' and response.get('identifier_matched') is True
                        and response.get('source_kind') == 'openalex_locations'
                        and row.get('url', '').startswith('https://doi.org/')):
                    keys.update('url:' + key for url in response.get('document_links', [])
                                if (key := url_key(url)))
                keys = list(keys)
                for key in keys[1:]:
                    self.parent[self.root(key)] = self.root(keys[0])

    def root(self, key):
        self.parent.setdefault(key, key)
        if self.parent[key] != key:
            self.parent[key] = self.root(self.parent[key])
        return self.parent[key]

    def matches(self, left, right):
        a, b = identities(left), identities(right)
        # Publication kind codes and countries must remain separate, even when
        # a page links to a different family member.
        patents_a = {k for k in a if k.startswith('patent:')}
        patents_b = {k for k in b if k.startswith('patent:')}
        if patents_a and patents_b and patents_a != patents_b:
            return False
        for arxiv in (False, True):
            doi_a = {k for k in a if k.startswith('doi:') and k.startswith('doi:10.48550/arxiv.') == arxiv}
            doi_b = {k for k in b if k.startswith('doi:') and k.startswith('doi:10.48550/arxiv.') == arxiv}
            if doi_a and doi_b and not doi_a & doi_b:
                return False
        return bool({self.root(k) for k in tokens(left)} & {self.root(k) for k in tokens(right)})
