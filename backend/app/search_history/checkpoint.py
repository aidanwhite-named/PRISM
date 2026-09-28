"""Translate saved search documents for a user-requested continuation."""
def findings(snapshot):
    if 'findings' in snapshot:
        return snapshot['findings']
    return [{'title': c.get('title', ''), 'url': c['url'],
             'document_number': c.get('document_number', ''),
             'reason': (c.get('document_classification') or {}).get('reason', ''),
             'reported_scope': '이전 검색 기록'}
            for c in snapshot.get('candidates', []) if c.get('url')]
