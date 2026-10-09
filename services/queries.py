from pathlib import Path

from sqlalchemy import func, select

from api.dependencies import get_entity
from api.models import CodeChunk, Project, Requirement, Snapshot, VerificationResult


def page(db, statement, limit: int, offset: int):
    total = db.scalar(select(func.count()).select_from(statement.subquery()))
    return {"items": list(db.scalars(statement.limit(limit).offset(offset))),
            "total": total, "limit": limit, "offset": offset}


def selected_snapshot(db, project_id: str, snapshot_id: str | None):
    project = get_entity(db, Project, project_id)
    identity = snapshot_id or project.latest_snapshot_id
    return get_entity(db, Snapshot, identity, project_id) if identity else None


def evidence_out(db, result):
    snapshot = get_entity(db, Snapshot, result.snapshot_id, result.project_id)
    output = []
    for cid in result.evidence_chunk_ids:
        chunk = db.get(CodeChunk, cid)
        if chunk is None:
            continue
        absolute = Path(snapshot.root_path) / chunk.file_path
        output.append({"chunk_id": cid, "file_path": chunk.file_path, "absolute_path": str(absolute),
                       "uri": absolute.as_uri(), "start_line": chunk.start_line,
                       "end_line": chunk.end_line, "code_snippet": chunk.content})
    return output


def verification_out(db, result):
    if result is None:
        return None
    return {"id": result.id, "requirement_id": result.requirement_id, "snapshot_id": result.snapshot_id,
            "revision": result.revision, "status": result.status, "reason": result.reason,
            "source": result.source, "suggested_code": result.suggested_code,
            "evidence": evidence_out(db, result)}


def requirements_with_results(db, project_id: str, snapshot_id: str | None):
    snapshot = selected_snapshot(db, project_id, snapshot_id)
    results = {}
    if snapshot:
        results = {r.requirement_id: r for r in db.scalars(select(VerificationResult).join(
            Requirement, Requirement.id == VerificationResult.requirement_id).where(
            VerificationResult.project_id == project_id, VerificationResult.snapshot_id == snapshot.id,
            VerificationResult.revision == Requirement.revision))}
    reqs = list(db.scalars(select(Requirement).where(Requirement.project_id == project_id)
                          .order_by(Requirement.req_id)))
    return snapshot, [(r, results.get(r.id) if results.get(r.id) and
                       results[r.id].revision == r.revision else None) for r in reqs]


def requirement_out(db, requirement, result=None):
    from api.schemas import RequirementSpec
    spec = RequirementSpec.model_validate(requirement).model_dump()
    return {**spec, "id": requirement.id, "project_id": requirement.project_id,
            "document_id": requirement.document_id, "revision": requirement.revision,
            "created_at": requirement.created_at, "analysis_state": "manual_reviewed" if result else "not_run",
            "status": result.status if result else None, "verification": verification_out(db, result)}
