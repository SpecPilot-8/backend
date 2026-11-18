# - 담당 파트: 백엔드 API / 테스트 사양서
# - 작성자: 이성규
# - 페이지 개요: v2 테스트 사양서·케이스·수행 이력·설계 검토·배치 저장 통합 API 라우터
# - 주요 기능:
#   - 수동/AI 사양서 생성, 시험 케이스 추가, 절차 개정 및 수행 결과 상태 관리
#   - 편집 표 원자적 배치 저장, 실행 요약, 설계 관점 점검 및 절차별 설계 적합성 검토 API 제공

from copy import deepcopy
from datetime import timezone
from typing import Annotated, Literal
from uuid import NAMESPACE_URL, UUID, uuid5

from fastapi import APIRouter, BackgroundTasks, Request
from pydantic import AwareDatetime, Field, model_validator

from api.errors import APIError
from api.v2.common import DB, Config, Input, Limit, Offset, QUEUE_LOCK, RevisionQuery, make_job, missing, now
from api.v2.storage import Resource, all_resources, create, historical, lock_project, page, public, remember, replace, replay, resource

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


class StepDesign(Input):
    intent_ids: list[str] = Field(min_length=1, max_length=100)
    rationale: str = Field(min_length=1, max_length=2000, pattern=r'\S')
    input_rationale: str = Field(min_length=1, max_length=2000, pattern=r'\S')
    basis: Literal['specification', 'review_needed']


class Step(Input):
    step_key: str = Field(min_length=1, max_length=100)
    order: int = Field(ge=1, le=100)
    action: str = Field(min_length=1, max_length=5000)
    expected: str = Field(min_length=1, max_length=10000)
    test_data: TestData
    requirement_links: list[RequirementLink] = Field(min_length=1, max_length=100)
    design: StepDesign | None = None


class GenerateRequest(Input):
    verification_run_id: str
    requirement_ids: list[str] = Field(min_length=1, max_length=1000)


class GenerationRequest(GenerateRequest):
    model_config_version: int | None = Field(default=None, ge=0)
    design_plan_version: str | None = None


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


def spec_out(item, revision=None, db=None):
    data = public(item, revision, db)
    fixed = data.pop('requirements', [])
    data['requirement_versions'] = [{'requirement_id': r['id'], 'revision': r['revision']} for r in fixed]
    plan = data.pop('design_plan', None)
    data['design_plan_version'] = plan['version'] if plan else None
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
    # 시나리오 상위 참조와 개별 단계 참조의 합집합이 같아야 하며 각 단계의 설계 관점도 해당 개정 조건과 일치해야 한다.
    for step in data['steps']:
        if step.get('design') is None:
            step.pop('design', None)
        union |= link_set(step['requirement_links'])
        from testgen.design import validate_design
        try:
            validate_design(step, spec.get('design_plan'))
        except ValueError:
            raise APIError(422, 'VALIDATION_ERROR', '시험 관점은 해당 개정의 연결 조건과 일치해야 합니다') from None
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
        previous = {s['step_key']: s for s in case.data['steps']}
        for step in data['steps']:
            old = previous.get(step['step_key'], {})
            if old and step.get('design') and step['design'] != old.get('design'):
                raise APIError(422, 'VALIDATION_ERROR', '생성 당시 설계 설명은 직접 변경할 수 없습니다')
            # 연결 조건이 바뀐 단계나 새로 추가된 단계는 과거 AI 설계 근거가 맞지 않으므로 설명을 비워 재검토 대상으로 분리한다.
            step['design'] = (deepcopy(old.get('design')) if
                              old.get('requirement_links') == step['requirement_links'] else None)
        data['requirement_links'] = links_from_steps(data['steps'])
    validate_case(db, p, spec.data, data)
    if data == case.data:
        raise APIError(422, 'EMPTY_CHANGE', '저장할 절차 변경이 없습니다')
    # 절차가 바뀌면 기존 코드 기반 예측 결과는 구버전 기준이므로 unknown으로 초기화한다.
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


def generation_input(p, body, db, settings):
    run = resource(db, p, body.verification_run_id, 'verification_run')
    if len(body.requirement_ids) != len(set(body.requirement_ids)):
        raise APIError(422, 'VALIDATION_ERROR', '요구사항 ID가 중복됩니다')
    selected = [deepcopy(r) for r in run.data['requirements'] if r['id'] in body.requirement_ids]
    if len(selected) != len(body.requirement_ids):
        raise APIError(422, 'VALIDATION_ERROR', '선택 검증 실행에 없는 요구사항입니다')
    from api.v2.storage import project
    from api.v2.server_llm import connection_state, server_profile
    config = project(db, p)
    from testgen.design import PLAN_VERSION
    if body.design_plan_version is not None and body.design_plan_version != PLAN_VERSION:
        raise APIError(409, 'CONFIG_CHANGED', '시험 설계 기준이 변경되었습니다. 생성 범위를 다시 확인하세요')
    if body.model_config_version is not None and config.config_version != body.model_config_version:
        raise APIError(409, 'CONFIG_CHANGED', '모델 설정이 변경되었습니다')
    enabled = server_profile(settings)['enabled']
    groups = {}
    if enabled:
        if connection_state(db, p, config.config_version, settings) != 'connected':
            raise APIError(409, 'MODEL_NOT_CONNECTED', '프로젝트 설정에서 서버 모델의 실제 연결을 먼저 확인하세요')
        from api.v2.test_generation_runner import prepare_groups
        groups = prepare_groups(public(run), selected)
    return run, selected, config, enabled, groups


@router.post('/test-specifications/generate/prepare')
def prepare_generation(p: str, body: GenerationRequest, db: DB, settings: Config):
    run, selected, config, enabled, groups = generation_input(p, body, db, settings)
    if not enabled:
        raise APIError(409, 'LLM_NOT_CONFIGURED', '서버 모델 설정이 필요합니다')
    from testgen.design import build_plan
    from api.v2.test_generation_runner import _split_batches
    return {'verification_run_id': run.id, 'snapshot_id': run.data['snapshot_id'], 'model_config_version': config.config_version,
            'provider': settings.llm_provider, 'model': settings.llm_model,
            'requirement_count': len(selected), 'condition_count': sum(len(r['conditions']) for r in selected),
            'subtopic_count': len(groups),
            # 큰 소주제는 배치로 나눠 부르므로 소주제 수가 아니라 배치 수가 실제 최대 호출 수다.
            'max_model_calls': sum(len(_split_batches(reqs)) for reqs in groups.values()),
            'design_plan': build_plan(selected),
            'warnings': ['예상 결과는 승인된 명세로 생성하고 코드 예측은 기존 판정에서 따로 계산합니다.',
                         '생성 결과는 검토할 초안입니다. 실제 수행 O/X는 생성하지 않습니다.']}


@router.post('/test-specifications/generate', status_code=202)
def generate(p: str, body: GenerationRequest, db: DB, settings: Config, request: Request, background_tasks: BackgroundTasks):
    lock_project(db, p)
    run, selected, config, enabled, groups = generation_input(p, body, db, settings)
    needed = [] if enabled else missing('model_connection', '서버 모델 연결 설정이 필요합니다')
    if run.data['status'] != 'completed':
        needed += missing('verification_result', '선택 실행의 코드 판정도 아직 완료되지 않았습니다')
    from api.v2.test_generation_runner import PROMPT_VERSION, RULES_VERSION, run_generation
    from testgen.design import build_plan
    with QUEUE_LOCK:
        if enabled and any(j.data['operation'] == 'generate_test_specification' and j.data['status'] in ('queued', 'running') for j in all_resources(db, p, 'job')):
            raise APIError(409, 'GENERATION_IN_PROGRESS', '테스트케이스 생성이 진행 중입니다. 기존 작업의 진행 상태를 확인하세요')
        model = {'config_version': config.config_version, 'profile_id': config.profile_id, 'provider': settings.llm_provider, 'model': settings.llm_model}
        spec = create(db, p, 'test_specification', {'verification_run_id': run.id, 'snapshot_id': run.data['snapshot_id'],
                      'revision': 1, 'origin': 'generated', 'status': 'queued' if enabled else 'blocked',
                      'generation_status': 'queued' if enabled else 'blocked', 'model_config_snapshot': model,
                      'prompt_version': PROMPT_VERSION, 'rules_version': RULES_VERSION,
                      'test_case_ids': [], 'case_versions': {}, 'requirements': selected, 'warnings': [], 'uncovered_conditions': [],
                      'design_plan': build_plan(selected), 'design_gaps': [],
                      'revision_history': [{'revision': 1, 'date': now(), 'reason': 'AI 테스트케이스 생성 요청'}]})
        job = make_job(db, p, 'generate_test_specification', {**body.model_dump(), 'test_specification_id': spec.id}, needed,
                       result_ref={'kind': 'test_specification', 'id': spec.id} if enabled else None,
                       progress={'stage': 'queued', 'completed': 0, 'total': len(groups)}, status='queued' if enabled else 'blocked')
        spec.data = {**spec.data, 'job_id': job['id']}
        db.commit()
    if enabled:
        background_tasks.add_task(run_generation, request.app.state.database, p, spec.id, settings)
    return job


def ensure_editable(spec):
    if spec.data['status'] in ('queued', 'running'):
        raise APIError(409, 'GENERATION_IN_PROGRESS', '생성 중에는 시험 초안을 편집할 수 없습니다')


@router.get('/test-specifications')
def specifications(p: str, db: DB, limit: Limit = 50, offset: Offset = 0):
    return page([spec_out(s) for s in all_resources(db, p, 'test_specification')], limit, offset)


@router.get('/test-specifications/{t}')
def specification(p: str, t: str, db: DB, revision: RevisionQuery = None):
    return spec_out(resource(db, p, t, 'test_specification'), revision, db)


@router.get('/test-specifications/{t}/design-review')
def review_design(p: str, t: str, db: DB, revision: RevisionQuery = None):
    from testgen.design import design_review
    spec = resource(db, p, t, 'test_specification')
    fixed = historical(db, spec, revision)
    return {'test_specification_id': t, **design_review(fixed, current_cases(db, p, spec, revision))}


def quality_report(spec_data, cases):
    """사양서 시험을 규칙으로 점검한다. LLM을 부르지 않으므로 조회할 때마다 다시 계산한다."""
    from testgen.quality import check
    report = check(spec_data['requirements'], cases)
    return {'summary': report.summary(), 'boundary': report.boundary, 'duplicates': report.duplicates,
            'invented': report.invented, 'borrowed': report.borrowed}


@router.get('/test-specifications/{t}/quality')
def review_quality(p: str, t: str, db: DB, revision: RevisionQuery = None):
    # 경계값 정확·바깥값 누락, 단계·시험 중복, 기획서에 없는 문구, 다른 요구사항 문구 혼입을 돌려준다.
    spec = resource(db, p, t, 'test_specification')
    return {'test_specification_id': t, **quality_report(historical(db, spec, revision), current_cases(db, p, spec, revision))}


@router.post('/test-cases', status_code=201)
def add_case(p: str, body: CaseCreate, db: DB):
    lock_project(db, p)
    spec = resource(db, p, body.test_specification_id, 'test_specification')
    ensure_editable(spec)
    if any(s.design for s in body.steps):
        raise APIError(422, 'VALIDATION_ERROR', '수동 시험에 AI 생성 설계 기록을 등록할 수 없습니다')
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
    ensure_editable(spec)
    data = changed_procedure(db, p, case, spec, body)
    replace(db, case, data, body.base_revision)
    update_spec(db, spec, {**spec.data['case_versions'], case.id: data['revision']}, '시험 절차 수정')
    return public(case)


def aggregate_execution(case, results):
    # 단계별 판정 중 failed, on_hold, passed, not_run 순으로 우선 적용해 케이스 상태 집계
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
    # 동일 client_save_id 재시도면 기존 응답을 그대로 돌려 멱등성을 보장
    saved = replay(db, p, scope, body.client_save_id, request)
    if saved is not None:
        return saved
    spec = resource(db, p, t, 'test_specification')
    ensure_editable(spec)
    # 기준 사양서 개정 불일치와 같은 시험 케이스 중복 수정 여부를 쓰기 전에 선검사
    if spec.data['revision'] != body.base_specification_revision:
        raise APIError(409, 'VERSION_CONFLICT', '사양서 버전이 변경되었습니다', details={'current_revision': spec.data['revision'], 'items': []})
    ids = [c.test_case_id for c in body.changes]
    if len(ids) != len(set(ids)):
        raise APIError(422, 'VALIDATION_ERROR', '같은 시험을 중복 변경할 수 없습니다')
    # 전체 변경분을 먼저 검증해 planned에 누적하고 하나라도 실패하면 부분 반영 차단
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
    # 검증을 모두 통과한 변경만 실제 리소스에 반영하고 사양서 case_versions 갱신
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


class ConditionReview(Input):
    requirement_id: str
    requirement_revision: int = Field(ge=1)
    condition_id: str
    decision: Literal['adequate', 'needs_changes', 'uncertain']
    reason: str = Field(min_length=1, max_length=2000, pattern=r'\S')


class SaveReview(Input):
    test_case_revision: int = Field(ge=1)
    base_version: int = Field(ge=0)
    client_review_id: UUID
    items: list[ConditionReview] = Field(min_length=1, max_length=200)


def review_key(item):
    return item['requirement_id'], item['requirement_revision'], item['condition_id']


def review_identity(p, tc, revision):
    return str(uuid5(NAMESPACE_URL, f'specpilot:test-design-review:{p}:{tc}:{revision}'))


def review_for_case(db, p, case):
    """시험 적합성에 대한 사람의 검토를 절차 개정별로 보존한다. 실제 수행 결과와 별개다."""
    # 케이스 ID와 절차 개정 조합으로 결정된 uuid5 키를 조회해 개정별 설계 검토를 분리 보관
    item = db.get(Resource, review_identity(p, case['id'], case['revision']))
    if item is not None and (item.project_id != p or item.kind != 'test_design_review'):
        raise APIError(409, 'RESOURCE_CONFLICT', '설계 검토 저장소를 확인해야 합니다')
    saved = public(item) if item else {'version': 0, 'items': [], 'updated_at': None}
    # 연결된 조건 전체 대비 판정 집계 중 needs_changes, uncertain, adequate, pending 순으로 상태 결정
    total = len(link_set(case['requirement_links']))
    counts = {s: sum(i['decision'] == s for i in saved['items']) for s in ('adequate', 'needs_changes', 'uncertain')}
    status = ('needs_changes' if counts['needs_changes'] else 'uncertain' if counts['uncertain']
              else 'adequate' if total and counts['adequate'] == total else 'pending')
    return {**saved, 'test_case_id': case['id'], 'test_case_revision': case['revision'], 'origin': 'manual',
            'status': status, 'summary': {'linked_conditions': total, 'reviewed_conditions': len(saved['items']), **counts},
            'limitations': ['사람이 기록한 시험 설계 검토입니다. 실제 구현 충족이나 시험 수행 통과를 뜻하지 않습니다.']}


@router.get('/test-cases/{tc}/design-review', tags=['v2 시험 설계 적합성 검토'])
def get_review(p: str, tc: str, db: DB, revision: RevisionQuery = None):
    case = resource(db, p, tc, 'test_case')
    return review_for_case(db, p, {'id': tc, **historical(db, case, revision)})


@router.put('/test-cases/{tc}/design-review', tags=['v2 시험 설계 적합성 검토'])
def save_review(p: str, tc: str, body: SaveReview, db: DB):
    lock_project(db, p)
    request = body.model_dump(mode='json')
    scope = f'test-design-review:{tc}'
    # 프로젝트 잠금 상태에서 동일 client_review_id 재요청이면 기존 응답 재사용
    previous = replay(db, p, scope, body.client_review_id, request)
    if previous is not None:
        return previous
    case = resource(db, p, tc, 'test_case')
    ensure_editable(resource(db, p, case.data['test_specification_id'], 'test_specification'))
    if case.data['revision'] != body.test_case_revision:
        raise APIError(409, 'CASE_REVISION_CHANGED', '시험 절차가 변경되었습니다. 최신 절차로 다시 검토하세요',
                       details={'current_revision': case.data['revision']})
    current = review_for_case(db, p, {'id': tc, **case.data})
    if current['version'] != body.base_version:
        raise APIError(409, 'VERSION_CONFLICT', '다른 설계 검토가 먼저 저장되었습니다',
                       details={'current_version': current['version']})
    items = request['items']
    keys = [review_key(i) for i in items]
    if len(keys) != len(set(keys)) or not set(keys) <= link_set(case.data['requirement_links']):
        raise APIError(422, 'VALIDATION_ERROR', '이 시험 개정에 연결된 수락 조건만 중복 없이 검토할 수 있습니다')
    combined = {review_key(i): i for i in current['items']}
    combined.update({review_key(i): i for i in items})
    data = {'test_case_id': tc, 'test_case_revision': body.test_case_revision,
            'version': current['version'] + 1, 'items': list(combined.values()), 'updated_at': now()}
    if current['version']:
        replace(db, resource(db, p, current['id'], 'test_design_review'), data, current['version'])
    else:
        create(db, p, 'test_design_review', data, review_identity(p, tc, body.test_case_revision))
    result = review_for_case(db, p, {'id': tc, **case.data})
    remember(db, p, scope, body.client_review_id, request, result)
    return result


def retest_report(db, p, spec, revision=None, verification_run_id=None):
    """사양서 작성 이후 요구사항 개정이나 새 검증 판정 변화로 다시 시험해야 하는 시험을 계산한다.

    시험 데이터는 바꾸지 않는다. 조회할 때마다 현재 요구사항과 최신 검증 실행을 기준으로 다시 판단한다.
    """
    from api.v2.verification import _run_states
    fixed = historical(db, spec, revision)
    base_run = resource(db, p, fixed['verification_run_id'], 'verification_run')
    if verification_run_id:
        new_run = resource(db, p, verification_run_id, 'verification_run')
    else:
        done = [r for r in all_resources(db, p, 'verification_run')
                if r.data['status'] == 'completed' and r.created_at > base_run.created_at]
        new_run = max(done, key=lambda r: r.created_at) if done else None
    before = after = {}
    if new_run is not None:
        _, before = _run_states(base_run)
        _, after = _run_states(new_run)
    cond_key = {c['id']: (r['stable_key'], c['stable_key']) for r in fixed['requirements'] for c in r['conditions']}
    current_revision = {}
    items = []
    for case in current_cases(db, p, spec, revision):
        reasons = []
        for link in case['requirement_links']:
            r = link['requirement_id']
            if r not in current_revision:
                current_revision[r] = resource(db, p, r, 'requirement').data['revision']
            if current_revision[r] > link['requirement_revision']:
                reasons.append({'kind': 'requirement_revised', 'requirement_id': r,
                                'before_revision': link['requirement_revision'], 'after_revision': current_revision[r]})
            for c in link['condition_ids']:
                req_key, key = cond_key.get(c, (None, None))
                a, b = before.get(key), after.get(key)
                if new_run is not None and key and a and b and a['status'] != b['status']:
                    reasons.append({'kind': 'verdict_changed', 'requirement_key': req_key, 'condition_key': key,
                                    'before_status': a['status'], 'after_status': b['status']})
        if reasons:
            items.append({'test_case_id': case['id'], 'test_no': case['test_no'], 'title': case.get('title', ''),
                          'retest_required': True, 'reasons': reasons})
    return {'test_specification_id': spec.id, 'base_run_id': base_run.id,
            'compared_run_id': new_run.id if new_run else None,
            'retest_count': len(items), 'items': items}


@router.get('/test-specifications/{t}/retest')
def review_retest(p: str, t: str, db: DB, revision: RevisionQuery = None, verification_run_id: str | None = None):
    # 요구사항 개정·판정 변화에 연결된 시험을 retest_required로 표시한다. verification_run_id가 없으면 최신 완료 실행과 비교한다.
    return retest_report(db, p, resource(db, p, t, 'test_specification'), revision, verification_run_id)


def step_evidence(run_data, step):
    """단계에 연결된 조건만 골라 그 조건의 판정과 코드 근거 위치를 돌려준다 (요구사항 전체 근거가 아님)."""
    items = []
    for link in step['requirement_links']:
        result = run_data['results'].get(link['requirement_id']) or {}
        evidence = {e['id']: e for e in result.get('evidence', [])}
        verdicts = {v['condition_id']: v for v in result.get('condition_verdicts', [])}
        req = next((r for r in run_data['requirements'] if r['id'] == link['requirement_id']), {})
        texts = {c['id']: c['text'] for c in req.get('conditions', [])}
        for cid in link['condition_ids']:
            v = verdicts.get(cid, {})
            items.append({'requirement_id': link['requirement_id'], 'requirement_key': req.get('stable_key', ''),
                          'condition_id': cid, 'condition_text': texts.get(cid, ''),
                          'status': v.get('status'),
                          'evidence': [{k: evidence[i][k] for k in ('id', 'chunk_id', 'file_path', 'start_line', 'end_line')}
                                       for i in v.get('evidence_ids', []) if i in evidence]})
    return items


def step_evidence_text(items):
    lines = []
    for item in items:
        key = f"{item['requirement_key']} " if item.get('requirement_key') else ''
        lines.append(f"[{item['status'] or '판정 없음'}] {key}{item['condition_text']}")
        lines += [f"  {e['file_path']}:{e['start_line']}-{e['end_line']}" for e in item['evidence']] or ['  근거 코드 없음']
    return '\n'.join(lines)


@router.get('/test-cases/{tc}/evidence')
def case_evidence(p: str, tc: str, db: DB, revision: RevisionQuery = None):
    # 시험 단계마다 연결된 조건의 판정과 근거 코드 위치만 돌려준다. 같은 요구사항의 다른 조건 근거는 섞지 않는다.
    case = historical(db, resource(db, p, tc, 'test_case'), revision)
    spec = resource(db, p, case['test_specification_id'], 'test_specification')
    run = resource(db, p, spec.data['verification_run_id'], 'verification_run')
    return {'test_case_id': tc, 'verification_run_id': run.id, 'snapshot_id': run.data['snapshot_id'],
            'steps': [{'step_key': s['step_key'], 'order': s['order'], 'conditions': step_evidence(run.data, s)}
                      for s in case['steps']]}
