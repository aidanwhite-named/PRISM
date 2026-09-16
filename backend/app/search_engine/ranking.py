from __future__ import annotations

import math
import re
from collections import Counter
from .models import tokens
from ..search_dates import evaluate as evaluate_date


def metadata_excerpt(text, features, budget=700):
    """Keep operation-bearing sentences at the end of abstracts, too."""
    sentences = [s.strip() for s in re.split(r'(?<=[.!?])\s+', text) if s.strip()]
    wanted = set(t for f in features for t in f.terms)
    ordered = sorted(enumerate(sentences), key=lambda pair: (
        -len(set(tokens(pair[1])) & wanted), pair[0]))
    selected, size = [], 0
    for index, sentence in ordered:
        remaining = budget - size
        if remaining < 80:
            break
        selected.append((index, sentence[:remaining]))
        size += len(selected[-1][1]) + 1
    return ' '.join(sentence for _, sentence in sorted(selected))


def rank(candidates, features, cutoff='', rrf_k=60):
    candidates = list(candidates)
    documents = [set(tokens(c.title + ' ' + ' '.join(str(v) for v in c.fields.values()))) for c in candidates]
    df = Counter(term for doc in documents for term in doc)
    for candidate, words in zip(candidates, documents):
        # Source rank is fused, never treated as a cosine probability.
        lists = {}
        for hit in candidate.discoveries:
            key = (hit['source'], hit['query_id'])
            lists[key] = min(lists.get(key, 10**6), hit['rank'])
        rrf = sum(1 / (rrf_k + position) for position in lists.values())
        coverage = []
        for feature in features:
            weights = {term: math.log(1 + (len(candidates) + 1) / (df[term] + 1)) for term in feature.terms}
            coverage.append(sum(w for term, w in weights.items() if term in words) / (sum(weights.values()) or 1))
        candidate.lexical_score = round(max(coverage, default=0), 5)
        matched = {row['feature']: {'explicit': 1, 'semantic': .8, 'partial': .4}.get(row['match'], 0)
                   for row in candidate.evidence if row.get('quote_verified')}
        candidate.verification_score = sum(matched.values()) / max(1, len(features))
        candidate.score = round(rrf, 6)
        candidate.date_status = evaluate_date(candidate.publication_date, cutoff).status
    def negative(c):
        return bool(features) and all(any(e['feature'] == f.id and e['match'] == 'absent'
                                         for e in c.evidence) for f in features)
    return sorted(candidates, key=lambda c: (negative(c), -c.verification_score, c.triage_rank, -c.lexical_score, -c.score, c.id))


def diverse_top(candidates, features, limit):
    """Preserve feature discoveries and then fill with family-diverse rankings."""
    selected = []
    families = set()
    def add(candidate):
        family = candidate.family_id or candidate.id
        if family in families or candidate in selected or len(selected) >= limit:
            return
        selected.append(candidate)
        families.add(family)
    for feature in features:
        hit = next((c for c in candidates if any(d['feature'] == feature.id for d in c.discoveries)
                    and c.date_status != 'after_cutoff'), None)
        if hit:
            add(hit)
    for candidate in candidates:
        if candidate.date_status != 'after_cutoff':
            add(candidate)
    return selected


def seed_shortlist(candidates, features, limit=2):
    """Cheap cross-feature metadata coverage, only to choose texts to inspect.

    An aggregate is useful here: matching one generic feature must not crowd out
    a seed spanning the input, operation and output. This is never an X verdict.
    """
    def coverage(candidate):
        words = set(tokens(candidate.title + ' ' + ' '.join(str(v) for k, v in candidate.fields.items()
                         if k.startswith('abstract') or k == 'web_snippet')))
        ratios = [len(words & set(f.terms)) / max(1, len(set(f.terms))) for f in features if f.terms]
        return sum(ratios) / max(1, len(ratios))
    ordered = sorted((c for c in candidates if c.date_status != 'after_cutoff'),
                     key=lambda c: (-coverage(c), -c.score, c.id))
    return diverse_top(ordered, [], limit)
