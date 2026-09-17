"""Settings API.

AI 실행 도구(Provider)의 API Key 입력란은 만들지 않는다. 각 CLI 에 저장된
로그인 세션만 사용한다.

외부 검색 데이터 소스(EPO OPS, OpenAlex, KIPRIS)의 자격증명은 별도로 저장한다.

  - 저장은 하되 응답으로 돌려주지 않는다(settings_service.redact_for_api).
    화면은 "설정됨/미설정"만 본다.
  - 연결 테스트는 사용자가 버튼을 눌렀을 때만 호출한다.
    검색 실행 중에는 활성 검색 백엔드가 저장된 자격증명을 사용한다.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel, Field, StrictInt

from .. import patent_search, settings_service
from ..config import DEFAULT_RUNTIME_CONTEXT, PATHS
from ..db import get_db
from ..providers.env import describe_filtering
from ..providers.registry import invalidate
from ..schemas import CredentialCheckOut, SettingsOut, SettingsUpdate
from ..patent_search import kipris_quota

router = APIRouter(prefix="/api/settings", tags=["settings"])


def _payload(session: Session) -> SettingsOut:
    values = settings_service.get_all(session)
    # 경고 문구는 가리기 **전** 값으로 만든다. 가린 값으로 만들면 자격증명을
    # 넣어 둔 사용자에게도 "설정되지 않았습니다"가 뜬다.
    warnings = settings_service.warnings_for(values)
    return SettingsOut(
        values=settings_service.redact_for_api(values),
        warnings=warnings,
        data_dir=str(PATHS.data_dir),
        runs_dir=str(PATHS.runs_dir),
        env_filtering=describe_filtering(),
        secrets_set=settings_service.secrets_set(values),
        epo_quota=settings_service.epo_quota_snapshot(values),
        kipris_quota=kipris_quota.snapshot(values.get(kipris_quota.KEY)),
    )


@router.get("", response_model=SettingsOut)
def get_settings(session: Session = Depends(get_db)) -> SettingsOut:
    return _payload(session)


@router.put("", response_model=SettingsOut)
def update_settings(
    payload: SettingsUpdate, session: Session = Depends(get_db)
) -> SettingsOut:
    try:
        settings_service.update(session, payload.values)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    session.commit()
    # 실행 파일 경로가 바뀌었을 수 있으므로 probe 캐시를 버린다.
    invalidate()
    return _payload(session)


@router.post("/epo/check", response_model=CredentialCheckOut)
def check_epo_credentials(session: Session = Depends(get_db)) -> CredentialCheckOut:
    """저장된 EPO OPS 자격증명으로 토큰 발급을 한 번 시도한다.

    사용자가 버튼을 눌렀을
    때만 실행되고, 특허 데이터는 요청하지 않으며, 받은 토큰은 저장하지 않는다.
    자격증명은 요청 본문이 아니라 저장된 값에서 읽는다 — 본문으로 받으면 비밀이
    프록시 로그와 브라우저 기록에 한 번 더 남는다.
    """
    values = settings_service.get_all(session)
    if not values.get(patent_search.EPO_SETTING_ENABLED, False):
        raise HTTPException(400, "EPO OPS 연동이 꺼져 있습니다.")
    result = patent_search.check_credentials(
        str(values.get(patent_search.EPO_SETTING_CONSUMER_KEY) or ""),
        str(values.get(patent_search.EPO_SETTING_CONSUMER_SECRET) or ""),
    )
    return CredentialCheckOut(
        ok=result.ok,
        detail=result.detail,
        http_status=result.http_status,
        expires_in=result.expires_in,
    )


@router.post("/openalex/check", response_model=CredentialCheckOut)
def check_openalex(session: Session = Depends(get_db)) -> CredentialCheckOut:
    """저장된 OpenAlex 키로 DOI 단건 조회를 한 번 한다(무료 호출).

    EPO 확인과 같은 원칙이다. 키는 요청 본문이 아니라 저장된 값에서 읽고,
    받은 문헌은 저장하지 않는다.
    """
    from ..patent_search import openalex_client

    values = settings_service.get_all(session)
    if not values.get(patent_search.LITERATURE_SETTING_ENABLED, False):
        raise HTTPException(400, "비특허문헌 연동이 꺼져 있습니다.")
    ok, detail, status = openalex_client.check_access(
        str(values.get("literature_openalex_api_key") or "")
    )
    return CredentialCheckOut(ok=ok, detail=detail, http_status=status)


@router.post('/kipris/check', response_model=CredentialCheckOut)
def check_kipris(session: Session = Depends(get_db)):
    values = settings_service.get_all(session)
    backend = patent_search.get_backend(values, 'kipris')
    if backend is None or not backend.status().configured:
        raise HTTPException(400, '키프리스 연동을 켜고 API 키를 저장하세요.')
    try:
        backend.search(patent_search.PatentSearchQuery('반도체', 1))
    except patent_search.PatentSearchError as exc:
        return CredentialCheckOut(ok=False, detail=str(exc))
    return CredentialCheckOut(ok=True, detail='키프리스 검색 API 연결을 확인했습니다. 1회 호출이 집계되었습니다.', http_status=200)


class KiprisUsageUpdate(BaseModel):
    month: str = Field(pattern=r'^\d{4}-\d{2}$')
    total_used: StrictInt = Field(ge=0, le=1000000)


@router.put('/kipris/usage', response_model=SettingsOut)
def update_kipris_usage(payload: KiprisUsageUpdate, session: Session = Depends(get_db)):
    try:
        kipris_quota.reconcile(payload.total_used, payload.month)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    session.expire_all()
    return _payload(session)


@router.post("/runtime-context/reset", response_model=SettingsOut)
def reset_runtime_context(session: Session = Depends(get_db)) -> SettingsOut:
    settings_service.update(session, {"runtime_context": DEFAULT_RUNTIME_CONTEXT})
    session.commit()
    return _payload(session)
