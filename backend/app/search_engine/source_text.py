"""Decode captured source text without ranking or model verification."""
import io
import re
from urllib.parse import urljoin, urlsplit
from html.parser import HTMLParser
from ..retrieval.extraction import PageRecord
from .fetcher import ArticleHTML, FetchError


class ArxivArticleHTML(ArticleHTML):
    """Extract the labelled LaTeXML document body, excluding site chrome."""
    def __init__(self):
        super().__init__()
        self.stack = []
        self.sections = 0

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        active = bool(self.stack and self.stack[-1][1]) or 'ltx_document' in attrs.get('class', '').split()
        if tag not in ('area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'param', 'source', 'track', 'wbr'):
            self.stack.append((tag, active))
        if active and tag == 'section' and 'ltx_section' in attrs.get('class', '').split():
            self.sections += 1
        if active and tag in ('p', 'div', 'section', 'h1', 'h2', 'h3', 'br', 'li'):
            self.parts.append('\n')
        if tag in ('script', 'style', 'nav', 'footer', 'noscript', 'iframe'):
            self.ignored += 1

    def handle_endtag(self, tag):
        if tag in ('script', 'style', 'nav', 'footer', 'noscript', 'iframe'):
            self.ignored = max(0, self.ignored - 1)
        if self.stack and self.stack[-1][1] and tag in ('p', 'div', 'section', 'h1', 'h2', 'h3', 'li'):
            self.parts.append('\n')
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                del self.stack[i:]
                break

    def handle_data(self, data):
        if self.stack and self.stack[-1][1] and not self.ignored:
            self.parts.append(data)


class PatentClaimsHTML(HTMLParser):
    """Read the labelled claims section, never the surrounding search page."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.sections = 0
        self.parts = []
        self.publication = ''
        self.in_publication = False

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'dd' and attrs.get('itemprop') == 'publicationNumber' and not self.publication:
            self.in_publication = True
        if tag == 'section' and (self.sections or attrs.get('itemprop') == 'claims'):
            self.sections += 1
        if self.sections and tag in ('claim', 'claim-text', 'br', 'p', 'div'):
            self.parts.append('\n')

    def handle_endtag(self, tag):
        if tag == 'dd':
            self.in_publication = False
        if tag == 'section' and self.sections:
            self.sections -= 1

    def handle_data(self, data):
        if self.in_publication:
            self.publication += data.strip()
        if self.sections:
            self.parts.append(data)

def source_from_fetch(fetched):
    from pypdf import PdfReader
    source = {'artifact_id': fetched.artifact_id, 'url': fetched.url, 'scope': 'full_text',
              'content_type': fetched.content_type, 'truncated': False}
    if fetched.content_type == 'application/pdf':
        reader = PdfReader(io.BytesIO(fetched.body))
        if len(reader.pages) > 150:
            raise FetchError('pdf_page_budget')
        pages = [PageRecord(i + 1, page.extract_text() or '') for i, page in enumerate(reader.pages)]
        if sum(p.char_count for p in pages) > 1000000:
            raise FetchError('pdf_text_budget')
        source['title'] = str((reader.metadata or {}).get('/Title', ''))
        from .input_documents import from_specification
        source['document_identifiers'] = [key.removeprefix('doi:') for key in
            from_specification(pages[0].text if pages else '')['identifiers'] if key.startswith('doi:')]
    else:
        parser = ArticleHTML()
        parser.feed(fetched.body.decode('utf-8', errors='replace'))
        text = parser.text()
        if any(marker in text.lower() for marker in ('verify you are human', 'checking your browser', 'enable javascript and cookies')):
            raise FetchError('access_challenge')
        pages = [PageRecord(1, text)]
        source['title'] = parser.title
        source['pdf_urls'] = list(dict.fromkeys(urljoin(fetched.url, link) for link in parser.pdf_links if link))
        metadata = parser.metadata
        source['document_identifiers'] = [metadata[key] for key in
            ('citation_doi', 'citation_arxiv_id') if metadata.get(key)]
        if metadata.get('citation_arxiv_id'):
            source['document_identifiers'] = [metadata.get('citation_doi', ''), 'arxiv:' + metadata['citation_arxiv_id']]
        source['document_links'] = [urljoin(fetched.url, link) for link in
            [*parser.document_links, metadata.get('citation_pdf_url', ''), metadata.get('citation_public_url', '')] if link]
        # Landing pages/snippets are never promoted to full text just for being long.
        source['scope'] = 'page_text'
        url = urlsplit(fetched.url)
        arxiv = re.fullmatch(r'/html/(\d{4}\.\d{4,5}(?:v\d+)?)/*', url.path, re.I)
        if url.hostname in ('arxiv.org', 'www.arxiv.org') and arxiv:
            article = ArxivArticleHTML()
            article.feed(fetched.body.decode('utf-8', errors='replace'))
            body = article.text()
            if article.sections and len(body) >= 100:
                source.update(scope='full_text', section='article', document_number='arxiv:' + arxiv[1])
                source['document_links'].extend('https://arxiv.org/' + form + '/' + arxiv[1]
                                                for form in ('abs', 'pdf', 'html'))
                source['pdf_urls'].append('https://arxiv.org/pdf/' + arxiv[1])
                pages = [PageRecord(1, body)]
        publication = re.fullmatch(r'/patent/([A-Z]{2}\d+[A-Z]\d?)(?:/[a-z-]+)?/?', url.path, re.I)
        if url.hostname == 'patents.google.com' and publication:
            claims = PatentClaimsHTML()
            claims.feed(fetched.body.decode('utf-8', errors='replace'))
            claim_text = ''.join(claims.parts).strip()
            if claims.publication.upper() == publication[1].upper() and len(claim_text) >= 100:
                source.update(scope='claims', section='claims', document_number=claims.publication.upper(),
                              publisher='Google Patents', language='as_served')
                # A labelled claim section needs no unrelated linked-PDF replacement.
                source.pop('pdf_urls', None)
                pages = [PageRecord(1, claim_text)]
    if not pages or sum(p.char_count for p in pages) < 100:
        raise FetchError('text_unavailable')
    return source, pages
