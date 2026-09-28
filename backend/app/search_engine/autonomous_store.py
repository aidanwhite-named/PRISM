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
    for key in ('reason', 'difference', 'reported_scope', 'publication_date', 'authors',
                'triage_status', 'triage_reason', 'core_matches', 'review_stage'):
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
def _lock(directory):
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / 'search_findings.lock').open('a+b') as handle:
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


def merge(directory, records, *, ranked=False):
    with _lock(directory):
        return _merge(directory, records, ranked=ranked)


def _merge(directory, records, *, ranked=False):
    # Validate the entire update before replacing the previous checkpoint.
    incoming = [normalize(row) for row in records]
    def key(row):
        return row['document_number'].casefold() or row['url']
    previous = {key(row): row for row in load(directory)}
    updated = {key(row): {**previous.get(key(row), {}), **row} for row in incoming}
    # The model may put its preferred findings first, without discarding other leads.
    merged = {**updated, **{k: v for k, v in previous.items() if k not in updated}} if ranked else {**previous, **updated}
    rows = list(merged.values())
    write_json(directory / FINDINGS_FILE, {'records': rows})
    return rows


def save_findings(tools, arguments):
    rows = merge(tools.work_dir, arguments['records'], ranked=arguments.get('ranked', False))
    return {'saved_findings': len(rows), 'scope_note': 'Saved in model order; source access and model-reported assessment are recorded separately.'}
