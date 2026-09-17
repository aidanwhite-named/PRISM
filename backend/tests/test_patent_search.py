"""검색 백엔드 등록과 모델 보고 출처 계약."""

from __future__ import annotations

import pytest

from app import patent_search, search_manifest
from app.patent_search import base


@pytest.mark.parametrize("backend_id", patent_search.BACKEND_IDS)
def test_backend_disabled_by_default(backend_id) -> None:
    assert patent_search.is_enabled({}, backend_id) is False
    assert patent_search.get_backend({}, backend_id) is None
    status = patent_search.describe({}, backend_id)
    assert status.enabled is False
    assert status.configured is False


def test_unknown_backend_is_none() -> None:
    assert patent_search.get_backend({"unknown_integration_enabled": True}, "unknown") is None


def test_register_backend_roundtrip() -> None:
    class _Fake(base.PatentSearchBackend):
        id = "fake_test"
        display_name = "가짜"

        def status(self) -> base.BackendStatus:
            return base.BackendStatus(self.id, self.display_name, True, True)

        def search(self, query: base.PatentSearchQuery) -> base.PatentSearchResponse:
            return base.PatentSearchResponse(records=(), total_found=0)

    patent_search.register_backend("fake_test", _Fake, "fake_test_enabled")
    try:
        backend = patent_search.get_backend(
            {"fake_test_enabled": True}, "fake_test"
        )
        assert isinstance(backend, _Fake)
    finally:
        patent_search._REGISTRY.pop("fake_test", None)
        patent_search._ENABLE_KEYS.pop("fake_test", None)


def test_model_reported_channel_stays_web_only() -> None:
    """모델이 주장할 수 있는 채널은 web 뿐이다.

    이전 단계에서는 patent_db 가 아예 없다는 것으로 이 불변식을 표현했다.
    지금은 PRISM 생산자용 채널로 존재하되 모델 보고 목록에는 없다는 형태로
    바뀌었다. 지켜야 할 것은 이름의 부재가 아니라 '모델이 그 라벨을 붙일 수
    없다'는 성질이다.
    """
    import json
    parsed, _ = search_manifest.parse(json.dumps({"candidates": [{
        "group": "A", "channel": "patent_db", "evidence_level": "official_full_text",
        "doc_number": "EP123A1", "mapping": [],
    }]}))
    assert "channel" not in parsed["candidates"][0]
    assert "evidence_level" not in parsed["candidates"][0]
