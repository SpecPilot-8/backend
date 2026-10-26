from copy import deepcopy
from pathlib import Path
import json
import pymupdf
import pytest
from api.errors import APIError
from api.v2.documents import ingest_extraction
from api.v2.storage import create, resource


def test_document_partial_blocks_and_extract_blocked(client, v2_project, v2_document):
    base = f'/api/v2/projects/{v2_project}/documents/{v2_document}'
    d = client.get(base).json()
    assert d['parse_status'] == 'partial' and 'storage_path' not in d and d['warnings']
    blocks = client.get(base + '/blocks?index=1').json()['items']
    assert '50 characters' in blocks[0]['text'] and blocks[0]['location']['bbox']
    body = {'document_revision': 1, 'model_config_version': 0}
    assert client.post(base + '/requirements/extract', json=body).status_code == 409
    r = client.post(base + '/requirements/extract', json={**body, 'accept_warnings': True})
    assert r.status_code == 202 and r.json()['status'] == 'blocked'
    assert r.json()['missing_features'][0]['code'] == 'requirement_extraction'
    assert client.post(base + '/parse', json={'document_revision': 1}).status_code == 202
    assert client.get(base + '/blocks').json()['items'][0]['id'] == blocks[0]['id']
    assert client.post(base + '/parse', json={'document_revision': 2}).status_code == 409


def test_requirement_review_and_revision_preserved(client, v2_project, v2_requirement):
    url = f"/api/v2/projects/{v2_project}/requirements/{v2_requirement['id']}"
    approved = client.put(url + '/review', json={'base_revision': 1, 'status': 'approved'}).json()
    assert approved['revision'] == 2 and approved['review_status'] == 'approved'
    changed = client.patch(url, json={'base_revision': 2, 'title': '수정된 제목'}).json()
    assert changed['revision'] == 3 and changed['review_status'] == 'pending'
    assert client.get(url + '?revision=2').json()['title'] == '제목 50자 제한'
    assert client.get(url + '?revision=2').json()['conditions'][0]['id'] == approved['conditions'][0]['id']
    assert changed['conditions'][0]['id'] != approved['conditions'][0]['id']
    assert client.patch(url, json={'base_revision': 2, 'title': '덮어쓰기'}).status_code == 409
    assert client.patch(url, json={'base_revision': 3, 'original_text': '원문변경'}).status_code == 422


def test_approve_all_requirements(client, v2_project, v2_requirement):
    url = f"/api/v2/projects/{v2_project}/requirements"
    res = client.post(f"{url}/review-all")
    assert res.status_code == 200
    data = res.json()
    assert data['success'] is True
    assert data['approved_count'] >= 1
    # Check that requirement is now approved
    req_res = client.get(f"{url}/{v2_requirement['id']}")
    assert req_res.json()['review_status'] == 'approved'


def test_related_historical_condition_survives_owner_edit(client, v2_project, v2_document, v2_requirement):
    base = f'/api/v2/projects/{v2_project}/requirements'
    old_condition = v2_requirement['conditions'][0]['id']
    body = {'document_id': v2_document, 'document_revision': 1, 'title': '관련 오류 표시', 'author_reason': '검토 추가',
            'conditions': [{'stable_key':'related-error', 'text':'제목 제한 오류 표시', 'category':'error_message',
                            'target':'frontend', 'verifiable':'code', 'related_condition_ids':[old_condition]}]}
    related = client.post(base, json=body)
    assert related.status_code == 201, related.text
    changed = client.patch(base + '/' + v2_requirement['id'], json={'base_revision':1, 'title':'수정된 제목 제한'})
    assert changed.status_code == 200 and changed.json()['conditions'][0]['id'] != old_condition
    approved = client.put(base + '/' + related.json()['id'] + '/review', json={'base_revision':1,'status':'approved'})
    assert approved.status_code == 200, approved.text
    assert approved.json()['conditions'][0]['related_condition_ids'] == [old_condition]
    assert client.get(base + '/' + v2_requirement['id'] + '?revision=1').json()['conditions'][0]['id'] == old_condition


def test_stale_condition_edit_returns_version_conflict_first(client, v2_project, v2_requirement):
    url = f'/api/v2/projects/{v2_project}/requirements/{v2_requirement["id"]}'
    approved = client.put(url + '/review', json={'base_revision':1,'status':'approved'})
    assert approved.status_code == 200
    result = client.patch(url, json={'base_revision':1, 'conditions':v2_requirement['conditions']})
    assert result.status_code == 409
    assert result.json()['error']['code'] == 'VERSION_CONFLICT'
    assert result.json()['error']['details']['current_revision'] == 2


def test_scope_and_bad_source_are_rejected(client, v2_project, v2_document, v2_requirement):
    other = client.post('/api/v2/projects', json={'name': '다른 프로젝트'}).json()['id']
    assert client.get(f'/api/v2/projects/{other}/documents/{v2_document}').status_code == 404
    body = {'document_id': v2_document, 'document_revision': 1, 'title': '위조 원문', 'author_reason': '검토',
            'source_refs': [{'block_id': 'missing', 'quote': '위조'}],
            'conditions': [{'stable_key': 'x', 'text': '규칙', 'category': 'data', 'target': 'backend', 'verifiable': 'code'}]}
    assert client.post(f'/api/v2/projects/{v2_project}/requirements', json=body).status_code == 422
    assert client.get(f'/api/v2/projects/{v2_project}/requirements').json()['total'] == 1
    assert client.get(f'/api/v2/projects/{v2_project}/requirements?subtopic_id={v2_requirement["subtopic_id"]}').json()['total'] == 1


def test_bad_upload_and_scan_not_success(client, v2_project):
    base = f'/api/v2/projects/{v2_project}/documents'
    assert client.post(base, files={'file': ('fake.pdf', b'%PDF-fake')}).status_code == 415
    assert client.post(base, files={'file': ('file.docx', b'x')}).status_code == 415
    with pymupdf.open() as pdf:
        pdf.new_page()
        content = pdf.tobytes()
    doc = client.post(base, files={'file': ('scan.pdf', content)}).json()
    job = client.post(base + '/' + doc['id'] + '/parse', json={'document_revision': 1}).json()
    assert job['status'] == 'failed'
    assert client.get(base + '/' + doc['id']).json()['parse_status'] == 'failed'


def test_teammate_output_validated_before_storage(client, v2_project):
    examples = Path(__file__).resolve().parents[1] / 'docs/examples'
    artifact = json.loads((examples / 'document-artifact.json').read_text(encoding='utf-8'))
    draft = json.loads((examples / 'requirement-extraction.json').read_text(encoding='utf-8'))
    with client.app.state.database.session() as db:
        doc = create(db, v2_project, 'document', {'revision': 1, 'blocks': artifact['blocks'], 'parse_status': 'parsed'})
        draft['document_id'] = doc.id
        artifact['document_id'] = doc.id
        for b in doc.data['blocks']:
            b['document_id'] = doc.id
        invalid = deepcopy(draft)
        invalid['requirements'][0]['source_refs'][0]['quote'] = '위조 인용'
        with pytest.raises(APIError) as error:
            ingest_extraction(db, v2_project, doc.id, invalid)
        assert error.value.code == 'MODEL_OUTPUT_INVALID'
        assert not db.query(type(doc)).filter_by(project_id=v2_project, kind='requirement').count()
        ids = ingest_extraction(db, v2_project, doc.id, draft)
        assert len(ids) == 4
        first = resource(db, v2_project, ids[0], 'requirement')
        assert first.data['review_status'] != 'approved'
        assert first.data['source_markers'] and first.data['conditions'][0]['id']
        s = resource(db, v2_project, first.data['subtopic_id'], 'subtopic')
        assert first.data['screen_id'] in s.data['screen_ids']


def test_document_file_returns_original_pdf(client, v2_project, v2_document):
    r = client.get(f'/api/v2/projects/{v2_project}/documents/{v2_document}/file')
    assert r.status_code == 200
    assert r.headers['content-type'] == 'application/pdf'
    assert r.headers['content-disposition'].startswith('inline')
    assert r.content.startswith(b'%PDF-')
    assert client.get(f'/api/v2/projects/{v2_project}/documents/missing/file').status_code == 404
