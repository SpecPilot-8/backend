from copy import deepcopy
from hashlib import sha256
import json

from sqlalchemy import ForeignKey, Integer, JSON, String, UniqueConstraint, select, update
from sqlalchemy.orm import Mapped, mapped_column

from api.database import Base
from api.errors import APIError
from api.models import new_id, now


class ProjectRecord(Base):
    __tablename__ = 'api_v2_project'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(200))
    tech_stack: Mapped[str] = mapped_column(String(32), default='auto', server_default='auto')
    workspace_path: Mapped[str | None] = mapped_column(String(4096), nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    config_version: Mapped[int] = mapped_column(Integer, default=0)
    profile_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[str] = mapped_column(String(40), default=now)


class Resource(Base):
    """Domain services validate payloads. Every mutation retains an immutable revision."""
    __tablename__ = 'api_v2_resource'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey('api_v2_project.id'), index=True)
    kind: Mapped[str] = mapped_column(String(40), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    data: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[str] = mapped_column(String(40), default=now)


class Revision(Base):
    __tablename__ = 'api_v2_revision'
    __table_args__ = (UniqueConstraint('resource_id', 'version'),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    resource_id: Mapped[str] = mapped_column(ForeignKey('api_v2_resource.id'), index=True)
    version: Mapped[int] = mapped_column(Integer)
    data: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[str] = mapped_column(String(40), default=now)


class SavedRequest(Base):
    __tablename__ = 'api_v2_saved_request'
    __table_args__ = (UniqueConstraint('project_id', 'scope', 'client_id'),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey('api_v2_project.id'))
    scope: Mapped[str] = mapped_column(String(200))
    client_id: Mapped[str] = mapped_column(String(36))
    request_hash: Mapped[str] = mapped_column(String(64))
    response: Mapped[dict] = mapped_column(JSON)


def project(db, p):
    item = db.get(ProjectRecord, p)
    if item is None:
        raise APIError(404, 'RESOURCE_NOT_FOUND', '프로젝트를 찾을 수 없습니다')
    return item


def resource(db, p, identity, kind):
    project(db, p)
    item = db.get(Resource, identity)
    if item is None or item.project_id != p or item.kind != kind:
        raise APIError(404, 'RESOURCE_NOT_FOUND', '요청한 프로젝트의 데이터를 찾을 수 없습니다')
    return item


def create(db, p, kind, data, identity=None):
    item = Resource(id=identity or new_id(), project_id=p, kind=kind, version=1,
                    data=deepcopy(data), created_at=now())
    db.add(item)
    db.flush()
    db.add(Revision(resource_id=item.id, version=1, data=deepcopy(data)))
    db.flush()
    return item


def replace(db, item, data, base_version=None):
    base = item.version if base_version is None else base_version
    result = db.execute(update(Resource).where(Resource.id == item.id, Resource.version == base)
                        .values(version=base + 1, data=deepcopy(data)),
                        execution_options={'synchronize_session': False})
    if result.rowcount != 1:
        db.refresh(item)
        raise APIError(409, 'VERSION_CONFLICT', '다른 수정이 먼저 저장되었습니다',
                       details={'current_version': item.version, 'current_revision': item.version})
    db.add(Revision(resource_id=item.id, version=base + 1, data=deepcopy(data)))
    db.flush()
    db.refresh(item)
    return item


def historical(db, item, version=None):
    if version is None or version == item.version:
        return deepcopy(item.data)
    old = db.scalar(select(Revision).where(Revision.resource_id == item.id, Revision.version == version))
    if old is None:
        raise APIError(404, 'RESOURCE_NOT_FOUND', '요청한 개정을 찾을 수 없습니다')
    return deepcopy(old.data)


def public(item, version=None, db=None):
    data = historical(db, item, version) if db is not None else deepcopy(item.data)
    return {'id': item.id, 'project_id': item.project_id, 'created_at': item.created_at, **data}


def all_resources(db, p, kind):
    project(db, p)
    return list(db.scalars(select(Resource).where(Resource.project_id == p, Resource.kind == kind)
                          .order_by(Resource.created_at, Resource.id)))


def page(items, limit, offset):
    return {'items': items[offset:offset + limit], 'total': len(items), 'limit': limit, 'offset': offset}


def request_hash(body):
    return sha256(json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def replay(db, p, scope, client_id, body):
    row = db.scalar(select(SavedRequest).where(SavedRequest.project_id == p, SavedRequest.scope == scope,
                                             SavedRequest.client_id == str(client_id)))
    if row:
        if row.request_hash != request_hash(body):
            raise APIError(409, 'IDEMPOTENCY_CONFLICT', '같은 저장 식별자로 다른 내용을 제출했습니다')
        return deepcopy(row.response)


def remember(db, p, scope, client_id, body, response):
    db.add(SavedRequest(project_id=p, scope=scope, client_id=str(client_id),
                        request_hash=request_hash(body), response=deepcopy(response)))
    db.flush()
