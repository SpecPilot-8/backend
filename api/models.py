from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import JSON, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from api.database import Base


def new_id() -> str:
    return str(uuid4())


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Project(Base):
    __tablename__ = "api_project"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(200))
    workspace_path: Mapped[str] = mapped_column(Text)
    llm_config: Mapped[dict] = mapped_column(JSON, default=dict)
    config_version: Mapped[int] = mapped_column(Integer, default=0)
    latest_snapshot_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[str] = mapped_column(String(40), default=now)


class Document(Base):
    __tablename__ = "api_document"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("api_project.id"), index=True)
    filename: Mapped[str] = mapped_column(String(255))
    format: Mapped[str] = mapped_column(String(10))
    storage_path: Mapped[str] = mapped_column(Text)
    text: Mapped[str] = mapped_column(Text)
    blocks: Mapped[list] = mapped_column(JSON)
    warnings: Mapped[list] = mapped_column(JSON)
    pipeline_status: Mapped[str] = mapped_column(String(30), default="awaiting_ai")
    created_at: Mapped[str] = mapped_column(String(40), default=now)


class Requirement(Base):
    __tablename__ = "api_requirement"
    __table_args__ = (UniqueConstraint("project_id", "req_id"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("api_project.id"), index=True)
    document_id: Mapped[str | None] = mapped_column(ForeignKey("api_document.id"), nullable=True)
    req_id: Mapped[str] = mapped_column(String(100))
    title: Mapped[str] = mapped_column(String(300))
    original_text: Mapped[str] = mapped_column(Text, default="")
    condition: Mapped[str] = mapped_column(Text, default="")
    action: Mapped[str] = mapped_column(Text, default="")
    expected: Mapped[str] = mapped_column(Text, default="")
    checklists: Mapped[list] = mapped_column(JSON, default=list)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[str] = mapped_column(String(40), default=now)


class Snapshot(Base):
    __tablename__ = "api_snapshot"
    __table_args__ = (UniqueConstraint("project_id", "content_hash"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("api_project.id"), index=True)
    root_path: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64))
    file_count: Mapped[int] = mapped_column(Integer)
    warnings: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[str] = mapped_column(String(40), default=now)


class CodeChunk(Base):
    __tablename__ = "api_chunk"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    snapshot_id: Mapped[str] = mapped_column(ForeignKey("api_snapshot.id"), index=True)
    file_path: Mapped[str] = mapped_column(Text)
    start_line: Mapped[int] = mapped_column(Integer)
    end_line: Mapped[int] = mapped_column(Integer)
    chunk_type: Mapped[str] = mapped_column(String(30))
    symbol: Mapped[str | None] = mapped_column(Text, nullable=True)
    content: Mapped[str] = mapped_column(Text)


class Job(Base):
    __tablename__ = "api_job"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("api_project.id"), index=True)
    operation: Mapped[str] = mapped_column(String(50))
    status: Mapped[str] = mapped_column(String(30), default="blocked")
    snapshot_id: Mapped[str | None] = mapped_column(ForeignKey("api_snapshot.id"), nullable=True)
    request_data: Mapped[dict] = mapped_column(JSON)
    model_config_snapshot: Mapped[dict] = mapped_column(JSON)
    missing_features: Mapped[list] = mapped_column(JSON)
    created_at: Mapped[str] = mapped_column(String(40), default=now)


class VerificationResult(Base):
    __tablename__ = "api_verification_result"
    __table_args__ = (UniqueConstraint("requirement_id", "snapshot_id", "revision"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("api_project.id"), index=True)
    requirement_id: Mapped[str] = mapped_column(ForeignKey("api_requirement.id"), index=True)
    snapshot_id: Mapped[str] = mapped_column(ForeignKey("api_snapshot.id"), index=True)
    revision: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(40))
    reason: Mapped[str] = mapped_column(Text)
    evidence_chunk_ids: Mapped[list] = mapped_column(JSON)
    suggested_code: Mapped[str | None] = mapped_column(Text, nullable=True)
    source: Mapped[str] = mapped_column(String(20), default="manual")
    updated_at: Mapped[str] = mapped_column(String(40), default=now)


class TestCase(Base):
    __tablename__ = "api_test_case"
    __table_args__ = (UniqueConstraint("project_id", "test_id"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("api_project.id"), index=True)
    requirement_id: Mapped[str] = mapped_column(ForeignKey("api_requirement.id"), index=True)
    revision: Mapped[int] = mapped_column(Integer)
    test_id: Mapped[str] = mapped_column(String(100))
    title: Mapped[str] = mapped_column(String(300))
    type: Mapped[str] = mapped_column(String(30))
    preconditions: Mapped[str] = mapped_column(Text)
    steps: Mapped[list] = mapped_column(JSON)
    input_data: Mapped[str] = mapped_column(Text)
    expected: Mapped[str] = mapped_column(Text)
    actual: Mapped[str] = mapped_column(Text, default="")
    result: Mapped[str | None] = mapped_column(String(20), nullable=True)
    notes: Mapped[str] = mapped_column(Text, default="")
    source: Mapped[str] = mapped_column(String(20), default="manual")
