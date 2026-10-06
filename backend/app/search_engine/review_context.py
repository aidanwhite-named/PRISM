"""Server-owned target components for search assessments."""
import json

FILE = 'search_review_context.json'


def load(directory):
    path = directory / FILE
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else None


def components(focus):
    return focus.get('components', []) if focus and focus.get('mode') == 'gap' else []


def assessment_status(matches):
    if not matches or any(m.get('status') == 'needs_review' for m in matches):
        return 'needs_review'
    return 'unavailable' if all(m.get('status') == 'unavailable' for m in matches) else 'source_checked'


def assessment_verdict(matches):
    for verdict in ('strong', 'partial'):
        if any(m.get('status') == 'source_checked' and m.get('verdict') == verdict for m in matches):
            return verdict
    return 'unavailable' if matches and all(m.get('status') == 'unavailable' for m in matches) else 'mismatch'
