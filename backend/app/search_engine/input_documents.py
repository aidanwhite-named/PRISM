"""Identify supplied documents without treating their references as inputs."""
import json
import re

from .source_observations import _identity
from .storage import write_json

CONTEXT_FILE = 'search_input_documents.json'
EXCLUSIONS_FILE = 'search_input_exclusions.json'


def from_specification(text):
    # PDF reference sections must never turn cited prior art into exclusions.
    first_page = re.split(r'(?m)^--- PAGE (?!1 ---)\d+ ---\s*$', text, maxsplit=1)[0]
    header = re.split(r'(?im)^\s*(?:references|bibliography|참고문헌)\s*$', first_page, maxsplit=1)[0]
    metadata = '\n'.join(line for line in header.splitlines() if re.search(
        r'(?i)digital object identifier|citation information:\s*doi|^\s*doi\s*:|^\s*https?://(?:dx\.)?doi\.org/', line))
    identifiers = []
    for match in re.finditer(r'10\.\s*((?:\d\s*){4,9})/\s*([^\s<>"\x00]+)', metadata):
        doi = '10.' + re.sub(r'\s+', '', match[1]) + '/' + match[2].rstrip('.,;)]}')
        key = _identity({'doi': doi})
        if key and key not in identifiers:
            identifiers.append(key)
    titles = []
    # A labelled DOI header followed by title lines and an author line is a
    # strong bibliographic cue; arbitrary prose is never used as a title.
    lines = header.splitlines()
    for i, line in enumerate(lines):
        if not re.search(r'(?i)^(?:digital object identifier|doi\s*:)', line.strip()):
            continue
        title = []
        for line in lines[i + 1:i + 9]:
            line = line.strip()
            if not line:
                continue
            if re.search(r'@|\d|\b(?:Member|IEEE|University)\b', line) or (line.isupper() and ',' in line):
                break
            title.append(line)
        value = ' '.join(title)
        if len(value) >= 40:
            titles.append(value)
    return {'identifiers': identifiers, 'titles': titles, 'urls': []}


def load(directory):
    path = directory / CONTEXT_FILE
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}


def title_key(value):
    return ''.join(c for c in value.casefold() if c.isalnum())


def matches(row, context):
    key = _identity(row)
    if key and key in context.get('identifiers', []):
        return True
    url = row.get('url', '')
    if url and url in context.get('urls', []):
        return True
    title = title_key(row.get('title', ''))
    return bool(title and any(title == title_key(t) for t in context.get('titles', [])))


def is_input(directory, row):
    if matches(row, load(directory)):
        return True
    url = row.get('url')
    return bool(url and any(url == r.get('url') or url in r.get('aliases', []) for r in exclusions(directory)))


def exclusions(directory):
    path = directory / EXCLUSIONS_FILE
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else []


def record_exclusion(directory, row):
    from .autonomous_store import _lock
    with _lock(directory, 'search_input_exclusions.lock'):
        rows = exclusions(directory)
        before = json.dumps(rows, ensure_ascii=False)
        item = {key: row.get(key, '') for key in ('title', 'url', 'document_number')}
        item['reason'] = '입력으로 제공한 동일 문헌'
        identity = _identity(row) or row.get('url') or title_key(row.get('title', ''))
        old = next((r for r in rows if
            (_identity(r) or r.get('url') or title_key(r.get('title', ''))) == identity
            or (item['title'] and title_key(r.get('title', '')) == title_key(item['title']))
            or (item['url'] and (item['url'] == r.get('url') or item['url'] in r.get('aliases', [])))), None)
        if old is None:
            rows.append(item)
        else:
            for key in ('title', 'url', 'document_number'):
                if not old.get(key) and item.get(key):
                    old[key] = item[key]
            if item['url'] and item['url'] != old['url'] and item['url'] not in old.get('aliases', []):
                old.setdefault('aliases', []).append(item['url'])
        if json.dumps(rows, ensure_ascii=False) != before:
            write_json(directory / EXCLUSIONS_FILE, rows)


def filter_records(directory, rows):
    context = load(directory)
    known_urls = {url for r in exclusions(directory) for url in [r.get('url'), *r.get('aliases', [])] if url}
    kept = []
    for row in rows:
        if matches(row, context) or (row.get('url') and row['url'] in known_urls):
            record_exclusion(directory, row)
        else:
            kept.append(row)
    return kept


def fetch_target(name, arguments):
    if name == 'source_fetch':
        return {'url': arguments['url']}
    if name == 'literature_fetch':
        return {'document_number': arguments['doi']}
    if name == 'epo_fetch':
        return {'document_number': arguments['publication_number']}
    return None
