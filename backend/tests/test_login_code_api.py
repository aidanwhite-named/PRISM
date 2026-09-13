from __future__ import annotations

import pytest

FAKE_CODE = "4/prism-fake-code-for-tests"


def test_code_endpoint_forwards_only_to_requested_login(client, monkeypatch):
    from app.api import providers

    async def submit(provider, session_id, value):
        assert (provider, session_id, value) == ("agy", "test-session", FAKE_CODE)
        return {"state": "WAITING_FOR_USER", "needs_authorization_code": False}

    monkeypatch.setattr(providers.LOGIN_MANAGER, "submit_authorization_code", submit)
    result = client.post("/api/providers/agy/login/test-session/code", json={"code": FAKE_CODE})
    assert result.status_code == 200
    assert FAKE_CODE not in result.text


@pytest.mark.parametrize("body", [
    '{"code":"' + FAKE_CODE,  # malformed JSON must not echo the raw body
    '{"code":"' + FAKE_CODE + '","token":"secret"}',
    '["' + FAKE_CODE + '"]',
    '{"code":"' + FAKE_CODE + '"}' + ' ' * 4096,
])
def test_invalid_code_requests_never_reflect_body(client, body):
    result = client.post(
        "/api/providers/agy/login/test-session/code", content=body,
        headers={"Content-Type": "application/json"},
    )
    assert result.status_code == 400
    assert FAKE_CODE not in result.text
    assert "secret" not in result.text


def test_code_endpoint_requires_csrf_header(client):
    result = client.post(
        "/api/providers/agy/login/test-session/code", json={"code": FAKE_CODE},
        headers={"X-PRISM-Client": ""},
    )
    assert result.status_code == 403


def test_code_endpoint_rejects_missing_or_finished_sessions(client):
    result = client.post("/api/providers/agy/login/missing/code", json={"code": FAKE_CODE})
    assert result.status_code == 400
    assert FAKE_CODE not in result.text
