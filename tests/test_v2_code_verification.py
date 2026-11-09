from copy import deepcopy
from io import BytesIO
import stat
from zipfile import ZipFile, ZipInfo
import pytest
from api.errors import APIError
from api.v2.storage import resource
from api.v2.verdict_adapter import ingest_verdict


def zip_bytes(name, content, symlink=False):
    buf = BytesIO()
    with ZipFile(buf, 'w') as z:
        info = ZipInfo(name)
        if symlink:
            info.create_system = 3
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
        z.writestr(info, content)
    return buf.getvalue()


@pytest.mark.parametrize('name,symlink', [('../x.js', False), ('/x.js', False), ('C:/x.js', False), ('x.js', True)])
def test_unsafe_zip_rejected(client, v2_project, name, symlink):
    r = client.post(f'/api/v2/projects/{v2_project}/code/sources', files={'file': ('code.zip', zip_bytes(name, 'bad', symlink))})
    assert r.status_code == 422


def test_snapshot_immutable_and_scoped(client, v2_project, v2_snapshot):
    base = f'/api/v2/projects/{v2_project}/code'
    original = client.get(base + f'/snapshots/{v2_snapshot}/chunks').json()['items']
    assert any('<= 10' in c['content'] for c in original)
    assert any(c['file_path'] == 'application.yml' for c in original)
    uploaded = client.post(base + '/sources', files={'file': ('new.zip', zip_bytes('new.js', 'export const limit = 50;'))}).json()
    changed = client.post(base + '/index', json={'source_id': uploaded['source_id']}).json()['result_ref']['id']
    assert changed != v2_snapshot
    assert client.get(base + f'/snapshots/{v2_snapshot}/chunks').json()['items'] == original
    assert client.get(base + f'/snapshots/{changed}/chunks/{original[0]["id"]}').status_code == 404
    assert client.post(base + '/index', json={'source_id': uploaded['source_id']}).json()['result_ref']['id'] == changed


def test_review_required_and_blocked_rows_keep_old_revision(client, v2_project, v2_requirement, v2_snapshot):
    base = f'/api/v2/projects/{v2_project}'
    body = {'snapshot_id': v2_snapshot, 'requirement_versions': [{'requirement_id': v2_requirement['id'], 'revision': 1}], 'model_config_version': 0}
    assert client.post(base + '/verification-runs', json=body).status_code == 409
    approved = client.put(base + f'/requirements/{v2_requirement["id"]}/review', json={'base_revision': 1, 'status': 'approved'}).json()
    body['requirement_versions'][0]['revision'] = approved['revision']
    job = client.post(base + '/verification-runs', json=body).json()
    assert job['status'] == 'blocked' and job['result_ref'] is None
    run = client.get(base + '/verification-runs').json()['items'][0]
    client.patch(base + f'/requirements/{v2_requirement["id"]}', json={'base_revision': 2, 'title': '새 명세'})
    row = client.get(base + f'/verification-runs/{run["id"]}/traceability').json()['items'][0]
    assert row['title'] == v2_requirement['title'] and row['requirement_revision'] == 2 and row['status'] is None
    summary = client.get(base + f'/verification-runs/{run["id"]}/summary').json()
    assert summary['total'] == summary['not_run'] == summary['denominator'] == 1
    assert summary['implementation_rate'] == 0
    detail = client.get(row['detail_url']).json()
    assert detail['evidence'] == [] and detail['status'] is None


def test_verdict_adapter_joins_real_evidence_and_rejects_hallucinated_ids(client, v2_project, v2_run):
    base = f'/api/v2/projects/{v2_project}'
    chunks = client.get(base + f'/code/snapshots/{v2_run["snapshot_id"]}/chunks').json()['items']
    with client.app.state.database.session() as db:
        run = resource(db, v2_project, v2_run['id'], 'verification_run')
        req = run.data['requirements'][0]
        output = {'contract_version': '2.0', 'snapshot_id': run.data['snapshot_id'], 'requirement_id': req['id'], 'requirement_revision': req['revision'],
                  'condition_verdicts': [{'condition_id': req['conditions'][0]['id'], 'status': 'mismatch', 'reason': '제한값 불일치',
                   'primary_chunk_ids': [chunks[0]['id']], 'reference_chunk_ids': [], 'message_match': 'not_applicable',
                   'server_validation': 'unknown', 'spec_suspect': ''}], 'findings': [], 'warnings': []}
        bad = deepcopy(output)
        bad['condition_verdicts'][0]['primary_chunk_ids'] = ['hallucinated']
        with pytest.raises(APIError):
            ingest_verdict(db, v2_project, run.id, bad, {chunks[0]['id']: 'this_screen'})
        result = ingest_verdict(db, v2_project, run.id, output, {chunks[0]['id']: 'this_screen'})
        assert result['evidence'][0]['code_snippet'] == chunks[0]['content']
        assert result['evidence'][0]['file_path'] == chunks[0]['file_path']
        assert result['status'] == 'mismatch'
    assert client.get(base + f'/verification-runs/{v2_run["id"]}/summary?status=mismatch').json()['total'] == 1
    assert client.get(base + f'/verification-runs/{v2_run["id"]}/summary?q=없는단어').json()['denominator'] == 0


def test_workspace_escape_and_source_limit(client, config):
    root = config.workspace_root / 'local'
    root.mkdir()
    (root / 'a.js').write_text('export const x = 1;')
    p = client.post('/api/v2/projects', json={'name': '로컬', 'workspace_path': str(root)}).json()['id']
    base = f'/api/v2/projects/{p}/code'
    assert client.post(base + '/index', json={'relative_path': '../'}).status_code == 422
    assert client.post(base + '/index', json={'relative_path': '.'}).json()['status'] == 'completed'
    assert client.post(base + '/sources', files={'file': ('x.zip', zip_bytes('x.js', 'x' * (config.max_source_file_bytes+1)))}).status_code == 413


def test_snapshot_files_returns_original_text_with_hash(client, v2_project, v2_snapshot):
    from hashlib import sha256
    base = f'/api/v2/projects/{v2_project}/code/snapshots'
    body = client.get(base + f'/{v2_snapshot}/files').json()
    assert body['snapshot_id'] == v2_snapshot and body['file_count'] == len(body['files']) > 0
    assert any(f['path'] == 'application.yml' for f in body['files'])
    assert all(f['sha256'] == sha256(f['content'].encode('utf-8')).hexdigest() for f in body['files'])
    assert 'files' not in client.get(base + f'/{v2_snapshot}').json()
    assert client.get(base + '/does-not-exist/files').status_code == 404


def test_text_chunks_keep_original_utf8_ranges_and_snapshot_history(client, v2_project, monkeypatch):
    from api.v2 import code
    from adapters.v2_verdict import ChunkIndex

    limit = code.TEXT_CHUNK_MAX_BYTES
    original = 'first\r\n' + 'a' * (limit - 1) + '한😀' * limit + '\r\nlast\n'
    source = original.encode('utf-8')
    base = f'/api/v2/projects/{v2_project}/code'
    response = client.post(base + '/sources', files={'file': ('source.zip', zip_bytes('application.yml', original))})
    assert response.status_code == 201
    request = {'source_id': response.json()['source_id']}
    snapshot = client.post(base + '/index', json=request).json()['result_ref']['id']
    chunks_url = base + f'/snapshots/{snapshot}/chunks'
    chunks = client.get(chunks_url).json()['items']
    ordered = sorted(chunks, key=lambda c: c['start_byte'])

    assert ordered[0]['start_byte'] == 0
    assert ordered[-1]['end_byte'] == len(source)
    assert [c['end_byte'] for c in ordered[:-1]] == [c['start_byte'] for c in ordered[1:]]
    for c in ordered:
        fragment = source[c['start_byte']:c['end_byte']]
        assert 0 < len(fragment) <= limit and fragment.decode('utf-8') == c['content']
        assert not fragment.endswith(b'\r')
    assert ordered[0]['start_line'] == ordered[0]['end_line'] == 1
    assert ordered[-1]['end_line'] == 3
    line_two = [c['id'] for c in chunks if c['start_line'] <= 2 <= c['end_line']]
    assert len(line_two) > 1
    assert ChunkIndex(chunks).find('application.yml', 2, 2) == (line_two, False)
    assert client.post(base + '/index', json=request).json()['result_ref']['id'] == snapshot

    with client.app.state.database.session() as db:
        stored = resource(db, v2_project, snapshot, 'snapshot')
        stored.data = {key: value for key, value in stored.data.items() if key != 'chunking_version'}
    assert client.post(base + '/index', json=request).json()['result_ref']['id'] != snapshot
    assert client.get(chunks_url).json()['items'] == chunks

    assert len(code.chunk_file('x.yml', 'x\n' * 121)) == 1
    monkeypatch.setattr(code, 'TEXT_CHUNK_MAX_BYTES', 8)
    assert [c['content'] for c in code.chunk_file('x.yml', 'a' * 7 + '\r\n')] == ['a' * 7, '\r\n']
