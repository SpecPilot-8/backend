from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from api.main import create_app
from api.errors import APIError
from api.v2 import extraction_runner as runner, server_llm
from api.v2.storage import create
from llm.base import LLMResponse

KEY = 'fake-key-not-for-production'


def sample_document():
    def block(identity, text, section, label=None, **structure):
        return {'id': identity, 'text': text, 'location': {'kind': 'page', 'index': 1},
                'extraction_method': 'text', 'structure': {'section': section, 'label': label, **structure}}
    return {'revision': 1, 'content_hash': 'example', 'parse_status': 'partial', 'parser_version': 'pdf-layout-2',
            'filename': 'example.pdf', 'warnings': [{'code': 'OCR_SOURCE_REVIEW', 'message': '1쪽 헤더 원문 확인', 'block_ids': ['real-depth']}],
            'unread_locations': [], 'screens': [{'page': 1, 'page_index': 0, 'screen_id': 'NOTICE-001', 'screen_name': '공지사항 작성',
                'depth': '공지사항', 'needs_review': True, 'review_reasons': ['OCR 원문 확인'], 'header_source': {'depth': 'ocr'}, 'header_confidence': {'depth': 0.9}}],
            'blocks': [block('real-depth', '공지사항', 'header', header_field='depth'),
                       block('real-name', '공지사항 작성', 'header', header_field='screen_name'),
                       block('real-sid', 'NOTICE-001', 'header', header_field='screen_id'),
                       block('real-row', '제목은50자까지입력한다.', 'description', '1')]}


def good_response():
    return {'requirements': [{'requirement_key': 'NOTICE-001-1', 'conditions': [
        {'statement': '제목은 50자까지 입력한다.', 'source_quotes': ['제목은 50자까지 입력한다.'],
         'verifiable': 'code', 'verifiable_reason': '입력 길이 검사', 'category': 'validation'}]}]}


@pytest.fixture
def web(config, monkeypatch):
    config = replace(config, llm_provider='anthropic', llm_model='test-model', llm_api_key=KEY)
    monkeypatch.setattr(server_llm, 'probe_client', lambda _: SimpleNamespace(complete_json=lambda **kwargs: LLMResponse({'ok': True}, '', 'test-model')))
    control = SimpleNamespace(data=good_response(), error=None, hook=None)
    calls = []
    def complete(**kwargs):
        calls.append(kwargs)
        if control.hook:
            control.hook()
        return LLMResponse(control.data, KEY, 'test-model', {'input_tokens': 20, 'output_tokens': 10}, error=control.error)
    monkeypatch.setattr(runner, 'analysis_client', lambda _: SimpleNamespace(complete_json=complete))
    with TestClient(create_app(config)) as c:
        p = c.post('/api/v2/projects', json={'name': '웹 추출'}).json()['id']
        with c.app.state.database.session() as db:
            d = create(db, p, 'document', sample_document()).id
        base = f'/api/v2/projects/{p}'
        assert c.put(base + '/llm', json={'profile_id': 'primary', 'base_version': 0}).status_code == 200
        assert c.post(base + '/llm/connection-check', json={'config_version': 1}).status_code == 200
        yield c, p, d, control, calls, config


def request(web):
    c, p, d, *_ = web
    r = c.post(f'/api/v2/projects/{p}/documents/{d}/requirements/extract', json={
        'document_revision': 1, 'model_config_version': 1, 'accept_warnings': True})
    assert r.status_code == 202, r.text
    return r.json(), c.get(f'/api/v2/projects/{p}/jobs/' + r.json()['id']).json()


def test_web_extract_runs_existing_decomposer_and_preserves_actual_source_ids(web):
    c, p, d, _, calls, config = web
    submitted, job = request(web)
    assert submitted['status'] == 'queued' and job['status'] == 'completed', job
    assert job['progress'] == {'stage': 'condition_extraction', 'completed': 1, 'total': 1}
    assert job['result'] == {'requirement_count': 1, 'condition_count': 1}
    assert len(calls) == 1 and 'category' in calls[0]['schema']['properties']['requirements']['items']['properties']['conditions']['items']['required']
    reqs = c.get(f'/api/v2/projects/{p}/requirements?document_id={d}').json()['items']
    assert len(reqs) == 1 and reqs[0]['review_status'] == 'needs_review'
    assert reqs[0]['source_refs'][0]['block_id'] == 'real-row'
    assert reqs[0]['conditions'][0]['source_refs'][0]['quote'] == '제목은50자까지입력한다.'
    assert reqs[0]['conditions'][0]['target'] == 'unknown'
    assert reqs[0]['conditions'][0]['category'] == 'validation'
    groups = c.get(f'/api/v2/projects/{p}/documents/{d}/requirement-groups').json()['topics']
    assert groups[0]['title'] == '공지사항' and groups[0]['subtopics'][0]['title'] == '공지사항 작성'
    assert KEY not in str(job) and KEY not in str(reqs)
    events = c.get(f'/api/v2/projects/{p}/jobs/{job["id"]}/events').text
    assert 'event: running' in events and 'event: completed' in events and KEY not in events
    with TestClient(create_app(config)) as reopened:
        assert reopened.get(f'/api/v2/projects/{p}/jobs/{job["id"]}').json()['status'] == 'completed'
    assert c.post(f'/api/v2/projects/{p}/documents/{d}/requirements/extract', json={
        'document_revision': 1, 'model_config_version': 1, 'accept_warnings': True}).status_code == 409
    assert len(calls) == 1


@pytest.mark.parametrize('mode', ['missing', 'quote', 'category', 'duplicate', 'authentication_error'])
def test_invalid_or_failed_model_output_is_not_adopted(web, mode):
    c, p, d, control, calls, _ = web
    if mode == 'missing':
        control.data['requirements'] = []
    elif mode == 'quote':
        control.data['requirements'][0]['conditions'][0]['source_quotes'] = ['원문에 없는 문장']
    elif mode == 'category':
        control.data['requirements'][0]['conditions'][0]['category'] = 'invented'
    elif mode == 'duplicate':
        control.data['requirements'].append(deepcopy(control.data['requirements'][0]))
    else:
        control.error = 'authentication_error'
    _, job = request(web)
    assert job['status'] == 'failed' and KEY not in str(job)
    assert job['error']['code'] in ('MODEL_OUTPUT_INVALID', 'LLM_AUTHENTICATION_FAILED')
    assert c.get(f'/api/v2/projects/{p}/requirements?document_id={d}').json()['total'] == 0
    assert len(calls) == 1


def test_changing_settings_during_call_does_not_adopt_old_result(web):
    c, p, d, control, _, _ = web
    control.hook = lambda: c.put(f'/api/v2/projects/{p}/llm', json={'profile_id': 'primary', 'base_version': 1})
    _, job = request(web)
    assert job['status'] == 'failed' and job['error']['code'] == 'CONFIG_CHANGED'
    assert c.get(f'/api/v2/projects/{p}/requirements?document_id={d}').json()['total'] == 0


def test_warning_acknowledgement_is_required(web):
    c, p, d, _, calls, _ = web
    r = c.post(f'/api/v2/projects/{p}/documents/{d}/requirements/extract', json={'document_revision': 1, 'model_config_version': 1})
    assert r.status_code == 409 and r.json()['error']['code'] == 'DOCUMENT_WARNINGS_NOT_ACCEPTED'
    assert calls == []


def test_pending_job_blocks_duplicate_requests_and_reparse_and_restart_is_terminal(web):
    c, p, d, _, calls, config = web
    with c.app.state.database.session() as db:
        j = create(db, p, 'job', {'operation': 'extract_requirements', 'status': 'queued', 'document_id': d,
            'events': [], 'request': {}, 'progress': None, 'missing_features': [], 'result_ref': None}).id
    base = f'/api/v2/projects/{p}'
    r = c.post(base + f'/documents/{d}/requirements/extract', json={'document_revision': 1, 'model_config_version': 1, 'accept_warnings': True})
    assert r.status_code == 409 and r.json()['error']['code'] == 'EXTRACTION_IN_PROGRESS'
    assert c.post(base + f'/documents/{d}/parse', json={'document_revision': 1, 'force': True}).status_code == 409
    with TestClient(create_app(config)) as restarted:
        job = restarted.get(base + f'/jobs/{j}').json()
        assert job['status'] == 'failed' and job['error']['code'] == 'SERVER_RESTARTED'
    assert calls == []


def test_draft_does_not_silently_drop_uncovered_source():
    doc = sample_document()
    doc['blocks'][-1]['text'] += '필수항목이다.'
    client = SimpleNamespace(complete_json=lambda **kwargs: LLMResponse(good_response(), '', 'test-model'))
    draft, _ = runner.extract_draft('doc', doc, client, lambda *a: None)
    assert any('연결되지 않은 구절' in reason for reason in draft['requirements'][0]['review_reasons'])


def test_missing_header_prevents_model_guessing():
    doc = sample_document()
    doc['screens'][0]['depth'] = ''
    with pytest.raises(APIError) as e:
        runner.prepare_document(doc)
    assert e.value.code == 'SCREEN_HEADER_REQUIRED'
