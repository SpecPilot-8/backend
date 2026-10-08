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
