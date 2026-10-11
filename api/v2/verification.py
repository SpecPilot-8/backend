from copy import deepcopy
from fastapi import APIRouter
from pydantic import Field
from api.errors import APIError
from api.v2.common import DB, Input, Limit, Offset, make_job, missing
from api.v2.storage import all_resources, create, page, project, public, resource

router = APIRouter(prefix='/api/v2/projects/{p}/verification-runs', tags=['v2 검증·추적표'])
STATUSES = ('implemented', 'partial', 'mismatch', 'not_found', 'needs_review', 'not_statically_verifiable')


class RequirementVersion(Input):
    requirement_id: str
    revision: int = Field(ge=1)


class RunRequest(Input):
    snapshot_id: str
    requirement_versions: list[RequirementVersion] = Field(min_length=1, max_length=1000)
    model_config_version: int = Field(ge=0)


@router.post('', status_code=202)
def verify(p: str, body: RunRequest, db: DB):
    config = project(db, p)
    if config.config_version != body.model_config_version:
        raise APIError(409, 'CONFIG_CHANGED', '모델 설정이 변경되었습니다')
    snap = resource(db, p, body.snapshot_id, 'snapshot')
    if snap.data['status'] != 'ready':
        raise APIError(409, 'SNAPSHOT_NOT_READY', '코드 스냅샷을 먼저 준비하세요')
    if len({x.requirement_id for x in body.requirement_versions}) != len(body.requirement_versions):
        raise APIError(422, 'VALIDATION_ERROR', '요구사항 ID가 중복됩니다')
    fixed = []
    for link in body.requirement_versions:
        item = resource(db, p, link.requirement_id, 'requirement')
        data = public(item, link.revision, db)
        if data['review_status'] != 'approved':
            raise APIError(409, 'REQUIREMENT_NOT_APPROVED', '선택 revision의 검토 확정이 필요합니다')
        sub = resource(db, p, data['subtopic_id'], 'subtopic')
        topic = resource(db, p, sub.data['topic_id'], 'topic')
        fixed.append({**data, 'topic_id': topic.id, 'topic_title': topic.data['title'], 'subtopic_title': sub.data['title']})
    needed = missing('implementation_judgment', '팀원 2의 조건별 코드 판정 어댑터가 필요합니다') + missing('call_flow', '호출·화면 도달 범위 분석 어댑터가 필요합니다')
    run = create(db, p, 'verification_run', {'snapshot_id': snap.id, 'requirement_versions': [x.model_dump() for x in body.requirement_versions],
                  'model_config_snapshot': {'profile_id': config.profile_id, 'config_version': config.config_version},
                  'prompt_version': None, 'rules_version': None, 'status': 'blocked', 'missing_features': needed,
                  'requirements': fixed, 'results': {}})
    job = make_job(db, p, 'verify', {'verification_run_id': run.id, **body.model_dump()}, needed)
    run.data = {**run.data, 'job_id': job['id']}
    db.flush()
    return job


def run_out(item):
    data = public(item)
    data.pop('requirements', None)
    data.pop('results', None)
    return data


@router.get('')
def runs(p: str, db: DB, limit: Limit = 50, offset: Offset = 0):
    return page([run_out(x) for x in all_resources(db, p, 'verification_run')], limit, offset)


@router.get('/{v}')
def run(p: str, v: str, db: DB):
    return run_out(resource(db, p, v, 'verification_run'))


def selected_rows(item, status=None, q=None):
    if status is not None and status not in (*STATUSES, 'not_run'):
        raise APIError(422, 'VALIDATION_ERROR', '알 수 없는 판정 필터입니다')
    items = []
    for req in item.data['requirements']:
        result = item.data['results'].get(req['id'])
        verdict = result['status'] if result else None
        if status is not None and status != (verdict or 'not_run'):
            continue
        if q and q.casefold() not in (req['title'] + req['stable_key'] + req['topic_title'] + req['subtopic_title']).casefold():
            continue
        items.append({'requirement_id': req['id'], 'requirement_revision': req['revision'], 'stable_key': req['stable_key'],
                      'title': req['title'], 'topic_id': req['topic_id'], 'subtopic_id': req['subtopic_id'],
                      'topic_title': req['topic_title'], 'subtopic_title': req['subtopic_title'],
                      'analysis_state': 'completed' if result else item.data['status'], 'status': verdict,
                      'reason': result['reason'] if result else '판정 모델 미연결: 분석되지 않았습니다',
                      'evidence_count': len(result['evidence']) if result else 0,
                      'finding_count': len(result['findings']) if result else 0,
                      'detail_url': f"/api/v2/projects/{item.project_id}/verification-runs/{item.id}/requirements/{req['id']}"})
    return items


@router.get('/{v}/traceability')
def traceability(p: str, v: str, db: DB, status: str | None = None, q: str | None = None, limit: Limit = 50, offset: Offset = 0):
    return page(selected_rows(resource(db, p, v, 'verification_run'), status, q), limit, offset)


@router.get('/{v}/summary')
def summary(p: str, v: str, db: DB, status: str | None = None, q: str | None = None):
    rows = selected_rows(resource(db, p, v, 'verification_run'), status, q)
    counts = {key: sum((r['status'] or 'not_run') == key for r in rows) for key in ('not_run', *STATUSES)}
    return {**counts, 'total': len(rows), 'denominator': len(rows),
            'implementation_rate': counts['implemented'] / len(rows) * 100 if rows else 0.0}


@router.get('/{v}/requirements/{r}')
def detail(p: str, v: str, r: str, db: DB):
    item = resource(db, p, v, 'verification_run')
    req = next((x for x in item.data['requirements'] if x['id'] == r), None)
    if req is None:
        raise APIError(404, 'RESOURCE_NOT_FOUND', '선택 실행에 포함되지 않은 요구사항입니다')
    result = item.data['results'].get(r)
    if result:
        return deepcopy(result)
    return {'run_id': v, 'snapshot_id': item.data['snapshot_id'], 'requirement_id': r, 'requirement_revision': req['revision'],
            'status': None, 'analysis_state': item.data['status'], 'reason': '판정 모델이 연결되지 않았습니다',
            'condition_verdicts': [{'condition_id': c['id'], 'status': None, 'reason': '미분석', 'evidence_ids': []} for c in req['conditions']],
            'findings': [], 'evidence': [], 'call_flow': {'nodes': [], 'edges': [], 'unresolved': []},
            'counts': {'findings': 0, 'affected_conditions': 0, 'evidence_locations': 0, 'files': 0},
            'warnings': item.data['missing_features']}
