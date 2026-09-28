"""Replay failed search shapes and stale material labels without live requests."""
import copy
from types import SimpleNamespace

import pytest

from app.patent_search import epo_cql, epo_client
from app.search_mcp_server import SearchTools, _EPO_SEARCH, _validate, _query_node, _response, error_response
from app.search_engine.source_observations import candidate_observation, with_observations
from app.search_engine.report import render
from .test_epo_search import FakeTransport, make_backend, ok, token_response
from . import epo_fixtures as fx


def term(value):
    return {'type': 'term', 'field': 'ta', 'value': value, 'match': 'all'}


@pytest.mark.parametrize('count,begin,expected', [(25, 1, '1-25'), (100, 101, '101-200'), (100, 1981, '1981-2000')])
def test_epo_tool_to_http_range_and_missing_group_type(tmp_path, count, begin, expected):
    from app.patent_search.artifacts import ArtifactStore
    transport = FakeTransport(token_response(), ok(fx.SEARCH_BIBLIO))
    tools = SearchTools(values={}, work_dir=tmp_path)
    tools.backends['epo'] = make_backend(transport, ArtifactStore(tmp_path / 'evidence'))
    args = {'query': {'op': 'and', 'items': [term('spectral subtraction'), term('adaptive noise estimation')]},
            'max_results': count, 'begin': begin}
    original = copy.deepcopy(args)
    _validate(args, _EPO_SEARCH['inputSchema'])
    result = tools._epo_search(args)
    assert f'Range={expected}' in transport.requests[1]['url']
    assert result['cql'] == '(ta all "spectral subtraction" and ta all "adaptive noise estimation")'
    assert result['query_normalizations'] == [{'path': 'query.type', 'inferred': 'group'}]
    assert args == original  # The journal retains the original model request.


def test_nested_boolean_groups_keep_meaning_and_report_inference():
    query = {'op': 'and', 'items': [
        {'op': 'or', 'items': [term('speech'), term('voice')]},
        {'type': 'group', 'op': 'not', 'items': [term('noise'), term('image')]},
    ]}
    _validate({'query': query}, _EPO_SEARCH['inputSchema'])
    corrections = []
    cql = epo_cql.build(_query_node(query, corrections=corrections))
    assert cql == '((ta all "speech" or ta all "voice") and (ta all "noise" not ta all "image"))'
    assert [x['path'] for x in corrections] == ['query.type', 'query.items[0].type']


@pytest.mark.parametrize('query', [
    {'op': 'and', 'items': [term('noise')], 'field': 'ta', 'value': 'speech'},
    {'type': 'term', 'op': 'and', 'items': [term('noise')]},
    {'op': 'xor', 'items': [term('noise')]},
    {'op': 'and', 'items': []},
    {'field': 'unknown', 'value': 'speech'},
])
def test_ambiguous_or_invalid_nodes_are_not_repaired(query):
    with pytest.raises(ValueError, match='invalid_cql_node'):
        _validate({'query': query}, _EPO_SEARCH['inputSchema'])


def test_oversized_request_has_actionable_pagination_recovery():
    args = {'query': term('speech'), 'max_results': 101}
    with pytest.raises(ValueError) as caught:
        _validate(args, _EPO_SEARCH['inputSchema'])
    result = error_response(caught.value, 'epo_search', args)
    assert result['recovery']['page_size']['maximum'] == 100
    assert 'coverage.next_begin' in result['recovery']['next_step']
    assert result['not_evidence_of_absence']


def test_pagination_stops_at_ops_total_retrievable_limit():
    from app.search_mcp_server import epo_search_advice
    page = {'coverage': {'returned_records': 100, 'total_results': 3000}, 'records': []}
    result = epo_search_advice(copy.deepcopy(page), begin=1)
    assert result['coverage']['next_begin'] == 101
    final = epo_search_advice(copy.deepcopy(page), begin=1901)
    assert final['coverage']['next_begin'] is None
    assert final['coverage']['retrieval_limit_reached']


def receipt(fields, *, ok=True, matched=True, doi='10.1234/example'):
    return {'id': 'fetch1', 'tool': 'literature_fetch', 'state': 'completed', 'ok': ok,
        'result': {'verification_scope': 'abstract', 'identifier_matched': matched,
                   'records': [{'document_number': doi, 'title': 'Paper', 'url': 'https://publisher.example/paper',
                                'fields': fields}]}}


def test_late_abstract_replaces_stale_display_without_rewriting_model_claim():
    candidate = {'title': 'Paper', 'document_number': '', 'url': 'https://doi.org/10.1234/EXAMPLE',
                 'reported_scope': '서지정보만 확인', 'date_status': 'publication_date_unknown',
                 'publication_date': '', 'reason': 'similar'}
    snapshot = {'candidates': [candidate], 'source_calls': [receipt({'title': 'Paper'})],
                'stop_reason': 'model_complete', 'elapsed_seconds': 1, 'warnings': [], 'summary': ''}
    assert with_observations(snapshot)['candidates'][0]['observed_scopes'] == ['bibliographic']
    snapshot['source_calls'].append(receipt({'abstract:en': 'An actual abstract.'}))
    updated = with_observations(snapshot)
    assert updated['candidates'][0]['observed_scopes'] == ['bibliographic', 'abstract']
    assert '초록 확보' in updated['candidates'][0]['observed_scope']
    assert '프로그램이 확보한 자료: 초록 확보' in render(snapshot)
    assert candidate['reported_scope'] == '서지정보만 확인'
    assert 'observed_scope' not in candidate


@pytest.mark.parametrize('fields', [{'title': 'Paper'}, {'abstract': ''}, {'abstract': '  '}])
def test_requesting_abstract_does_not_prove_abstract_was_obtained(fields):
    candidate = {'url': 'https://doi.org/10.1234/example'}
    observed = candidate_observation(candidate, [receipt(fields)])
    assert observed['observed_scopes'] == ['bibliographic']
    assert observed['source_receipts'][0]['scope'] == 'bibliographic'


@pytest.mark.parametrize('changes', [{'ok': False}, {'matched': False}, {'doi': '10.1234/different'}])
def test_failed_or_mismatched_fetch_cannot_upgrade_scope(changes):
    candidate = {'document_number': '10.1234/example', 'url': 'https://publisher.example/paper'}
    assert not candidate_observation(candidate, [receipt({'abstract': 'Wrong content'}, **changes)])['observed_scopes']


@pytest.mark.parametrize('fields,expected', [({'title': 'Paper'}, 'bibliographic'), ({'abstract': 'Actual text'}, 'abstract')])
def test_fetch_response_scope_uses_returned_fields(fields, expected):
    record = SimpleNamespace(fields={k: SimpleNamespace(value=v, evidence=None) for k, v in fields.items()},
        doc_number='10.1234/example', title='Paper', source_url='https://doi.org/10.1234/example')
    response = SimpleNamespace(source_stats=[], records=[record], total_found=1, failed_sources=[],
        raw_artifact_id='', fetched_at='', http_status=200, request_url='', notes=[])
    result = _response(response, scope='abstract')
    assert result['requested_scope'] == 'abstract'
    assert result['verification_scope'] == expected
