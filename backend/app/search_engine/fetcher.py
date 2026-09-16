"""Credential-free HTTPS fetching with pinned public DNS and bounded redirects."""
from __future__ import annotations

import ipaddress
import socket
import ssl
import time
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urlsplit, urljoin

import urllib3
from ..patent_search.artifacts import ArtifactStore


class FetchError(ValueError):
    pass


def public_target(url: str):
    parts = urlsplit(url)
    if (parts.scheme != 'https' or not parts.hostname or parts.username or parts.password
            or parts.port not in (None, 443) or any(ord(c) < 33 for c in url)):
        raise FetchError('unsafe_url')
    host = parts.hostname.encode('idna').decode('ascii')
    answers = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    addresses = list(dict.fromkeys(row[4][0] for row in answers))
    if not addresses or any(not ipaddress.ip_address(address).is_global for address in addresses):
        raise FetchError('non_public_address')
    # Pin the connection to the validated address. TLS still verifies the hostname.
    addresses.sort(key=lambda address: ':' in address)
    return host, addresses[0], parts.path or '/', parts.query


@dataclass
class Fetched:
    url: str
    body: bytes
    content_type: str
    artifact_id: str


class SafeFetcher:
    def __init__(self, store: ArtifactStore, *, max_bytes=12 * 1024 * 1024, timeout=15):
        self.store, self.max_bytes, self.timeout = store, max_bytes, timeout

    def get(self, url: str) -> Fetched:
        deadline = time.monotonic() + self.timeout
        for _ in range(4):
            host, address, path, query = public_target(url)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise FetchError('fetch_deadline')
            context = ssl.create_default_context()
            try:
                import truststore
                context = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            except ImportError:
                pass
            pool = urllib3.HTTPSConnectionPool(address, port=443, ssl_context=context,
                assert_hostname=host, server_hostname=host,
                timeout=urllib3.Timeout(connect=min(5, remaining), read=min(8, remaining), total=remaining))
            response = None
            try:
                response = pool.urlopen('GET', path + ('?' + query if query else ''),
                    headers={'Host': host, 'User-Agent': 'PRISM-Literature/1.0', 'Accept-Encoding': 'identity'},
                    redirect=False, retries=False, preload_content=False)
                if response.status in (301, 302, 303, 307, 308):
                    location = response.headers.get('Location')
                    if not location:
                        raise FetchError('redirect_without_location')
                    url = urljoin(url, location)
                    continue
                if response.status != 200:
                    raise FetchError('http_' + str(response.status))
                if response.headers.get('Content-Encoding', 'identity').lower() not in ('', 'identity'):
                    raise FetchError('compressed_response_not_supported')
                if int(response.headers.get('Content-Length') or 0) > self.max_bytes:
                    raise FetchError('download_too_large')
                body = bytearray()
                for block in response.stream(65536, decode_content=False):
                    if time.monotonic() >= deadline:
                        raise FetchError('fetch_deadline')
                    body.extend(block)
                    if len(body) > self.max_bytes:
                        raise FetchError('download_too_large')
                mime = response.headers.get('Content-Type', '').split(';')[0].lower()
                data = bytes(body)
                if not (data.startswith(b'%PDF-') or mime in ('text/html', 'application/xhtml+xml', 'text/plain')):
                    raise FetchError('unsupported_content_type')
                return Fetched(url, data, 'application/pdf' if data.startswith(b'%PDF-') else mime, self.store.put(data))
            finally:
                if response is not None:
                    response.close()
                pool.close()
        raise FetchError('too_many_redirects')


class ArticleHTML(HTMLParser):
    """Strip executable/navigation content, retain headings and paragraph boundaries."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.ignored = 0
        self.parts = []
        self.pdf_links = []
        self.in_title = False
        self.title = ''
        self.abstract_page = False

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in ('script', 'style', 'nav', 'footer', 'noscript', 'iframe'):
            self.ignored += 1
        if tag == 'title':
            self.in_title = True
        if tag in ('p', 'div', 'section', 'h1', 'h2', 'h3', 'br', 'li'):
            self.parts.append('\n')
        if tag == 'meta' and attrs.get('name') == 'citation_pdf_url':
            self.pdf_links.append(attrs.get('content', ''))
        if tag == 'a' and attrs.get('href', '').split('?')[0].endswith('.pdf'):
            self.pdf_links.append(attrs['href'])

    def handle_endtag(self, tag):
        if tag in ('script', 'style', 'nav', 'footer', 'noscript', 'iframe'):
            self.ignored = max(0, self.ignored - 1)
        if tag == 'title':
            self.in_title = False
        if tag in ('p', 'div', 'section', 'h1', 'h2', 'h3', 'li'):
            self.parts.append('\n')

    def handle_data(self, data):
        if self.in_title:
            self.title += data
        if not self.ignored:
            self.parts.append(data)

    def text(self):
        return '\n'.join(line.strip() for line in ''.join(self.parts).splitlines() if line.strip())
