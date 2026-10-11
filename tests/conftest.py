import pytest
from fastapi.testclient import TestClient

from api.config import Settings
from api.main import create_app


@pytest.fixture
def config(tmp_path):
    root = tmp_path / "workspace"
    root.mkdir()
    return Settings(data_dir=tmp_path / "data", workspace_root=root,
                    database_url=f"sqlite:///{tmp_path / 'database.sqlite3'}")


@pytest.fixture
def client(config):
    with TestClient(create_app(config)) as value:
        yield value


@pytest.fixture
def project(client, config):
    source = config.workspace_root / "demo"
    source.mkdir()
    (source / "auth.py").write_text("def locked(failures):\n    return failures >= 10\n", encoding="utf-8")
    result = client.post("/api/v1/projects", json={"name": "Demo", "workspace_path": str(source)})
    assert result.status_code == 201, result.text
    return result.json()


@pytest.fixture
def requirement(client, project):
    result = client.post("/api/v1/requirements", json={
        "project_id": project["id"], "req_id": "REQ-001", "title": "계정 잠금",
        "condition": "로그인 실패 5회", "action": "계정을 잠금", "expected": "로그인 차단",
        "checklists": [{"category": "LOGIC", "target": "backend", "item": "실패 횟수가 5 이상이면 잠금"}],
    })
    assert result.status_code == 201, result.text
    return result.json()


@pytest.fixture
def snapshot(client, project):
    result = client.post("/api/v1/code/index", json={"project_id": project["id"]})
    assert result.status_code == 201, result.text
    return result.json()


@pytest.fixture
def v2_project(client):
    r = client.post('/api/v2/projects', json={'name': 'v2 프로젝트'})
    assert r.status_code == 201
    return r.json()['id']


@pytest.fixture
def v2_document(client, v2_project):
    import pymupdf
    with pymupdf.open() as pdf:
        pdf.new_page().insert_text((50, 50), 'Title must be at most 50 characters.')
        content = pdf.tobytes()
    r = client.post(f'/api/v2/projects/{v2_project}/documents', files={'file': ('sample.pdf', content, 'application/pdf')})
    assert r.status_code == 201, r.text
    d = r.json()['id']
    r = client.post(f'/api/v2/projects/{v2_project}/documents/{d}/parse', json={'document_revision': 1})
    assert r.status_code == 202 and r.json()['status'] == 'completed'
    return d


@pytest.fixture
def v2_requirement(client, v2_project, v2_document):
    body = {'document_id': v2_document, 'document_revision': 1, 'title': '제목 50자 제한',
            'author_reason': '담당자가 추가한 검토용 규칙',
            'conditions': [{'stable_key': 'title-limit', 'text': '제목 최대 50자', 'category': 'validation',
                            'target': 'frontend', 'verifiable': 'code'}]}
    r = client.post(f'/api/v2/projects/{v2_project}/requirements', json=body)
    assert r.status_code == 201, r.text
    return r.json()
