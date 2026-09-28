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


def test_model_cannot_assign_verified_source_labels() -> None:
    """Stored model findings do not certify source channels or evidence levels."""
    from app.search_engine.autonomous_store import normalize
    parsed = normalize({
        "title": "Sensor", "url": "https://example.org/paper", "reason": "Shared relation",
        "group": "A", "channel": "patent_db", "evidence_level": "official_full_text",
        "doc_number": "EP123A1", "mapping": [],
    })
    assert "channel" not in parsed
    assert "evidence_level" not in parsed
