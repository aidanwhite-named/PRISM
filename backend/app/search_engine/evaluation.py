"""Small, explicit known-relevant evaluation; unjudged documents are not negatives."""
from __future__ import annotations
from datetime import datetime

from ..patent_search.literature_client import arxiv_identity
from .categories import normalize


def work_key(value):
    arxiv = arxiv_identity(value)
    if arxiv:
        return 'arxiv:' + arxiv[0]
    return str(value).lower().removeprefix('https://doi.org/').replace(' ', '')


def metrics(manifest, case, k=20):
    engine = manifest.get('engine') or {}
    candidates = engine.get('candidates') if engine else (manifest.get('reported') or {}).get('candidates', [])
    candidates = candidates or []
    ranks = []
    for relevant in case['known_relevant']:
        expected = {work_key(identity) for identity in relevant['identifiers']}
        found = None
        for rank, candidate in enumerate(candidates, 1):
            observed = {work_key(candidate.get(key, '')) for key in ('document_number', 'doc_number', 'doi', 'url')}
            if expected & observed or (relevant.get('family_id') and relevant['family_id'] == candidate.get('family_id')):
                found = rank
                break
        ranks.append({'label': relevant['label'], 'rank': found, 'judgment': relevant['judgment']})
    hits = sum(row['rank'] is not None and row['rank'] <= k for row in ranks)
    elapsed = engine.get('elapsed_seconds')
    if not engine:
        try:
            elapsed = (datetime.fromisoformat(manifest['completed_at']) - datetime.fromisoformat(manifest['started_at'])).total_seconds()
        except (KeyError, ValueError, TypeError):
            pass
    evidence_key = 'evidence' if engine else 'mapping'
    return {'case': case['id'], 'k': k, 'known_relevant_recall_at_k': hits / len(ranks) if ranks else None,
        'known_relevant_ranks': ranks, 'candidate_count': len(candidates),
        'classification': engine.get('classification') if engine else manifest.get('deadline_classification'),
        'classified_documents': sum(bool(normalize((c.get('document_classification') or {}).get('group') if engine else c.get('group'))) for c in candidates),
        'insufficient_information_documents': sum((c.get('document_classification') or {}).get('status') == 'insufficient_information' if engine else c.get('note', '').startswith('자료 부족:') for c in candidates),
        'low_relevance_documents': sum((c.get('document_classification') or {}).get('status') == 'low_relevance' if engine else c.get('note', '').startswith('관련성 낮음:') for c in candidates),
        'first_candidate_seconds': engine.get('first_candidate_seconds'),
        'elapsed_seconds': elapsed, 'usage': engine.get('usage') or manifest.get('usage'),
        'verified_evidence_rows': sum(sum(bool(e.get('quote_verified')) for e in c.get(evidence_key, [])) for c in candidates),
        'relation_rows': sum(len(c.get(evidence_key, [])) for c in candidates),
        'full_text_documents': sum(c.get('data_status') == 'FULL_TEXT' for c in candidates),
        'query_cache_hits': sum(bool(q.get('cache_hit')) for q in engine.get('queries', [])),
        'limits': ['Partial relevance judgments, not anticipation/novelty findings.',
                   'Known-relevant set is incomplete; no corpus-wide recall or precision claimed.',
                   'Single runs and source/model/cache differences must be reported separately.']}
