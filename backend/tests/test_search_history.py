"""Saved classifications remain readable without the retired search executor."""
from copy import deepcopy

from app.search_history.checkpoint import findings
from app.search_history.snapshot import render


def test_saved_snapshot_renders_all_families_without_reclassifying():
    snapshot = {
        'stop_reason': 'classification_incomplete', 'elapsed_seconds': 300,
        'features': [], 'queries': [], 'warnings': [],
        'candidates': [dict(id=str(i), family_id='', document_number=f'EP{i}A1',
            title=f'Saved document {i}', url=f'https://example.org/{i}',
            publication_date='', date_status='unknown', data_status='ABSTRACT_ONLY',
            document_classification={'group': 'A', 'reason': 'Saved judgment'},
            evidence=[], acquisitions=[]) for i in range(30)],
    }
    before = deepcopy(snapshot)
    text = render(snapshot)
    assert '### 30.' in text and 'X분류' in text and '분류 미완료' in text
    assert snapshot == before


def test_continuation_translates_saved_candidates_without_old_execution_fields():
    snapshot = {'candidates': [{'title': 'Saved source', 'url': 'https://example.org/paper',
        'document_number': '', 'document_classification': {'group': 'B', 'reason': 'Shared relationship'}}]}
    rows = findings(snapshot)
    assert rows[0]['reason'] == 'Shared relationship'
    assert 'document_classification' not in rows[0]
    assert findings({'findings': [], **snapshot}) == []
