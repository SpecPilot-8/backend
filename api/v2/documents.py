from copy import deepcopy
from hashlib import sha256
from pathlib import Path
from typing import Annotated, Literal

import pymupdf
from fastapi import APIRouter, File, UploadFile, Query
from fastapi.responses import FileResponse
from pydantic import Field, model_validator
from sqlalchemy import select

from api.errors import APIError
from api.models import new_id
from api.v2.common import Config, DB, Input, Limit, Offset, RevisionQuery, make_job, missing
from api.v2.document_contracts import validate_extraction
from api.v2.storage import Revision, all_resources, create, page, project, public, replace, resource

router = APIRouter(prefix='/api/v2/projects/{p}', tags=['v2 문서·요구사항'])


class DocumentVersion(Input):
    document_revision: int = Field(ge=1)


class ExtractRequest(DocumentVersion):
    model_config_version: int = Field(ge=0)
    accept_warnings: bool = False


class SourceRef(Input):
    block_id: str = Field(min_length=1)
    quote: str = Field(min_length=1)


class Constraint(Input):
    field: str = Field(min_length=1, max_length=200)
    operator: Literal['le', 'lt', 'ge', 'gt', 'eq']
    value: float
    unit: str = Field(min_length=1, max_length=100)
    unit_definition: str | None = None


class ConditionWrite(Input):
    id: str | None = None
    stable_key: str = Field(min_length=1, max_length=200)
    text: str = Field(min_length=1, max_length=10000)
    category: Literal['display', 'validation', 'navigation', 'business_logic', 'data', 'error_message']
    target: Literal['frontend', 'backend', 'both', 'unknown'] = Field(
        description='원문에 명시된 구현 위치. 언급이 없으면 unknown이며 근거 후보 필터로 사용하지 않습니다')
    verifiable: Literal['code', 'not_statically_verifiable']
    source_refs: list[SourceRef] = Field(default_factory=list)
    constraint: Constraint | None = None
    ambiguity: list[str] = Field(default_factory=list)
    related_condition_ids: list[str] = Field(default_factory=list)


class RequirementCreate(Input):
    document_id: str
    document_revision: int = Field(ge=1)
    subtopic_id: str | None = None
    screen_id: str | None = None
    title: str = Field(min_length=1, max_length=300)
    original_text: str = Field(default='', max_length=20000)
    condition: str = Field(default='', max_length=10000)
    action: str = Field(default='', max_length=10000)
    expected: str = Field(default='', max_length=10000)
    req_type: Literal['functional', 'non_functional'] = 'functional'
    origin: Literal['manual'] = 'manual'
    author_reason: str = Field(min_length=1, max_length=2000)
    source_refs: list[SourceRef] = Field(default_factory=list)
    conditions: list[ConditionWrite] = Field(min_length=1, max_length=200)


class RequirementEdit(Input):
    base_revision: int = Field(ge=1)
    subtopic_id: str | None = None
    title: str | None = Field(default=None, min_length=1, max_length=300)
    condition: str | None = Field(default=None, max_length=10000)
    action: str | None = Field(default=None, max_length=10000)
    expected: str | None = Field(default=None, max_length=10000)
    conditions: list[ConditionWrite] | None = Field(default=None, min_length=1, max_length=200)

    @model_validator(mode='after')
    def changes_present(self):
        if not (self.model_fields_set - {'base_revision'}) or any(getattr(self, k) is None for k in self.model_fields_set - {'base_revision'}):
            raise ValueError('실제 변경 필드가 필요합니다')
        return self


class ReviewRequest(Input):
    base_revision: int = Field(ge=1)
    status: Literal['approved']


def doc_out(item):
    data = public(item)
    for k in ['storage_path', 'blocks', 'unread_locations', 'topic_ids', 'requirement_ids']:
        data.pop(k, None)
    return data


def requirement_out(db, item, revision=None):
    data = public(item, revision, db)
    doc = resource(db, item.project_id, data['document_id'], 'document')
    blocks = {b['id']: b for b in doc.data['blocks']}
    for owner in [data] + data['conditions']:
        owner['source_refs'] = [{**r, 'location': blocks[r['block_id']]['location']} for r in owner['source_refs']]
    return data


def check_document_version(item, revision):
    if item.data['revision'] != revision:
        raise APIError(409, 'VERSION_CONFLICT', '문서 버전이 변경되었습니다', details={'current_revision': item.data['revision']})


def refs_valid(refs, blocks):
    lookup = {b['id']: b for b in blocks}
    for ref in refs:
        if ref['block_id'] not in lookup or ref['quote'] not in lookup[ref['block_id']]['text']:
            raise APIError(422, 'VALIDATION_ERROR', '출처가 해당 문서 원문과 일치하지 않습니다')


def validate_scope(db, p, data):
    d = resource(db, p, data['document_id'], 'document')
    check_document_version(d, data['document_revision'])
    sub = resource(db, p, data['subtopic_id'], 'subtopic')
    if (sub.data['document_id'], sub.data['document_revision']) != (d.id, data['document_revision']):
        raise APIError(422, 'VALIDATION_ERROR', '소주제의 문서 버전이 다릅니다')
    if data.get('screen_id') and data['screen_id'] not in sub.data['screen_ids']:
        raise APIError(422, 'VALIDATION_ERROR', '화면과 소주제의 소속이 다릅니다')
    refs_valid(data.get('source_refs', []), d.data['blocks'])
    return d, sub


def condition_payloads(db, p, data, old=None):
    blocks, _ = validate_scope(db, p, data)
    old_conditions = {x['id']: x for x in (old or {}).get('conditions', [])}
    keys = [x['stable_key'] for x in data['conditions']]
    if len(keys) != len(set(keys)):
        raise APIError(422, 'VALIDATION_ERROR', '조건 키가 중복됩니다')
    result = []
    for c in data['conditions']:
        c = deepcopy(c)
        if c.get('id') is not None and c['id'] not in old_conditions:
            raise APIError(422, 'VALIDATION_ERROR', '기존 조건 ID를 확인하세요')
        c['id'] = new_id()  # Each requirement revision owns new condition IDs.
        refs_valid(c['source_refs'], blocks.data['blocks'])
        result.append(c)
    id_mapping = {x['id']: y['id'] for x, y in zip(data['conditions'], result) if x.get('id')}
    related_requirements = [r for r in all_resources(db, p, 'requirement')
                            if r.data['document_id'] == data['document_id'] and r.data['document_revision'] == data['document_revision']
                            and (not old or r.id != old.get('id'))]
    allowed = set(id_mapping) | {c['id'] for r in related_requirements for c in r.data['conditions']}
    # Condition IDs belong to immutable revisions, including other requirements' history.
    for revision in db.scalars(select(Revision).where(Revision.resource_id.in_([r.id for r in related_requirements]))):
        if (revision.data.get('document_id'), revision.data.get('document_revision')) == (data['document_id'], data['document_revision']):
            allowed.update(c['id'] for c in revision.data['conditions'])
    for c in result:
        if not set(c['related_condition_ids']) <= allowed:
            raise APIError(422, 'VALIDATION_ERROR', '관련 조건 ID가 해당 문서에 없습니다')
        c['related_condition_ids'] = [id_mapping.get(x, x) for x in c['related_condition_ids']]
        if c['id'] in c['related_condition_ids']:
            raise APIError(422, 'VALIDATION_ERROR', '자기 조건을 참조할 수 없습니다')
    return result


def unclassified(db, p, d):
    subs = [x for x in all_resources(db, p, 'subtopic') if x.data['document_id'] == d.id and x.data['document_revision'] == d.data['revision'] and x.data['key'] == '_manual_unclassified']
    if subs:
        return subs[0].id
    common = {'document_id': d.id, 'document_revision': d.data['revision'], 'title_origin': 'unclassified',
              'source_refs': [], 'review_reasons': ['사용자가 수동 추가한 요구사항의 기능 소속 확인 필요']}
    t = create(db, p, 'topic', {**common, 'key': '_manual_unclassified', 'title': '미분류'})
    s = create(db, p, 'subtopic', {**common, 'topic_id': t.id, 'key': '_manual_unclassified', 'title': '기능 확인 필요', 'screen_ids': []})
    return s.id


@router.post('/documents', status_code=201)
async def upload_document(p: str, db: DB, config: Config, file: Annotated[UploadFile, File()]):
    project(db, p)
    filename = Path((file.filename or '').replace('\\', '/')).name
    if Path(filename).suffix.lower() != '.pdf':
        raise APIError(415, 'UNSUPPORTED_FORMAT', '현재 PDF 업로드만 지원합니다')
    content = await file.read(config.max_upload_bytes + 1)
    if len(content) > config.max_upload_bytes:
        raise APIError(413, 'FILE_TOO_LARGE', '문서 업로드 한도를 초과했습니다')
    try:
        with pymupdf.open(stream=content, filetype='pdf') as pdf:
            if pdf.needs_pass or not content.startswith(b'%PDF-') or len(pdf) > 500 or not len(pdf):
                raise ValueError
    except Exception:
        raise APIError(415, 'UNSUPPORTED_FORMAT', '유효한 비암호화 PDF(1~500쪽)가 필요합니다') from None
    identity = new_id()
    target = config.data_dir / 'uploads' / f'{identity}.pdf'
    target.write_bytes(content)
    item = create(db, p, 'document', {'filename': filename, 'format': 'pdf', 'content_hash': sha256(content).hexdigest(),
                  'revision': 1, 'parse_status': 'uploaded', 'warnings': [], 'blocks': [], 'unread_locations': [],
                  'storage_path': str(target), 'extraction_status': 'not_run'}, identity)
    return doc_out(item)


@router.get('/documents')
def documents(p: str, db: DB, limit: Limit = 50, offset: Offset = 0):
    return page([doc_out(x) for x in all_resources(db, p, 'document')], limit, offset)


@router.get('/documents/{d}')
def document(p: str, d: str, db: DB):
    return doc_out(resource(db, p, d, 'document'))


@router.get('/documents/{d}/file')
def document_file(p: str, d: str, db: DB):
    # 화면에서 판정 옆에 원본 페이지를 띄우는 용도. 블록 location(page, bbox)과 같은 좌표계(PDF pt)다.
    item = resource(db, p, d, 'document')
    path = Path(item.data.get('storage_path') or '')
    if not path.is_file():
        raise APIError(404, 'RESOURCE_NOT_FOUND', '원본 파일이 없습니다')
    return FileResponse(path, media_type='application/pdf', filename=item.data['filename'], content_disposition_type='inline')


@router.post('/documents/{d}/parse', status_code=202)
def parse(p: str, d: str, body: DocumentVersion, db: DB):
    item = resource(db, p, d, 'document')
    check_document_version(item, body.document_revision)
    # Replaying parse never replaces source block IDs referenced by saved requirements.
    if item.data['parse_status'] in ('parsed', 'partial'):
        return make_job(db, p, 'parse_document', body.model_dump(), result_ref={'kind': 'document', 'id': d})
    blocks, unread = [], []
    try:
        with pymupdf.open(item.data['storage_path']) as pdf:
            for index, pg in enumerate(pdf, 1):
                found = False
                for raw in pg.get_text('blocks', sort=True):
                    if raw[6] != 0 or not raw[4].strip():
                        continue
                    found = True
                    blocks.append({'id': new_id(), 'document_id': d, 'document_revision': body.document_revision,
                                   'location': {'kind': 'page', 'index': index, 'bbox': [raw[0], raw[1], raw[2]-raw[0], raw[3]-raw[1]]},
                                   'text': raw[4], 'extraction_method': 'text', 'structure': {'section': 'paragraph', 'label': None,
                                   'parent_block_id': None, 'reading_order': len(blocks) + 1}, 'warnings': []})
                    if len(blocks) > 10000 or sum(len(b['text']) for b in blocks) > 2000000:
                        raise ValueError
                if not found:
                    unread.append({'kind': 'page', 'index': index})
    except Exception:
        replace(db, item, {**item.data, 'parse_status': 'failed', 'warnings': [{'code': 'PARSE_FAILED', 'message': 'PDF 읽기 실패 또는 처리 한도 초과', 'block_ids': []}]})
        j = make_job(db, p, 'parse_document', body.model_dump(), missing('document_parser', 'PDF 읽기 실패 또는 처리 한도 초과'))
        row = resource(db, p, j['id'], 'job')
        row.data = {**row.data, 'status': 'failed', 'events': [{'id': 1, 'event': 'failed', 'data': {**j, 'status': 'failed'}}]}
        db.flush()
        return {**j, 'status': 'failed'}
    warnings = [{'code': 'LAYOUT_REVIEW_REQUIRED', 'message': '기본 텍스트 추출입니다. 화면 헤더·Description/Action·표 구조 인식 어댑터가 필요합니다', 'block_ids': []}]
    if unread:
        warnings.append({'code': 'OCR_REQUIRED', 'message': '텍스트가 없는 페이지에 OCR이 필요합니다', 'block_ids': []})
    status = 'partial' if blocks else 'failed'
    replace(db, item, {**item.data, 'parse_status': status, 'blocks': blocks, 'warnings': warnings, 'unread_locations': unread})
    if not blocks:
        j = make_job(db, p, 'parse_document', body.model_dump(), missing('ocr', '읽을 수 있는 원문이 없습니다'))
        row = resource(db, p, j['id'], 'job')
        row.data = {**row.data, 'status': 'failed', 'events': [{'id': 1, 'event': 'failed', 'data': {**j, 'status': 'failed'}}]}
        db.flush()
        return {**j, 'status': 'failed'}
    return make_job(db, p, 'parse_document', body.model_dump(), result_ref={'kind': 'document', 'id': d},
                    progress={'stage': 'text_blocks', 'completed': len(blocks), 'total': len(blocks)})


@router.get('/documents/{d}/blocks')
def blocks(p: str, d: str, db: DB, location_kind: Literal['page', 'sheet', 'slide', 'section'] | None = None,
           index: Annotated[int | None, Query(ge=1)] = None, limit: Limit = 50, offset: Offset = 0):
    item = resource(db, p, d, 'document')
    return page([b for b in item.data['blocks'] if (location_kind is None or b['location']['kind'] == location_kind)
                 and (index is None or b['location']['index'] == index)], limit, offset)


@router.get('/documents/{d}/outline')
def outline(p: str, d: str, db: DB):
    item = resource(db, p, d, 'document')
    return {'document_revision': item.data['revision'], 'items': [{'title': b['text'], 'location': b['location'], 'block_ids': [b['id']]}
            for b in item.data['blocks'] if b['structure']['section'] == 'header'], 'warnings': item.data['warnings']}


@router.post('/documents/{d}/requirements/extract', status_code=202)
def extract(p: str, d: str, body: ExtractRequest, db: DB):
    item = resource(db, p, d, 'document')
    check_document_version(item, body.document_revision)
    if item.data['parse_status'] not in ('parsed', 'partial'):
        raise APIError(409, 'DOCUMENT_NOT_PARSED', '읽을 수 있는 원문 블록을 먼저 준비하세요')
    if item.data['parse_status'] == 'partial' and not body.accept_warnings:
        raise APIError(409, 'DOCUMENT_WARNINGS_NOT_ACCEPTED', '부분 파싱 경고를 확인한 뒤 요청하세요')
    config = project(db, p)
    if config.config_version != body.model_config_version:
        raise APIError(409, 'CONFIG_CHANGED', '모델 설정 버전이 변경되었습니다')
    return make_job(db, p, 'extract_requirements', body.model_dump(), missing('requirement_extraction', '팀원 1의 의미 추출 모델 어댑터가 필요합니다'))


@router.get('/documents/{d}/requirement-groups')
def groups(p: str, d: str, db: DB, document_revision: RevisionQuery = None):
    item = resource(db, p, d, 'document')
    revision = document_revision or item.data['revision']
    check_document_version(item, revision)
    topics = [public(t) for t in all_resources(db, p, 'topic') if t.data['document_id'] == d and t.data['document_revision'] == revision]
    subs = all_resources(db, p, 'subtopic')
    for t in topics:
        t['subtopics'] = [public(s) for s in subs if s.data['topic_id'] == t['id']]
    return {'document_revision': revision, 'topics': topics}


@router.post('/requirements', status_code=201)
def add_requirement(p: str, body: RequirementCreate, db: DB):
    d = resource(db, p, body.document_id, 'document')
    check_document_version(d, body.document_revision)
    data = body.model_dump()
    data['subtopic_id'] = body.subtopic_id or unclassified(db, p, d)
    data['conditions'] = condition_payloads(db, p, data)
    data.update(stable_key=new_id(), revision=1, review_status='needs_review', review_reasons=['수동 추가'], source_markers=[])
    return requirement_out(db, create(db, p, 'requirement', data))


@router.get('/requirements')
def requirements(p: str, db: DB, document_id: str | None = None, document_revision: RevisionQuery = None,
                 topic_id: str | None = None, subtopic_id: str | None = None, screen_id: str | None = None,
                 review_status: Literal['pending', 'needs_review', 'approved'] | None = None,
                 q: str | None = None, limit: Limit = 50, offset: Offset = 0):
    filters = []
    if document_id:
        d = resource(db, p, document_id, 'document')
        if document_revision:
            check_document_version(d, document_revision)
        filters.append((d.id, d.data['revision']))
    for identity, kind in [(topic_id, 'topic'), (subtopic_id, 'subtopic'), (screen_id, 'screen')]:
        if identity:
            v = resource(db, p, identity, kind)
            filters.append((v.data['document_id'], v.data['document_revision']))
    if len(set(filters)) > 1 or (document_revision is not None and any(r != document_revision for _, r in filters)):
        raise APIError(422, 'VALIDATION_ERROR', '필터의 문서 버전이 충돌합니다')
    if topic_id and subtopic_id and resource(db, p, subtopic_id, 'subtopic').data['topic_id'] != topic_id:
        raise APIError(422, 'VALIDATION_ERROR', '주제와 소주제 필터가 충돌합니다')
    if subtopic_id and screen_id and screen_id not in resource(db, p, subtopic_id, 'subtopic').data['screen_ids']:
        raise APIError(422, 'VALIDATION_ERROR', '소주제와 화면 필터가 충돌합니다')
    if topic_id and screen_id and not any(s.data['topic_id'] == topic_id and screen_id in s.data['screen_ids'] for s in all_resources(db, p, 'subtopic')):
        raise APIError(422, 'VALIDATION_ERROR', '주제와 화면 필터가 충돌합니다')
    items = []
    for r in all_resources(db, p, 'requirement'):
        data = r.data
        if filters and (data['document_id'], data['document_revision']) != filters[0]:
            continue
        if document_revision and data['document_revision'] != document_revision:
            continue
        if subtopic_id and data['subtopic_id'] != subtopic_id:
            continue
        if topic_id and resource(db, p, data['subtopic_id'], 'subtopic').data['topic_id'] != topic_id:
            continue
        if screen_id and data['screen_id'] != screen_id:
            continue
        if review_status and data['review_status'] != review_status:
            continue
        if q and q.casefold() not in (data['title'] + data['original_text']).casefold():
            continue
        items.append(requirement_out(db, r))
    return page(items, limit, offset)


@router.get('/requirements/{r}')
def get_requirement(p: str, r: str, db: DB, revision: RevisionQuery = None):
    return requirement_out(db, resource(db, p, r, 'requirement'), revision)


@router.patch('/requirements/{r}')
def edit_requirement(p: str, r: str, body: RequirementEdit, db: DB):
    item = resource(db, p, r, 'requirement')
    check_requirement_revision(item, body.base_revision)
    data = {**deepcopy(item.data), **body.model_dump(exclude_unset=True, exclude={'base_revision'})}
    if 'conditions' not in body.model_fields_set:
        data['conditions'] = deepcopy(item.data['conditions'])
    data['conditions'] = condition_payloads(db, p, data, public(item))
    data.update(revision=body.base_revision + 1, review_status='pending')
    return requirement_out(db, replace(db, item, data, body.base_revision))


@router.put('/requirements/{r}/review')
def approve(p: str, r: str, body: ReviewRequest, db: DB):
    item = resource(db, p, r, 'requirement')
    check_requirement_revision(item, body.base_revision)
    data = deepcopy(item.data)
    data['conditions'] = condition_payloads(db, p, data, public(item))
    data.update(revision=body.base_revision + 1, review_status='approved')
    return requirement_out(db, replace(db, item, data, body.base_revision))


def check_requirement_revision(item, base_revision):
    if item.version != base_revision:
        raise APIError(409, 'VERSION_CONFLICT', '다른 수정이 먼저 저장되었습니다',
                       details={'current_version': item.version, 'current_revision': item.version})


def ingest_extraction(db, p, d, draft):
    """Internal adapter boundary, not a public bypass for user/model approval."""
    doc = resource(db, p, d, 'document')
    artifact = {'document_id': d, 'document_revision': doc.data['revision'], 'blocks': doc.data['blocks']}
    if doc.data['parse_status'] not in ('parsed', 'partial'):
        raise APIError(409, 'DOCUMENT_NOT_PARSED', '파싱된 문서가 필요합니다')
    draft = validate_extraction(draft, artifact)
    if doc.data.get('topic_ids') or doc.data.get('requirement_ids'):
        raise APIError(409, 'EXTRACTION_ALREADY_STORED', '재추출 채택 정책을 먼저 적용해야 합니다')
    topics, subs, screens, conditions = {}, {}, {}, {}
    for t in draft['topics']:
        topics[t['key']] = new_id()
        for s in t['subtopics']:
            subs[s['key']] = new_id()
    screens = {s['screen_key']: new_id() for s in draft['screens']}
    conditions = {c['stable_key']: new_id() for r in draft['requirements'] for c in r['conditions']}
    common = {'document_id': d, 'document_revision': doc.data['revision']}
    for t in draft['topics']:
        create(db, p, 'topic', {**common, **{k: v for k, v in t.items() if k != 'subtopics'}}, topics[t['key']])
        for s in t['subtopics']:
            create(db, p, 'subtopic', {**common, **{k: v for k, v in s.items() if k != 'screen_keys'},
                                     'topic_id': topics[t['key']], 'screen_ids': [screens[k] for k in s['screen_keys']]}, subs[s['key']])
    for s in draft['screens']:
        create(db, p, 'screen', {**common, **{k: v for k, v in s.items() if k != 'base_screen_key'},
                               'base_screen_id': screens.get(s['base_screen_key'])}, screens[s['screen_key']])
    req_ids = []
    for r in draft['requirements']:
        converted = []
        for c in r['conditions']:
            converted.append({**{k: v for k, v in c.items() if k != 'related_condition_keys'},
                              'id': conditions[c['stable_key']],
                              'ambiguity': [c['ambiguity']] if c['ambiguity'] else [],
                              'related_condition_ids': [conditions[k] for k in c['related_condition_keys']]})
        needs = r['review_reasons'] or any(c['ambiguity'] for c in r['conditions'])
        data = {**common, **{k: v for k, v in r.items() if k not in ('subtopic_key', 'screen_key', 'conditions')},
                'subtopic_id': subs[r['subtopic_key']], 'screen_id': screens.get(r['screen_key']),
                'conditions': converted, 'revision': 1, 'review_status': 'needs_review' if needs else 'pending'}
        req_ids.append(create(db, p, 'requirement', data).id)
    replace(db, doc, {**doc.data, 'topic_ids': list(topics.values()), 'requirement_ids': req_ids,
                     'extraction_status': 'completed' if req_ids else 'empty', 'extraction_warnings': draft['warnings']})
    return req_ids
