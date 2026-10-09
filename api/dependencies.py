from fastapi import Request
from sqlalchemy.orm import Session

from api.errors import APIError


def session(request: Request):
    with request.app.state.database.session() as db:
        yield db


def settings(request: Request):
    return request.app.state.settings


def get_entity(db: Session, model, identity: str, project_id: str | None = None):
    value = db.get(model, identity)
    if value is None or (project_id is not None and value.project_id != project_id):
        raise APIError(404, "RESOURCE_NOT_FOUND", "요청한 프로젝트의 데이터를 찾을 수 없습니다")
    return value
