from api.v2.code import chunk_file
from tests.test_v2_source_metadata import upload

JAVA = '''package app;

public class MemberService {
    public void signup(String email) {
        if (email.length() > 50) {
            throw new IllegalArgumentException("too long");
        }
    }
}
'''
FILES = {
    'src/app/MemberService.java': JAVA,
    'src/main.js': 'export const titleLimit = 50;\nexport function check(v) { return v.length <= titleLimit; }\n',
    'templates/notice.html': '<form><input name="title" maxlength="50"></form>\n',
}
FIELDS = ('file_path', 'start_line', 'end_line', 'symbol', 'content', 'chunk_type')


def index(client, project, files):
    source = upload(client, project, files).json()['source_id']
    base = f'/api/v2/projects/{project}/code'
    job = client.post(base + '/index', json={'source_id': source}).json()
    sid = job['result_ref']['id']
    chunks = client.get(f'{base}/snapshots/{sid}/chunks?limit=200').json()['items']
    return client.get(f'{base}/snapshots/{sid}').json(), chunks


def comparable(chunks):
    return sorted(tuple(c.get(k) for k in FIELDS) for c in chunks)


def test_unchanged_files_reuse_chunks_and_match_fresh_chunking(client, v2_project):
    first, old_chunks = index(client, v2_project, FILES)
    assert first['incremental'] == {'base_snapshot_id': None, 'reused_files': 0, 'chunked_files': 3}
    changed = {**FILES, 'src/main.js': FILES['src/main.js'].replace('50', '100')}
    second, new_chunks = index(client, v2_project, changed)
    assert second['id'] != first['id']
    assert second['incremental'] == {'base_snapshot_id': first['id'], 'reused_files': 2, 'chunked_files': 1}
    fresh = [c for path, text in sorted(changed.items()) for c in chunk_file(path, text)]
    assert comparable(new_chunks) == comparable(fresh)  # 복사한 결과 = 처음부터 다시 자른 결과
    assert not {c['id'] for c in new_chunks} & {c['id'] for c in old_chunks}  # 새 스냅샷은 새 청크 id
    assert all(c['snapshot_id'] == second['id'] for c in new_chunks)
    again = client.get(f"/api/v2/projects/{v2_project}/code/snapshots/{first['id']}/chunks?limit=200").json()['items']
    assert comparable(again) == comparable(old_chunks)  # 이전 스냅샷 청크는 그대로
    assert 'file_hashes' not in second


def test_added_deleted_and_moved_files_are_chunked_again(client, v2_project):
    first, _ = index(client, v2_project, FILES)
    moved = {'src/app/service/MemberService.java': JAVA, 'src/main.js': FILES['src/main.js'],
             'src/extra.js': 'export const pageSize = 25;\n'}
    second, chunks = index(client, v2_project, moved)
    assert second['incremental'] == {'base_snapshot_id': first['id'], 'reused_files': 1, 'chunked_files': 2}
    assert {c['file_path'] for c in chunks} == set(moved)
    fresh = [c for path, text in sorted(moved.items()) for c in chunk_file(path, text)]
    assert comparable(chunks) == comparable(fresh)
