import json
import subprocess
from pathlib import Path

import pytest

from app import patent_search, search_channels, settings_service
from app.config import DEFAULTS
from app.patent_search.artifacts import ArtifactStore
from app.patent_search.base import PatentSearchQuery
from app.patent_search.kiwee_backend import KiweeBackend, PROFILE
from app.patent_search import kiwee_client, parsers
from app.search_mcp_server import SearchTools


def payload(rows=None, total=None, **header):
    rows = rows if rows is not None else [{'pn_s': 'KR10-2023-0012345A',
        'tl': '센서 제어 장치', 'ab': '모터 속도를 제어한다', 'pd': '20230303', 'ipc': ['G05B', 'H02P']}]
    return json.dumps({'responseHeader': {'status': 0, **header},
        'response': {'numFound': len(rows) if total is None else total, 'docs': rows}}, ensure_ascii=False).encode()


def configured(transport):
    backend = KiweeBackend(transport=transport)
    backend.configure({**DEFAULTS, 'kiwee_integration_enabled': True})
    return backend


def test_search_preserves_query_paging_and_evidence():
    calls = []
    raw = payload(total=15)
    backend = configured(lambda *args: (calls.append(args) or (200, raw)))
    result = backend.search(PatentSearchQuery('모터 센서', 5), begin=2)
    endpoint, params, _ = calls[0]
    assert endpoint == kiwee_client.ENDPOINT
    assert params == {'q': '(tl:"모터" OR ab:"모터" OR cl:"모터") AND (tl:"센서" OR ab:"센서" OR cl:"센서")',
        'wt': 'json', 'defType': 'lucene', 'rows': 5, 'start': 5, 'shards': 'shd_kr'}
    assert result.total_found == 15
    assert result.records[0].doc_number == 'KR1020230012345A'
    field = result.records[0].fields['abstract']
    from app.config import PATHS
    stored = ArtifactStore(PATHS.evidence_dir).read(field.evidence.artifact_id)
    assert stored == raw
    extracted = parsers.extract(stored, field.evidence.field_path, PROFILE)
    assert extracted.text == '모터 속도를 제어한다'
    assert extracted.translation_state == 'unknown'
    assert not extracted.raw_capable


@pytest.mark.parametrize('data', [b'<html>login</html>', b'[]', b'{"error": {}}',
    b'{"response":{"docs":[],"numFound":true}}', b'{"response":{"docs":[null],"numFound":1}}',
    payload(partialResults=True), payload(partialResults='omitted')])
def test_invalid_or_partial_response_is_failure(data):
    with pytest.raises(kiwee_client.KiweeError):
        configured(lambda *args: (200, data)).search(PatentSearchQuery('sensor', 5))


def test_zero_is_distinct_from_auth_failure():
    assert configured(lambda *args: (200, payload([]))).search(PatentSearchQuery('sensor', 5)).total_found == 0
    with pytest.raises(kiwee_client.KiweeError) as exc:
        configured(lambda *args: (403, b'login')).search(PatentSearchQuery('sensor', 5))
    assert exc.value.fault_code == 'KIWEE.AUTH'
    assert exc.value.http_status == 403


def test_missing_publication_identity_is_not_invented():
    backend = configured(lambda *args: (200, payload([{'id_kipi': 'internal-123', 'pn_s': '123', 'tl': 'title'}])))
    result = backend.search(PatentSearchQuery('sensor', 5))
    assert not result.records
    assert result.source_stats[0]['unidentified_records'] == 1
    assert result.total_found == 1


def test_disabled_backend_and_invalid_inputs_never_call_network():
    calls = []
    backend = KiweeBackend(transport=lambda *args: calls.append(args))
    with pytest.raises(patent_search.PatentSearchNotConfigured):
        backend.search(PatentSearchQuery('sensor'))
    backend.configure({**DEFAULTS, 'kiwee_integration_enabled': True})
    for query, options in [(PatentSearchQuery(' ', 5), {}), (PatentSearchQuery('sensor', 51), {}),
                           (PatentSearchQuery('sensor', 5), {'begin': 0}),
                           (PatentSearchQuery('{!xmlparser}x', 5), {'query_mode': 'solr'})]:
        with pytest.raises(ValueError):
            backend.search(query, **options)
    assert calls == []


@pytest.mark.parametrize('url', ['http://example.com/solr/select', 'https://u:p@example.com/solr/select',
    'https://example.com/solr/update', 'https://example.com/solr/select?rows=500',
    'https://example.com/solr/select#secret', 'https://example.com:bad/solr/select'])
def test_read_only_endpoint_validation(url):
    with pytest.raises(ValueError):
        settings_service._coerce('kiwee_endpoint', url)


def test_shards_cannot_inject_a_remote_server():
    with pytest.raises(ValueError):
        settings_service._coerce('kiwee_shards', 'http://127.0.0.1:8000')
    assert settings_service._coerce('kiwee_shards', 'shd_kr,shd_us') == 'shd_kr,shd_us'


def test_windows_transport_keeps_tls_checks_and_keys_in_store(monkeypatch):
    captured = []
    def run(args, **kwargs):
        captured.append((args, kwargs))
        Path(args[args.index('--output') + 1]).write_bytes(payload([]))
        return subprocess.CompletedProcess(args, 0, b'200', b'')
    monkeypatch.setattr(kiwee_client.shutil, 'which', lambda name: 'curl.exe')
    monkeypatch.setattr(kiwee_client.subprocess, 'run', run)
    monkeypatch.setattr(kiwee_client.subprocess, 'CREATE_NO_WINDOW', 0, raising=False)
    thumbprint = 'AB' * 20
    status, body = kiwee_client._curl_transport(kiwee_client.ENDPOINT, b'q=sensor',
        {'kiwee_certificate_thumbprint': thumbprint})
    args, kwargs = captured[0]
    assert status == 200 and body == payload([])
    assert args[1] == '--disable'
    assert '--insecure' not in args and '-k' not in args and '--location' not in args
    assert args[args.index('--cert') + 1] == 'CurrentUser\\MY\\' + thumbprint
    assert kwargs['input'] == b'q=sensor'
    assert kwargs['timeout'] == 30


def test_tls_error_does_not_leak_stderr(monkeypatch):
    monkeypatch.setattr(kiwee_client.shutil, 'which', lambda name: 'curl.exe')
    monkeypatch.setattr(kiwee_client.subprocess, 'CREATE_NO_WINDOW', 0, raising=False)
    monkeypatch.setattr(kiwee_client.subprocess, 'run', lambda *args, **kwargs:
        subprocess.CompletedProcess(args, 60, b'000', b'private diagnostics'))
    with pytest.raises(kiwee_client.KiweeError) as exc:
        kiwee_client._curl_transport(kiwee_client.ENDPOINT, b'q=sensor', {})
    assert exc.value.fault_code == 'KIWEE.TLS'
    assert 'private diagnostics' not in str(exc.value)


def test_mcp_tool_registration_and_pagination(monkeypatch, tmp_path):
    values = {**DEFAULTS, 'kiwee_integration_enabled': True}
    def init(self):
        self.values = {}
        self.transport = lambda *args: (200, payload(total=12))
    monkeypatch.setattr(KiweeBackend, '__init__', init)
    tools = SearchTools(values=values, work_dir=tmp_path)
    assert 'kiwee_search' in {d['name'] for d in tools.tool_definitions()}
    assert 'mcp__prism-search__kiwee_search' in search_channels.available_mcp_names(tools.statuses())
    result = tools.call('kiwee_search', {'query': '센서', 'begin': 2, 'max_results': 5})
    assert result['coverage']['next_page'] == 3
    assert result['coverage']['server_records_in_page'] == 1
    assert result['solr_query'].startswith('(tl:')
    disabled = SearchTools(values=DEFAULTS, work_dir=tmp_path / 'disabled')
    assert 'kiwee_search' not in {d['name'] for d in disabled.tool_definitions()}


def test_trial_api_shows_raw_unidentified_record_and_tls_failure(client, monkeypatch):
    assert client.put('/api/settings', json={'values': {'kiwee_integration_enabled': True}}).status_code == 200
    monkeypatch.setattr('app.patent_search.kiwee_backend.live_transport', lambda *args:
        (200, payload([{'id_kipi': 'local-id', 'tl': '센서'}])))
    result = client.post('/api/kiwee/search', json={'query': '센서'}).json()
    assert result['ok'] is True and result['total_found'] == 1
    assert result['records'][0]['document_number'] == ''
    assert 'id_kipi' in result['returned_fields']
    def fail(*args):
        raise kiwee_client._tls_error()
    monkeypatch.setattr('app.patent_search.kiwee_backend.live_transport', fail)
    result = client.post('/api/kiwee/search', json={'query': '센서'}).json()
    assert result['ok'] is False
    assert result['total_found'] is None
    assert result['error_code'] == 'KIWEE.TLS'
    assert client.post('/api/kiwee/search', json={'query': ' ', 'max_results': 5}).status_code == 400
    assert client.post('/api/kiwee/search', json={'query': '센서', 'begin': True}).status_code == 422
    client.put('/api/settings', json={'values': {'kiwee_integration_enabled': False}})
