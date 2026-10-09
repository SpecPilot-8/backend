import json
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from api.config import Settings
from api.dependencies import get_entity, session, settings
from api.models import Job, Project
from api.schemas import (
    Capability, JobOut, Page, ProjectCreate, ProjectOut,
)
from services import capabilities as features
from services import queries, workflow

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
