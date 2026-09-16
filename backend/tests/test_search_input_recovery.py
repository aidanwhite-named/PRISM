"""Real failure shapes: reject before network, explain recovery, preserve queries."""
import copy

import pytest

from app.search_mcp_server import SearchTools, _EPO_SEARCH, _query_node, _validate, error_response
from app.search_manifest import read_tool_journal
from app.patent_search import epo_cql


def check(args):
    _validate(args, _EPO_SEARCH["inputSchema"])
    return epo_cql.build(_query_node(args["query"]))


def test_wrapped_group_is_rejected_with_actionable_retry_instructions():
    args = {"query": {"group": {"type": "group", "op": "or", "items": [
        {"type": "term", "field": "ta", "value": "parent bone child bone"},
        {"type": "term", "field": "ta", "value": "joint rotation limit"},
    ]}}, "max_results": 20}
    before = copy.deepcopy(args)
    with pytest.raises(ValueError) as failure:
        check(args)
    response = error_response(failure.value, "epo_search", args)
    assert "arguments.query" in response["detail"]
    assert "query.group" in response["recovery"]["next_step"]
    assert response["not_evidence_of_absence"]
    assert args == before
    # This retry changes only the request structure, never the technical terms.
    corrected = {**args, "query": args["query"]["group"]}
    assert check(corrected) == '(ta all "parent bone child bone" or ta all "joint rotation limit")'


@pytest.mark.parametrize("count", [30, 0, True, "20"])
def test_invalid_count_reports_path_and_range(count):
    args = {"query": {"field": "ta", "value": "joint rotation"}, "max_results": count}
    with pytest.raises(ValueError, match="arguments.max_results.*1 to 20") as failure:
        check(args)
    response = error_response(failure.value, "epo_search", args)
    assert "begin" in response["recovery"]["next_step"]
    assert check({**args, "max_results": 20, "begin": 21}) == 'ta all "joint rotation"'


def test_nested_nodes_are_validated_and_supported_depth_is_preserved():
    args = {"query": {"type": "group", "op": "and", "items": [
        {"type": "group", "op": "or", "items": [
            {"type": "term", "field": "ta", "value": "joint limit"}]},
        {"type": "date_range", "begin": "19000101", "end": "20260916"}]}}
    assert 'pd within "19000101 20260916"' in check(args)
    args["query"]["items"][0]["items"][0]["field"] = "not_a_field"
    with pytest.raises(ValueError, match=r"arguments.query.items\[0\].items\[0\].field"):
        check(args)


def test_cql_text_limits_still_apply_after_schema_validation():
    with pytest.raises(epo_cql.CqlError):
        check({"query": {"field": "ta", "value": 'joint*'}})


def test_failed_request_is_journaled_with_retry_details_before_network(tmp_path, monkeypatch):
    tools = SearchTools(values={}, work_dir=tmp_path)
    monkeypatch.setattr(tools, "tool_definitions", lambda: [_EPO_SEARCH])
    def no_network(*args):
        pytest.fail("Invalid arguments must never reach the backend")
    monkeypatch.setattr(tools, "_backend", no_network)
    args = {"query": {"field": "ta", "value": "joint limit"}, "max_results": 30}
    with pytest.raises(ValueError, match="arguments.max_results"):
        tools.call("epo_search", args)
    failure = read_tool_journal(tmp_path)[-1]
    assert failure["ok"] is False
    assert failure["arguments"] == args
    assert failure["recovery"]["example_arguments"]["max_results"] == 20
    assert failure["not_evidence_of_absence"]
