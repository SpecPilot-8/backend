import json
from typing import Annotated
from fastapi import APIRouter, Header
from fastapi.responses import StreamingResponse
from pydantic import Field, SecretStr
from sqlalchemy import select, update
from api.errors import APIError
from api.models import new_id, now
from api.v2.common import Config, DB, Input, Limit, Offset, job_out, missing
from api.v2.storage import ProjectRecord, all_resources, page, project, resource
from services.workspace import workspace_path

router = APIRouter(prefix='/api/v2', tags=['v2 프로젝트·모델·작업'])


class ProjectCreate(Input):
    name: str = Field(min_length=1, max_length=200)
    workspace_path: str | None = Field(default=None, min_length=1, max_length=4096)


class ProjectEdit(Input):
    base_version: int = Field(ge=1)
    name: str = Field(min_length=1, max_length=200)


class CredentialCreate(Input):
    profile_id: str = Field(min_length=1, max_length=100)
    api_key: SecretStr


class ModelSetting(Input):
    base_version: int = Field(ge=0)
    profile_id: str = Field(min_length=1, max_length=100)
    credential_ref: str | None = None


class ConnectionCheck(Input):
    config_version: int = Field(ge=0)


def project_out(value):
    return {'id': value.id, 'name': value.name, 'version': value.version,
            'created_at': value.created_at, 'selected_model_profile_id': value.profile_id}


@router.get('/capabilities')
def capabilities(config: Config):
    return {'api_version': '2', 'formats': [{'format': 'pdf', 'status': 'partial', 'limitations': ['ocr', 'layout_semantics']}], 'stacks': [{'name': 'Java/JavaScript/HTML', 'status': 'partial', 'limitations': ['call_flow', 'screen_reachability']}, {'name': 'TypeScript', 'status': 'partial', 'limitations': ['text_only']}],
            'limits': {'upload_bytes': config.max_upload_bytes, 'source_bytes': config.max_source_bytes,
                       'source_files': config.max_source_files, 'source_file_bytes': config.max_source_file_bytes},
            'features': [{'code': 'projects', 'status': 'ready'}, {'code': 'jobs', 'status': 'ready'}, {'code': 'documents', 'status': 'partial'}, {'code': 'requirement_management', 'status': 'ready'}, {'code': 'requirement_extraction', 'status': 'not_implemented'}, {'code': 'code_snapshots', 'status': 'ready'}, {'code': 'verification_queries', 'status': 'ready'}, {'code': 'implementation_judgment', 'status': 'not_implemented'}, {'code': 'call_flow', 'status': 'not_implemented'}],
            'missing_features': missing('model_adapters', '팀원 모델 어댑터가 연결되지 않았습니다')}


@router.post('/projects', status_code=201)
def create_project(body: ProjectCreate, db: DB, config: Config):
    value = ProjectRecord(id=new_id(), name=body.name, version=1, config_version=0, created_at=now(),
                          workspace_path=str(workspace_path(body.workspace_path, config)) if body.workspace_path else None)
    db.add(value)
    db.flush()
    return project_out(value)


@router.get('/projects')
def projects(db: DB, limit: Limit = 50, offset: Offset = 0):
    return page([project_out(x) for x in db.scalars(select(ProjectRecord).order_by(ProjectRecord.created_at, ProjectRecord.id))], limit, offset)


@router.get('/projects/{p}')
def get_project(p: str, db: DB):
    return project_out(project(db, p))


@router.patch('/projects/{p}')
def edit_project(p: str, body: ProjectEdit, db: DB):
    value = project(db, p)
    result = db.execute(update(ProjectRecord).where(ProjectRecord.id == p, ProjectRecord.version == body.base_version)
                        .values(name=body.name, version=body.base_version + 1), execution_options={'synchronize_session': False})
    db.refresh(value)
    if result.rowcount != 1:
        raise APIError(409, 'VERSION_CONFLICT', '프로젝트 변경 버전이 충돌했습니다', details={'current_version': value.version})
    return project_out(value)


@router.get('/model-profiles')
def profiles():
    return {'items': [{'id': 'primary', 'provider': 'unconfigured', 'model': 'unconfigured',
            'display_name': '기본 모델 (연결 대기)', 'adapter_status': 'not_connected', 'enabled': False,
            'capabilities': ['requirement_extraction', 'implementation_judgment', 'test_generation'], 'credential_required': True}]}


@router.get('/projects/{p}/llm')
def model_settings(p: str, db: DB):
    value = project(db, p)
    return {'profile_id': value.profile_id, 'config_version': value.config_version,
            'credential_configured': False, 'connection_status': 'not_connected'}


@router.post('/projects/{p}/credentials', status_code=201)
def credentials(p: str, body: CredentialCreate, db: DB):
    project(db, p)
    raise APIError(501, 'FEATURE_NOT_IMPLEMENTED', '키 저장은 비밀 저장소 연동 후 활성화됩니다', missing('credential_vault', '암호화 비밀 저장소가 필요합니다'))


@router.put('/projects/{p}/llm')
def set_model(p: str, body: ModelSetting, db: DB):
    value = project(db, p)
    if value.config_version != body.base_version:
        raise APIError(409, 'CONFIG_CHANGED', '모델 설정 버전이 변경되었습니다', details={'current_version': value.config_version})
    if body.profile_id != 'primary':
        raise APIError(422, 'VALIDATION_ERROR', '지원 목록에 없는 모델입니다')
    raise APIError(501, 'FEATURE_NOT_IMPLEMENTED', '실제 모델 어댑터 연결이 필요합니다', missing('model_adapter', '선정된 단일 모델 어댑터가 필요합니다'))


@router.post('/projects/{p}/llm/connection-check')
def connection(p: str, body: ConnectionCheck, db: DB):
    if project(db, p).config_version != body.config_version:
        raise APIError(409, 'CONFIG_CHANGED', '모델 설정 버전이 변경되었습니다')
    raise APIError(501, 'FEATURE_NOT_IMPLEMENTED', '실제 연결 검사는 아직 지원하지 않습니다', missing('model_connection_check', '실제 제공자 호출 어댑터가 필요합니다'))


@router.get('/projects/{p}/jobs')
def jobs(p: str, db: DB, status: str | None = None, limit: Limit = 50, offset: Offset = 0):
    if status is not None and status not in {'queued', 'running', 'completed', 'failed', 'blocked', 'cancelled'}:
        raise APIError(422, 'VALIDATION_ERROR', '알 수 없는 작업 상태입니다')
    return page([job_out(x) for x in all_resources(db, p, 'job') if status is None or x.data['status'] == status], limit, offset)


@router.get('/projects/{p}/jobs/{j}')
def job(p: str, j: str, db: DB):
    return job_out(resource(db, p, j, 'job'))


@router.get('/projects/{p}/jobs/{j}/events')
def events(p: str, j: str, db: DB, last_event_id: Annotated[str | None, Header()] = None):
    item = resource(db, p, j, 'job')
    try:
        cursor = int(last_event_id) if last_event_id is not None else 0
        if cursor < 0:
            raise ValueError
    except ValueError:
        raise APIError(422, 'VALIDATION_ERROR', 'Last-Event-ID는 0 이상의 이벤트 번호입니다') from None
    saved = [x for x in item.data['events'] if x['id'] > cursor]
    lines = [f"id: {x['id']}\nevent: {x['event']}\ndata: {json.dumps(x['data'], ensure_ascii=False)}\n\n" for x in saved]
    return StreamingResponse(iter(lines), media_type='text/event-stream', headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})
