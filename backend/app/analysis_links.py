"""Validate source references for multi-passage and derivation assessments.

The model judges technical meaning. This module checks that every asserted link
points to an accepted source passage and that no declared claim limitation vanishes.
It does not turn document combinations into a single-document similarity score.
"""
from __future__ import annotations

import re


def text(value):
    return value.strip() if isinstance(value, str) else ''


def compact(value):
    return re.sub(r'\s+', '', text(value))


def references(value, allowed, *, minimum=1):
    if (not isinstance(value, list) or len(value) < minimum
            or any(not isinstance(v, str) or v not in allowed for v in value)
            or len(set(value)) != len(value)):
        return None
    return value


def limitations_for(feature, rows):
    """Require exact feature spans, including numeric signs and operators.

    This checks the reviewed component, not completeness against the entire claim.
    Overlap is allowed for shared subjects/relations; ambiguous repeated spans must
    be disambiguated by returning a longer continuous span.
    """
    if not isinstance(rows, list) or not rows or len(rows) > 64:
        return {}, False
    source = compact(feature)
    covered, result = set(), {}
    for row in rows:
        if not isinstance(row, dict):
            return {}, False
        ident, span = text(row.get('id')), compact(row.get('text'))
        start = source.find(span)
        if (not ident or ident in result or not span or start < 0
                or source.find(span, start + 1) >= 0):
            return {}, False
        result[ident] = {'id': ident, 'text': text(row['text'])}
        covered.update(range(start, start + len(span)))
    # Mathematical symbols (%, <, >=, minus, ranges, etc.) are limitations too.
    required = {i for i, ch in enumerate(source) if ch not in ',.;:()[]{}、。，；：（）「」『』'}
    return result, bool(required and required <= covered)


def supports_for(rows, limitations, allowed):
    if not isinstance(rows, list) or len(rows) != len(limitations):
        return None
    result, seen = [], set()
    for row in rows:
        if not isinstance(row, dict):
            return None
        ident, status = row.get('limitation_id'), row.get('status')
        if (not isinstance(ident, str) or ident not in limitations or ident in seen
                or status not in ('direct', 'inferred', 'missing', 'contradicted')
                or not text(row.get('reason'))):
            return None
        refs = references(row.get('candidate_ids'), allowed,
                          minimum=0 if status == 'missing' else 1)
        if refs is None or (status == 'missing' and refs):
            return None
        seen.add(ident)
        result.append({'limitation_id': ident, 'status': status,
                       'candidate_ids': refs, 'reason': text(row['reason'])})
    return result


def explanation(value, allowed):
    if not isinstance(value, dict) or not text(value.get('reason')):
        return None
    refs = references(value.get('candidate_ids'), allowed)
    if refs is None:
        return None
    return {'reason': text(value['reason']), 'candidate_ids': refs}


def validate_links(component, assessments, row, axes, states):
    """Only real, semantically reviewed passages can participate in linked evidence."""
    limitations, complete = limitations_for(component['feature'], row.get('limitations'))
    issues, groups, routes = [], [], []
    if not complete:
        issues.append('limitation_coverage_unverified')
    allowed = {cid: a for cid, a in assessments.items()
               if a['verdict'] in ('direct', 'partial') and a.get('quote_verified')}
    raw_groups = row.get('evidence_sets', [])
    if not isinstance(raw_groups, list) or len(raw_groups) > 24:
        raw_groups = []
        issues.append('invalid_evidence_sets')
    ids = set(assessments)
    duplicate_ids = {text(g.get('id')) for g in raw_groups if isinstance(g, dict)
                     and sum(isinstance(x, dict) and x.get('id') == g.get('id') for x in raw_groups) > 1}
    for raw in raw_groups:
        if not isinstance(raw, dict):
            issues.append('invalid_evidence_set'); continue
        ident = text(raw.get('id'))
        refs = references(raw.get('candidate_ids'), allowed)
        if not complete or not ident or ident in ids or ident in duplicate_ids or refs is None:
            issues.append('invalid_evidence_set'); continue
        ids.add(ident)
        aliases = {allowed[cid]['attachment'] for cid in refs}
        support = supports_for(raw.get('supports'), limitations, refs)
        coherence = explanation(raw.get('coherence'), refs)
        verdict, score, axis = raw.get('verdict'), raw.get('similarity'), raw.get('axes')
        if (len(aliases) != 1 or support is None or not coherence
                or raw['coherence'].get('status') != 'same_embodiment'
                or not isinstance(axis, dict) or any(not isinstance(axis.get(a), str) or axis[a] not in states for a in axes)
                or not text(raw.get('relation')) or type(score) is not int
                or verdict not in ('direct', 'partial')
                or not (80 <= score <= 100 if verdict == 'direct' else 1 <= score < 80)):
            issues.append('unsupported_evidence_set'); continue
        # Every participating quote must have a stated role in the assessment.
        used = {cid for s in support for cid in s['candidate_ids']} | set(coherence['candidate_ids'])
        if set(refs) != used:
            issues.append('unused_evidence_set_passage'); continue
        if verdict == 'direct' and (any(s['status'] != 'direct' for s in support)
                or any(axis[a] in ('different', 'unknown') for a in axes)
                or axis['relation'] != 'same' or text(raw.get('difference'))):
            issues.append('inconsistent_direct_evidence_set'); continue
        if verdict == 'partial' and not text(raw.get('difference')):
            issues.append('missing_evidence_set_difference'); continue
        groups.append({'id': ident, 'attachment': next(iter(aliases)), 'candidate_ids': refs,
                       'verdict': verdict, 'similarity': score, 'axes': axis,
                       'relation': text(raw['relation']), 'difference': text(raw.get('difference')),
                       'supports': support, 'coherence': coherence})

    raw_routes = row.get('derivations')
    if not isinstance(raw_routes, list) or len(raw_routes) > 8:
        issues.append('derivation_review_missing_or_invalid')
        raw_routes = []
    for raw in raw_routes:
        if not isinstance(raw, dict):
            issues.append('invalid_derivation'); continue
        refs = references(raw.get('candidate_ids'), allowed)
        if not complete or refs is None:
            issues.append('invalid_derivation_sources'); continue
        aliases = {allowed[cid]['attachment'] for cid in refs}
        kind, conclusion = raw.get('kind'), raw.get('conclusion')
        supports = supports_for(raw.get('supports'), limitations, refs)
        motivation = explanation(raw.get('motivation'), refs)
        compatibility = explanation(raw.get('compatibility'), refs)
        remaining = text(raw.get('remaining_difference'))
        if (kind not in ('single_document', 'combination')
                or (len(aliases) < 2 if kind == 'combination' else len(aliases) != 1)
                or supports is None or not motivation or not compatibility
                or raw['compatibility'].get('status') not in ('compatible', 'incompatible', 'unknown')
                or not text(raw.get('modification')) or not text(raw.get('reason'))
                or conclusion not in ('supported', 'remaining_gap', 'not_supported', 'insufficient')):
            issues.append('unsupported_derivation'); continue
        used = ({cid for s in supports for cid in s['candidate_ids']}
                | set(motivation['candidate_ids']) | set(compatibility['candidate_ids']))
        if set(refs) != used:
            issues.append('unused_derivation_passage'); continue
        if conclusion == 'supported' and (remaining or any(s['status'] in ('missing', 'contradicted') for s in supports)
                or raw['compatibility']['status'] != 'compatible'):
            issues.append('inconsistent_supported_derivation'); continue
        if conclusion != 'supported' and not remaining:
            issues.append('missing_derivation_gap'); continue
        routes.append({'kind': kind, 'candidate_ids': refs, 'supports': supports,
                       'motivation': motivation, 'compatibility': {**compatibility, 'status': raw['compatibility']['status']},
                       'modification': text(raw['modification']), 'conclusion': conclusion,
                       'reason': text(raw['reason']), 'remaining_difference': remaining})
    return {'limitations': list(limitations.values()), 'limitation_coverage_complete': complete,
            'evidence_sets': groups, 'derivations': routes}, issues
