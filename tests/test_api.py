from dataclasses import replace
from io import BytesIO
from pathlib import Path

import pymupdf
import pytest
from docx import Document
from fastapi.testclient import TestClient
from openpyxl import Workbook, load_workbook

from api.main import create_app




def params(project, snapshot=None):
    result = {"project_id": project["id"]}
    if snapshot:
        result["snapshot_id"] = snapshot["id"]
    return result


def chunks(client, project, snapshot):
    return client.get("/api/v1/code/chunks", params=params(project, snapshot)).json()["items"]


def verdict(client, project, requirement, snapshot, status="mismatch", **extra):
    return client.post("/api/v1/verification-results/import", json={
        "project_id": project["id"], "requirement_id": requirement["id"], "revision": requirement["revision"],
        "snapshot_id": snapshot["id"], "status": status, "reason": "5회 잠금 요구사항이 10회로 구현됨",
        "evidence_chunk_ids": [chunks(client, project, snapshot)[0]["id"]], **extra,
    })


def case_body(project, requirement):
    return {"project_id": project["id"], "requirement_id": requirement["id"], "revision": requirement["revision"],
            "test_id": "TC-001", "title": "=SUM(1,2)", "type": "boundary", "preconditions": "실패 4회",
            "steps": ["잘못된 비밀번호로 로그인"], "input_data": "wrong", "expected": "계정 잠금"}


def docx_bytes():
    document = Document()
    document.add_paragraph("실패 5회 시 계정을 잠근다")
    table = document.add_table(rows=1, cols=2)
    table.cell(0, 0).text = "REQ-001"
    table.cell(0, 1).text = "잠금"
    result = BytesIO()
    document.save(result)
    return result.getvalue()


def xlsx_bytes():
    workbook = Workbook()
    workbook.active["B2"] = "REQ-001"
    workbook.active["C2"] = "=1+1"
    result = BytesIO()
    workbook.save(result)
    return result.getvalue()


def pdf_bytes(text=True):
    document = pymupdf.open()
    page = document.new_page()
    if text:
        page.insert_text((50, 50), "Lock after 5 failures")
    result = document.tobytes()
    document.close()
    return result


def test_manual_workflow(client, project, requirement, snapshot):
    assert requirement["status"] is None
    assert requirement["analysis_state"] == "not_run"
    response = verdict(client, project, requirement, snapshot, suggested_code="return failures >= 5")
    assert response.status_code == 200, response.text
    evidence = response.json()["evidence"][0]
    assert evidence["start_line"] == 1 and evidence["end_line"] == 2
    assert evidence["code_snippet"] == "def locked(failures):\n    return failures >= 10"
    assert evidence["uri"] == (Path(project["workspace_path"]) / "auth.py").as_uri()
    result = client.get("/api/v1/diagnostics", params=params(project)).json()
    assert result["items"][0]["range"] == {
        "start": {"line": 0, "character": 0}, "end": {"line": 1, "character": 25}}
    assert result["items"][0]["severity"] == 0
    dashboard = client.get(f"/api/v1/projects/{project['id']}/dashboard").json()
    assert dashboard["counts"]["mismatch"] == 1 and dashboard["evaluated"] == 1
    assert dashboard["implemented_percent"] == 0
    assert client.get(f"/api/v1/requirements/{requirement['id']}/fix", params=params(project)).json()[
        "suggested_code"] == "return failures >= 5"
    assert client.post("/api/v1/test-cases", json=case_body(project, requirement)).status_code == 201
    exported = client.post("/api/test-spec/export", json=params(project))
    assert exported.status_code == 200
    workbook = load_workbook(BytesIO(exported.content))
    sheet = workbook.active
    assert sheet["C2"].value == "=SUM(1,2)" and sheet["C2"].data_type == "s"
    assert sheet["J2"].value is None  # No fabricated PASS result.
    assert "10회로 구현됨" in sheet["K2"].value and sheet["L2"].value == "manual"
    assert list(sheet.data_validations.dataValidation)[0].formula1 == '"PASS,FAIL"'
    trace = client.post("/api/v1/traceability/export", json=params(project))
    workbook = load_workbook(BytesIO(trace.content))
    assert workbook.active["F2"].value == "mismatch"
    assert workbook.active["H2"].value == "auth.py:1-2"


def test_snapshot_identity_and_historical_results(client, project, requirement, snapshot):
    old_chunks = chunks(client, project, snapshot)
    assert verdict(client, project, requirement, snapshot, "implemented").status_code == 200
    repeated = client.post("/api/v1/code/index", json=params(project)).json()
    assert repeated["id"] == snapshot["id"]
    assert chunks(client, project, repeated) == old_chunks
    source = Path(project["workspace_path"]) / "auth.py"
    source.write_text("def locked(failures):\n    return failures >= 5\n", encoding="utf-8")
    changed = client.post("/api/v1/code/index", json=params(project)).json()
    assert changed["id"] != snapshot["id"]
    url = f"/api/v1/requirements/{requirement['id']}"
    assert client.get(url, params=params(project)).json()["status"] is None
    assert client.get(url, params=params(project, snapshot)).json()["status"] == "implemented"
    assert chunks(client, project, snapshot) == old_chunks
    source.write_text("def locked(failures):\n    return failures >= 10\n", encoding="utf-8")
    assert client.post("/api/v1/code/index", json=params(project)).json()["id"] == snapshot["id"]
    assert client.get(url, params=params(project)).json()["status"] == "implemented"


def test_requirement_revision_hides_old_results_and_tests(client, project, requirement, snapshot):
    verdict(client, project, requirement, snapshot, "implemented")
    body = case_body(project, requirement)
    created = client.post("/api/v1/test-cases", json=body)
    assert created.status_code == 201
    url = f"/api/v1/requirements/{requirement['id']}"
    changed = {key: requirement[key] for key in
               ("req_id", "title", "original_text", "condition", "action", "expected", "checklists")}
    changed["condition"] = "실패 3회"
    response = client.put(url, params=params(project), json=changed)
    assert response.json()["revision"] == 2 and response.json()["status"] is None
    assert client.get("/api/v1/test-cases", params=params(project)).json()["total"] == 0
    assert client.post("/api/v1/test-spec/export", json=params(project)).status_code == 409
    assert verdict(client, project, requirement, snapshot).status_code == 409
    assert client.post("/api/v1/test-cases", json=body).status_code == 409
    assert client.put(url, params=params(project), json=changed).json()["revision"] == 2
    requirement["revision"] = 2
    assert verdict(client, project, requirement, snapshot, "partial").status_code == 200
    assert client.get(url, params=params(project)).json()["status"] == "partial"
    body.update(revision=2, actual="잠금 확인", result="PASS")
    updated = client.put(f"/api/v1/test-cases/{created.json()['id']}", json=body)
    assert updated.status_code == 200 and updated.json()["result"] == "PASS"
    assert client.get("/api/v1/test-cases", params=params(project)).json()["total"] == 1


@pytest.mark.parametrize("filename,content,kind", [
    ("plan.docx", docx_bytes, "paragraph"), ("plan.xlsx", xlsx_bytes, "cell"), ("plan.pdf", pdf_bytes, "text"),
])
def test_document_parsers(client, project, filename, content, kind):
    result = client.post("/api/spec/parse", data=params(project), files={"file": (filename, content())})
    assert result.status_code == 201, result.text
    doc = result.json()
    assert doc["blocks"][0]["kind"] == kind
    assert doc["pipeline_status"] == "awaiting_ai" and doc["missing_features"]
    assert "location" in doc["blocks"][0]
    if filename.endswith("docx"):
        assert doc["blocks"][1]["rows"] == [["REQ-001", "잠금"]]
    if filename.endswith("xlsx"):
        assert doc["blocks"][1]["text"] == "=1+1"
    listed = client.get("/api/v1/documents", params=params(project)).json()
    assert listed["total"] == 1
    details = client.get(f"/api/v1/documents/{doc['id']}", params=params(project)).json()
    assert details["text"] == doc["text"]
    extraction = client.post(f"/api/v1/documents/{doc['id']}/requirements/extract", params=params(project))
    assert extraction.status_code == 202 and extraction.json()["status"] == "blocked"
    assert client.get("/api/v1/requirements", params=params(project)).json()["total"] == 0


def test_scanned_pdf_warns_without_fake_text(client, project):
    response = client.post("/api/v1/documents/parse", data=params(project),
                           files={"file": ("scan.pdf", pdf_bytes(False))})
    assert response.status_code == 201
    assert response.json()["text"] == "" and response.json()["blocks"] == []
    assert any("OCR" in warning for warning in response.json()["warnings"])


@pytest.mark.parametrize("filename,data,status", [
    ("bad.docx", b"broken", 422), ("bad.pdf", b"broken", 422), ("bad.xlsx", b"broken", 422),
    ("plan.txt", b"text", 415), ("empty.docx", b"", 422),
])
def test_invalid_upload_not_saved(client, project, config, filename, data, status):
    result = client.post("/api/spec/parse", data=params(project), files={"file": (filename, data)})
    assert result.status_code == status
    assert list((config.data_dir / "uploads").iterdir()) == []
    assert client.get("/api/v1/documents", params=params(project)).json()["total"] == 0


def test_upload_size_limit(config):
    settings = replace(config, max_upload_bytes=4)
    with TestClient(create_app(settings)) as client:
        project = client.post("/api/v1/projects", json={"name": "small", "workspace_path": str(config.workspace_root)}).json()
        response = client.post("/api/spec/parse", data=params(project), files={"file": ("file.pdf", b"12345")})
        assert response.status_code == 413
        assert list((config.data_dir / "uploads").iterdir()) == []


def test_project_isolation(client, project, requirement, snapshot, config):
    other = client.post("/api/v1/projects", json={"name": "Other", "workspace_path": str(config.workspace_root)}).json()
    paths = [f"/api/v1/requirements/{requirement['id']}", f"/api/v1/code/snapshots/{snapshot['id']}",
             f"/api/v1/code/chunks/{chunks(client, project, snapshot)[0]['id']}"]
    for url in paths:
        assert client.get(url, params=params(other)).status_code == 404
    assert client.post("/api/v1/verify/run", json={**params(other), "snapshot_id": snapshot["id"]}).status_code == 404
    assert client.post("/api/v1/test-cases", json={**case_body(project, requirement),
                                                 "project_id": other["id"]}).status_code == 404


def test_evidence_must_belong_to_snapshot(client, project, requirement, snapshot):
    source = Path(project["workspace_path"]) / "auth.py"
    source.write_text("def locked():\n    return True\n", encoding="utf-8")
    other = client.post("/api/v1/code/index", json=params(project)).json()
    result = verdict(client, project, requirement, snapshot,
                     evidence_chunk_ids=[chunks(client, project, other)[0]["id"]])
    assert result.status_code == 422
    assert verdict(client, project, requirement, snapshot, evidence_chunk_ids=[]).status_code == 422


@pytest.mark.parametrize("relative_path", ["../", "/etc", ".venv"])
def test_source_path_boundaries(client, project, relative_path):
    (Path(project["workspace_path"]) / ".venv").mkdir(exist_ok=True)
    response = client.post("/api/v1/code/index", json={**params(project), "relative_path": relative_path})
    assert response.status_code == 422


def test_source_symlink_exclusion_and_ast_parsers(client, project, config):
    root = Path(project["workspace_path"])
    outside = config.workspace_root.parent / "outside.py"
    outside.write_text("private = True\n", encoding="utf-8")
    (root / "leak.py").symlink_to(outside)
    (root / "Example.java").write_text("class Example { public boolean locked(int n) { return n >= 5; } }", encoding="utf-8")
    (root / "ui.js").write_text("function locked(n) { return n >= 5; }", encoding="utf-8")
    (root / "page.html").write_text('<form action="/login"><input name="password" /></form>', encoding="utf-8")
    (root / "page.tsx").write_text("export const Page = () => <div/>;", encoding="utf-8")
    snapshot = client.post("/api/v1/code/index", json=params(project)).json()
    assert snapshot["file_count"] == 5 and any("page.tsx" in w for w in snapshot["warnings"])
    stored = chunks(client, project, snapshot)
    assert {c["file_path"] for c in stored} == {"auth.py", "Example.java", "ui.js", "page.html", "page.tsx"}
    assert client.post("/api/v1/code/index", json={**params(project), "relative_path": "leak.py"}).status_code == 422


def test_source_size_limit_and_no_snapshot(config):
    with TestClient(create_app(replace(config, max_source_file_bytes=4))) as client:
        (config.workspace_root / "large.py").write_text("value = 100\n", encoding="utf-8")
        project = client.post("/api/v1/projects", json={"name": "small", "workspace_path": str(config.workspace_root)}).json()
        assert client.post("/api/v1/code/index", json=params(project)).status_code == 413
        assert client.get("/api/v1/code/snapshots", params=params(project)).json()["total"] == 0


def test_python_decorator_and_module_statements_preserved(client, project):
    path = Path(project["workspace_path"]) / "auth.py"
    path.write_text('from fastapi import FastAPI\napp = FastAPI()\n\n@app.get("/login")\n'
                    'def login():\n    return {"ok": True}\n', encoding="utf-8")
    snapshot = client.post("/api/v1/code/index", json=params(project)).json()
    stored = chunks(client, project, snapshot)
    assert len(stored) == 3
    assert stored[2]["start_line"] == 4
    assert stored[2]["content"].startswith('@app.get("/login")')
    assert stored[0]["content"] == "from fastapi import FastAPI"


def test_duplicate_rollback_pagination_and_filter(client, project, requirement):
    body = {"project_id": project["id"], "req_id": "REQ-001", "title": "duplicate"}
    assert client.post("/api/v1/requirements", json=body).status_code == 409
    body["req_id"] = "REQ-002"
    assert client.post("/api/v1/requirements", json=body).status_code == 201
    response = client.get("/api/v1/requirements", params={**params(project), "limit": 1, "offset": 1}).json()
    assert response["total"] == 2 and response["items"][0]["req_id"] == "REQ-002"
    assert client.get("/api/v1/requirements", params={**params(project), "q": "계정"}).json()["total"] == 1
    assert client.get("/api/v1/requirements", params={**params(project), "limit": 0}).status_code == 422


def test_unavailable_apis_are_explicit(client, project, requirement, snapshot):
    for endpoint in ("call-flow", "fix"):
        response = client.get(f"/api/v1/requirements/{requirement['id']}/{endpoint}", params=params(project))
        assert response.status_code == 501 and response.json()["error"]["missing_features"]
    response = client.post(f"/api/v1/requirements/{requirement['id']}/checklists/generate", params=params(project))
    assert response.status_code == 202 and response.json()["status"] == "blocked"
    assert client.post("/api/v1/test-spec/generate", json=params(project)).status_code == 202
    assert client.post(f"/api/v1/requirements/{requirement['id']}/reverify", params=params(project),
                       json={"snapshot_id": snapshot["id"]}).status_code == 202
    assert client.post("/api/v1/test-spec/export", json=params(project)).status_code == 409


def test_persistence_and_openapi(client, config, project):
    with TestClient(create_app(config)) as reopened:
        assert reopened.get(f"/api/v1/projects/{project['id']}").json()["name"] == "Demo"
        assert reopened.get("/health").json() == {"status": "ok", "ai_status": "not_connected"}
        paths = reopened.get("/openapi.json").json()["paths"]
        assert all(p in paths for p in ("/api/spec/parse", "/api/code/verify", "/api/test-spec/export"))
        assert reopened.get("/docs").status_code == 200
        capability = reopened.get("/api/v1/capabilities").json()
        assert any(c["code"] == "implementation_judgment" and c["status"] == "not_implemented" for c in capability)


def test_core_project_persistence_and_routes(client, config, project):
    with TestClient(create_app(config)) as reopened:
        assert reopened.get(f"/api/v1/projects/{project['id']}").json()["name"] == "Demo"
        assert reopened.get("/health").json()["status"] == "ok"
        paths = reopened.get("/openapi.json").json()["paths"]
        assert "/api/v1/projects" in paths and "/api/v1/jobs" in paths
        assert reopened.get("/docs").status_code == 200
        assert reopened.get("/api/v1/jobs", params=params(project)).json()["total"] == 0
    invalid = client.post("/api/v1/projects", json={"name": "Outside", "workspace_path": str(config.workspace_root.parent)})
    assert invalid.status_code == 422


def test_snapshot_reuse_preserves_chunk_ids(client, project, snapshot):
    original = chunks(client, project, snapshot)
    repeated = client.post("/api/v1/code/index", json=params(project)).json()
    assert repeated["id"] == snapshot["id"]
    assert chunks(client, project, repeated) == original


def test_verification_job_reports_missing_features(client, project, requirement, snapshot):
    response = client.post("/api/code/verify", json=params(project, snapshot))
    assert response.status_code == 202
    job = response.json()
    assert job["status"] == "blocked"
    assert "implementation_judgment" in [item["code"] for item in job["missing_features"]]
    assert client.get(job["poll_url"]).json()["id"] == job["id"]
    assert "event: blocked" in client.get(job["events_url"]).text
