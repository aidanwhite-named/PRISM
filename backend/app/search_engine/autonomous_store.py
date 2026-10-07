"""Unbounded, model-ordered findings. This is storage, not a relevance gate."""
from __future__ import annotations

import json
import os
from contextlib import contextmanager

from .storage import write_json
from .web_progress import finding

FINDINGS_FILE = 'search_findings.json'


def normalize(raw):
    row = finding(raw)
    if row is None:
        raise ValueError('A finding needs a title and a valid public HTTPS source URL matching its identifier.')
    row.pop('review', None)
    for key in ('reason', 'difference', 'reported_scope', 'publication_date', 'authors',
                'triage_reason', 'core_matches'):
        row[key] = raw.get(key, '') if isinstance(raw.get(key, ''), str) else ''
    row['reason'] = row['reason'] or row['snippet']
    row['triage_status'] = row.get('triage_status') or 'unreviewed'
    row['review_stage'] = row.get('review_stage') or 'metadata'
    return row


def load(directory):
    path = directory / FINDINGS_FILE
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding='utf-8'))['records']


@contextmanager
def _lock(directory, filename='search_findings.lock'):
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / filename).open('a+b') as handle:
        handle.seek(0, 2)
        if handle.tell() == 0:
            handle.write(b'0')
            handle.flush()
        handle.seek(0)
        if os.name == 'nt':
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl
            fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == 'nt':
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_UN)


def merge(directory, records, *, ranked=False, failures=None):
    with _lock(directory):
        return _merge(directory, records, ranked=ranked, failures=failures)


def _merge(directory, records, *, ranked=False, failures=None):
    from .input_documents import filter_records
    from .document_identity import DocumentIndex, primary_identity
    from .. import search_manifest
    from .storage import identifier
    records = filter_records(directory, records)
    incoming = []
    for raw in records:
        try:
            incoming.append((raw, normalize(raw)))
        except ValueError as exc:
            if failures is None:
                raise
            failures.append({'title': raw.get('title', ''), 'url': raw.get('url', ''),
                             'code': 'invalid_finding', 'detail': str(exc)})
    index = DocumentIndex(search_manifest.read_tool_journal(directory))
    rows = []

    def combine(old, row):
        merged = {**old, **row}
        merged['document_id'] = old.get('document_id') or row.get('document_id') or identifier(
            primary_identity(old or row) or (old or row)['url'])
        merged['source_urls'] = list(dict.fromkeys([*old.get('source_urls', []),
            old.get('url', ''), *row.get('source_urls', []), row['url']]))
        merged['source_urls'] = [url for url in merged['source_urls'] if url]
        numbers = list(dict.fromkeys(n.strip() for value in
            (old.get('document_number', ''), row.get('document_number', ''))
            for n in value.split(';') if n.strip()))
        merged['document_number'] = '; '.join(numbers)
        # A later provisional save must not erase an already validated result.
        previous = old.get('search_review') or {}
        current = row.get('search_review') or {}
        if current.get('component_matches'):
            from .review_context import assessment_status, assessment_verdict, review_state
            prior = {m['component_id']: m for m in previous.get('component_matches', [])}
            matches = []
            for match in current['component_matches']:
                saved_match = prior.pop(match['component_id'], {})
                if (saved_match.get('status') == 'source_checked' and match.get('status') != 'source_checked'
                        or saved_match and review_state(saved_match) != 'unreviewed' and review_state(match) == 'unreviewed'):
                    match = {**saved_match, 'last_attempt': match}
                matches.append(match)
            matches.extend(prior.values())
            merged['search_review'] = {**current, 'component_matches': matches,
                'status': 'needs_review' if current.get('context_issues') else assessment_status(matches),
                'verdict': assessment_verdict(matches),
                'issues': [*current.get('context_issues', []), *(issue for m in matches for issue in m.get('issues', []))]}
            return merged
        if previous.get('component_matches'):
            merged['search_review'] = previous
            if current:
                merged['last_review_attempt'] = current
            return merged
        if (old.get('url') and old['url'] != row['url'] and old.get('triage_status') != 'rejected'
                and row.get('triage_status') == 'rejected' and not current):
            # A duplicate source rejected in discovery is an alias of the
            # existing candidate, not a reason to reject that whole document.
            for field in ('triage_status', 'triage_reason', 'reason', 'core_matches'):
                if field in old:
                    merged[field] = old[field]
        if previous.get('status') == 'source_checked' and current.get('status') != 'source_checked':
            merged['search_review'] = previous
            if current:
                merged['last_review_attempt'] = current
        elif not current and previous:
            merged['search_review'] = previous
        return merged

    def upsert(row):
        matches = [i for i, old in enumerate(rows) if index.matches(old, row)]
        if not matches:
            row = combine({}, row)
            rows.append(row)
            return row
        position = matches[0]
        old = rows[position]
        for i in matches[1:]:
            old = combine(old, rows[i])
        merged = combine(old, row)
        for i in reversed(matches[1:]):
            rows.pop(i)
        rows[position] = merged
        return merged

    for row in filter_records(directory, load(directory)):
        upsert(row)
    order = []
    for raw, row in incoming:
        old = next((old for old in rows if index.matches(old, row)), {})
        # Partial saves must not erase earlier assessments with default values.
        for field in ('triage_status', 'review_stage', 'triage_reason', 'core_matches',
                      'reason', 'difference', 'reported_scope', 'publication_date', 'authors', 'snippet', 'feature'):
            if field not in raw and field in old:
                row[field] = old[field]
        # Review-only updates may omit the previously confirmed DOI/arXiv
        # identifiers. Resolve against the whole stored document, not just the
        # minimal row the model sends this time.
        row = combine(old, row)
        if 'review' in raw:
            from .search_review import validate
            try:
                row['search_review'] = validate(directory, row, raw['review'])
            except Exception as exc:
                if failures is None:
                    raise
                detail = f'{type(exc).__name__}: {exc}'
                row['search_review'] = {'status': 'needs_review',
                    'issues': ['문헌별 분류 저장 처리 오류: ' + detail]}
                failures.append({'title': row['title'], 'url': row['url'],
                                 'code': 'review_save_failed', 'detail': detail})
        saved = upsert(row)
        if saved['document_id'] not in order:
            order.append(saved['document_id'])
        write_json(directory / FINDINGS_FILE, {'records': rows})
    if ranked:
        rows.sort(key=lambda r: order.index(r['document_id']) if r['document_id'] in order else len(order))
    write_json(directory / FINDINGS_FILE, {'records': rows})
    return rows


def save_findings(tools, arguments):
    failures = []
    rows = merge(tools.work_dir, arguments['records'], ranked=arguments.get('ranked', False), failures=failures)
    from .search_review import feedback
    from .review_context import load as review_context
    from .input_documents import exclusions
    return {'saved_findings': len(rows), 'failed_findings': failures, 'saved_reviews': [
                {'document_id': row['document_id'], 'url': row['url'],
                 'status': row['search_review']['status'], 'group': row['search_review'].get('group'),
                 'component_matches': row['search_review'].get('component_matches', [])}
                for row in rows if row.get('search_review')],
            'excluded_input_documents': exclusions(tools.work_dir),
            **feedback(rows, cutoff=tools.cutoff, focus=review_context(tools.work_dir)),
            'scope_note': '원문 문자 검증과 AI의 기술적 유사성 판단은 구분됩니다. 미검토·검증 보완 항목은 현재 상태이며 추가 실행을 요구하지 않습니다.'}
