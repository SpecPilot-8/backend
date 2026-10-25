"""파이프라인 결과(PostgreSQL)를 v2 API 저장소에 채운다 (LLM 호출 없음).

    python scripts/load_into_v2.py                 # Spring, React 두 프로젝트
    python scripts/load_into_v2.py --stack react

스택마다 v2 프로젝트를 하나 만들고 화면이 실제 데이터를 보여주도록 아래를 차례로 한다.
  1. 기획서 PDF 업로드 (v2 공개 API)
  2. 문서 블록을 파이프라인 파서 결과로 채택하고 요구사항·조건 저장 (adapters/v2_extraction.py → ingest_extraction)
  3. 요구사항 검토 확정 (v2 공개 API). 파이프라인 결과를 그대로 확정한 것이라 사람 검토를 대신하지 않는다
  4. 샘플 코드 인덱싱 (v2 공개 API, 로컬 워크스페이스)
  5. 검증 실행 생성 후 조건별 판정 저장 (adapters/v2_verdict.py → ingest_verdict)

v2 서버(uvicorn)와 같은 SQLite 파일을 쓴다(SPECPILOT_DATABASE_URL, 기본 .data/api.sqlite3).
결과에 기획서 원문과 샘플 코드가 들어가므로 .data/는 git에서 제외한다.
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from fastapi.testclient import TestClient

from adapters.v2_extraction import build_artifact, build_extraction
from adapters.v2_verdict import ChunkIndex, build_verdict
from api.main import create_app
from api.v2.documents import ingest_extraction
from api.v2.storage import all_resources, replace, resource
from api.v2.verdict_adapter import ingest_verdict
from matcher.data import connect

REPO = Path(__file__).resolve().parent.parent
STACKS = {"spring": ("spring-sample", "Spring Boot"), "react": ("react-sample", "React + Express")}


def _rows(conn, sql, params=()):
    cur = conn.execute(sql, params)
    cols = [d.name for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def _ok(res, expected=(200, 201, 202)):
    if res.status_code not in expected:
        sys.exit(f"{res.request.method} {res.request.url.path} 실패 {res.status_code}: {res.text[:500]}")
    return res.json()


def load_pipeline(conn, repo_id: str):
    screens = _rows(conn, "SELECT * FROM screen")
    requirements = _rows(conn, "SELECT * FROM requirement")
    run_id = conn.execute("SELECT max(run_id) FROM requirement_condition").fetchone()[0]
    conditions = _rows(conn, """
        SELECT rc.*, q.stable_key AS requirement_key FROM requirement_condition rc
        JOIN requirement q ON q.id = rc.requirement_id WHERE rc.run_id = %s""", (run_id,))
    # 요구사항 전체를 판정한 가장 최근 실행 (scripts/export_trace.py와 같은 기준)
    verdict_run = conn.execute("""
        SELECT r.id, s.root_path FROM verdict_run r JOIN snapshot s ON s.id = r.snapshot_id
        WHERE s.repo_id = %s AND r.decompose_run_id = %s
          AND (SELECT count(*) FROM requirement_verdict v WHERE v.verdict_run_id = r.id) = %s
        ORDER BY r.id DESC LIMIT 1""", (repo_id, run_id, len(requirements))).fetchone()
    if verdict_run is None:
        sys.exit(f"{repo_id}: 최근 조건 분해로 요구사항 전체를 판정한 실행이 없다")
    verdicts = _rows(conn, """
        SELECT cv.id, q.stable_key || '-C' || rc.seq AS condition_key, q.stable_key AS requirement_key,
               cv.status, cv.reasoning, cv.message_match, cv.server_validation, cv.spec_suspect
        FROM condition_verdict cv
        JOIN requirement_condition rc ON rc.id = cv.condition_id
        JOIN requirement q ON q.id = rc.requirement_id
        WHERE cv.verdict_run_id = %s""", (verdict_run[0],))
    evidence = _rows(conn, """
        SELECT e.condition_verdict_id, e.role, c.file_path, c.start_line, c.end_line
        FROM condition_verdict_evidence e JOIN chunk c ON c.id = e.chunk_id
        JOIN condition_verdict cv ON cv.id = e.condition_verdict_id
        WHERE cv.verdict_run_id = %s ORDER BY e.role, e.rank""", (verdict_run[0],))
    ev = {}
    for e in evidence:
        ev.setdefault(e["condition_verdict_id"], []).append(e)
    for v in verdicts:
        v["evidence"] = ev.get(v["id"], [])
    return screens, requirements, conditions, verdicts, verdict_run


def load_stack(client: TestClient, conn, stack: str) -> None:
    repo_id, label = STACKS[stack]
    screens, requirements, conditions, verdicts, (verdict_run_id, root_path) = load_pipeline(conn, repo_id)
    code_root = (REPO / root_path).resolve()
    pdf = Path({s["source_file"] for s in screens}.pop())
    if not pdf.is_absolute():
        pdf = REPO / pdf
    base = "/api/v2/projects"

    project = _ok(client.post(base, json={"name": f"씽크트리 샘플 ({label})", "workspace_path": str(code_root)}))
    p = project["id"]
    with pdf.open("rb") as f:
        doc = _ok(client.post(f"{base}/{p}/documents", files={"file": (pdf.name, f, "application/pdf")}))
    d = doc["id"]

    db_ = client.app.state.database
    with db_.session() as db:
        item = resource(db, p, d, "document")
        artifact = build_artifact(d, item.data["revision"], "sha256:" + item.data["content_hash"], screens, requirements)
        blocks = [{**b, "document_id": d, "document_revision": item.data["revision"]} for b in artifact["blocks"]]
        # 기본 텍스트 파싱 대신 파이프라인 파서(화면 헤더·Description/Action 행 인식) 결과를 문서 블록으로 채택한다
        replace(db, item, {**item.data, "parse_status": "parsed", "blocks": blocks, "warnings": [],
                           "unread_locations": []})
        draft = build_extraction(artifact, screens, requirements, conditions)
        req_ids = ingest_extraction(db, p, d, draft)

    approved = {}
    for r in req_ids:
        current = _ok(client.get(f"{base}/{p}/requirements/{r}"))
        approved[r] = _ok(client.put(f"{base}/{p}/requirements/{r}/review",
                                     json={"base_revision": current["revision"], "status": "approved"}))

    job = _ok(client.post(f"{base}/{p}/code/index", json={"relative_path": "."}))
    snapshot = job["result_ref"]["id"]
    config_version = _ok(client.get(f"{base}/{p}/llm"))["config_version"]
    _ok(client.post(f"{base}/{p}/verification-runs", json={
        "snapshot_id": snapshot, "model_config_version": config_version,
        "requirement_versions": [{"requirement_id": r, "revision": a["revision"]} for r, a in approved.items()]}))
    run_id = _ok(client.get(f"{base}/{p}/verification-runs"))["items"][0]["id"]

    by_req = {}
    for v in verdicts:
        by_req.setdefault(v["requirement_key"], []).append(v)
    warnings = []
    with db_.session() as db:
        chunks = [{"id": c.id, **c.data} for c in all_resources(db, p, "chunk") if c.data["snapshot_id"] == snapshot]
        index = ChunkIndex(chunks)
        for r, req in approved.items():
            verdict, scopes, warn = build_verdict(req, snapshot, by_req[req["stable_key"]], index)
            ingest_verdict(db, p, run_id, verdict, scopes)
            warnings += warn

    print(f"[{label}] 프로젝트 {p}")
    print(f"  문서 {d}: 블록 {len(blocks)}, 요구사항 {len(req_ids)}, 조건 {sum(len(r['conditions']) for r in draft['requirements'])}")
    print(f"  스냅샷 {snapshot}: 청크 {len(chunks)}")
    print(f"  검증 실행 {run_id}: 파이프라인 판정 실행 #{verdict_run_id}에서 조건 판정 {len(verdicts)}개")
    summary = _ok(client.get(f"{base}/{p}/verification-runs/{run_id}/summary"))
    print("  요약:", {k: summary[k] for k in ("implemented", "partial", "mismatch", "not_found", "needs_review",
                                            "not_statically_verifiable", "not_run")})
    if warnings:
        print(f"  근거 대응 경고 {len(warnings)}건 (앞 5건)")
        for w in warnings[:5]:
            print("   ", w)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stack", choices=[*STACKS, "all"], default="all")
    args = ap.parse_args()
    stacks = list(STACKS) if args.stack == "all" else [args.stack]
    with connect() as conn, TestClient(create_app()) as client:
        for stack in stacks:
            load_stack(client, conn, stack)


if __name__ == "__main__":
    main()
