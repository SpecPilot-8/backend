from fastapi.testclient import TestClient
from api.main import create_app
from api.v2.common import make_job, missing


def test_project_revision_and_persistence(client, config):
    p = client.post('/api/v2/projects', json={'name': '한글 프로젝트'}).json()
    url = '/api/v2/projects/' + p['id']
    assert client.patch(url, json={'base_version': 1, 'name': '수정'}).json()['version'] == 2
    r = client.patch(url, json={'base_version': 1, 'name': '덮어쓰기'})
    assert r.status_code == 409 and r.json()['error']['details']['current_version'] == 2
    with TestClient(create_app(config)) as reopened:
        assert reopened.get(url).json()['name'] == '수정'
        assert reopened.get('/api/v2/projects').json()['total'] == 1
    assert client.get('/api/v2/projects?limit=201').status_code == 422


def test_secret_never_echoed_or_stored(client):
    p = client.post('/api/v2/projects', json={'name': '모델'}).json()['id']
    url = f'/api/v2/projects/{p}'
    for body in [{'profile_id': 'primary', 'api_key': 'test-secret'}, {'profile_id': 'primary', 'api_key': 'test-secret', 'extra': 'test-secret'}]:
        r = client.post(url + '/credentials', json=body)
        assert r.status_code in (501, 422)
        assert 'test-secret' not in r.text
        assert r.json()['error']['request_id'] == r.headers['x-request-id']
    assert client.get(url + '/llm').json()['credential_configured'] is False
    assert client.get('/api/v2/model-profiles').json()['items'][0]['enabled'] is False


def test_jobs_persisted_replay_and_project_scope(client):
    p = client.post('/api/v2/projects', json={'name': '첫 프로젝트'}).json()['id']
    other = client.post('/api/v2/projects', json={'name': '다른 프로젝트'}).json()['id']
    with client.app.state.database.session() as db:
        j = make_job(db, p, 'extract', {}, missing('requirement_extraction', '모델 미연결'))
    url = j['events_url']
    first = client.get(url)
    assert 'id: 1' in first.text and 'event: blocked' in first.text
    assert client.get(url, headers={'Last-Event-ID': '1'}).text == ''
    assert client.get(url, headers={'Last-Event-ID': 'bad'}).status_code == 422
    assert client.get(j['poll_url'].replace(p, other)).status_code == 404
    assert client.get(f'/api/v2/projects/{p}/jobs?status=blocked').json()['total'] == 1


def test_cors_patch_and_event_header(client):
    r = client.options('/api/v2/projects/a', headers={'Origin': 'http://localhost:5173', 'Access-Control-Request-Method': 'PATCH', 'Access-Control-Request-Headers': 'Last-Event-ID'})
    assert r.status_code == 200
