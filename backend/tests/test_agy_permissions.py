"""이전 검색 경로의 agy 페이지 열람 권한 읽기 검사."""

from __future__ import annotations

import json

import pytest

from app.providers import agy_permissions


@pytest.fixture()
def settings_file(tmp_path, monkeypatch):
    """실제 홈 디렉터리 대신 임시 파일을 보게 만든다."""
    path = tmp_path / "antigravity-cli" / "settings.json"
    monkeypatch.setenv("PRISM_AGY_SETTINGS_PATH", str(path))
    return path


def _write(path, document: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")


def test_broken_json_surfaces_as_an_error_state_instead_of_raising(settings_file):
    """읽기 경로는 터지지 않는다. 실패를 error 칸에 담아 화면에 보인다."""
    settings_file.parent.mkdir(parents=True, exist_ok=True)
    settings_file.write_text("{", encoding="utf-8")

    state = agy_permissions.read_state()

    assert state.error
    assert state.allowed_hosts == ()
    # 목록을 읽지 못한 실행은 "제한 없음"이 아니라 "하나도 열 수 없음"이다.
    assert agy_permissions.allowed_hosts() == ()


def test_rules_that_are_not_read_url_are_ignored(settings_file):
    """다른 종류의 규칙은 열람 허용 호스트로 읽지 않는다."""
    _write(
        settings_file,
        {
            "permissions": {
                "allow": ["run_command(git)", "write_to_file(*)", "read_url(arxiv.org)"]
            }
        },
    )

    state = agy_permissions.read_state()

    assert state.allowed_hosts == ("arxiv.org",)


def test_settings_no_longer_manage_page_permissions(client, settings_file):
    _write(settings_file, {"permissions": {"allow": []}})
    before = settings_file.read_bytes()
    response = client.get("/api/settings")
    assert response.status_code == 200
    assert "agy_permissions" not in response.json()
    assert "literature_contact_email" not in response.json()["values"]
    assert "agy_allowlist_migration" not in response.json()["values"]
    assert client.post("/api/settings/agy-permissions/apply").status_code in {404, 405}
    assert client.put("/api/settings", json={"values": {"literature_contact_email": "old@example.com"}}).status_code == 400
    assert settings_file.read_bytes() == before
