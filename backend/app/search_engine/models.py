from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
import time
from dataclasses import dataclass, asdict, field
from pathlib import Path

from ..search_manifest import identity_key, normalize_url
from ..search_dates import parse_publication_date
from ..patent_search.literature_client import arxiv_identity


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.' + uuid.uuid4().hex + '.tmp')
    with temporary.open('w', encoding='utf-8') as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.flush()
        os.fsync(handle.fileno())
    try:
        for attempt in range(6):
            try:
                os.replace(temporary, path)
                break
            except PermissionError as exc:
                # Windows readers briefly deny replacement while opening the
                # counter/checkpoint. Keep the old complete JSON until retry.
                if getattr(exc, 'winerror', None) not in (5, 32) or attempt == 5:
                    raise
                time.sleep(0.01 * (attempt + 1))
    finally:
        if temporary.exists():
            temporary.unlink()


def identifier(value: str) -> str:
    return hashlib.sha256(value.encode('utf-8')).hexdigest()[:20]


def tokens(text: str) -> list[str]:
    return list(dict.fromkeys(re.findall(r'[a-zA-Z][a-zA-Z0-9-]*|[가-힣]{2,}', text.lower())))


@dataclass
class Feature:
    id: str
    text: str
    terms: list[str] = field(default_factory=list)
    phrases: list[str] = field(default_factory=list)
    queries: list[str] = field(default_factory=list)
    relation: str = ''
    korean_terms: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Limits:
    seconds: int
    queries: int
    pool: int
    documents: int
    llm_calls: int
    input_tokens: int

    @classmethod
    def for_depth(cls, depth: str, values: dict | None = None):
        defaults = {'fast': (45, 6, 80, 3, 3, 60000),
                    'deep': (120, 12, 200, 10, 5, 120000),
                    'exhaustive': (300, 30, 500, 20, 8, 240000)}
        base = defaults.get(depth, defaults['deep'])
        settings = (values or {}).get('progressive_search_limits') or {}
        overrides = settings.get(depth, {}) if isinstance(settings, dict) else {}
        overrides = overrides if isinstance(overrides, dict) else {}
        names = ('seconds', 'queries', 'pool', 'documents', 'llm_calls', 'input_tokens')
        ceilings = (900, 60, 1000, 40, 12, 500000)
        def number(name, default):
            try:
                return int(overrides.get(name, default))
            except (ValueError, TypeError, OverflowError):
                return default
        return cls(*(min(ceiling, max(1, number(name, default)))
                     for name, default, ceiling in zip(names, base, ceilings)))


@dataclass
class Candidate:
    id: str
    document_number: str
    title: str
    url: str
    source: str
    publication_date: str = ''
    family_id: str = ''
    fields: dict = field(default_factory=dict)
    evidence_refs: dict = field(default_factory=dict)
    discoveries: list[dict] = field(default_factory=list)
    evidence: list[dict] = field(default_factory=list)
    document_classification: dict | None = None
    acquisitions: list[dict] = field(default_factory=list)
    score: float = 0.0
    lexical_score: float = 0.0
    verification_score: float = 0.0
    triage_rank: int = 1000000
    data_status: str = 'METADATA_ONLY'
    date_status: str = 'publication_date_unknown'


class Ledger:
    """Only the event-loop owner mutates this ledger; workers return data."""

    def __init__(self, directory: Path, pool_limit: int):
        self.directory = directory
        self.pool_limit = pool_limit
        self.candidates: dict[str, Candidate] = {}
        self.events: list[dict] = []

    def event(self, kind: str, **payload):
        from datetime import datetime, timezone
        row = {'event': kind, 'at': datetime.now(timezone.utc).isoformat(), **payload}
        self.events.append(row)
        self.directory.mkdir(parents=True, exist_ok=True)
        with (self.directory / 'search_trace.jsonl').open('a', encoding='utf-8') as handle:
            handle.write(json.dumps(row, ensure_ascii=False) + '\n')
            handle.flush()
            os.fsync(handle.fileno())

    def add(self, record: dict, *, source: str, query_id: str, feature: str, rank: int):
        number = str(record.get('document_number') or record.get('doi') or '')
        url = normalize_url(record.get('url') or '')
        if not number and not url:
            return None
        key = identity_key(doi=number) if number.lower().startswith('10.') else (
            identity_key(number) if number else 'url:' + url)
        arxiv = arxiv_identity(number) or arxiv_identity(url)
        url_arxiv = arxiv_identity(url)
        if arxiv and not arxiv[1] and url_arxiv and url_arxiv[0] == arxiv[0]:
            arxiv = url_arxiv
        if arxiv:
            # Keep versions separate for evidence/date provenance; merge DOI/URL
            # spelling only when both identify the same version (or no version).
            key = 'arxiv:' + arxiv[0] + arxiv[1]
        cid = identifier(key)
        fields = dict(record.get('fields') or {})
        if cid not in self.candidates:
            if len(self.candidates) >= self.pool_limit:
                self.event('candidate_deferred', identity=key, reason='pool_budget', query_id=query_id)
                return None
            self.candidates[cid] = Candidate(cid, number, str(record.get('title') or ''), url, source)
        candidate = self.candidates[cid]
        candidate.title = candidate.title or str(record.get('title') or '')
        candidate.url = candidate.url or url
        # Keep source provenance and dates; do not substitute a family member's fields.
        date = str(record.get('publication_date') or fields.get('publication_date') or '')
        normalized, precision = parse_publication_date(date)
        if normalized:
            fields['publication_date_raw'] = date
            fields['publication_date_precision'] = precision
            date = normalized
        if date and not candidate.publication_date:
            candidate.publication_date = date
        elif date and date != candidate.publication_date:
            self.event('date_conflict', candidate=cid, previous=candidate.publication_date, incoming=date, source=source)
        candidate.family_id = str(fields.get('family_id') or candidate.family_id)
        candidate.fields.update(fields)
        candidate.evidence_refs.update(record.get('evidence_refs') or {})
        discovery = {'source': source, 'query_id': query_id, 'feature': feature, 'rank': rank}
        if discovery not in candidate.discoveries:
            candidate.discoveries.append(discovery)
        if any(k.startswith('abstract') and v for k, v in fields.items()):
            if candidate.data_status == 'METADATA_ONLY':
                candidate.data_status = 'ABSTRACT_ONLY'
        self.event('candidate_discovered', candidate=cid, **discovery)
        return candidate

    def save(self):
        write_json(self.directory / 'candidates.json', [asdict(c) for c in self.candidates.values()])
