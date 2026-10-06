"""Public repository discovery and actionable alternatives to an unavailable DOI."""
import json
import re
from datetime import datetime, timezone
from urllib.parse import urljoin, urlsplit

from ..patent_search.literature_client import normalize_doi, LiteratureError


def public_url(value):
    if not isinstance(value, str) or not value:
        return ''
    try:
        p = urlsplit(value)
        return value if (p.scheme == 'https' and p.hostname and not p.username
                         and not p.password and p.port in (None, 443)
                         and not any(ord(c) < 33 for c in value)) else ''
    except ValueError:
        return ''


def fallback_searches(title, doi):
    title = ' '.join(str(title or '').split()).replace('"', '').strip()[:350]
    phrase = title or doi
    result = []
    if title:
        result += [
            {'tool': 'literature_search', 'arguments': {'source': 'arxiv', 'query': f'ti:"{title}"', 'max_results': 5}},
            {'tool': 'literature_search', 'arguments': {'source': 'openreview', 'query': title,
                                                     'openreview_mode': 'exact_title', 'max_results': 5}},
        ]
    result += [{'tool': 'web_search', 'query': f'"{phrase}" site:{domain}'} for domain in
               ('arxiv.org', 'openreview.net', 'openaccess.thecvf.com', 'aclanthology.org', 'core.ac.uk')]
    result.append({'tool': 'web_search', 'query': f'"{phrase}" author manuscript PDF repository'})
    return result


def openreview_search(tools, arguments):
    from ..config import PATHS
    from ..patent_search.artifacts import ArtifactStore
    call = tools._backend('literature')._require_client().search_openreview(
        arguments['query'], rows=arguments.get('max_results', 10),
        mode=arguments.get('openreview_mode', 'title_terms'))
    store = ArtifactStore(PATHS.evidence_dir)
    aid = store.put(call.body)
    body = json.loads(call.body)
    if not isinstance(body, dict) or not isinstance(body.get('notes'), list):
        raise ValueError('invalid_openreview_search_response')
    records = []
    for i, note in enumerate(body['notes'][:arguments.get('max_results', 10)]):
        if not isinstance(note, dict) or note.get('replyto') or note.get('ddate'):
            continue
        ident, content = note.get('id'), note.get('content') or {}
        if not isinstance(ident, str) or not re.fullmatch(r'[A-Za-z0-9_-]+', ident) or not isinstance(content, dict):
            continue
        def value(key):
            field = content.get(key)
            return field.get('value') if isinstance(field, dict) else field
        title = value('title')
        if not isinstance(title, str) or not title.strip():
            continue
        fields, refs = {'title': title}, {}
        for key in ('title', 'abstract'):
            text = value(key)
            if not isinstance(text, str) or not text.strip():
                continue
            fields[key] = text
            refs[key] = {'artifact_id': aid,
                'field_path': f'notes/{i}/content/{key}' + ('/value' if isinstance(content[key], dict) else ''),
                'profile_id': 'generic_json'}
        forum = f'https://openreview.net/forum?id={ident}'
        raw_pdf = value('pdf')
        pdf = public_url(urljoin('https://openreview.net', raw_pdf)) if isinstance(raw_pdf, str) and raw_pdf.strip() else ''
        html = public_url(str(value('html') or ''))
        # Only the note's own paper links; no references or review attachments.
        links = list(dict.fromkeys(link for link in (forum, pdf, html) if link))
        number = ''
        for link in (str(value('doi') or ''), html, pdf):
            try:
                number = normalize_doi(link)
                break
            except LiteratureError:
                pass
        date = ''
        if isinstance(note.get('pdate'), (int, float)) and note['pdate'] > 0:
            try:
                date = datetime.fromtimestamp(note['pdate'] / 1000, timezone.utc).date().isoformat()
            except (ValueError, OverflowError, OSError):
                pass
        records.append({'document_number': number, 'title': title, 'url': forum,
            'publication_date': date, 'fields': fields, 'evidence_refs': refs,
            'source_urls': links, 'document_links': links, 'pdf_urls': [pdf] if pdf else [],
            'repository_note_id': ident})
    return {'records': records, 'total_found': body.get('count'), 'raw_artifact_id': aid,
        'request_url': call.url, 'http_status': call.status, 'source_kind': 'openreview_search',
        'verification_scope': 'bibliographic_search', 'untrusted_external_data': True,
        'query': arguments['query'], 'search_mode': arguments.get('openreview_mode', 'title_terms'),
        'publication_cutoff': tools.cutoff or None,
        'scope_note': 'Public OpenReview paper metadata only, including preprints/imported records. Search hits and PDF links are not body evidence. Fetch each selected paper PDF with source_fetch; confirm identity, version and public date.'}
