import json
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from api.config import Settings
from api.dependencies import get_entity, session, settings
from api.errors import APIError
from api.models import Document, Job, Project, Requirement, VerificationResult
from api.models import new_id
from api.schemas import (
    Capability, DocumentDetail, DocumentOut, JobOut, Page, ProjectCreate, ProjectOut, RequirementCreate,
    RequirementOut, RequirementSpec,
)
from services import capabilities as features
from services import documents, queries, workflow

from services.workspace import workspace_path

router = APIRouter()
DB = Annotated[Session, Depends(session)]
Config = Annotated[Settings, Depends(settings)]
Limit = Annotated[int, Query(ge=1, le=200)]
Offset = Annotated[int, Query(ge=0)]
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"




@router.get("/health", tags=["system"])
def health(db: DB):
    db.execute(select(1))
    return {"status": "ok", "ai_status": "not_connected"}


@router.get("/api/v1/capabilities", response_model=list[Capability], tags=["system"])
def capabilities(request: Request):
    return features.capabilities({r.path for r in request.app.routes if hasattr(r, "path")})


@router.post("/api/v1/project/init", response_model=ProjectOut, status_code=201, tags=["projects"])
@router.post("/api/v1/projects", response_model=ProjectOut, status_code=201, tags=["projects"])
def create_project(body: ProjectCreate, db: DB, config: Config):
    project = Project(name=body.name, workspace_path=str(workspace_path(body.workspace_path, config)))
    db.add(project)
    db.commit()
    return project


@router.get("/api/v1/projects", response_model=Page[ProjectOut], tags=["projects"])
def list_projects(db: DB, limit: Limit = 50, offset: Offset = 0):
    return queries.page(db, select(Project).order_by(Project.created_at, Project.id), limit, offset)


@router.get("/api/v1/projects/{project_id}", response_model=ProjectOut, tags=["projects"])
def get_project(project_id: str, db: DB):
    return get_entity(db, Project, project_id)


def document_out(document, detail=False):
    data = {field: getattr(document, field) for field in
            ("id", "project_id", "filename", "format", "pipeline_status", "warnings", "created_at")}
    data["missing_features"] = features.missing("requirement_extraction", "checklist_decomposition")
    if detail:
        data.update(text=document.text, blocks=document.blocks)
    return data


@router.post("/api/spec/parse", response_model=DocumentDetail, status_code=201, tags=["documents"])
@router.post("/api/v1/documents/parse", response_model=DocumentDetail, status_code=201, tags=["documents"])
def parse_document(db: DB, config: Config, project_id: Annotated[str, Form()],
                   file: Annotated[UploadFile, File()]):
    get_entity(db, Project, project_id)
    filename = Path((file.filename or "").replace("\\", "/")).name
    if not filename or len(filename) > 255:
        raise APIError(422, "INVALID_FILENAME", "파일 이름을 확인하세요")
    # Read only a bounded amount before parsing or saving anything.
    try:
        data = file.file.read(config.max_upload_bytes + 1)
    finally:
        file.file.close()
    if len(data) > config.max_upload_bytes:
        raise APIError(413, "UPLOAD_TOO_LARGE", "업로드 용량 제한을 초과합니다")
    text, blocks, warnings = documents.extract(data, filename)
    identity = new_id()
    path = config.data_dir / "uploads" / f"{identity}{Path(filename).suffix.lower()}"
    document = Document(id=identity, project_id=project_id, filename=filename,
                        format=path.suffix[1:], storage_path=str(path), text=text, blocks=blocks,
                        warnings=warnings)
    try:
        path.write_bytes(data)
        db.add(document)
        db.commit()
    except Exception:
        path.unlink(missing_ok=True)
        raise
    return document_out(document, detail=True)


@router.get("/api/v1/documents", response_model=Page[DocumentOut], tags=["documents"])
def list_documents(project_id: str, db: DB, limit: Limit = 50, offset: Offset = 0):
    get_entity(db, Project, project_id)
    result = queries.page(db, select(Document).where(Document.project_id == project_id)
                          .order_by(Document.created_at, Document.id), limit, offset)
    result["items"] = [document_out(d) for d in result["items"]]
    return result


@router.get("/api/v1/documents/{document_id}", response_model=DocumentDetail, tags=["documents"])
def get_document(document_id: str, project_id: str, db: DB):
    return document_out(get_entity(db, Document, document_id, project_id), detail=True)


@router.post("/api/v1/documents/{document_id}/requirements/extract",
             response_model=JobOut, status_code=202, tags=["analysis"])
def extract_requirements(document_id: str, project_id: str, db: DB):
    get_entity(db, Document, document_id, project_id)
    project = get_entity(db, Project, project_id)
    return workflow.job_out(workflow.create_blocked_job(
        db, project, "extract_requirements", {"document_id": document_id}))


def current_result(db, requirement, snapshot_id=None):
    snapshot = queries.selected_snapshot(db, requirement.project_id, snapshot_id)
    if snapshot:
        return db.scalar(select(VerificationResult).where(
            VerificationResult.requirement_id == requirement.id,
            VerificationResult.snapshot_id == snapshot.id, VerificationResult.revision == requirement.revision))
    return None


@router.post("/api/v1/requirements", response_model=RequirementOut, status_code=201, tags=["requirements"])
def create_requirement(body: RequirementCreate, db: DB):
    get_entity(db, Project, body.project_id)
    if body.document_id:
        get_entity(db, Document, body.document_id, body.project_id)
    requirement = Requirement(**body.model_dump())
    db.add(requirement)
    db.commit()
    return queries.requirement_out(db, requirement)


@router.put("/api/v1/requirements/{requirement_id}", response_model=RequirementOut, tags=["requirements"])
def update_requirement(requirement_id: str, project_id: str, body: RequirementSpec, db: DB):
    requirement = get_entity(db, Requirement, requirement_id, project_id)
    values = body.model_dump()
    changed = any(getattr(requirement, key) != value for key, value in values.items())
    if changed:
        for key, value in values.items():
            setattr(requirement, key, value)
        requirement.revision += 1
    db.commit()
    return queries.requirement_out(db, requirement, current_result(db, requirement))


@router.get("/api/v1/requirements", response_model=Page[RequirementOut], tags=["requirements"])
def list_requirements(project_id: str, db: DB, snapshot_id: str | None = None,
                      status: str | None = None, q: str | None = None,
                      limit: Limit = 50, offset: Offset = 0):
    _, rows = queries.requirements_with_results(db, project_id, snapshot_id)
    items = [queries.requirement_out(db, req, result) for req, result in rows
             if (status is None or (result.status if result else "not_run") == status)
             and (q is None or q.casefold() in f"{req.req_id} {req.title}".casefold())]
    return {"items": items[offset:offset + limit], "total": len(items), "limit": limit, "offset": offset}


@router.get("/api/v1/requirements/{requirement_id}", response_model=RequirementOut, tags=["requirements"])
def get_requirement(requirement_id: str, project_id: str, db: DB, snapshot_id: str | None = None):
    req = get_entity(db, Requirement, requirement_id, project_id)
    return queries.requirement_out(db, req, current_result(db, req, snapshot_id))


@router.get("/api/v1/requirements/{requirement_id}/checklists", tags=["requirements"])
def get_checklists(requirement_id: str, project_id: str, db: DB):
    req = get_entity(db, Requirement, requirement_id, project_id)
    return {"requirement_id": req.id, "revision": req.revision, "source": "manual", "items": req.checklists}


@router.post("/api/v1/requirements/{requirement_id}/checklists/generate",
             response_model=JobOut, status_code=202, tags=["analysis"])
def generate_checklists(requirement_id: str, project_id: str, db: DB):
    req = get_entity(db, Requirement, requirement_id, project_id)
    project = get_entity(db, Project, project_id)
    return workflow.job_out(workflow.create_blocked_job(db, project, "generate_checklists",
                                                       {"requirement_id": req.id, "revision": req.revision}))


@router.get("/api/v1/jobs", response_model=Page[JobOut], tags=["analysis"])
def list_jobs(project_id: str, db: DB, limit: Limit = 50, offset: Offset = 0):
    get_entity(db, Project, project_id)
    result = queries.page(db, select(Job).where(Job.project_id == project_id)
                          .order_by(Job.created_at, Job.id), limit, offset)
    result["items"] = [workflow.job_out(j) for j in result["items"]]
    return result


@router.get("/api/v1/jobs/{job_id}", response_model=JobOut, tags=["analysis"])
def get_job(job_id: str, project_id: str, db: DB):
    return workflow.job_out(get_entity(db, Job, job_id, project_id))


@router.get("/api/v1/jobs/{job_id}/events", tags=["analysis"])
def job_events(job_id: str, project_id: str, db: DB):
    job = workflow.job_out(get_entity(db, Job, job_id, project_id))
    # All jobs are terminal blocked today. Send one event and close instead of an idle fake stream.
    event = f"id: {job_id}\nevent: blocked\ndata: {json.dumps(job, ensure_ascii=False)}\n\n"
    return StreamingResponse(iter([event]), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
