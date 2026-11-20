# [api/v2/impact.py]
# - 담당 파트: 백엔드 분석 파이프라인 / 변경 영향
# - 작성자: 정래원
# - 페이지 개요: 코드가 바뀌었을 때 영향받는 화면·요구사항·시험을 정적 호출 그래프로 찾는다.
# - 주요 기능:
#   1) 검증 실행의 스냅샷과 새 스냅샷의 파일을 비교해 바뀐 파일을 찾는다 (또는 파일을 직접 지정)
#   2) 화면마다 진입 파일에서 도달하는 코드 범위에 바뀐 파일이 들어 있으면 영향 화면으로 본다
#   3) 영향 화면의 요구사항과 그 요구사항에 연결된 시험을 모은다
# 모델 호출 없음. 판정을 다시 하지 않고 "어디를 다시 봐야 하는가"만 알려준다.
from pathlib import Path
from tempfile import TemporaryDirectory

from api.errors import APIError
from api.v2.storage import all_resources, public, resource
from api.v2.verification_scope import make_builder


def changed_files(before, after):
    """{경로: 'modified'|'added'|'deleted'}"""
    out = {path: 'modified' for path in before.keys() & after.keys() if before[path] != after[path]}
    out.update({path: 'added' for path in after.keys() - before.keys()})
    out.update({path: 'deleted' for path in before.keys() - after.keys()})
    return out


def _reached_files(db, p, snapshot_id, scope_config):
    """{화면 키: 그 화면 범위에 든 파일 경로 집합}. 진입 파일이 스냅샷에 없는 화면은 빠진다."""
    snapshot = resource(db, p, snapshot_id, 'snapshot').data
    raw = [public(c) for c in all_resources(db, p, 'chunk') if c.data['snapshot_id'] == snapshot_id]
    entries = {key: [f for f in files if f in snapshot['files']] for key, files in scope_config['entries'].items()}
    entries = {key: files for key, files in entries.items() if files}
    if not entries:
        return {}
    with TemporaryDirectory(prefix='specpilot-impact-') as folder:
        builder, _, _, metadata = make_builder(snapshot, raw, {**scope_config, 'entries': entries}, Path(folder))
        return {key: {metadata[cid]['file_path'] for cid in builder.scope(key).all} for key in entries}


def impact_report(db, p, run, snapshot_id=None, files=None):
    data = run.data
    if not snapshot_id and not files:
        raise APIError(422, 'IMPACT_INPUT_REQUIRED', '비교할 스냅샷이나 바뀐 파일 경로를 지정하세요')
    if not data.get('scope_config'):
        raise APIError(409, 'IMPACT_SCOPE_MISSING', '화면 진입 파일이 없는 검증 실행이라 영향 범위를 계산할 수 없습니다')
    base_files = resource(db, p, data['snapshot_id'], 'snapshot').data['files']
    changes = {}
    if snapshot_id:
        changes = changed_files(base_files, resource(db, p, snapshot_id, 'snapshot').data['files'])
    for path in files or []:
        if path not in base_files:
            raise APIError(422, 'IMPACT_FILE_UNKNOWN', f'검증 실행의 스냅샷에 없는 파일입니다: {path}')
        changes.setdefault(path, 'modified')

    # 바뀐·지워진 파일은 기존 스냅샷의 범위로, 바뀐·새 파일은 새 스냅샷의 범위로 본다.
    # 새 파일이 기존 진입점에서 호출되기 시작한 경우도 잡기 위해서다.
    reached = {key: set(v) for key, v in _reached_files(db, p, data['snapshot_id'], data['scope_config']).items()}
    if snapshot_id:
        for key, paths in _reached_files(db, p, snapshot_id, data['scope_config']).items():
            reached.setdefault(key, set()).update(paths)

    entry_sets = data['scope_config']['entries']
    by_group = {}
    for req in data['requirements']:
        by_group.setdefault(req.get('screen_id') or req['subtopic_id'], []).append(req)
    screens = []
    for key, reqs in by_group.items():
        if key not in entry_sets:
            continue
        via = sorted(path for path in changes if path in reached.get(key, set()))
        removed_entries = sorted(f for f in entry_sets[key] if changes.get(f) == 'deleted')
        if not via and not removed_entries:
            continue
        screens.append({
            'screen_key': key, 'title': reqs[0].get('subtopic_title', ''),
            'via_files': [{'path': f, 'change': changes[f], 'entry': f in entry_sets[key]} for f in via],
            'entry_removed': removed_entries,
            'requirements': [{'requirement_id': r['id'], 'stable_key': r['stable_key'], 'title': r.get('title', '')}
                             for r in reqs],
        })

    from api.v2.testing import current_cases
    affected = {r['requirement_id'] for s in screens for r in s['requirements']}
    cases = []
    for spec in all_resources(db, p, 'test_specification'):
        for case in current_cases(db, p, spec):
            linked = sorted({link['requirement_id'] for link in case['requirement_links']} & affected)
            if linked:
                cases.append({'test_specification_id': spec.id, 'test_case_id': case['id'],
                              'test_no': case['test_no'], 'title': case.get('title', ''), 'requirement_ids': linked})
    untouched = sorted(path for path in changes if not any(path in reached.get(k, set()) for k in entry_sets))
    return {
        'verification_run_id': run.id, 'base_snapshot_id': data['snapshot_id'], 'snapshot_id': snapshot_id,
        'changed_files': [{'path': f, 'change': c} for f, c in sorted(changes.items())],
        'unreached_files': untouched,
        'screen_count': len(screens), 'requirement_count': len(affected), 'test_case_count': len(cases),
        'screens': screens, 'test_cases': cases,
        'method': '정적 호출 그래프 기준. 동적 호출·리플렉션은 잡지 못한다.',
    }
