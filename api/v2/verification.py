from copy import deepcopy
from fastapi import APIRouter, BackgroundTasks, Request
from pydantic import Field
from typing import Literal
from api.errors import APIError
from api.v2.common import DB, Config, Input, Limit, Offset, make_job, missing, job_out
from api.v2.storage import all_resources, create, page, project, public, resource

router = APIRouter(prefix='/api/v2/projects/{p}/verification-runs', tags=['v2 검증·추적표'])
STATUSES = ('implemented', 'partial', 'mismatch', 'not_found', 'needs_review', 'not_statically_verifiable')


class RequirementVersion(Input):
    requirement_id: str
    revision: int = Field(ge=1)


class ScopeConfig(Input):
    stack: Literal['spring', 'react']
    entries: dict[str, list[str]]
    confirmed: Literal[True]


class RunRequest(Input):
    snapshot_id: str
    requirement_versions: list[RequirementVersion] = Field(min_length=1, max_length=1000)
    model_config_version: int = Field(ge=0)
    scope_config: ScopeConfig | None = None


def fixed_input(p, body, db):
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
    return snap, fixed


@router.post('/prepare')
def prepare(p: str, body: RunRequest, db: DB):
    from api.v2.verification_scope import scope_options
    snap, fixed = fixed_input(p, body, db)
    return scope_options(db, p, snap, fixed, getattr(project(db, p), 'tech_stack', 'auto'))


@router.post('', status_code=202)
def verify(p: str, body: RunRequest, db: DB, settings: Config, request: Request, background_tasks: BackgroundTasks):
    config = project(db, p)
    if config.config_version != body.model_config_version:
        raise APIError(409, 'CONFIG_CHANGED', '모델 설정이 변경되었습니다')
    snap, fixed = fixed_input(p, body, db)
    from api.v2.server_llm import server_profile, connection_state
    enabled = server_profile(settings)['enabled']
    if enabled and connection_state(db, p, config.config_version, settings) != 'connected':
        raise APIError(409, 'MODEL_NOT_CONNECTED', '프로젝트 설정에서 서버 모델의 실제 연결을 먼저 확인하세요')
    scope = body.scope_config.model_dump() if body.scope_config else None
    if enabled:
        from api.v2.verification_scope import scope_options, validate_scope
        validate_scope(scope, scope_options(db, p, snap, fixed, getattr(config, 'tech_stack', 'auto')))
    needed = [] if enabled else missing('model_connection', '서버 모델 연결을 설정하고 화면 진입 파일을 확인하세요')
    from api.v2.verification_runner import PROMPT_VERSION, RULES_VERSION, run_verification
    from api.v2.extraction_runner import QUEUE_LOCK
    run = create(db, p, 'verification_run', {'snapshot_id': snap.id, 'requirement_versions': [x.model_dump() for x in body.requirement_versions],
                  'model_config_snapshot': {'profile_id': config.profile_id, 'config_version': config.config_version,
                                            'provider': settings.llm_provider, 'model': settings.llm_model},
                  'scope_config': scope,
                  'prompt_version': PROMPT_VERSION, 'rules_version': RULES_VERSION, 'status': 'queued' if enabled else 'blocked', 'missing_features': needed,
                  'requirements': fixed, 'results': {}})
    with QUEUE_LOCK:
        if enabled and any(j.data['operation'] == 'verify' and j.data['status'] in ('queued', 'running') for j in all_resources(db, p, 'job')):
            raise APIError(409, 'VERIFICATION_IN_PROGRESS', '이 프로젝트의 코드 검증이 진행 중입니다. 진행 상태를 확인하세요')
        job = make_job(db, p, 'verify', {'verification_run_id': run.id, **body.model_dump()}, needed,
                       result_ref={'kind': 'verification_run', 'id': run.id} if enabled else None,
                       progress={'stage': 'queued', 'completed': 0, 'total': len(fixed)}, status='queued' if enabled else 'blocked')
        run.data = {**run.data, 'job_id': job['id']}
        if enabled:
            create(db, p, 'verification_scope', {'snapshot_id': snap.id, **scope})
        db.commit()
    if enabled:
        background_tasks.add_task(run_verification, request.app.state.database, p, run.id, settings)
    return job_out(resource(db, p, job['id'], 'job'))


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
                      'reason': result['reason'] if result else item.data.get('error', {}).get('message', '판정이 아직 완료되지 않았습니다'),
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
            'status': None, 'analysis_state': item.data['status'], 'reason': item.data.get('error', {}).get('message', '판정이 아직 완료되지 않았습니다'),
            'condition_verdicts': [{'condition_id': c['id'], 'status': None, 'reason': '미분석', 'evidence_ids': []} for c in req['conditions']],
            'findings': [], 'evidence': [], 'call_flow': {'nodes': [], 'edges': [], 'unresolved': []},
            'counts': {'findings': 0, 'affected_conditions': 0, 'evidence_locations': 0, 'files': 0},
            'warnings': item.data['missing_features']}
