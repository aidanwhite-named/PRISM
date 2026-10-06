from app.search_engine import autonomous_store
from app.search_engine.report import render
from app.search_engine.search_review import promising_candidate, summary_counts
from .test_autonomous_search import engine, finding


def test_promising_survives_access_failure_and_partial_saves(tmp_path):
    autonomous_store.merge(tmp_path, [finding(triage_status='promising',
        triage_reason='공식 초록에서 두 상태 생성→점유 정합→부분 복원을 확인.',
        difference='사전학습 모델의 입력은 본문 확인 필요.',
        review={'verdict': 'unavailable', 'reason': '본문 접근 차단', 'passages': []})])
    autonomous_store.merge(tmp_path, [finding(reported_scope='공식 초록, 본문 접근 불가')])
    e = engine(tmp_path)
    e.records = autonomous_store.load(tmp_path)
    snapshot = e.snapshot()
    row, = snapshot['candidates']
    assert row['triage_status'] == 'promising'
    assert promising_candidate(row)
    assert row['search_review']['status'] == 'unavailable'
    assert summary_counts([row])['X'] == 0
    assert summary_counts([row])['unavailable'] == 1
    text = render(snapshot)
    assert '## 유력 후보 · 분류 보류' in text
    assert '[유력 후보 · 분류 보류] Found source 1' in text
    assert '사전학습 모델의 입력은 본문 확인 필요.' in text
    assert '원문 확보·확인 불가' in text


def test_access_failure_alone_does_not_imply_promising(tmp_path):
    autonomous_store.merge(tmp_path, [finding(triage_status='candidate',
        review={'verdict': 'unavailable', 'reason': '본문 접근 차단', 'passages': []})])
    e = engine(tmp_path)
    e.records = autonomous_store.load(tmp_path)
    row, = e.snapshot()['candidates']
    assert row['triage_status'] == 'hold'
    assert not promising_candidate(row)
    assert not promising_candidate({'triage_status': 'promising'})


def test_verified_or_mismatched_candidate_leaves_promising_group():
    for review in ({'status': 'source_checked', 'group': 'X'},
                   {'status': 'needs_review', 'verdict': 'mismatch'}):
        assert not promising_candidate({'triage_status': 'promising',
            'triage_reason': '초기 자료상 근접', 'search_review': review})
