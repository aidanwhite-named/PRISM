"""Answer PDF registration, review, approval and local writing style."""
from __future__ import annotations

import shutil
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from .. import answer_extraction, answer_library as library, settings_service
from ..answer_models import AnswerCase, AnswerExtraction, AnswerRevision
from ..db import get_db
from ..enums import JobKind
from ..ingestion.security import UnsafeFilename
from ..ingestion.service import ingest_one
from ..models import ExecutionJob, utcnow
from ..schemas import JobCreate
from .jobs import _limits, _read_limited, _resolve_provider

router = APIRouter(prefix="/api/answers", tags=["answer-library"])


def get_case(session, case_id):
    case = session.get(AnswerCase, case_id)
    if case is None:
        raise HTTPException(404, "정답 사례를 찾을 수 없습니다.")
    return case


def out(case, session, detail=True):
    result = {key: getattr(case, key) for key in (
        "id", "title", "status", "revision", "edit_version", "source_job_id", "source_job_label",
        "created_at", "updated_at", "extraction_error")}
    result["example_count"] = len((case.draft or {}).get("examples", []))
    if detail:
        result.update(claim_text=case.claim_text, report_text=case.report_text,
            original_report=case.original_report, files=case.files, draft=case.draft)
        attempts = session.query(AnswerExtraction).filter_by(case_id=case.id).order_by(AnswerExtraction.created_at.desc()).all()
        result["extractions"] = [{k: getattr(a, k) for k in ("id", "status", "provider", "model",
            "error", "result_text", "parsed_result", "prompt_sha256", "execution_manifest", "created_at")} for a in attempts]
    return result


@router.get("")
def list_cases(session: Session = Depends(get_db)):
    return [out(row, session, False) for row in session.query(AnswerCase).order_by(AnswerCase.updated_at.desc())]


@router.get("/style")
def get_style():
    try:
        return {"text": library.read_style(), "path": str(library.style_path()),
                "max_chars": library.STYLE_MAX_CHARS, "token_budget": library.CONTEXT_TOKEN_BUDGET}
    except (ValueError, OSError) as exc:
        raise HTTPException(422, str(exc)) from exc


class StyleUpdate(BaseModel):
    text: str = Field(max_length=library.STYLE_MAX_CHARS)


@router.put("/style")
def update_style(payload: StyleUpdate):
    library.write_style(payload.text)
    return get_style()


@router.post("")
async def register_case(title: str = Form(..., min_length=1, max_length=200),
    claim_text: str = Form(default="", max_length=100000), source_job_id: str = Form(default=""),
    report: UploadFile | None = File(default=None), sources: list[UploadFile] = File(default_factory=list),
    session: Session = Depends(get_db)):
    case_id = str(uuid.uuid4())
    if not title.strip():
        raise HTTPException(422, "사례 이름을 입력하십시오.")
    root = library.case_dir(case_id)
    original = None
    if source_job_id:
        original = session.get(ExecutionJob, source_job_id)
        if original is None or original.job_kind != JobKind.PATENT_ANALYSIS:
            raise HTTPException(404, "연결할 구성대비 실행을 찾을 수 없습니다.")
    if report is None and not (original and original.result_text):
        raise HTTPException(400, "정답 보고서 PDF·TXT·MD를 등록하거나 결과가 있는 실행을 선택하십시오.")
    limits = _limits(session)
    inherited = [a for a in original.attachments if a.included] if original else []
    if len(sources) + len(inherited) + 1 > limits.max_files:
        raise HTTPException(400, "정답 보고서와 연결 문헌의 수가 업로드 한도를 넘습니다.")
    files, errors, consumed = [], [], 0
    def add_item(item, kind):
        files.append({"id": item.attachment_id, "kind": kind, "name": item.original_filename,
            "sha256": item.sha256, "file": str(Path(item.stored_path).relative_to(root)),
            "text_file": str(Path(item.normalized_text_path).relative_to(root)) if item.normalized_text_path else None,
            "read_ok": item.read_ok, "error": item.error, "page_count": item.page_count})
        if item.error:
            errors.append(f"{item.original_filename}: {item.error}")
    try:
        root.mkdir(parents=True, exist_ok=True)
        if report:
            data, consumed = await _read_limited(report, limits, consumed)
            item = ingest_one(report.filename or "report.pdf", data, root, True, limits)
            add_item(item, "report")
            # Keep partial text for review; never silently approve scanned PDFs.
            report_text = Path(item.normalized_text_path).read_text(encoding="utf-8") if item.normalized_text_path else ""
        else:
            report_text = original.result_text or ""
            item = ingest_one("정답보고서.md", report_text.encode("utf-8"), root, True, limits)
            add_item(item, "report")
            consumed += item.size_bytes
        for row in inherited:
            path = Path(row.stored_path)
            if not path.is_file():
                raise ValueError(f"원본 문헌이 없습니다: {row.original_filename}")
            if path.stat().st_size > limits.max_file_size_bytes or consumed + path.stat().st_size > limits.max_total_upload_bytes:
                raise ValueError("연결 문헌이 업로드 크기 한도를 넘습니다.")
            data = path.read_bytes()
            import hashlib
            if row.sha256 and hashlib.sha256(data).hexdigest() != row.sha256:
                raise ValueError(f"원본 문헌이 변경되었습니다: {row.original_filename}")
            item = ingest_one(row.original_filename, data, root, True, limits, row.role)
            consumed += len(data)
            add_item(item, "source")
        for upload in sources:
            data, consumed = await _read_limited(upload, limits, consumed)
            add_item(ingest_one(upload.filename or "source.pdf", data, root, True, limits), "source")
        case = AnswerCase(id=case_id, title=title.strip(), claim_text=claim_text.strip() or (original.claim_text if original else ""),
            source_job_id=original.id if original else None,
            source_job_label=original.prompt_name if original else "",
            original_report=report_text, report_text=report_text, files=files,
            draft={"style_rules": [], "examples": []}, extraction_error="\n".join(errors) or None)
        session.add(case)
        session.commit()
        return out(case, session)
    except (ValueError, OSError, UnsafeFilename) as exc:
        session.rollback()
        if root.is_dir():
            shutil.rmtree(root)  # UUID path verified by case_dir; no shared files.
        raise HTTPException(422, str(exc)) from exc


@router.get("/{case_id}")
def detail(case_id: str, session: Session = Depends(get_db)):
    return out(get_case(session, case_id), session)


class CaseUpdate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    claim_text: str = Field(max_length=100000)
    report_text: str = Field(min_length=1, max_length=1000000)
    draft: library.CaseContent
    edit_version: int


def check_editable(session, case, expected_version=None):
    if expected_version is not None and case.edit_version != expected_version:
        raise HTTPException(409, "다른 화면에서 변경되었습니다. 새로 불러온 뒤 저장하십시오.")
    if session.query(AnswerExtraction).filter(AnswerExtraction.case_id == case.id,
            AnswerExtraction.status.in_(["queued", "running"])).first():
        raise HTTPException(409, "AI 정리가 끝나거나 취소된 뒤 수정·확정할 수 있습니다.")


@router.put("/{case_id}")
def update_case(case_id: str, payload: CaseUpdate, session: Session = Depends(get_db)):
    case = get_case(session, case_id)
    check_editable(session, case, payload.edit_version)
    case.title, case.claim_text, case.report_text = payload.title, payload.claim_text, payload.report_text
    try:
        case.draft = library.validate_content(case, payload.draft.model_dump())
    except (ValueError, OSError) as exc:
        session.rollback()
        raise HTTPException(422, str(exc)) from exc
    case.edit_version += 1
    case.status = "draft"
    case.updated_at = utcnow()
    session.commit()
    return out(case, session)


class VersionRequest(BaseModel):
    edit_version: int


@router.post("/{case_id}/approve")
def approve_case(case_id: str, payload: VersionRequest, session: Session = Depends(get_db)):
    case = get_case(session, case_id)
    check_editable(session, case, payload.edit_version)
    try:
        library.approve(session, case)
    except (ValueError, OSError) as exc:
        raise HTTPException(422, str(exc)) from exc
    session.commit()
    return out(case, session)


@router.post("/{case_id}/archive")
def archive_case(case_id: str, payload: VersionRequest, session: Session = Depends(get_db)):
    case = get_case(session, case_id)
    check_editable(session, case, payload.edit_version)
    case.status = "draft" if case.status == "archived" else "archived"
    case.edit_version += 1
    case.updated_at = utcnow()
    session.commit()
    return out(case, session)


class ExtractRequest(BaseModel):
    provider: str | None = None
    model: str | None = None


@router.post("/{case_id}/extract")
async def extract(case_id: str, payload: ExtractRequest, session: Session = Depends(get_db)):
    case = get_case(session, case_id)
    check_editable(session, case)
    if not case.report_text.strip():
        raise HTTPException(422, "추출할 보고서 본문이 없습니다. 텍스트 PDF를 등록하거나 본문을 입력하십시오.")
    if any(f["kind"] == "report" and not f["read_ok"] for f in case.files) and case.report_text == case.original_report:
        raise HTTPException(422, "정답 PDF를 정상 추출하지 못했습니다. 본문을 수정하거나 텍스트 PDF를 등록하십시오.")
    values = settings_service.get_all(session)
    provider, model = await _resolve_provider(JobCreate(provider=payload.provider, model=payload.model), values)
    try:
        attempt = answer_extraction.create_attempt(session, case, provider, model)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    session.commit()
    answer_extraction.start(attempt.id)
    return out(case, session)


@router.post("/{case_id}/extractions/{attempt_id}/cancel")
async def cancel(case_id: str, attempt_id: str, session: Session = Depends(get_db)):
    attempt = session.get(AnswerExtraction, attempt_id)
    if attempt is None or attempt.case_id != case_id:
        raise HTTPException(404, "정리 기록을 찾을 수 없습니다.")
    await answer_extraction.cancel(attempt_id)
    session.expire_all()
    return out(get_case(session, case_id), session)


@router.get("/{case_id}/revisions")
def revisions(case_id: str, session: Session = Depends(get_db)):
    get_case(session, case_id)
    return [{"revision": row.revision, "snapshot": row.snapshot, "created_at": row.created_at}
        for row in session.query(AnswerRevision).filter_by(case_id=case_id).order_by(AnswerRevision.revision.desc())]


@router.get("/{case_id}/files/{file_id}")
def download(case_id: str, file_id: str, session: Session = Depends(get_db)):
    case = get_case(session, case_id)
    item = next((f for f in case.files if f["id"] == file_id), None)
    if not item:
        raise HTTPException(404, "파일을 찾을 수 없습니다.")
    root = library.case_dir(case_id)
    path = (root / item["file"]).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise HTTPException(404, "원본 파일을 찾을 수 없습니다.")
    return FileResponse(path, filename=item["name"])
