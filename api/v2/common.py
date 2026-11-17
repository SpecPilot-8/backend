# - 담당 파트: 백엔드 API
# - 작성자: 이성규
# - 페이지 개요: v2 API 공용 의존성, 스키마 기반 클래스, 워크스페이스·비동기 작업 관리 모듈
# - 주요 기능:
#   - DB 세션·Settings 의존성 주입, 엄격한 Input 베이스 모델 및 워크스페이스 경로 검증 제공
#   - 전역 작업·큐 잠금, 비동기 작업 생성·상태 전이 및 서버 재시작 시 중단 작업 복구

from pathlib import Path
from threading import Lock
from typing import Annotated

from fastapi import Depends, Query, Request
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

from api.config import Settings
from api.errors import APIError
from api.v2.storage import Resource, create, new_id, now, public, replace, resource

# 로컬 백그라운드 LLM 분석 작업과 작업 접수 큐의 경합을 막는 프로세스 내 락이다.
WORK_LOCK = Lock()
QUEUE_LOCK = Lock()


def session(request: Request):
    with request.app.state.database.session() as db:
        yield db


def settings(request: Request):
    return request.app.state.settings


DB = Annotated[Session, Depends(session)]
Config = Annotated[Settings, Depends(settings)]
Limit = Annotated[int, Query(ge=1, le=200)]
Offset = Annotated[int, Query(ge=0)]
RevisionQuery = Annotated[int | None, Query(ge=1)]


class Input(BaseModel):
    # 알 수 없는 필드나 NaN/Inf 수치 입력을 직렬화 경계에서 즉시 차단한다.
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)


def workspace_path(value: str, config: Settings) -> Path:
    # 심볼릭 링크를 실제 경로로 풀어 허용된 워크스페이스 루트 이탈 여부를 판별한다.
    path = Path(value).expanduser().resolve()
    if not path.is_relative_to(config.workspace_root):
        raise APIError(422, 'WORKSPACE_OUTSIDE_ALLOWED_ROOT', '워크스페이스가 설정한 허용 루트 밖에 있습니다')
    if not path.is_dir():
        raise APIError(422, 'WORKSPACE_NOT_FOUND', '워크스페이스 디렉터리를 찾을 수 없습니다')
    return path


def missing(code, message):
    return [{'code': code, 'message': message}]


def job_out(item):
    p, j = item.project_id, item.id
    data = public(item)
    data.pop('events', None)
    data.pop('request', None)
    return {**data, 'poll_url': f'/api/v2/projects/{p}/jobs/{j}', 'events_url': f'/api/v2/projects/{p}/jobs/{j}/events'}


def make_job(db, p, operation, request, missing=None, result_ref=None, progress=None, status=None):
    status = status or ('blocked' if missing else 'completed')
    item = create(db, p, 'job', {'operation': operation, 'request': request, 'status': status,
                               'progress': progress, 'result_ref': result_ref, 'missing_features': missing or [], 'events': []})
    item.data = {**item.data, 'events': [{'id': 1, 'event': status, 'data': job_out(item)}]}
    db.flush()
    return job_out(item)


def transition_job(db, job, status, **fields):
    # SSE Last-Event-ID 재연결 시 누락 이벤트를 재생할 수 있도록 증가 ID와 스냅샷을 누적한다.
    data = {**job.data, **fields, 'status': status}
    event = {**job_out(job), **fields, 'status': status}
    data['events'] = [*job.data['events'], {'id': len(job.data['events']) + 1, 'event': status, 'data': event}]
    replace(db, job, data)


def recover_interrupted(database):
    # 서버 재시작으로 백그라운드 스레드가 끊긴 작업은 자동 재호출 대신 실패로 정리해 중복 과금을 막는다.
    with database.session() as db:
        for job in db.scalars(select(Resource).where(Resource.kind == 'job')):
            if job.data.get('operation') in ('extract_requirements', 'verify', 'generate_test_specification') and job.data.get('status') in ('queued', 'running'):
                data = {**job.data, 'status': 'failed', 'error': {'code': 'SERVER_RESTARTED', 'message': '서버가 재시작되어 분석을 중단했습니다. 자동 재호출하지 않았습니다'}}
                event = {**job_out(job), 'status': 'failed', 'error': data['error']}
                data['events'] = [*job.data['events'], {'id': len(job.data['events']) + 1, 'event': 'failed', 'data': event}]
                replace(db, job, data)
                if job.data.get('operation') == 'verify':
                    run_id = job.data.get('request', {}).get('verification_run_id')
                    if run_id:
                        run = resource(db, job.project_id, run_id, 'verification_run')
                        replace(db, run, {**run.data, 'status': 'failed', 'error': data['error'], 'missing_features': []})
                if job.data.get('operation') == 'generate_test_specification':
                    spec_id = job.data.get('request', {}).get('test_specification_id')
                    if spec_id:
                        spec = resource(db, job.project_id, spec_id, 'test_specification')
                        spec.data = {**spec.data, 'status': 'failed', 'generation_status': 'failed', 'error': data['error']}


__all__ = [
    'Config', 'DB', 'Input', 'Limit', 'Offset', 'QUEUE_LOCK', 'RevisionQuery',
    'WORK_LOCK', 'job_out', 'make_job', 'missing', 'new_id', 'now',
    'recover_interrupted', 'session', 'settings', 'transition_job', 'workspace_path',
]
