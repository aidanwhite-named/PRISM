import pytest

from app.config import DEFAULTS, PATHS
from app.db import session_scope
from app.models import ExecutionJob
from app.patent_search.artifacts import ArtifactStore
from app.search_engine import autonomous_store
from app.search_engine.comparison_sources import acquire
from app.search_engine.fetcher import Fetched, SafeFetcher
from .conftest import wait_for_job
from .test_autonomous_search import finding
from .test_autonomous_search_api import autonomous_runtime
from .pdf_fixture import build_pdf


@pytest.fixture(autouse=True)
def analysis_provider(client):
    from app.models import AppSetting
    # The API intentionally cannot configure a test-only provider. Seed the
    # isolated test database, including when an earlier test saved empty state.
    with session_scope() as session:
        row = session.get(AppSetting, 'default_provider')
        old = row.value if row else None
        session.merge(AppSetting(key='default_provider', value='test'))
    yield
    with session_scope() as session:
        row = session.get(AppSetting, 'default_provider')
        if old is None:
            session.delete(row)
        else:
            row.value = old


def html_source(monkeypatch, html):
    calls = []
    def get(self, url):
        calls.append(url)
        body = html.encode()
        return Fetched(url, body, 'text/html', ArtifactStore(PATHS.evidence_dir).put(body))
    monkeypatch.setattr(SafeFetcher, 'get', get)
    return calls


PATENT = ('<dd itemprop="publicationNumber">US1234567A</dd>'
          '<section itemprop="description">' + 'A controller connects input A to output B. ' * 20
          + '</section><section itemprop="claims">A controls B.</section>')
CANDIDATE = {'id': 'p', 'title': 'Controller', 'document_number': 'US1234567A',
             'url': 'https://patents.google.com/patent/US1234567A/en'}


def test_acquire_labelled_patent_and_reuse_without_network(monkeypatch, tmp_path):
    calls = html_source(monkeypatch, PATENT)
    item, receipt = acquire(CANDIDATE, tmp_path, tmp_path / 'first')
    assert item['read_ok'] and item['role'] == 'CITATION'
    assert receipt['scope'] == 'description_and_claims'
    second, _ = acquire(CANDIDATE, tmp_path, tmp_path / 'second')
    assert second['sha256'] == item['sha256']
    assert len(calls) == 1


@pytest.mark.parametrize('html', [
    '<p>' + 'An abstract about A and B. ' * 100 + '</p>',
    PATENT.replace('US1234567A', 'US9999999A'),
])
def test_abstract_or_wrong_patent_is_not_full_text(monkeypatch, tmp_path, html):
    html_source(monkeypatch, html)
    item, receipt = acquire(CANDIDATE, tmp_path, tmp_path / 'batch')
    assert item is None
    assert receipt['status'] == 'hold'


def test_literature_pdf_preserves_pages_and_ignores_reference_links(monkeypatch, tmp_path):
    calls = []
    def get(self, url):
        calls.append(url)
        if url.endswith('/article.pdf'):
            body = build_pdf(['Controller methods and experimental evidence.', 'Detailed results and discussion.'])
            mime = 'application/pdf'
        else:
            body = b'<a href="reference.pdf">Reference</a><meta name="citation_pdf_url" content="/article.pdf">'
            mime = 'text/html'
        return Fetched(url, body, mime, self.store.put(body))
    monkeypatch.setattr(SafeFetcher, 'get', get)
    item, receipt = acquire({**CANDIDATE, 'url': 'https://example.org/article'}, tmp_path, tmp_path / 'batch')
    assert item['page_count'] == 2
    assert receipt['scope'] == 'pdf_text'
    assert calls == ['https://example.org/article', 'https://example.org/article.pdf']


@pytest.mark.parametrize('section', ['claims', 'full_text'])
def test_reuses_search_raw_source_instead_of_its_excerpt(monkeypatch, tmp_path, section):
    import json
    from app.search_engine.storage import identifier
    aid = ArtifactStore(PATHS.evidence_dir).put(PATENT.encode())
    cache = tmp_path / ('source-' + identifier(CANDIDATE['url'] + section) + '.json')
    cache.write_text(json.dumps({'url': CANDIDATE['url'], 'raw_artifact_id': aid, 'text': 'truncated claims'}))
    def refuse(*args):
        raise AssertionError('Should reuse the raw artifact')
    monkeypatch.setattr(SafeFetcher, 'get', refuse)
    item, receipt = acquire(CANDIDATE, tmp_path, tmp_path / 'batch')
    assert item['char_count'] > 500
    assert receipt['artifact_id'] == aid


@pytest.mark.parametrize('number', ['arxiv:2412.12906', 'arxiv:2412.12906v1; DOI 10.1234/example'])
def test_arxiv_labelled_html_is_acquired_as_full_article(monkeypatch, tmp_path, number):
    html_source(monkeypatch, '<nav>Site navigation</nav><article class="ltx_document">'
                '<section class="ltx_section"><h2>Method</h2><p>'
                + 'A controller connects input A to output B. ' * 20 + '</p></section></article>')
    candidate = {'id': 'arxiv', 'title': 'Controller paper', 'document_number': number,
                 'url': 'https://arxiv.org/html/2412.12906v1'}
    item, receipt = acquire(candidate, tmp_path, tmp_path / 'batch')
    assert item['read_ok'] and item['role'] == 'CITATION'
    assert receipt['scope'] == 'article_html'
    assert '도면 이미지 제외' in receipt['reason']
    from pathlib import Path
    delivered = Path(item['normalized_text_path']).read_text(encoding='utf-8')
    assert 'A controller connects input A to output B.' in delivered
    assert 'Site navigation' not in delivered


@pytest.mark.parametrize('number,html', [
    ('arxiv:2412.12906', '<p>' + 'An abstract about controllers. ' * 20 + '</p>'),
    ('arxiv:9999.99999', '<article class="ltx_document"><section class="ltx_section"><p>'
     + 'A controller connects input A to output B. ' * 20 + '</p></section></article>'),
])
def test_arxiv_unlabelled_page_or_wrong_document_is_held(monkeypatch, tmp_path, number, html):
    html_source(monkeypatch, html)
    item, receipt = acquire({'id': 'arxiv', 'title': 'Controller paper', 'document_number': number,
                             'url': 'https://arxiv.org/html/2412.12906v1'}, tmp_path, tmp_path / 'batch')
    assert item is None and receipt['status'] == 'hold'


def test_triage_partial_updates_keep_validated_fields(tmp_path):
    autonomous_store.merge(tmp_path, [finding(triage_status='candidate', review_stage='core_components')])
    autonomous_store.merge(tmp_path, [finding(reason='updated')])
    assert autonomous_store.load(tmp_path)[0]['triage_status'] == 'candidate'
    row = autonomous_store.normalize(finding(triage_status='invented', review_stage='invented'))
    assert row['triage_status'] == 'unreviewed' and row['review_stage'] == 'metadata'


def search_with_candidates(client, candidates):
    result = client.post('/api/jobs', json={'job_kind': 'similarity_search', 'provider': 'test-search',
                                          'claim_text': 'A controls B'}).json()
    search = wait_for_job(client, result['id'])
    with session_scope() as session:
        parent = session.get(ExecutionJob, search['id'])
        manifest = parent.search_manifest
        parent.search_manifest = {**manifest, 'engine': {**manifest['engine'], 'candidates': candidates}}
    return search['id']


@pytest.mark.parametrize('dependent', ['', '청구항 2. 제1항에 있어서, 속도에 따라 임계값을 조정.'])
def test_search_to_real_analysis_and_idempotency(client, autonomous_runtime, monkeypatch, dependent):
    monkeypatch.setitem(DEFAULTS, 'default_provider', 'test')
    calls = html_source(monkeypatch, PATENT)
    search_id = search_with_candidates(client, [CANDIDATE])
    if dependent:
        with session_scope() as session:
            session.get(ExecutionJob, search_id).search_focus = {'mode': 'gap', 'origin': 'dependent_claims',
                'dependent_claim_text': dependent, 'components': []}
    response = client.post(f'/api/jobs/{search_id}/comparisons', json={'candidate_ids': ['p']})
    assert response.status_code == 201, response.text
    record = response.json()
    child = wait_for_job(client, record['job_id'])
    assert child['status'] == 'SUCCEEDED', child['errors']
    assert child['job_kind'] == 'patent_analysis'
    assert child['source_job_id'] == search_id
    assert child['claim_text'] == 'A controls B' + ('\n\n' + dependent if dependent else '')
    assert child['attachments'][0]['role'] == 'CITATION'
    assert child['result_text']
    again = client.post(f'/api/jobs/{search_id}/comparisons', json={'candidate_ids': ['p']}).json()
    assert again['job_id'] == child['id']
    assert len(calls) == 1
    records = client.get(f'/api/jobs/{search_id}/comparisons').json()
    assert len(records) == 1 and records[0]['status'] == 'SUCCEEDED'


def test_hold_does_not_start_analysis_and_invalid_selection_rejected(client, autonomous_runtime, monkeypatch):
    html_source(monkeypatch, '<p>Only abstract.</p>')
    search_id = search_with_candidates(client, [CANDIDATE])
    result = client.post(f'/api/jobs/{search_id}/comparisons', json={'candidate_ids': ['p']})
    assert result.status_code == 201, result.text
    assert result.json()['job_id'] is None
    assert result.json()['sources'][0]['status'] == 'hold'
    assert client.post(f'/api/jobs/{search_id}/comparisons', json={'candidate_ids': ['missing']}).status_code == 400
    assert client.post(f'/api/jobs/{search_id}/comparisons', json={'candidate_ids': ['p'] * 6}).status_code == 422


@pytest.mark.parametrize('status', ['FAILED', 'CANCELLED'])
def test_failed_comparison_can_be_retried_without_changing_old_job(client, autonomous_runtime, monkeypatch, status):
    html_source(monkeypatch, PATENT)
    search_id = search_with_candidates(client, [CANDIDATE])
    endpoint = f'/api/jobs/{search_id}/comparisons'
    first = client.post(endpoint, json={'candidate_ids': ['p']}).json()
    wait_for_job(client, first['job_id'])
    with session_scope() as session:
        session.get(ExecutionJob, first['job_id']).status = status
    response = client.post(endpoint, json={'candidate_ids': ['p']})
    assert response.status_code == 201, response.text
    second = response.json()
    assert second['job_id'] != first['job_id']
    wait_for_job(client, second['job_id'])
    assert client.get(f"/api/jobs/{first['job_id']}").json()['status'] == status
    again = client.post(endpoint, json={'candidate_ids': ['p']}).json()
    assert again['job_id'] == second['job_id']
    assert len(client.get(endpoint).json()) == 2


def test_partial_acquisition_only_hands_usable_sources_to_analysis(client, autonomous_runtime, monkeypatch):
    monkeypatch.setitem(DEFAULTS, 'default_provider', 'test')
    def get(self, url):
        body = PATENT.encode() if 'patents.google.com' in url else b'<p>Only an abstract</p>'
        return Fetched(url, body, 'text/html', self.store.put(body))
    monkeypatch.setattr(SafeFetcher, 'get', get)
    search_id = search_with_candidates(client, [CANDIDATE,
        {**CANDIDATE, 'id': 'q', 'url': 'https://example.org/abstract'},
        {**CANDIDATE, 'id': 'late', 'date_status': 'after_cutoff'}])
    assert client.post(f'/api/jobs/{search_id}/comparisons', json={'candidate_ids': ['late']}).status_code == 400
    result = client.post(f'/api/jobs/{search_id}/comparisons', json={'candidate_ids': ['p', 'q']})
    assert result.status_code == 201, result.text
    assert [s['status'] for s in result.json()['sources']] == ['ready', 'hold']
    job = wait_for_job(client, result.json()['job_id'])
    assert job['status'] == 'SUCCEEDED', job['errors']
    assert len(job['attachments']) == 1
