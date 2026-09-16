"""Small, explicit known-relevant evaluation; unjudged documents are not negatives."""
from __future__ import annotations

from ..patent_search.literature_client import arxiv_identity


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
    return {'case': case['id'], 'k': k, 'known_relevant_recall_at_k': hits / len(ranks) if ranks else None,
        'known_relevant_ranks': ranks, 'candidate_count': len(candidates),
        'first_candidate_seconds': engine.get('first_candidate_seconds'),
        'elapsed_seconds': engine.get('elapsed_seconds'), 'usage': engine.get('usage') or manifest.get('usage'),
        'verified_evidence_rows': sum(sum(bool(e.get('quote_verified')) for e in c.get('evidence', [])) for c in candidates),
        'relation_rows': sum(len(c.get('evidence', [])) for c in candidates),
        'full_text_documents': sum(c.get('data_status') == 'FULL_TEXT' for c in candidates),
        'query_cache_hits': sum(bool(q.get('cache_hit')) for q in engine.get('queries', [])),
        'limits': ['Partial relevance judgments, not anticipation/novelty findings.',
                   'Known-relevant set is incomplete; no corpus-wide recall or precision claimed.',
                   'Single runs and source/model/cache differences must be reported separately.']}
