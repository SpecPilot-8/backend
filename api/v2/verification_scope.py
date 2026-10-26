"""사용자가 확인한 진입 파일로만 정적 화면 범위를 만든다."""
import json
from pathlib import Path, PurePosixPath

from api.errors import APIError
from api.v2.storage import all_resources, resource
from matcher.data import ChunkRow
from matcher.scope import ScopeBuilder

TEST_DIRS = {'test', 'tests', '__tests__', '__mocks__', '__fixtures__'}
CONFIG_SUFFIXES = {'.yml', '.yaml', '.properties', '.sql', '.json', '.xml'}


def production_file(path):
    p = PurePosixPath(path)
    return not (set(p.parts) & TEST_DIRS or any(marker in p.name for marker in ('.test.', '.spec.'))
                or p.name in ('package-lock.json', 'npm-shrinkwrap.json', 'pnpm-lock.yaml', 'yarn.lock'))


def wrapper_prefix(files):
    parts = [PurePosixPath(p).parts for p in files]
    prefix = []
    for values in zip(*parts):
        if len(set(values)) != 1 or values[0] in ('src', 'client', 'server'):
            break
        prefix.append(values[0])
    # 파일 하나의 이름을 디렉터리로 제거하지 않는다.
    return '/'.join(prefix) + '/' if prefix and all(len(p) > len(prefix) for p in parts) else ''


def screen_groups(db, p, requirements):
    groups = {}
    for req in requirements:
        key = req.get('screen_id') or req['subtopic_id']
        if key in groups:
            continue
        source_id = None
        if req.get('screen_id'):
            source_id = resource(db, p, req['screen_id'], 'screen').data.get('source_screen_id')
        else:
            doc = resource(db, p, req['document_id'], 'document')
            page_by_block = {b['id']: b['location'].get('index') for b in doc.data['blocks']}
            pages = {page_by_block.get(r['block_id']) for r in req.get('source_refs', [])}
            found = [s for s in doc.data.get('screens', []) if s['page'] in pages]
            if len(found) == 1:
                source_id = found[0]['screen_id']
        groups[key] = {'key': key, 'title': req['subtopic_title'], 'source_screen_id': source_id}
    return list(groups.values())


def scope_options(db, p, snapshot, requirements, hint='auto'):
    files = sorted(f for f in snapshot.data['files'] if production_file(f))
    prefix = wrapper_prefix(files)
    normalized = {f.removeprefix(prefix): f for f in files}
    detected = ('spring' if any(f.endswith('.java') for f in files)
                else 'react' if any(f.endswith(('.js', '.jsx')) for f in files) else '')
    stack = hint if hint in ('spring', 'react') else detected
    groups = screen_groups(db, p, requirements)
    previous = [r for r in all_resources(db, p, 'verification_scope') if r.data['snapshot_id'] == snapshot.id]
    saved = previous[-1].data if previous else {}
    presets = {}
    if stack:
        source = Path(__file__).resolve().parents[2] / 'matcher' / 'scope' / 'entries' / f'{stack}-sample.json'
        presets = json.loads(source.read_text())
    for group in groups:
        entries = saved.get('entries', {}).get(group['key'], [])
        if not entries:
            entries = [normalized[f] for f in presets.get(group['source_screen_id'], []) if f in normalized]
        group['suggested_files'] = [f for f in entries if f in files]
    entry_files = [f for f in files if PurePosixPath(f).suffix.lower() in ('.java', '.js', '.jsx', '.html', '.htm', '.ts', '.tsx')]
    return {'snapshot_id': snapshot.id, 'stack': saved.get('stack', stack), 'files': entry_files, 'groups': groups,
            'requires_confirmation': True, 'requirement_count': len(requirements),
            'max_model_calls': len(groups) + len(requirements), 'warnings': [
                '추천 진입 파일은 전달된 예시 프로젝트용 매핑입니다. 실제 화면과 파일이 맞는지 확인하세요.',
                '호출 흐름은 정적 참조 분석이며 실제 실행 추적이 아닙니다. 동적 호출·미지원 언어는 별도 검토가 필요합니다.']}


def validate_scope(scope, options):
    if scope is None:
        raise APIError(409, 'SCREEN_ENTRY_REQUIRED', '코드 검증 전에 화면별 진입 파일을 확인하세요')
    files = set(options['files'])
    groups = {g['key'] for g in options['groups']}
    if set(scope['entries']) != groups or any(not values or len(values) > 20 or len(set(values)) != len(values)
            or not set(values) <= files for values in scope['entries'].values()):
        raise APIError(422, 'SCREEN_ENTRY_INVALID', '각 화면에 현재 스냅샷의 소스 파일을 한 개 이상 연결하세요. 테스트 파일은 근거로 사용할 수 없습니다')


def make_builder(snapshot, raw_chunks, scope, root):
    files = {f: text for f, text in snapshot['files'].items() if production_file(f)}
    if len(raw_chunks) > 5000 or sum(len(t) for t in files.values()) > 500000:
        raise APIError(413, 'VERIFICATION_LIMIT_EXCEEDED', '현재 검증 한도는 소스 50만 문자·5000청크입니다')
    prefix = wrapper_prefix(files)
    rows, ids, metadata = [], {}, {}
    for item in sorted(raw_chunks, key=lambda c: (c['file_path'], c['start_line'], c['end_line'], c['id'])):
        if not production_file(item['file_path']):
            continue
        cid = len(rows) + 1
        rel = item['file_path'].removeprefix(prefix)
        kind = 'config' if PurePosixPath(rel).suffix in CONFIG_SUFFIXES else item['chunk_type']
        rows.append(ChunkRow(cid, kind, rel, item.get('symbol'), item['content']))
        ids[cid], metadata[cid] = item['id'], item
    for name, content in files.items():
        rel = PurePosixPath(name.removeprefix(prefix))
        if rel.is_absolute() or '..' in rel.parts or '\\' in str(rel):
            raise APIError(422, 'UNSAFE_SOURCE', '스냅샷 경로가 안전하지 않습니다')
        path = root / str(rel)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding='utf-8')
    entries = {key: [f.removeprefix(prefix) for f in values] for key, values in scope['entries'].items()}
    return ScopeBuilder(f"{scope['stack']}-sample", rows, str(root), entries=entries), {r.id: r for r in rows}, ids, metadata
