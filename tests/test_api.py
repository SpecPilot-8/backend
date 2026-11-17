from fastapi.testclient import TestClient

from api.main import create_app


def test_core_health_persistence_and_v2_routes(client, config):
    created = client.post("/api/v2/projects", json={"name": "Demo"})
    assert created.status_code == 201, created.text
    project_id = created.json()["id"]

    with TestClient(create_app(config)) as reopened:
        assert reopened.get(f"/api/v2/projects/{project_id}").json()["name"] == "Demo"
        health = reopened.get("/health")
        assert health.status_code == 200
        assert health.json() == {"status": "ok", "api_version": "2"}
        paths = reopened.get("/openapi.json").json()["paths"]
        assert "/api/v2/projects" in paths
        assert not any(path.startswith("/api/v1") for path in paths)
        assert reopened.get("/docs").status_code == 200

    invalid = client.post(
        "/api/v2/projects",
        json={"name": "Outside", "workspace_path": str(config.workspace_root.parent)},
    )
    assert invalid.status_code == 422
