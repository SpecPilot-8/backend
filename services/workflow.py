from api.models import Job
from services.capabilities import missing

OPERATIONS = {
    "extract_requirements": ("requirement_extraction", "checklist_decomposition", "llm_connection"),
    "verify": ("code_matching", "call_flow", "implementation_judgment", "fix_generation", "llm_connection"),
    "generate_tests": ("test_case_generation", "llm_connection"),
    "generate_checklists": ("checklist_decomposition", "llm_connection"),
}


def create_blocked_job(db, project, operation: str, request_data: dict, snapshot_id: str | None = None):
    # One model configuration is frozen for the entire future analysis run; never copy API keys.
    job = Job(project_id=project.id, operation=operation, status="blocked", snapshot_id=snapshot_id,
              request_data=request_data,
              model_config_snapshot={**project.llm_config, "config_version": project.config_version},
              missing_features=missing(*OPERATIONS[operation]))
    db.add(job)
    db.flush()
    db.commit()
    return job


def job_out(job):
    return {"id": job.id, "project_id": job.project_id, "operation": job.operation,
            "status": job.status, "snapshot_id": job.snapshot_id,
            "model_config_snapshot": job.model_config_snapshot,
            "missing_features": job.missing_features, "created_at": job.created_at,
            "poll_url": f"/api/v1/jobs/{job.id}?project_id={job.project_id}",
            "events_url": f"/api/v1/jobs/{job.id}/events?project_id={job.project_id}"}
