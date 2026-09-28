"""허용 목록이 실제로 프롬프트에 들어가는가, 그리고 논문 채널 기본값.

두 가지를 함께 지킨다.

  1. 비특허문헌(Crossref·Europe PMC) 채널은 기본으로 켜져 있다. 사용자가
     명시적으로 끈 선택은 그대로 보존된다.
  2. agy 검색 실행의 프롬프트는 **그 순간 파일에 있는** 허용 목록을 말한다.
     코드에 박힌 목록이 아니다 — 사용자가 파일을 고치면 다음 실행이 곧바로
     그것을 말해야 하고, 그러지 않으면 모델은 열 수 없는 주소를 열려다
     실행 전체를 날린다.
"""

from __future__ import annotations

import json

import pytest

from app import config, job_assembly, settings_service
from app.db import session_scope
from app.enums import JobKind
from app.models import AppSetting


# ------------------------------------------------------- 논문 채널 기본값


def test_literature_channel_is_on_by_default() -> None:
    """새 설치와 이 키가 없는 기존 설치에서 기본 ON.

    유사문헌 검색 작업 자체가 외부 검색이다. 그 안에서 논문 채널만 따로 꺼
    두는 것은 보호가 아니라 결함이었다 — 웹 검색은 논문을 익명 링크와 요약문
    으로만 돌려주므로, 제목과 DOI 가 붙은 후보를 만드는 유일한 경로가 이쪽이다.
    """
    assert config.DEFAULTS["literature_integration_enabled"] is True


def test_installs_without_the_key_read_as_on(client) -> None:
    """DB 에 행이 없으면 기본값이 그대로 답이다."""
    with session_scope() as session:
        row = session.get(AppSetting, "literature_integration_enabled")
        if row is not None:
            session.delete(row)
        session.flush()
        assert (
            settings_service.get_all(session)["literature_integration_enabled"]
            is True
        )


def test_an_explicit_off_survives_the_new_default(client) -> None:
    """사용자가 화면에서 끄면 그 선택이 기본값을 덮는다.

    기본값을 바꾸는 변경에서 가장 위험한 것은 "껐는데 다시 켜지는" 경우다.
    저장은 사용자가 바꾼 키만 하므로 그 행이 남아 있는 한 기본값은 닿지 않는다.
    """
    with session_scope() as session:
        settings_service.update(session, {"literature_integration_enabled": False})
    try:
        with session_scope() as session:
            values = settings_service.get_all(session)
        assert values["literature_integration_enabled"] is False
    finally:
        with session_scope() as session:
            settings_service.update(
                session, {"literature_integration_enabled": True}
            )
