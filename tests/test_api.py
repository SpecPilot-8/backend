from dataclasses import replace
from io import BytesIO
from pathlib import Path

import pymupdf
import pytest
from docx import Document
from fastapi.testclient import TestClient
from openpyxl import Workbook

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
