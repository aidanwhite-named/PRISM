"""특허·비특허문헌 검색 연동 모듈 (Provider·벤더 중립).

PRISM 본체는 공통 인터페이스와 팩토리에 의존한다. 백엔드는 각각의 설정 키로
활성화하며, 호출부는 사용할 백엔드 ID를 명시한다. 새 검색 공급자는
PatentSearchBackend를 구현하고 register_backend로 등록한다.

증거 등급은 보존 아티팩트와 등록된 소스 프로필로 검증한다. 원문 등급은
중앙 정책과 소스 프로필의 raw_capable 조건을 모두 충족해야 부여한다.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from .artifacts import (
    ArtifactCorrupted,
    ArtifactError,
    ArtifactIdInvalid,
    ArtifactMissing,
    ArtifactStore,
    compute_id,
)
from .base import (
    ORIGINAL_SOURCE_KINDS,
    SOURCE_KINDS,
    SOURCE_OFFICIAL_XML,
    BackendStatus,
    EvidenceRef,
    FieldValue,
    PatentRecord,
    PatentSearchBackend,
    PatentSearchDisabled,
    PatentSearchError,
    PatentSearchNotConfigured,
    PatentSearchQuery,
    PatentSearchResponse,
)
from .epo_backend import (
    SETTING_CONSUMER_KEY as EPO_SETTING_CONSUMER_KEY,
    SETTING_CONSUMER_SECRET as EPO_SETTING_CONSUMER_SECRET,
    SETTING_ENABLED as EPO_SETTING_ENABLED,
    SETTING_QUOTA_STATE as EPO_SETTING_QUOTA_STATE,
    CredentialCheck,
    DetailBudgetExceeded,
    EpoOpsBackend,
    check_credentials,
)
from .epo_client import (
    MAX_RESULTS_PER_QUERY as EPO_MAX_RESULTS_PER_QUERY,
    OpsAuthError,
    OpsBudgetExceeded,
    OpsCancelled,
    OpsError,
    OpsUnavailable,
)
from .epo_cql import CqlError as EpoCqlError, DateRange, Group, Term
from .epo_parser import (
    PROFILE_EPO_OPS_XML,
    EpoDocument,
    EpoXmlError,
)
from .epo_quota import (
    WARN_RATIO as QUOTA_WARN_RATIO,
    WEEKLY_QUOTA_BYTES,
    QuotaExceeded,
    QuotaLedger,
    QuotaState,
    Throttled,
)
from .kipris_backend import KiprisBackend
from .literature_backend import (
    BACKEND_ID as LITERATURE_BACKEND_ID,
    CONSTITUENTS as LITERATURE_CONSTITUENTS,
    SETTING_ENABLED as LITERATURE_SETTING_ENABLED,
    LiteratureBackend,
)
from .literature_client import (
    LiteratureBudgetExceeded,
    LiteratureError,
    looks_like_doi,
    normalize_doi,
    plain_query,
)
from .literature_parser import (
    PROFILE_CROSSREF_JSON,
    PROFILE_EUROPEPMC_JSON,
)
from .parsers import (
    PROFILE_GENERIC_JSON,
    ExtractedField,
    SourceProfile,
    raw_capable_profiles,
    register_profile,
)
from .policy import RAW_DISABLED, RAW_ENABLED, EvidencePolicy
from .provenance import (
    MATCH_EXACT,
    MATCH_KINDS,
    MATCH_NONE,
    MATCH_NORMALIZED,
    ExcerptVerification,
    summarize,
    verify_excerpt,
    verify_record_excerpt,
)

# 백엔드마다 독립적으로 켜고 끄는 설정 키.
_ENABLE_KEYS: dict[str, str] = {
    "epo": EPO_SETTING_ENABLED,
    "literature": LITERATURE_SETTING_ENABLED,
    "kipris": "kipris_integration_enabled",
}

# 화면이 상태를 보여 줄 백엔드 전체. 등록 순서가 표시 순서다.
BACKEND_IDS = ("epo", "literature", "kipris")

_REGISTRY: dict[str, Callable[[], PatentSearchBackend]] = {
    "epo": EpoOpsBackend,
    "literature": LiteratureBackend,
    "kipris": KiprisBackend,
}

__all__ = [
    "BACKEND_IDS",
    "EPO_SETTING_ENABLED",
    "EPO_SETTING_CONSUMER_KEY",
    "EPO_SETTING_CONSUMER_SECRET",
    "EPO_SETTING_QUOTA_STATE",
    "EPO_MAX_RESULTS_PER_QUERY",
    "LITERATURE_BACKEND_ID",
    "LITERATURE_CONSTITUENTS",
    "LITERATURE_SETTING_ENABLED",
    "LiteratureBackend",
    "LiteratureBudgetExceeded",
    "LiteratureError",
    "PROFILE_CROSSREF_JSON",
    "PROFILE_EUROPEPMC_JSON",
    "looks_like_doi",
    "normalize_doi",
    "plain_query",
    "CredentialCheck",
    "DetailBudgetExceeded",
    "EpoCqlError",
    "EpoDocument",
    "EpoOpsBackend",
    "EpoXmlError",
    "DateRange",
    "Group",
    "Term",
    "OpsAuthError",
    "OpsBudgetExceeded",
    "OpsCancelled",
    "OpsError",
    "OpsUnavailable",
    "PROFILE_EPO_OPS_XML",
    "QUOTA_WARN_RATIO",
    "QuotaExceeded",
    "QuotaLedger",
    "QuotaState",
    "Throttled",
    "WEEKLY_QUOTA_BYTES",
    "check_credentials",
    "describe_all",
    "BackendStatus",
    "PatentRecord",
    "PatentSearchBackend",
    "PatentSearchDisabled",
    "PatentSearchError",
    "PatentSearchNotConfigured",
    "PatentSearchQuery",
    "PatentSearchResponse",
    "is_enabled",
    "get_backend",
    "describe",
    "register_backend",
    "ArtifactCorrupted",
    "ArtifactError",
    "ArtifactIdInvalid",
    "ArtifactMissing",
    "ArtifactStore",
    "compute_id",
    "EvidenceRef",
    "FieldValue",
    "ORIGINAL_SOURCE_KINDS",
    "SOURCE_KINDS",
    "ExcerptVerification",
    "MATCH_EXACT",
    "MATCH_KINDS",
    "MATCH_NONE",
    "MATCH_NORMALIZED",
    "EvidencePolicy",
    "RAW_DISABLED",
    "RAW_ENABLED",
    "ExtractedField",
    "SourceProfile",
    "PROFILE_GENERIC_JSON",
    "SOURCE_OFFICIAL_XML",
    "raw_capable_profiles",
    "register_profile",
    "summarize",
    "verify_excerpt",
    "verify_record_excerpt",
]


def register_backend(
    backend_id: str,
    factory: Callable[[], PatentSearchBackend],
    enable_key: str,
) -> None:
    """새 백엔드와 독립적인 활성화 설정 키를 등록한다."""
    _REGISTRY[backend_id] = factory
    _ENABLE_KEYS[backend_id] = enable_key


def is_enabled(
    values: Mapping[str, Any], backend_id: str
) -> bool:
    """그 백엔드의 설정 토글 상태. values 는 settings_service.get_all 결과."""
    key = _ENABLE_KEYS.get(backend_id)
    if key is None:
        return False
    return bool(values.get(key, False))


def get_backend(
    values: Mapping[str, Any], backend_id: str
) -> PatentSearchBackend | None:
    """활성 백엔드. 연동이 꺼져 있거나 알 수 없는 백엔드면 None.

    None 을 돌려주는 것은 '연동 안 함'이라는 정상 상태다. 호출부는 None 이면
    예전 경로(웹 검색만)를 그대로 쓴다.
    """
    if not is_enabled(values, backend_id):
        return None
    factory = _REGISTRY.get(backend_id)
    if factory is None:
        return None
    backend = factory()
    backend.configure(values)
    return backend


def describe(
    values: Mapping[str, Any], backend_id: str
) -> BackendStatus:
    """백엔드 상태를 네트워크 없이 보고한다. Settings 경고 문구의 출처."""
    enabled = is_enabled(values, backend_id)
    factory = _REGISTRY.get(backend_id)
    if factory is None:
        return BackendStatus(
            backend_id=backend_id,
            display_name=backend_id,
            enabled=enabled,
            configured=False,
            detail="등록되지 않은 백엔드입니다.",
        )
    backend = factory()
    backend.configure(values)
    if not enabled:
        return BackendStatus(
            backend_id=backend.id,
            display_name=backend.display_name,
            enabled=False,
            configured=False,
            detail="연동이 꺼져 있습니다.",
        )
    return backend.status()


def describe_all(values: Mapping[str, Any]) -> tuple[BackendStatus, ...]:
    """등록된 백엔드 전체의 상태. 화면과 경고 문구가 같은 것을 본다."""
    return tuple(describe(values, backend_id) for backend_id in BACKEND_IDS)
