"""Settings-only web diagnostics retained after restoring the Sept 6 search pipeline.

Diagnostic status does not gate normal search execution.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app import search_channels

NOW = datetime(2026, 9, 12, 6, 40, tzinfo=timezone.utc)

# 실제로 남은 실행 기록(job ab510504)의 모양. 손으로 지어내지 않는다.
# agy_stream 이 오류 dict 를 str() 로 눌러서 넘기므로 문자열이다 — 파이썬 repr 을
# 그대로 사용자에게 보이지 않는 것까지 여기서 지킨다.
AGY_FAILURE = "{'type': 'TOOL_ERROR', 'message': 'no summary returned from GenerateContent'}"
FAILED_RUN_CALLS = [
    {"name": "search_web", "ok": False, "error": AGY_FAILURE},
    {"name": "search_web", "ok": False, "error": AGY_FAILURE},
    {"name": "read_url_content", "ok": True, "input": {"url": "https://patents.google.com/patent/US10846933B2/en"}},
    {"name": "view_file", "ok": True},
]


def _values(record=None):
    values = {"epo_integration_enabled": False,
              "literature_integration_enabled": False}
    if record is not None:
        values[search_channels.WEB_HEALTH_KEY] = {"agy": record}
    return values


def test_web_is_not_available_without_a_measurement():
    status = _diagnostic_availability(_values(), "agy", now=NOW)["web"]
    assert status["status"] == "unverified"
    assert "확인" in status["detail"]


def test_measured_failure_is_reported_as_failure_with_its_reason():
    record = search_channels.web_evidence(FAILED_RUN_CALLS, ("search_web",),
                                          cli_version="1.2.2", now=NOW)
    assert record["ok"] is False
    assert record["detail"] == "no summary returned from GenerateContent"

    status = _diagnostic_availability(_values(record), "agy",
                                          cli_version="1.2.2", now=NOW)["web"]
    assert status["status"] == "unreachable"
    assert "no summary returned from GenerateContent" in status["detail"]


def test_measured_success_is_the_only_road_to_available():
    record = search_channels.web_evidence(
        [{"name": "search_web", "ok": True}], ("search_web",), cli_version="1.2.2", now=NOW)
    status = _diagnostic_availability(_values(record), "agy",
                                          cli_version="1.2.2", now=NOW)["web"]
    assert status["status"] == "available"


def test_last_weeks_success_does_not_speak_for_today():
    """9/5 성공 기록이 9/12 실행을 보증하지 않는다. 그 사이 CLI 가 바뀌었다."""
    old = search_channels.web_evidence(
        [{"name": "search_web", "ok": True}], ("search_web",), cli_version="1.1.26",
        now=NOW - timedelta(days=7))
    assert _diagnostic_availability(_values(old), "agy", now=NOW)["web"]["status"] == "unverified"

    # TTL 안이라도 CLI 가 바뀌었으면 다시 모름이다.
    fresh_but_upgraded = dict(old, at=NOW.isoformat())
    status = _diagnostic_availability(_values(fresh_but_upgraded), "agy",
                                          cli_version="1.2.2", now=NOW)["web"]
    assert status["status"] == "unverified"
    assert "1.1.26" in status["detail"] and "1.2.2" in status["detail"]


def test_unknown_outcome_is_not_recorded_as_evidence():
    """Codex 는 성공 신호를 안 준다. 모름을 실패로도 성공으로도 적지 않는다."""
    assert search_channels.web_evidence([{"name": "web_search", "ok": None}],
                                        ("web_search",), now=NOW) is None
    assert search_channels.web_evidence([{"name": "view_file", "ok": True}],
                                        ("search_web",), now=NOW) is None




def test_a_run_with_no_channel_at_all_is_blocked_before_it_spends_tokens():
    """오늘의 실제 설정: agy + web 사망 + EPO 는 agy 에서 못 씀."""
    record = search_channels.web_evidence(FAILED_RUN_CALLS, ("search_web",), now=NOW)
    dead = _diagnostic_availability(_values(record), "agy", now=NOW)
    assert search_channels.no_usable_channel(dead) is True

    # 아직 확인한 적 없는 설치를 막지는 않는다. 모름은 사망이 아니다.
    unknown = _diagnostic_availability(_values(), "agy", now=NOW)
    assert search_channels.no_usable_channel(unknown) is False

    # EPO 가 살아 있으면(claude/codex) web 이 죽어도 실행은 의미가 있다.
    with_epo = dict(dead, epo={"status": "available", "detail": ""})
    assert search_channels.no_usable_channel(with_epo) is False




def _diagnostic_availability(values, provider, **kwargs):
    statuses = search_channels.availability(values, provider)
    record = values.get(search_channels.WEB_HEALTH_KEY, {}).get(provider)
    statuses["web"] = search_channels.web_status(record, **kwargs)
    return statuses
