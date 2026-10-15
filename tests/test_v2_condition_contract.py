from copy import deepcopy
from io import BytesIO
import json
from pathlib import Path
from zipfile import ZipFile

import pytest

from api.errors import APIError
from api.v2.document_contracts import validate_extraction
from api.v2.documents import ingest_extraction
from api.v2.storage import create, resource
from api.v2.verdict_adapter import ingest_verdict

EXAMPLES = Path(__file__).resolve().parents[1] / 'docs/examples'


def example():
    return (json.loads((EXAMPLES / 'requirement-extraction.json').read_text()),
            json.loads((EXAMPLES / 'document-artifact.json').read_text()))


@pytest.mark.parametrize('constraint', ['omitted', None, {'field': '제목', 'operator': 'le', 'value': 50, 'unit': '자', 'unit_definition': None}])
def test_extraction_accepts_optional_constraint_and_keeps_original(constraint):
    draft, artifact = example()
    condition = draft['requirements'][0]['conditions'][0]
    condition.pop('constraint', None)
    if constraint != 'omitted':
        condition['constraint'] = constraint
    before = deepcopy(draft)
    normalized = validate_extraction(draft, artifact)
    result = normalized['requirements'][0]['conditions'][0]
    assert result['constraint'] == (None if constraint == 'omitted' else constraint)
    assert result['text'] == condition['text'] and result['source_refs'] == condition['source_refs']
    assert draft == before


@pytest.mark.parametrize('field,value', [('constraint', {}), ('constraint', '50자'), ('category', 'unknown_category')])
def test_extraction_still_rejects_invalid_condition_fields(field, value):
    draft, artifact = example()
    draft['requirements'][0]['conditions'][0][field] = value
    with pytest.raises(APIError) as error:
        validate_extraction(draft, artifact)
    assert error.value.code == 'MODEL_OUTPUT_INVALID'


@pytest.mark.parametrize('verifiable', ['code', 'not_statically_verifiable'])
def test_manual_display_condition_round_trip(client, v2_project, v2_document, verifiable):
    base = f'/api/v2/projects/{v2_project}/requirements'
    response = client.post(base, json={
        'document_id': v2_document, 'document_revision': 1, 'title': '입력한 글자 수 표시',
        'author_reason': '화면 표시 조건 검토', 'conditions': [
            {'stable_key': 'counter', 'text': '입력한 글자 수를 표시한다', 'category': 'display',
             'target': 'unknown', 'verifiable': verifiable}]})
    assert response.status_code == 201, response.text
    req = response.json()
    assert req['conditions'][0]['constraint'] is None
    changed = client.patch(base + '/' + req['id'], json={'base_revision': 1, 'conditions': req['conditions']})
    assert changed.status_code == 200, changed.text
    approved = client.put(base + '/' + req['id'] + '/review', json={'base_revision': 2, 'status': 'approved'})
    assert approved.status_code == 200
    for query in ['', '?revision=1']:
        condition = client.get(base + '/' + req['id'] + query).json()['conditions'][0]
        assert condition['category'] == 'display' and condition['target'] == 'unknown'
        assert condition['verifiable'] == verifiable
    schema = client.get('/openapi.json').json()['components']['schemas']['ConditionWrite']
    assert 'display' in schema['properties']['category']['enum']


@pytest.mark.parametrize('ambiguity', ['', '표시 시점 확인 필요'])
def test_extracted_display_can_be_edited_after_ingestion(client, v2_project, ambiguity):
    draft, artifact = example()
    condition = draft['requirements'][0]['conditions'][2]
    condition.update(category='display', target='unknown', ambiguity=ambiguity)
    condition.pop('constraint', None)
    with client.app.state.database.session() as db:
        doc = create(db, v2_project, 'document', {'revision': 1, 'blocks': artifact['blocks'], 'parse_status': 'parsed'})
        draft['document_id'] = doc.id
        ids = ingest_extraction(db, v2_project, doc.id, draft)
    base = f'/api/v2/projects/{v2_project}/requirements/{ids[0]}'
    stored = client.get(base).json()
    conditions = stored['conditions']
    assert conditions[2]['constraint'] is None
    assert conditions[2]['ambiguity'] == ([ambiguity] if ambiguity else [])
    # location is a joined read-only field; edit only the write contract.
    for item in conditions:
        item['source_refs'] = [{'block_id': ref['block_id'], 'quote': ref['quote']} for ref in item['source_refs']]
    edited = client.patch(base, json={'base_revision': 1, 'conditions': conditions})
    assert edited.status_code == 200, edited.text
    approved = client.put(base + '/review', json={'base_revision': 2, 'status': 'approved'})
    assert approved.status_code == 200
    assert approved.json()['conditions'][2]['category'] == 'display'


@pytest.mark.parametrize('target,server_validation,include_server', [
    ('unknown', 'present', True), ('frontend', 'present', True),
    ('backend', 'present', True), ('unknown', 'missing', False)])
def test_target_does_not_filter_frontend_or_backend_evidence(client, v2_project, v2_requirement,
                                                          target, server_validation, include_server):
    base = f'/api/v2/projects/{v2_project}'
    condition = deepcopy(v2_requirement['conditions'][0])
    condition['target'] = target
    req_id = v2_requirement['id']
    assert client.patch(base + f'/requirements/{req_id}', json={'base_revision': 1, 'conditions': [condition]}).status_code == 200
    approved = client.put(base + f'/requirements/{req_id}/review', json={'base_revision': 2, 'status': 'approved'}).json()
    buffer = BytesIO()
    with ZipFile(buffer, 'w') as archive:
        archive.writestr('screen.js', 'export function valid(title) { return title.length <= 50; }')
        if include_server:
            archive.writestr('Service.java', 'class Service { boolean valid(String title) { return title.length() <= 50; } }')
    source = client.post(base + '/code/sources', files={'file': ('code.zip', buffer.getvalue())}).json()
    snapshot = client.post(base + '/code/index', json={'source_id': source['source_id']}).json()['result_ref']['id']
    client.post(base + '/verification-runs', json={'snapshot_id': snapshot,
        'requirement_versions': [{'requirement_id': req_id, 'revision': 3}], 'model_config_version': 0})
    run_id = client.get(base + '/verification-runs').json()['items'][0]['id']
    chunks = client.get(base + f'/code/snapshots/{snapshot}/chunks').json()['items']
    assert {Path(c['file_path']).suffix for c in chunks} == ({'.js', '.java'} if include_server else {'.js'})
    primary = [c['id'] for c in chunks]
    draft = {'contract_version': '2.0', 'snapshot_id': snapshot, 'requirement_id': req_id, 'requirement_revision': 3,
        'condition_verdicts': [{'condition_id': approved['conditions'][0]['id'], 'status': 'implemented',
            'reason': '같은 화면에서 제한을 검사한다', 'primary_chunk_ids': primary, 'reference_chunk_ids': [],
            'message_match': 'not_applicable', 'server_validation': server_validation, 'spec_suspect': ''}],
        'findings': [], 'warnings': []}
    with client.app.state.database.session() as db:
        assert resource(db, v2_project, run_id, 'verification_run').data['requirements'][0]['conditions'][0]['target'] == target
        # unknown target must not bypass screen-reachability validation.
        with pytest.raises(APIError):
            ingest_verdict(db, v2_project, run_id, draft, {c: 'other_screen' for c in primary})
        result = ingest_verdict(db, v2_project, run_id, draft, {c: 'this_screen' for c in primary})
        assert {e['chunk_id'] for e in result['evidence']} == set(primary)
        assert result['condition_verdicts'][0]['server_validation'] == server_validation
        assert result['status'] == 'implemented'
