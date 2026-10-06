"""Derive obtained material from returned fields, never from a requested scope."""
from urllib.parse import unquote, urlsplit
import re

from .. import search_manifest
from .document_identity import DocumentIndex, primary_identity


SCOPE_LABELS = {
    'bibliographic': '서지정보', 'abstract': '초록', 'claims': '청구항',
    'description': '명세서 설명', 'full_text': '본문', 'page_text': '웹페이지 텍스트',
}


def record_scopes(record):
    fields = record.get('fields') or {}
    scopes = [name for name in SCOPE_LABELS if name != 'bibliographic' and any(
        key.split(':')[0] == name and isinstance(value, str) and value.strip()
        for key, value in fields.items())]
    if not scopes and (record.get('title') or record.get('document_number') or fields):
        scopes = ['bibliographic']
    return scopes


def _identity(record):
    exact = primary_identity(record)
    if exact:
        return exact
    number = str(record.get('document_number') or record.get('doi') or '').strip()
    if number:
        if number.lower().startswith(('10.', 'doi:', 'https://doi.org/', 'http://doi.org/', 'arxiv:')):
            return search_manifest.identity_key(doi=number)
        return search_manifest.identity_key(number)
    try:
        parts = urlsplit(record.get('url') or '')
        if parts.hostname in ('doi.org', 'dx.doi.org'):
            return search_manifest.identity_key(doi=unquote(parts.path.lstrip('/')))
        if parts.hostname == 'patents.google.com':
            match = re.fullmatch(r'/patent/([A-Z]{2}\d+[A-Z]\d?)(?:/[a-z-]+)?/?', parts.path, re.I)
            if match:
                return search_manifest.identity_key(match[1])
    except ValueError:
        pass
    return ''


def candidate_observation(candidate, calls):
    receipts, scopes = [], []
    index = DocumentIndex(calls)
    for call in calls:
        result = call.get('result') or {}
        if call.get('ok') is not True or result.get('identifier_matched') is False:
            continue
        for record in result.get('records', []):
            if not index.matches(candidate, record):
                continue
            observed = record_scopes(record)
            scopes.extend(observed)
            receipts.append({'call_id': call.get('id', ''), 'tool': call.get('tool', ''),
                'scope': ', '.join(observed) or 'not_available',
                'requested_scope': result.get('requested_scope', result.get('verification_scope', '')),
                'observed_scopes': observed,
                'evidence_refs': record.get('evidence_refs', {}),
                'raw_artifact_id': result.get('raw_artifact_id', '')})
    scopes = [scope for scope in SCOPE_LABELS if scope in scopes]
    material = [scope for scope in scopes if scope != 'bibliographic']
    if material:
        label = '·'.join(SCOPE_LABELS[s] for s in material) + ' 확보'
        if material == ['abstract']:
            label += ' · 본문 확보 기록 없음'
    elif scopes:
        label = '서지정보만 확보 · 초록·본문 확보 기록 없음'
    else:
        label = '확보 범위 기록 없음'
    return {'source_receipts': receipts, 'observed_scopes': scopes, 'observed_scope': label}


def with_observations(snapshot):
    """Refresh historical presentation without rewriting stored model statements."""
    if 'source_calls' not in snapshot:
        return snapshot
    return {**snapshot, 'candidates': [
        {**c, **candidate_observation(c, snapshot['source_calls'])}
        for c in snapshot.get('candidates', [])]}
