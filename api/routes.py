import json
from collections import Counter
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Query, Request, Response, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from api.config import Settings
from api.dependencies import get_entity, session, settings
from api.errors import APIError
from api.models import CodeChunk, Document, Job, Project, Requirement, Snapshot, TestCase, VerificationResult
from api.models import new_id, now
from api.schemas import (
    Capability, ChunkOut, CodeIndexRequest, ConnectionCheck, DocumentDetail, DocumentOut, ExportRequest,
    GenerateTestsRequest, JobOut, LLMSettings, Page, ProjectCreate, ProjectOut, RequirementCreate,
    RequirementOut, RequirementSpec, ReverifyRequest, SnapshotOut, TestCaseCreate, TestCaseOut,
    VerificationImport, VerificationOut, VerifyRequest,
)
from services import capabilities as features
from services import code, documents, exports, queries, workflow

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


def snapshot_out(db, snapshot):
    count = db.scalar(select(func.count()).select_from(CodeChunk).where(CodeChunk.snapshot_id == snapshot.id))
    return {
        **{field: getattr(snapshot, field) for field in
           ("id", "project_id", "root_path", "content_hash", "file_count", "warnings", "created_at")},
        "chunk_count": count,
    }


@router.post("/api/v1/code/index", response_model=SnapshotOut, status_code=201, tags=["code"])
def index_code(body: CodeIndexRequest, db: DB, config: Config):
    project = get_entity(db, Project, body.project_id)
    root, digest, count, chunks, warnings = code.index_source(project.workspace_path, body.relative_path, config)
    snapshot = db.scalar(select(Snapshot).where(Snapshot.project_id == project.id, Snapshot.content_hash == digest))
    if snapshot is None:
        snapshot = Snapshot(project_id=project.id, root_path=str(root), content_hash=digest,
                            file_count=count, warnings=warnings)
        db.add(snapshot)
        db.flush()
        for chunk in chunks:
            db.add(CodeChunk(snapshot_id=snapshot.id, file_path=chunk.file_path, start_line=chunk.start_line,
                             end_line=chunk.end_line, chunk_type=chunk.chunk_type,
                             symbol=chunk.symbol_fqn, content=chunk.content))
    project.latest_snapshot_id = snapshot.id
    db.commit()
    return snapshot_out(db, snapshot)


@router.get("/api/v1/code/snapshots", response_model=Page[SnapshotOut], tags=["code"])
def list_snapshots(project_id: str, db: DB, limit: Limit = 50, offset: Offset = 0):
    get_entity(db, Project, project_id)
    result = queries.page(db, select(Snapshot).where(Snapshot.project_id == project_id)
                          .order_by(Snapshot.created_at, Snapshot.id), limit, offset)
    result["items"] = [snapshot_out(db, s) for s in result["items"]]
    return result


@router.get("/api/v1/code/snapshots/{snapshot_id}", response_model=SnapshotOut, tags=["code"])
def get_snapshot(snapshot_id: str, project_id: str, db: DB):
    return snapshot_out(db, get_entity(db, Snapshot, snapshot_id, project_id))


@router.get("/api/v1/code/chunks", response_model=Page[ChunkOut], tags=["code"])
def list_chunks(project_id: str, snapshot_id: str, db: DB, limit: Limit = 50, offset: Offset = 0):
    get_entity(db, Snapshot, snapshot_id, project_id)
    return queries.page(db, select(CodeChunk).where(CodeChunk.snapshot_id == snapshot_id)
                        .order_by(CodeChunk.file_path, CodeChunk.start_line, CodeChunk.id), limit, offset)


@router.get("/api/v1/code/chunks/{chunk_id}", response_model=ChunkOut, tags=["code"])
def get_chunk(chunk_id: str, project_id: str, db: DB):
    chunk = get_entity(db, CodeChunk, chunk_id)
    get_entity(db, Snapshot, chunk.snapshot_id, project_id)
    return chunk


def choose_requirements(db, project_id, ids):
    reqs = list(db.scalars(select(Requirement).where(Requirement.project_id == project_id)))
    if ids is not None:
        reqs = [get_entity(db, Requirement, identity, project_id) for identity in dict.fromkeys(ids)]
    if not reqs:
        raise APIError(409, "NO_REQUIREMENTS", "먼저 요구사항을 추출하거나 수동 등록하세요",
                       features.missing("requirement_extraction"))
    return [{"id": r.id, "revision": r.revision} for r in reqs]


@router.post("/api/code/verify", response_model=JobOut, status_code=202, tags=["analysis"])
@router.post("/api/v1/verify/run", response_model=JobOut, status_code=202, tags=["analysis"])
def verify(body: VerifyRequest, db: DB):
    project = get_entity(db, Project, body.project_id)
    get_entity(db, Snapshot, body.snapshot_id, project.id)
    reqs = choose_requirements(db, project.id, body.requirement_ids)
    return workflow.job_out(workflow.create_blocked_job(db, project, "verify", {"requirements": reqs},
                                                       body.snapshot_id))


@router.post("/api/v1/requirements/{requirement_id}/reverify",
             response_model=JobOut, status_code=202, tags=["analysis"])
def reverify(requirement_id: str, project_id: str, body: ReverifyRequest, db: DB):
    return verify(VerifyRequest(project_id=project_id, snapshot_id=body.snapshot_id,
                                requirement_ids=[requirement_id]), db)


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


@router.post("/api/v1/verification-results/import", response_model=VerificationOut, tags=["results"])
def import_verification(body: VerificationImport, db: DB):
    req = get_entity(db, Requirement, body.requirement_id, body.project_id)
    get_entity(db, Snapshot, body.snapshot_id, body.project_id)
    check_revision(req, body.revision)
    for identity in body.evidence_chunk_ids:
        chunk = get_entity(db, CodeChunk, identity)
        if chunk.snapshot_id != body.snapshot_id:
            raise APIError(422, "INVALID_EVIDENCE", "근거 청크가 선택한 스냅샷에 속하지 않습니다")
    result = current_result(db, req, body.snapshot_id)
    values = body.model_dump()
    if result is None:
        result = VerificationResult(**values)
        db.add(result)
    else:
        for key, value in values.items():
            setattr(result, key, value)
        result.updated_at = now()
    db.commit()
    return queries.verification_out(db, result)


def check_revision(req, revision):
    if req.revision != revision:
        raise APIError(409, "STALE_REQUIREMENT", "요구사항이 변경되었습니다. 현재 버전을 다시 조회하세요")


@router.get("/api/v1/requirements/{requirement_id}/call-flow", tags=["results"])
def call_flow(requirement_id: str, project_id: str, db: DB):
    get_entity(db, Requirement, requirement_id, project_id)
    features.not_implemented("call_flow")


@router.get("/api/v1/requirements/{requirement_id}/fix", tags=["results"])
def get_fix(requirement_id: str, project_id: str, db: DB, snapshot_id: str | None = None):
    req = get_entity(db, Requirement, requirement_id, project_id)
    result = current_result(db, req, snapshot_id)
    if result is None or not result.suggested_code:
        features.not_implemented("fix_generation")
    return {"requirement_id": req.id, "snapshot_id": result.snapshot_id, "revision": result.revision,
            "source": "manual", "suggested_code": result.suggested_code,
            "evidence": queries.evidence_out(db, result), "application_status": "suggestion_only"}


@router.get("/api/v1/traceability", response_model=Page[RequirementOut], tags=["results"])
def traceability(project_id: str, db: DB, snapshot_id: str | None = None,
                  limit: Limit = 50, offset: Offset = 0):
    return list_requirements(project_id, db, snapshot_id, limit=limit, offset=offset)


@router.get("/api/v1/projects/{project_id}/dashboard", tags=["results"])
def dashboard(project_id: str, db: DB, snapshot_id: str | None = None):
    snapshot, rows = queries.requirements_with_results(db, project_id, snapshot_id)
    counts = Counter(r.status if r else "not_run" for _, r in rows)
    for status in ("implemented", "partial", "mismatch", "not_found", "needs_review",
                   "not_statically_verifiable", "not_run"):
        counts.setdefault(status, 0)
    total = len(rows)
    evaluated = total - counts["not_run"]
    return {"project_id": project_id, "snapshot_id": snapshot.id if snapshot else None,
            "total": total, "counts": dict(counts), "evaluated": evaluated,
            "analysis_coverage_percent": round(100 * evaluated / total, 2) if total else 0,
            "implemented_percent": round(100 * counts["implemented"] / total, 2) if total else 0,
            "result_source": "manual", "ai_status": "not_connected"}


@router.get("/api/v1/diagnostics", tags=["results"])
def diagnostics(project_id: str, db: DB, snapshot_id: str | None = None):
    snapshot, rows = queries.requirements_with_results(db, project_id, snapshot_id)
    items = []
    for req, result in rows:
        if result is None or result.status not in ("mismatch", "partial"):
            continue
        for evidence in queries.evidence_out(db, result):
            items.append({"requirement_id": req.id, "req_id": req.req_id, "revision": req.revision,
                          "uri": evidence["uri"], "file_path": evidence["file_path"],
                          "range": {"start": {"line": evidence["start_line"] - 1, "character": 0},
                                    "end": {"line": evidence["end_line"] - 1,
                                            "character": len(evidence["code_snippet"].splitlines()[-1])}},
                          "severity": 0 if result.status == "mismatch" else 1,
                          "code": req.req_id, "source": "SpecPilot (manual)", "message": result.reason})
    return {"snapshot_id": snapshot.id if snapshot else None, "items": items}


@router.post("/api/v1/test-spec/generate", response_model=JobOut, status_code=202, tags=["tests"])
def generate_tests(body: GenerateTestsRequest, db: DB):
    project = get_entity(db, Project, body.project_id)
    reqs = choose_requirements(db, project.id, body.requirement_ids)
    snapshot = queries.selected_snapshot(db, project.id, None)
    return workflow.job_out(workflow.create_blocked_job(db, project, "generate_tests", {"requirements": reqs},
                                                       snapshot.id if snapshot else None))


@router.post("/api/v1/test-cases", response_model=TestCaseOut, status_code=201, tags=["tests"])
def create_test_case(body: TestCaseCreate, db: DB):
    req = get_entity(db, Requirement, body.requirement_id, body.project_id)
    check_revision(req, body.revision)
    case = TestCase(**body.model_dump())
    db.add(case)
    db.commit()
    return case


@router.put("/api/v1/test-cases/{test_case_id}", response_model=TestCaseOut, tags=["tests"])
def update_test_case(test_case_id: str, body: TestCaseCreate, db: DB):
    case = get_entity(db, TestCase, test_case_id, body.project_id)
    req = get_entity(db, Requirement, body.requirement_id, body.project_id)
    check_revision(req, body.revision)
    for key, value in body.model_dump().items():
        setattr(case, key, value)
    db.commit()
    return case


def current_cases(project_id):
    return select(TestCase).join(Requirement, TestCase.requirement_id == Requirement.id).where(
        TestCase.project_id == project_id, TestCase.revision == Requirement.revision).order_by(TestCase.test_id)


@router.get("/api/v1/test-cases", response_model=Page[TestCaseOut], tags=["tests"])
def list_test_cases(project_id: str, db: DB, limit: Limit = 50, offset: Offset = 0):
    get_entity(db, Project, project_id)
    return queries.page(db, current_cases(project_id), limit, offset)


def download(data, filename):
    return Response(content=data, media_type=XLSX,
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@router.post("/api/test-spec/export", tags=["tests"], response_class=Response,
             responses={200: {"content": {XLSX: {"schema": {"type": "string", "format": "binary"}}}}})
@router.post("/api/v1/test-spec/export", tags=["tests"], response_class=Response,
             responses={200: {"content": {XLSX: {"schema": {"type": "string", "format": "binary"}}}}})
def export_tests(body: ExportRequest, db: DB):
    get_entity(db, Project, body.project_id)
    snapshot, rows = queries.requirements_with_results(db, body.project_id, body.snapshot_id)
    cases = list(db.scalars(current_cases(body.project_id)))
    if not cases:
        raise APIError(409, "NO_TEST_CASES", "내보낼 테스트 케이스가 없습니다. 생성기를 연결하거나 수동 등록하세요",
                       features.missing("test_case_generation"))
    notes = {r.id: f"불일치 검토 필요: {result.reason}" for r, result in rows
             if result and result.status == "mismatch"}
    return download(exports.test_spec(cases, {r.id: r for r, _ in rows}, notes), "test-spec.xlsx")


@router.post("/api/v1/traceability/export", tags=["results"], response_class=Response,
             responses={200: {"content": {XLSX: {"schema": {"type": "string", "format": "binary"}}}}})
def export_traceability(body: ExportRequest, db: DB):
    project = get_entity(db, Project, body.project_id)
    snapshot, rows = queries.requirements_with_results(db, project.id, body.snapshot_id)
    return download(exports.traceability(project, snapshot,
                    [(req, queries.verification_out(db, result)) for req, result in rows]), "traceability.xlsx")


@router.get("/api/v1/projects/{project_id}/llm", tags=["settings"])
def llm_settings(project_id: str, db: DB):
    project = get_entity(db, Project, project_id)
    return {"config": project.llm_config, "config_version": project.config_version,
            "connection_status": "not_connected", "model_limit": 1, "api_key_storage": "client_secret_storage"}


@router.put("/api/v1/projects/{project_id}/llm", tags=["settings"])
def set_llm_settings(project_id: str, body: LLMSettings, db: DB):
    project = get_entity(db, Project, project_id)
    if project.llm_config and (body.provider != project.llm_config["provider"] or
                               body.model != project.llm_config["model"]):
        features.not_implemented("multi_model")
    value = body.model_dump()
    if project.llm_config != value:
        project.llm_config = value
        project.config_version += 1
    db.commit()
    return llm_settings(project_id, db)


@router.post("/api/v1/projects/{project_id}/llm/connection-check", tags=["settings"])
def check_connection(project_id: str, body: ConnectionCheck, db: DB):
    get_entity(db, Project, project_id)
    features.not_implemented("llm_connection")
