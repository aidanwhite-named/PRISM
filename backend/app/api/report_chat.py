"""Report-local asynchronous conversation endpoints."""
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .. import report_chat, settings_service
from ..db import get_db
from ..enums import JobKind, JobStatus
from ..models import ExecutionJob, ReportChatTurn
from ..schemas import JobCreate
from .jobs import _resolve_provider

router = APIRouter(prefix='/api/jobs', tags=['report-chat'])


def report(session, job_id):
    job = session.get(ExecutionJob, job_id)
    if job is None:
        raise HTTPException(404, '보고서를 찾을 수 없습니다.')
    if job.job_kind != JobKind.PATENT_ANALYSIS:
        raise HTTPException(400, '구성대비 보고서에서 대화를 시작할 수 있습니다.')
    if job.status not in (JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.CANCELLED) or not (job.result_text or '').strip():
        raise HTTPException(409, '보고서 생성이 끝난 뒤 대화를 시작할 수 있습니다.')
    return job


class Question(BaseModel):
    question: str = Field(min_length=1, max_length=10000)
    request_id: UUID


@router.get('/{job_id}/chat')
def history(job_id: str, session: Session = Depends(get_db)):
    report(session, job_id)
    return [report_chat.out(t) for t in report_chat.turns(session, job_id)]


@router.post('/{job_id}/chat', status_code=202)
async def ask(job_id: str, payload: Question, session: Session = Depends(get_db)):
    job = report(session, job_id)
    if not payload.question.strip():
        raise HTTPException(422, '질문을 입력해 주세요.')
    request_id = str(payload.request_id)
    existing = session.query(ReportChatTurn).filter_by(job_id=job.id, request_id=request_id).first()
    if existing:
        return report_chat.out(existing)
    values = settings_service.get_all(session)
    provider_id, model = await _resolve_provider(JobCreate(provider=job.provider, model=job.model), values)
    try:
        turn = report_chat.create_turn(session, job, payload.question.strip(), request_id, provider_id, model, values)
        session.commit()
    except IntegrityError:
        session.rollback()
        existing = session.query(ReportChatTurn).filter_by(job_id=job.id, request_id=request_id).first()
        if existing:
            return report_chat.out(existing)
        raise HTTPException(409, '이 보고서의 답변을 이미 작성 중입니다.') from None
    except (ValueError, OSError) as exc:
        session.rollback()
        raise HTTPException(422, str(exc)) from exc
    report_chat.start(turn.id)
    return report_chat.out(turn)


@router.post('/{job_id}/chat/{turn_id}/cancel')
async def cancel(job_id: str, turn_id: str, session: Session = Depends(get_db)):
    report(session, job_id)
    turn = session.get(ReportChatTurn, turn_id)
    if turn is None or turn.job_id != job_id:
        raise HTTPException(404, '이 보고서의 대화를 찾을 수 없습니다.')
    await report_chat.cancel(turn_id)
    session.expire_all()
    return report_chat.out(session.get(ReportChatTurn, turn_id))
