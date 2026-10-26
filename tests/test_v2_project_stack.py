from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from api.main import create_app
from api.v2.migrations import upgrade


def test_stack_saved_restored_and_legacy_updates_preserve_it(client, config):
    p = client.post('/api/v2/projects', json={'name': '분류', 'tech_stack': 'react'}).json()
    assert p['tech_stack'] == 'react'
    url = '/api/v2/projects/' + p['id']
    r = client.patch(url, json={'name': '수정', 'base_version': 1})
    assert r.json()['tech_stack'] == 'react'
    r = client.patch(url, json={'name': '수정', 'base_version': 2, 'tech_stack': 'typescript'})
    assert r.json()['tech_stack'] == 'typescript'
    assert client.patch(url, json={'name': '충돌', 'base_version': 2, 'tech_stack': 'spring'}).status_code == 409
    with TestClient(create_app(config)) as reopened:
        assert reopened.get(url).json()['tech_stack'] == 'typescript'
        assert reopened.get('/api/v2/projects').json()['items'][0]['tech_stack'] == 'typescript'
    assert client.post('/api/v2/projects', json={'name': '기본'}).json()['tech_stack'] == 'auto'
    assert client.post('/api/v2/projects', json={'name': '잘못된 값', 'tech_stack': 'typo'}).status_code == 422
    assert client.patch(url, json={'name': '오류', 'base_version': 3, 'tech_stack': 'typo'}).status_code == 422


def test_old_project_schema_migration_preserves_data_and_is_idempotent(tmp_path):
    engine = create_engine(f'sqlite:///{tmp_path}/old.sqlite3')
    with engine.begin() as c:
        c.execute(text('CREATE TABLE api_v2_project (id VARCHAR(36) PRIMARY KEY, name VARCHAR(200), workspace_path VARCHAR(4096), version INTEGER, config_version INTEGER, profile_id VARCHAR(100), created_at VARCHAR(40))'))
        c.execute(text("INSERT INTO api_v2_project VALUES ('old', '기존', NULL, 3, 0, NULL, '2026-10-26')"))
    upgrade(engine)
    upgrade(engine)
    with engine.connect() as c:
        assert c.execute(text('SELECT name, version, tech_stack FROM api_v2_project')).one() == ('기존', 3, 'auto')
        assert c.scalar(text("SELECT count(*) FROM api_schema_migration WHERE version='0002_project_tech_stack'")) == 1
    engine.dispose()
