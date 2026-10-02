"""Acquire selected technical sources without an additional model call.

Landing pages are never treated as full text. Public PDFs and labelled Google
Patents description/claims are handed to the normal attachment ingestion path.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit
from urllib3.exceptions import HTTPError
from pypdf.errors import PdfReadError

from ..config import PATHS
from ..enums import AttachmentRole
from ..ingestion.service import IngestionLimits, ingest_one
from ..patent_search.artifacts import ArtifactStore, ArtifactError
from .fetcher import ArticleHTML, Fetched, FetchError, SafeFetcher
from .source_text import PatentClaimsHTML, source_from_fetch
from .storage import identifier, write_json


class DescriptionHTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.depth = 0
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag == 'section' and (self.depth or dict(attrs).get('itemprop') == 'description'):
            self.depth += 1
        if self.depth and tag in ('p', 'div', 'br', 'heading'):
            self.parts.append('\n')

    def handle_endtag(self, tag):
        if tag == 'section' and self.depth:
            self.depth -= 1

    def handle_data(self, data):
        if self.depth:
            self.parts.append(data)


class CitationHTML(ArticleHTML):
    def __init__(self):
        super().__init__()
        self.citation_pdfs = []

    def handle_starttag(self, tag, attrs):
        super().handle_starttag(tag, attrs)
        values = dict(attrs)
        if tag == 'meta' and values.get('name', '').lower() == 'citation_pdf_url':
            self.citation_pdfs.append(values.get('content', ''))


def acquire(candidate, search_dir, work_dir):
    """Return a usable attachment and provenance, or a reason for holding it."""
    result = {'candidate_id': candidate['id'], 'title': candidate['title'],
              'url': candidate['url'], 'status': 'hold'}
    store = ArtifactStore(PATHS.evidence_dir)
    fetcher = SafeFetcher(store)
    cache_dir = search_dir / 'comparison-sources'
    cache_dir.mkdir(parents=True, exist_ok=True)

    def fetch(url):
        # Reuse the search tool's immutable raw response, never its truncated text window.
        for section in ('page', 'claims', 'full_text'):
            prior = search_dir / ('source-' + identifier(url + section) + '.json')
            if prior.exists():
                try:
                    capture = json.loads(prior.read_text(encoding='utf-8'))
                    raw = store.read(capture['raw_artifact_id'])
                    return Fetched(capture['url'], raw,
                                   'application/pdf' if raw.startswith(b'%PDF-') else 'text/html',
                                   capture['raw_artifact_id'])
                except (ValueError, OSError, KeyError, ArtifactError):
                    pass
        key = hashlib.sha256(url.encode()).hexdigest()
        cache = cache_dir / (key + '.json')
        if cache.exists():
            try:
                record = json.loads(cache.read_text(encoding='utf-8'))
                return Fetched(record['url'], store.read(record['artifact_id']),
                               record['content_type'], record['artifact_id'])
            except (ValueError, OSError, KeyError, ArtifactError):
                pass
        fetched = fetcher.get(url)
        write_json(cache, {'url': fetched.url, 'artifact_id': fetched.artifact_id,
                          'content_type': fetched.content_type})
        return fetched

    try:
        fetched = fetch(candidate['url'])
        body = fetched.body
        extension = '.pdf'
        scope = 'pdf_text'
        if fetched.content_type != 'application/pdf':
            html = body.decode('utf-8', errors='replace')
            article = CitationHTML()
            article.feed(html)
            claims, description = PatentClaimsHTML(), DescriptionHTML()
            claims.feed(html)
            description.feed(html)
            technical = ''.join(description.parts).strip()
            number = candidate.get('document_number', '').upper()
            if (urlsplit(fetched.url).hostname == 'patents.google.com' and number
                    and claims.publication and claims.publication.upper() != number):
                raise FetchError('검색 후보와 본문의 공개번호가 다릅니다. 원문을 확인하십시오.')
            if (urlsplit(fetched.url).hostname in ('arxiv.org', 'www.arxiv.org')
                    and urlsplit(fetched.url).path.startswith('/html/')):
                source, pages = source_from_fetch(fetched)
                def identity(value):
                    match = re.search(r'(?:^|arxiv:\s*)(\d{4}\.\d{4,5})(?:v\d+)?', value.lower())
                    return match[1] if match else ''
                if source['scope'] != 'full_text':
                    raise FetchError('arXiv의 본문 영역을 확인하지 못했습니다. 원문 PDF를 첨부해 분석할 수 있습니다.')
                if number and identity(source.get('document_number', '')) != identity(number):
                    raise FetchError('검색 후보와 본문의 arXiv 번호가 다릅니다. 원문을 확인하십시오.')
                scope, extension = 'article_html', '.txt'
                body = (f"{candidate['title']}\n문헌번호: {source['document_number']}\n출처: {fetched.url}\n"
                        '[논문 HTML 본문 · 도면 이미지 제외]\n' + '\n\n'.join(page.text for page in pages)).encode('utf-8')
            elif (urlsplit(fetched.url).hostname == 'patents.google.com' and number
                    and claims.publication.upper() == number and len(technical) >= 100):
                scope, extension = 'description_and_claims', '.txt'
                body = (f"{candidate['title']}\n공개번호: {number}\n출처: {fetched.url}\n"
                        f"[설명]\n{technical}\n[청구항]\n{''.join(claims.parts)}").encode('utf-8')
            else:
                # Follow only an explicit PDF link; no guessed publisher endpoints.
                # Generic PDF hyperlinks may be references or supplemental material.
                # Only publisher-labelled citation PDFs qualify on literature pages.
                pdf_links = (article.pdf_links if urlsplit(fetched.url).hostname == 'patents.google.com'
                             and number and claims.publication.upper() == number else article.citation_pdfs)
                links = list(dict.fromkeys(urljoin(fetched.url, link) for link in pdf_links if link))
                fetched_pdf = None
                for link in links[:2]:
                    try:
                        document = fetch(link)
                        if document.content_type == 'application/pdf':
                            fetched_pdf = document
                            break
                    except (ValueError, OSError, HTTPError):
                        continue
                if fetched_pdf is None:
                    raise FetchError('초록·서지 페이지에서 읽을 수 있는 본문을 확보하지 못했습니다. 원문 PDF를 첨부해 분석할 수 있습니다.')
                fetched, body = fetched_pdf, fetched_pdf.body
        name = 'source-' + hashlib.sha256(candidate['id'].encode()).hexdigest()[:12] + extension
        item = ingest_one(name, body, work_dir, True, IngestionLimits(), role=AttachmentRole.CITATION)
        if not item.read_ok:
            raise FetchError(item.error or '문헌의 텍스트를 추출하지 못했습니다.')
        result.update(status='ready', attachment_id=item.attachment_id, filename=name,
                      fetched_url=fetched.url, artifact_id=fetched.artifact_id,
                      scope=scope, sha256=item.sha256,
                      reason=('논문 HTML 본문 확보(도면 이미지 제외).' if scope == 'article_html' else
                              '설명·청구항 텍스트 확보(도면 제외).' if scope == 'description_and_claims'
                              else 'PDF 텍스트 확보(원문 완전성 미검증).') + ' 구성 대응 여부는 분석에서 판단합니다.')
        return asdict(item), result
    except (ValueError, OSError, HTTPError, PdfReadError, ArtifactError) as exc:
        result['reason'] = str(exc)[:500]
        return None, result
