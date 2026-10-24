from copy import deepcopy
from datetime import timezone
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter
from pydantic import AwareDatetime, Field, model_validator
from sqlalchemy import update

from api.errors import APIError
from api.models import now
from api.v2.common import DB, Input, Limit, Offset, RevisionQuery, make_job, missing
from api.v2.storage import ProjectRecord, all_resources, create, historical, page, public, replace, replay, remember, resource

router = APIRouter(prefix='/api/v2/projects/{p}', tags=['v2 시험 절차·수행·표 저장'])
EXPORT_SECTIONS = {'login': '01. 로그인', 'signup': '02. 회원가입', 'password_find': '03. 비밀번호찾기',
                   'notice': '04. 공지사항', 'mypage': '05. 마이페이지', 'authorization': '06. 권한', 'general': '07. 기타 기능'}


class RequirementLink(Input):
    requirement_id: str
    requirement_revision: int = Field(ge=1)
    condition_ids: list[str] = Field(min_length=1, max_length=200)


class TestData(Input):
    account: str | None = Field(default=None, max_length=1000)
    input: str = Field(default='', max_length=10000)
    notes: str = Field(default='', max_length=10000)


class Step(Input):
    step_key: str = Field(min_length=1, max_length=100)
    order: int = Field(ge=1, le=100)
    action: str = Field(min_length=1, max_length=5000)
    expected: str = Field(min_length=1, max_length=10000)
    test_data: TestData
    requirement_links: list[RequirementLink] = Field(min_length=1, max_length=100)


class GenerateRequest(Input):
    verification_run_id: str
    requirement_ids: list[str] = Field(min_length=1, max_length=1000)


class CaseCreate(Input):
    test_specification_id: str
    export_section_key: str = Field(min_length=1, max_length=100)
    subtopic_id: str
    title: str = Field(min_length=1, max_length=300)
    type: Literal['positive', 'negative', 'boundary']
    preconditions: str = Field(default='', max_length=10000)
    requirement_links: list[RequirementLink] = Field(min_length=1, max_length=100)
    steps: list[Step] = Field(min_length=1, max_length=100)


class Procedure(Input):
    title: str | None = Field(default=None, min_length=1, max_length=300)
    preconditions: str | None = Field(default=None, max_length=10000)
    steps: list[Step] | None = Field(default=None, min_length=1, max_length=100)

    @model_validator(mode='after')
    def present(self):
        if not self.model_fields_set or any(getattr(self, k) is None for k in self.model_fields_set):
            raise ValueError('변경 필드가 필요합니다')
        return self


class ProcedureEdit(Procedure):
    base_revision: int = Field(ge=1)

    @model_validator(mode='after')
    def changed(self):
        if not self.model_fields_set - {'base_revision'}:
            raise ValueError('변경 필드가 필요합니다')
        return self


class ExecutionCreate(Input):
    test_case_revision: int = Field(ge=1)
    client_execution_id: UUID
    environment: str = Field(default='', max_length=2000)
    tested_build: str = Field(default='', max_length=2000)


class StepResult(Input):
    step_key: str = Field(min_length=1, max_length=100)
    status: Literal['not_run', 'passed', 'failed', 'on_hold']
    actual: str = Field(default='', max_length=10000)
    notes: str = Field(default='', max_length=10000)
    performed_at: AwareDatetime | None = None

    @model_validator(mode='after')
    def actual_required(self):
        if self.status in ('passed', 'failed') and (not self.actual.strip() or self.performed_at is None):
            raise ValueError('실제 결과와 수행 시각이 필요합니다')
        if self.status == 'on_hold' and not self.notes.strip():
            raise ValueError('보류 사유가 필요합니다')
        if self.performed_at:
            self.performed_at = self.performed_at.astimezone(timezone.utc)
        return self


class ExecutionEdit(Input):
    base_version: int = Field(ge=1)
    client_edit_id: UUID
    step_results: list[StepResult] = Field(min_length=1, max_length=100)


class ExecutionBatchCreate(Input):
    operation: Literal['create']
    bind_to: Literal['after_save']
    step_results: list[StepResult] = Field(min_length=1, max_length=100)
    environment: str = Field(default='', max_length=2000)
    tested_build: str = Field(default='', max_length=2000)


class ExecutionBatchUpdate(Input):
    operation: Literal['update']
    execution_id: str
    base_version: int = Field(ge=1)
    step_results: list[StepResult] = Field(min_length=1, max_length=100)


class Change(Input):
    test_case_id: str
    base_test_case_revision: int = Field(ge=1)
    procedure: Procedure | None = None
    execution: Annotated[ExecutionBatchCreate | ExecutionBatchUpdate, Field(discriminator='operation')] | None = None

    @model_validator(mode='after')
    def nonempty(self):
        if self.procedure is None and self.execution is None:
            raise ValueError('절차 또는 실제 결과 변경이 필요합니다')
        return self


class Batch(Input):
    base_specification_revision: int = Field(ge=1)
    client_save_id: UUID
    changes: list[Change] = Field(min_length=1, max_length=200)


def lock_project(db, p):
    # Serialize save/replay/attempt allocation within a project on SQLite and PostgreSQL.
    result = db.execute(update(ProjectRecord).where(ProjectRecord.id == p).values(version=ProjectRecord.version))
    if result.rowcount != 1:
        raise APIError(404, 'RESOURCE_NOT_FOUND', '프로젝트를 찾을 수 없습니다')


def spec_out(item, revision=None, db=None):
    data = public(item, revision, db)
    data.pop('requirements', None)
    return data


def link_set(links):
    pairs, result = set(), set()
    for link in links:
        pair = (link['requirement_id'], link['requirement_revision'])
        if pair in pairs or len(link['condition_ids']) != len(set(link['condition_ids'])):
            raise APIError(422, 'VALIDATION_ERROR', '요구사항/조건 참조가 중복됩니다')
        pairs.add(pair)
        result.update((*pair, c) for c in link['condition_ids'])
    return result


def links_from_steps(steps):
    grouped = {}
    for step in steps:
        for r, revision, c in link_set(step['requirement_links']):
            grouped.setdefault((r, revision), set()).add(c)
    return [{'requirement_id': r, 'requirement_revision': revision, 'condition_ids': sorted(ids)}
            for (r, revision), ids in sorted(grouped.items())]


def validate_case(db, p, spec, data):
    if data['export_section_key'] not in EXPORT_SECTIONS:
        raise APIError(422, 'VALIDATION_ERROR', '지원하지 않는 출력 영역입니다')
    sub = resource(db, p, data['subtopic_id'], 'subtopic')
    fixed = {(r['id'], r['revision']): r for r in spec['requirements']}
    keys = [s['step_key'] for s in data['steps']]
    if len(keys) != len(set(keys)) or [s['order'] for s in data['steps']] != list(range(1, len(keys)+1)):
        raise APIError(422, 'VALIDATION_ERROR', '단계 키는 고유하고 순서는 1부터 연속이어야 합니다')
    combined = link_set(data['requirement_links'])
    union = set()
    for step in data['steps']:
        union |= link_set(step['requirement_links'])
    if combined != union:
        raise APIError(422, 'VALIDATION_ERROR', '시나리오 참조는 단계 참조의 합집합이어야 합니다')
    linked_subs = set()
    for r, revision, c in union:
        req = fixed.get((r, revision))
        if req is None or c not in {v['id'] for v in req['conditions']}:
            raise APIError(422, 'VALIDATION_ERROR', '선택 사양서의 요구사항/조건 revision이 아닙니다')
        stored = historical(db, resource(db, p, r, 'requirement'), revision)
        if stored['review_status'] != 'approved':
            raise APIError(409, 'REQUIREMENT_NOT_APPROVED', '검토된 요구사항이 필요합니다')
        linked_subs.add(req['subtopic_id'])
    if sub.id not in linked_subs:
        raise APIError(422, 'VALIDATION_ERROR', '시험 소주제와 참조 요구사항이 다릅니다')


def changed_procedure(db, p, case, spec, procedure):
    data = {**deepcopy(case.data), **procedure.model_dump(mode='json', exclude_unset=True, exclude={'base_revision'})}
    if 'steps' in procedure.model_fields_set:
        data['requirement_links'] = links_from_steps(data['steps'])
    validate_case(db, p, spec.data, data)
    if data == case.data:
        raise APIError(422, 'EMPTY_CHANGE', '저장할 절차 변경이 없습니다')
    data.update(revision=case.data['revision']+1, prediction={'status': 'unknown', 'reason': '절차가 수정되어 예측 갱신이 필요합니다', 'verification_run_id': spec.data['verification_run_id'], 'primary_chunk_ids': []})
    return data


def update_spec(db, spec, versions, reason):
    revision = spec.data['revision'] + 1
    data = {**spec.data, 'revision': revision, 'case_versions': versions, 'test_case_ids': list(versions), 'status': 'draft',
            'revision_history': spec.data['revision_history'] + [{'revision': revision, 'date': now(), 'reason': reason}]}
    return replace(db, spec, data)


def create_case(db, p, spec, data, origin='manual', prediction=None):
    validate_case(db, p, spec.data, data)
    count = len([c for c in all_resources(db, p, 'test_case') if c.data['test_specification_id'] == spec.id]) + 1
    case = create(db, p, 'test_case', {**data, 'test_no': f'TC-{spec.id[:8]}-{count:04}', 'revision': 1, 'origin': origin,
                  'prediction': prediction or {'status': 'unknown', 'reason': '수동 시험: 코드 예측 없음', 'verification_run_id': spec.data['verification_run_id'], 'primary_chunk_ids': []}})
    return case


@router.post('/test-specifications', status_code=201)
def create_manual_specification(p: str, body: GenerateRequest, db: DB):
    lock_project(db, p)
    run = resource(db, p, body.verification_run_id, 'verification_run')
    if len(body.requirement_ids) != len(set(body.requirement_ids)):
        raise APIError(422, 'VALIDATION_ERROR', '요구사항 ID가 중복됩니다')
    selected = [deepcopy(r) for r in run.data['requirements'] if r['id'] in body.requirement_ids]
    if len(selected) != len(body.requirement_ids):
        raise APIError(422, 'VALIDATION_ERROR', '선택 검증 실행에 없는 요구사항입니다')
    spec = create(db, p, 'test_specification', {'verification_run_id': run.id, 'revision': 1, 'status': 'draft',
                  'origin': 'manual', 'test_case_ids': [], 'case_versions': {}, 'requirements': selected,
                  'revision_history': [{'revision': 1, 'date': now(), 'reason': '사용자 수동 사양서 등록'}]})
    return spec_out(spec)


@router.post('/test-specifications/generate', status_code=202)
def generate(p: str, body: GenerateRequest, db: DB):
    lock_project(db, p)
    run = resource(db, p, body.verification_run_id, 'verification_run')
    if len(body.requirement_ids) != len(set(body.requirement_ids)):
        raise APIError(422, 'VALIDATION_ERROR', '요구사항 ID가 중복됩니다')
    selected = [deepcopy(r) for r in run.data['requirements'] if r['id'] in body.requirement_ids]
    if len(selected) != len(body.requirement_ids):
        raise APIError(422, 'VALIDATION_ERROR', '선택 검증 실행에 없는 요구사항입니다')
    spec = create(db, p, 'test_specification', {'verification_run_id': run.id, 'revision': 1, 'status': 'blocked', 'test_case_ids': [],
                  'case_versions': {}, 'requirements': selected, 'revision_history': [{'revision': 1, 'date': now(), 'reason': '사양서 등록'}]})
    needed = missing('test_case_generation', '팀원 2의 단계별 테스트 생성 모델 어댑터가 필요합니다')
    if run.data['status'] != 'completed':
        needed += missing('verification_result', '선택 실행의 코드 판정도 아직 완료되지 않았습니다')
    job = make_job(db, p, 'generate_test_specification', {**body.model_dump(), 'test_specification_id': spec.id}, needed)
    spec.data = {**spec.data, 'job_id': job['id']}
    db.flush()
    return job


@router.get('/test-specifications')
def specifications(p: str, db: DB, limit: Limit = 50, offset: Offset = 0):
    return page([spec_out(s) for s in all_resources(db, p, 'test_specification')], limit, offset)


@router.get('/test-specifications/{t}')
def specification(p: str, t: str, db: DB, revision: RevisionQuery = None):
    return spec_out(resource(db, p, t, 'test_specification'), revision, db)


@router.post('/test-cases', status_code=201)
def add_case(p: str, body: CaseCreate, db: DB):
    lock_project(db, p)
    spec = resource(db, p, body.test_specification_id, 'test_specification')
    case = create_case(db, p, spec, body.model_dump(mode='json'))
    update_spec(db, spec, {**spec.data['case_versions'], case.id: 1}, '수동 시험 추가')
    return public(case)


@router.get('/test-cases')
def cases(p: str, test_specification_id: str, db: DB, requirement_id: str | None = None,
          limit: Limit = 50, offset: Offset = 0, specification_revision: RevisionQuery = None):
    spec = resource(db, p, test_specification_id, 'test_specification')
    versions = historical(db, spec, specification_revision)['case_versions']
    if requirement_id:
        resource(db, p, requirement_id, 'requirement')
    items = [public(resource(db, p, tc, 'test_case'), rev, db) for tc, rev in versions.items()]
    return page([c for c in items if not requirement_id or any(r['requirement_id'] == requirement_id for r in c['requirement_links'])], limit, offset)


@router.get('/test-cases/{tc}')
def case(p: str, tc: str, db: DB, revision: RevisionQuery = None):
    return public(resource(db, p, tc, 'test_case'), revision, db)


@router.patch('/test-cases/{tc}/procedure')
def procedure(p: str, tc: str, body: ProcedureEdit, db: DB):
    lock_project(db, p)
    case = resource(db, p, tc, 'test_case')
    spec = resource(db, p, case.data['test_specification_id'], 'test_specification')
    data = changed_procedure(db, p, case, spec, body)
    replace(db, case, data, body.base_revision)
    update_spec(db, spec, {**spec.data['case_versions'], case.id: data['revision']}, '시험 절차 수정')
    return public(case)


def aggregate_execution(case, results):
    statuses = {r['step_key']: r['status'] for r in results}
    values = [statuses.get(s['step_key'], 'not_run') for s in case['steps']]
    if 'failed' in values:
        return 'failed'
    if 'on_hold' in values:
        return 'on_hold'
    return 'passed' if values and all(s == 'passed' for s in values) else 'not_run'


def validate_results(case, results):
    keys = [r['step_key'] for r in results]
    if len(keys) != len(set(keys)) or not set(keys) <= {s['step_key'] for s in case['steps']}:
        raise APIError(422, 'VALIDATION_ERROR', '수행 단계가 중복되거나 해당 절차에 없습니다')


def new_execution(db, p, case, revision, environment='', tested_build='', results=None):
    procedure = historical(db, case, revision)
    results = results or []
    validate_results(procedure, results)
    attempts = [e.data['attempt_no'] for e in all_resources(db, p, 'test_execution') if e.data['test_case_id'] == case.id]
    return create(db, p, 'test_execution', {'test_case_id': case.id, 'test_case_revision': revision, 'version': 1,
                  'attempt_no': max(attempts, default=0)+1, 'status': aggregate_execution(procedure, results),
                  'step_results': results, 'environment': environment, 'tested_build': tested_build, 'updated_at': now()})


def edited_execution(db, p, item, results):
    procedure = historical(db, resource(db, p, item.data['test_case_id'], 'test_case'), item.data['test_case_revision'])
    validate_results(procedure, results)
    merged = {r['step_key']: r for r in item.data['step_results']}
    merged.update({r['step_key']: r for r in results})
    combined = [merged[s['step_key']] for s in procedure['steps'] if s['step_key'] in merged]
    return {**item.data, 'step_results': combined, 'status': aggregate_execution(procedure, combined),
            'version': item.data['version']+1, 'updated_at': now()}


def execution_out(db, item):
    data = public(item)
    procedure = historical(db, resource(db, item.project_id, data['test_case_id'], 'test_case'), data['test_case_revision'])
    recorded = {s['step_key']: s for s in data['step_results']}
    data['step_results'] = [recorded.get(s['step_key'], {'step_key': s['step_key'], 'status': 'not_run', 'actual': '', 'notes': '', 'performed_at': None}) for s in procedure['steps']]
    return data


@router.post('/test-cases/{tc}/executions', status_code=201)
def add_execution(p: str, tc: str, body: ExecutionCreate, db: DB):
    lock_project(db, p)
    scope = f'create-execution:{tc}'
    request = body.model_dump(mode='json')
    saved = replay(db, p, scope, body.client_execution_id, request)
    if saved is not None:
        return saved
    case = resource(db, p, tc, 'test_case')
    item = new_execution(db, p, case, body.test_case_revision, body.environment, body.tested_build)
    result = public(item)
    remember(db, p, scope, body.client_execution_id, request, result)
    return result


@router.get('/test-cases/{tc}/executions')
def executions(p: str, tc: str, db: DB, limit: Limit = 50, offset: Offset = 0):
    resource(db, p, tc, 'test_case')
    return page([execution_out(db, e) for e in all_resources(db, p, 'test_execution') if e.data['test_case_id'] == tc], limit, offset)


@router.patch('/test-executions/{e}')
def edit_execution(p: str, e: str, body: ExecutionEdit, db: DB):
    lock_project(db, p)
    scope = f'edit-execution:{e}'
    request = body.model_dump(mode='json')
    saved = replay(db, p, scope, body.client_edit_id, request)
    if saved is not None:
        return saved
    item = resource(db, p, e, 'test_execution')
    replace(db, item, edited_execution(db, p, item, [s.model_dump(mode='json') for s in body.step_results]), body.base_version)
    result = execution_out(db, item)
    remember(db, p, scope, body.client_edit_id, request, result)
    return result


@router.post('/test-specifications/{t}/edit-batches')
def save_batch(p: str, t: str, body: Batch, db: DB):
    lock_project(db, p)
    scope = f'edit-batch:{t}'
    request = body.model_dump(mode='json', exclude_unset=True)
    saved = replay(db, p, scope, body.client_save_id, request)
    if saved is not None:
        return saved
    spec = resource(db, p, t, 'test_specification')
    if spec.data['revision'] != body.base_specification_revision:
        raise APIError(409, 'VERSION_CONFLICT', '사양서 버전이 변경되었습니다', details={'current_revision': spec.data['revision'], 'items': []})
    ids = [c.test_case_id for c in body.changes]
    if len(ids) != len(set(ids)):
        raise APIError(422, 'VALIDATION_ERROR', '같은 시험을 중복 변경할 수 없습니다')
    planned = []
    for change in body.changes:
        try:
            case = resource(db, p, change.test_case_id, 'test_case')
            if case.id not in spec.data['case_versions']:
                raise APIError(422, 'VALIDATION_ERROR', '선택 사양서의 시험이 아닙니다')
            if case.data['revision'] != change.base_test_case_revision:
                raise APIError(409, 'VERSION_CONFLICT', '시험 절차 버전이 변경되었습니다', details={'current_revision': case.data['revision']})
            case_data = changed_procedure(db, p, case, spec, change.procedure) if change.procedure else deepcopy(case.data)
            execution_item, execution_data = None, None
            if change.execution:
                results = [s.model_dump(mode='json') for s in change.execution.step_results]
                validate_results(case_data, results)
                if change.execution.operation == 'update':
                    execution_item = resource(db, p, change.execution.execution_id, 'test_execution')
                    if change.procedure or execution_item.data['test_case_id'] != case.id or execution_item.data['test_case_revision'] != case_data['revision']:
                        raise APIError(409, 'EXECUTION_REVISION_CONFLICT', '절차 변경은 새 수행 회차로 기록하세요')
                    if execution_item.data['version'] != change.execution.base_version:
                        raise APIError(409, 'VERSION_CONFLICT', '수행 회차 버전이 변경되었습니다', details={'current_version': execution_item.data['version']})
                    execution_data = edited_execution(db, p, execution_item, results)
            planned.append((change, case, case_data, execution_item, execution_data))
        except APIError as error:
            error.details = {'items': [{'test_case_id': change.test_case_id, 'field': 'procedure' if change.procedure else 'execution', 'code': error.code, **error.details}]}
            raise
    changed_cases, changed_executions = [], []
    versions = deepcopy(spec.data['case_versions'])
    for change, case, case_data, execution_item, execution_data in planned:
        if change.procedure:
            replace(db, case, case_data, change.base_test_case_revision)
            versions[case.id] = case_data['revision']
        changed_cases.append(public(case))
        if change.execution:
            if change.execution.operation == 'create':
                execution_item = new_execution(db, p, case, case_data['revision'], change.execution.environment,
                                               change.execution.tested_build, [s.model_dump(mode='json') for s in change.execution.step_results])
            else:
                replace(db, execution_item, execution_data, change.execution.base_version)
            changed_executions.append(execution_out(db, execution_item))
    if any(c.procedure for c in body.changes):
        update_spec(db, spec, versions, '표 편집 저장')
    result = {'client_save_id': str(body.client_save_id), 'specification_revision': spec.data['revision'],
              'test_cases': changed_cases, 'executions': changed_executions, 'saved_at': now()}
    remember(db, p, scope, body.client_save_id, request, result)
    return result


def current_cases(db, p, spec, revision=None, requirement_id=None):
    data = historical(db, spec, revision)
    cases = [public(resource(db, p, c, 'test_case'), r, db) for c, r in data['case_versions'].items()]
    return [c for c in cases if not requirement_id or any(r['requirement_id'] == requirement_id for r in c['requirement_links'])]


def latest_execution(db, p, case):
    items = [e for e in all_resources(db, p, 'test_execution') if e.data['test_case_id'] == case['id'] and e.data['test_case_revision'] == case['revision']]
    return max(items, key=lambda e: e.data['attempt_no'], default=None)


@router.get('/test-specifications/{t}/execution-summary')
def execution_summary(p: str, t: str, db: DB, revision: RevisionQuery = None, requirement_id: str | None = None,
                      execution_selection: Literal['latest_per_case'] = 'latest_per_case'):
    spec = resource(db, p, t, 'test_specification')
    if requirement_id:
        resource(db, p, requirement_id, 'requirement')
    cases = current_cases(db, p, spec, revision, requirement_id)
    scenarios, steps = [], []
    for case in cases:
        execution = latest_execution(db, p, case)
        scenarios.append(execution.data['status'] if execution else 'not_run')
        results = {s['step_key']: s['status'] for s in execution.data['step_results']} if execution else {}
        steps.extend(results.get(s['step_key'], 'not_run') for s in case['steps'])
    def counts(values):
        return {'total': len(values), **{k: values.count(k) for k in ('not_run', 'passed', 'failed', 'on_hold')}}
    return {'scenario_summary': counts(scenarios), 'step_summary': counts(steps)}
