from typing import Annotated
from fastapi import Depends, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session
from api.config import Settings
from api.dependencies import session, settings
from api.v2.storage import create, public

DB = Annotated[Session, Depends(session)]
Config = Annotated[Settings, Depends(settings)]
Limit = Annotated[int, Query(ge=1, le=200)]
Offset = Annotated[int, Query(ge=0)]
RevisionQuery = Annotated[int | None, Query(ge=1)]


class Input(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)


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
