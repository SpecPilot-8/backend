"""시험 적합성에 대한 사람의 검토를 절차 개정별로 보존한다. 실제 수행 결과와 별개다."""
from typing import Literal
from uuid import NAMESPACE_URL, UUID, uuid5

from fastapi import APIRouter
from pydantic import Field

from api.errors import APIError
from api.models import now
from api.v2.common import DB, Input, RevisionQuery
from api.v2.storage import Resource, create, historical, public, remember, replace, replay, resource
from api.v2.testing import ensure_editable, link_set, lock_project

router = APIRouter(prefix='/api/v2/projects/{p}', tags=['v2 시험 설계 적합성 검토'])


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


def key(item):
    return item['requirement_id'], item['requirement_revision'], item['condition_id']


def identity(p, tc, revision):
    return str(uuid5(NAMESPACE_URL, f'specpilot:test-design-review:{p}:{tc}:{revision}'))


def review_for_case(db, p, case):
    item = db.get(Resource, identity(p, case['id'], case['revision']))
    if item is not None and (item.project_id != p or item.kind != 'test_design_review'):
        raise APIError(409, 'RESOURCE_CONFLICT', '설계 검토 저장소를 확인해야 합니다')
    saved = public(item) if item else {'version': 0, 'items': [], 'updated_at': None}
    total = len(link_set(case['requirement_links']))
    counts = {s: sum(i['decision'] == s for i in saved['items']) for s in ('adequate', 'needs_changes', 'uncertain')}
    status = ('needs_changes' if counts['needs_changes'] else 'uncertain' if counts['uncertain']
              else 'adequate' if total and counts['adequate'] == total else 'pending')
    return {**saved, 'test_case_id': case['id'], 'test_case_revision': case['revision'], 'origin': 'manual',
            'status': status, 'summary': {'linked_conditions': total, 'reviewed_conditions': len(saved['items']), **counts},
            'limitations': ['사람이 기록한 시험 설계 검토입니다. 실제 구현 충족이나 시험 수행 통과를 뜻하지 않습니다.']}


@router.get('/test-cases/{tc}/design-review')
def get_review(p: str, tc: str, db: DB, revision: RevisionQuery = None):
    case = resource(db, p, tc, 'test_case')
    return review_for_case(db, p, {'id': tc, **historical(db, case, revision)})


@router.put('/test-cases/{tc}/design-review')
def save_review(p: str, tc: str, body: SaveReview, db: DB):
    lock_project(db, p)
    request = body.model_dump(mode='json')
    scope = f'test-design-review:{tc}'
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
    keys = [key(i) for i in items]
    if len(keys) != len(set(keys)) or not set(keys) <= link_set(case.data['requirement_links']):
        raise APIError(422, 'VALIDATION_ERROR', '이 시험 개정에 연결된 수락 조건만 중복 없이 검토할 수 있습니다')
    combined = {key(i): i for i in current['items']}
    combined.update({key(i): i for i in items})
    data = {'test_case_id': tc, 'test_case_revision': body.test_case_revision,
            'version': current['version'] + 1, 'items': list(combined.values()), 'updated_at': now()}
    if current['version']:
        replace(db, resource(db, p, current['id'], 'test_design_review'), data, current['version'])
    else:
        create(db, p, 'test_design_review', data, identity(p, tc, body.test_case_revision))
    result = review_for_case(db, p, {'id': tc, **case.data})
    remember(db, p, scope, body.client_review_id, request, result)
    return result
