"""PRISM v2 answer library. Independent from execution-history deletion.

Approved revisions and model extraction attempts are append-only. Jobs keep a
complete copy of the selected examples, so later edits cannot change an input.
"""
from sqlalchemy import Column, DateTime, ForeignKey, Integer, JSON, String, Text, UniqueConstraint

from .models import Base, _uuid, utcnow


class AnswerCase(Base):
    __tablename__ = "answer_cases"
    id = Column(String(36), primary_key=True, default=_uuid)
    title = Column(String(200), nullable=False)
    claim_text = Column(Text, nullable=False, default="")
    source_job_id = Column(String(36), nullable=True, index=True)
    source_job_label = Column(Text, nullable=False, default="")
    original_report = Column(Text, nullable=False, default="")
    report_text = Column(Text, nullable=False, default="")
    files = Column(JSON, nullable=False, default=list)
    draft = Column(JSON, nullable=False, default=dict)
    status = Column(String(20), nullable=False, default="draft", index=True)
    revision = Column(Integer, nullable=False, default=0)
    edit_version = Column(Integer, nullable=False, default=0)
    __mapper_args__ = {"version_id_col": edit_version, "version_id_generator": False}
    extraction_error = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)


class AnswerRevision(Base):
    __tablename__ = "answer_revisions"
    id = Column(String(36), primary_key=True, default=_uuid)
    case_id = Column(String(36), ForeignKey("answer_cases.id", ondelete="CASCADE"), nullable=False, index=True)
    revision = Column(Integer, nullable=False)
    snapshot = Column(JSON, nullable=False)
    context = Column(JSON, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    __table_args__ = (UniqueConstraint("case_id", "revision"),)


class AnswerExtraction(Base):
    __tablename__ = "answer_extractions"
    id = Column(String(36), primary_key=True, default=_uuid)
    case_id = Column(String(36), ForeignKey("answer_cases.id", ondelete="CASCADE"), nullable=False, index=True)
    status = Column(String(20), nullable=False, default="queued", index=True)
    input_version = Column(Integer, nullable=False)
    provider = Column(String(30), nullable=False)
    model = Column(String(160), nullable=True)
    prompt_snapshot = Column(Text, nullable=False)
    system_snapshot = Column(Text, nullable=False)
    prompt_sha256 = Column(String(64), nullable=False)
    result_text = Column(Text, nullable=True)
    parsed_result = Column(JSON, nullable=True)
    execution_manifest = Column(JSON, nullable=True)
    error = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    completed_at = Column(DateTime(timezone=True), nullable=True)


class ReportContextSnapshot(Base):
    __tablename__ = "report_context_snapshots"
    job_id = Column(String(36), ForeignKey("execution_jobs.id", ondelete="CASCADE"), primary_key=True)
    manifest = Column(JSON, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
