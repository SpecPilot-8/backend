from io import BytesIO
from zipfile import ZipFile

import pytest


def archive(items):
    buffer = BytesIO()
    with ZipFile(buffer, 'w') as zip_file:
        for name, value in items.items():
            zip_file.writestr(name, value)
    return buffer.getvalue()


def upload(client, project, items):
    return client.post(f'/api/v2/projects/{project}/code/sources',
                       files={'file': ('code.zip', archive(items))})


def test_macos_metadata_does_not_break_upload_or_enter_snapshot(client, v2_project):
    files = {'project/src/main.js': 'export const titleLimit = 50;',
             '__MACOSX/project/src/._main.js': b'\x00\xff\xfeAppleDouble',
             'project/src/._other.java': 'metadata that happens to be valid UTF-8',
             'project/.AppleDouble/fake.js': b'\xff', 'project/.DS_Store': b'\xff'}
    response = upload(client, v2_project, files)
    assert response.status_code == 201, response.text
    base = f'/api/v2/projects/{v2_project}/code'
    job = client.post(base + '/index', json={'source_id': response.json()['source_id']}).json()
    snapshot_id = job['result_ref']['id']
    snapshot = client.get(base + '/snapshots/' + snapshot_id).json()
    chunks = client.get(base + '/snapshots/' + snapshot_id + '/chunks').json()['items']
    assert snapshot['file_count'] == 1
    assert {c['file_path'] for c in chunks} == {'project/src/main.js'}
    clean = upload(client, v2_project, {'project/src/main.js': files['project/src/main.js']}).json()
    clean_job = client.post(base + '/index', json={'source_id': clean['source_id']}).json()
    assert clean_job['result_ref']['id'] == snapshot_id


def test_metadata_only_archive_has_no_source(client, v2_project):
    response = upload(client, v2_project, {'__MACOSX/src/._file.js': b'\xff'})
    assert response.status_code == 422 and response.json()['error']['code'] == 'EMPTY_SOURCE'


def test_ignored_metadata_still_cannot_escape_archive(client, v2_project):
    response = upload(client, v2_project, {'__MACOSX/../escape.js': b'\xff'})
    assert response.status_code == 422 and response.json()['error']['code'] == 'UNSAFE_ARCHIVE'


def test_metadata_still_counts_toward_archive_limits(client, config, v2_project):
    response = upload(client, v2_project, {'src/main.js': 'export const x = 1;',
        '__MACOSX/._file.js': b'x' * (config.max_source_file_bytes + 1)})
    assert response.status_code == 413 and response.json()['error']['code'] == 'SOURCE_LIMIT_EXCEEDED'


@pytest.mark.parametrize('mode', ['zip', 'workspace'])
def test_real_non_utf8_source_names_file_without_lossy_conversion(client, config, mode):
    raw = '제목'.encode('cp949')
    if mode == 'zip':
        p = client.post('/api/v2/projects', json={'name': '인코딩'}).json()['id']
        response = upload(client, p, {'src/screen.js': raw})
    else:
        root = config.workspace_root / 'encoding'
        (root / 'src').mkdir(parents=True)
        (root / 'src/screen.js').write_bytes(raw)
        p = client.post('/api/v2/projects', json={'name': '인코딩', 'workspace_path': str(root)}).json()['id']
        response = client.post(f'/api/v2/projects/{p}/code/index', json={'relative_path': '.'})
    assert response.status_code == 422
    error = response.json()['error']
    assert error['code'] == 'SOURCE_ENCODING_UNSUPPORTED'
    assert error['details']['file_path'] == 'src/screen.js'
    assert 'src/screen.js' in error['message']


def test_local_source_excludes_macos_files_and_directories(client, config):
    root = config.workspace_root / 'mac'
    root.mkdir()
    (root / 'main.js').write_text('export const value = 1;', encoding='utf-8')
    (root / '._main.js').write_bytes(b'\xff')
    (root / '__MACOSX').mkdir()
    (root / '__MACOSX/fake.js').write_bytes(b'\xff')
    p = client.post('/api/v2/projects', json={'name': '로컬', 'workspace_path': str(root)}).json()['id']
    job = client.post(f'/api/v2/projects/{p}/code/index', json={'relative_path': '.'})
    assert job.status_code == 202 and job.json()['status'] == 'completed'
    snapshot = client.get(f'/api/v2/projects/{p}/code/snapshots/' + job.json()['result_ref']['id']).json()
    assert snapshot['file_count'] == 1
