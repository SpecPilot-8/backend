from copy import deepcopy
from uuid import uuid4
from fastapi.testclient import TestClient
from api.main import create_app


def base(p):
    return f'/api/v2/projects/{p}'


def create_execution(client, p, case, client_id=None):
    return client.post(base(p) + f'/test-cases/{case["id"]}/executions', json={'test_case_revision': case['revision'], 'client_execution_id': str(client_id or uuid4()), 'environment': 'QA'})


def result(key, status='passed'):
    return {'step_key': key, 'status': status, 'actual': '사람이 확인한 결과', 'notes': '', 'performed_at': '2026-10-11T05:00:00Z'}


def test_execution_retry_and_partial_pass_not_total_pass(client, v2_project, v2_case):
    token = uuid4()
    first = create_execution(client, v2_project, v2_case, token)
    assert first.status_code == 201 and first.json()['step_results'] == []
    assert create_execution(client, v2_project, v2_case, token).json() == first.json()
    execution = first.json()
    url = base(v2_project) + f'/test-executions/{execution["id"]}'
    body = {'base_version': 1, 'client_edit_id': str(uuid4()), 'step_results': [result('step-1')]}
    saved = client.patch(url, json=body)
    assert saved.status_code == 200 and saved.json()['status'] == 'not_run'
    assert saved.json()['version'] == 2
    assert client.patch(url, json=body).json() == saved.json()
    bad_retry = {**body, 'step_results': [result('step-2')]}
    assert client.patch(url, json=bad_retry).status_code == 409
    assert client.patch(url, json={**body, 'client_edit_id': str(uuid4())}).status_code == 409
    second = client.patch(url, json={'base_version': 2, 'client_edit_id': str(uuid4()), 'step_results': [result('step-2')]}).json()
    assert second['status'] == 'passed'


def test_procedure_revision_keeps_old_execution_and_invalidates_prediction(client, v2_project, v2_case):
    e = create_execution(client, v2_project, v2_case).json()
    client.patch(base(v2_project) + f'/test-executions/{e["id"]}', json={'base_version': 1, 'client_edit_id': str(uuid4()), 'step_results': [result('step-1'), result('step-2')]})
    changed = client.patch(base(v2_project) + f'/test-cases/{v2_case["id"]}/procedure', json={'base_revision': 1, 'preconditions': '다른 환경'}).json()
    assert changed['revision'] == 2 and changed['prediction']['status'] == 'unknown'
    assert client.get(base(v2_project) + f'/test-cases/{v2_case["id"]}?revision=1').json()['preconditions'] == '작성 화면'
    summary = client.get(base(v2_project) + f'/test-specifications/{v2_case["test_specification_id"]}/execution-summary').json()
    assert summary['scenario_summary'] == {'total': 1, 'not_run': 1, 'passed': 0, 'failed': 0, 'on_hold': 0}
    old = client.get(base(v2_project) + f'/test-specifications/{v2_case["test_specification_id"]}/execution-summary?revision=2').json()
    assert old['scenario_summary']['passed'] == 1
    assert summary['step_summary']['total'] == 2


def test_atomic_batch_and_retry_and_new_revision_binding(client, config, v2_project, v2_case):
    spec = v2_case['test_specification_id']
    body = {'base_specification_revision': 2, 'client_save_id': str(uuid4()), 'changes': [
        {'test_case_id': v2_case['id'], 'base_test_case_revision': 1, 'procedure': {'preconditions': '수정 전제'},
         'execution': {'operation': 'create', 'bind_to': 'after_save', 'step_results': [result('step-1', 'failed')], 'tested_build': 'build-123'}}]}
    url = base(v2_project) + f'/test-specifications/{spec}/edit-batches'
    r = client.post(url, json=body)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data['specification_revision'] == 3 and data['executions'][0]['test_case_revision'] == 2
    assert data['executions'][0]['status'] == 'failed'
    assert client.post(url, json=body).json() == data
    with TestClient(create_app(config)) as reopened:
        assert reopened.post(url, json=body).json() == data
        assert reopened.get(base(v2_project) + f'/test-cases/{v2_case["id"]}').json()['preconditions'] == '수정 전제'
    conflicting = {**body, 'client_save_id': str(uuid4())}
    assert client.post(url, json=conflicting).status_code == 409


def test_batch_failure_saves_nothing_and_old_execution_cannot_move(client, v2_project, v2_case):
    e = create_execution(client, v2_project, v2_case).json()
    original = deepcopy(v2_case)
    second_body = {k: v for k, v in original.items() if k in ('test_specification_id','export_section_key','subtopic_id','title','type','preconditions','requirement_links','steps')}
    second_body['title'] = '두 번째 시험'
    second = client.post(base(v2_project) + '/test-cases', json=second_body).json()
    body = {'base_specification_revision': 3, 'client_save_id': str(uuid4()), 'changes': [
        {'test_case_id': original['id'], 'base_test_case_revision': 1, 'procedure': {'title': '저장되면 안 됨'}},
        {'test_case_id': second['id'], 'base_test_case_revision': 99, 'procedure': {'title': '잘못된 버전'}}]}
    url = base(v2_project) + f'/test-specifications/{original["test_specification_id"]}/edit-batches'
    assert client.post(url, json=body).status_code == 409
    assert client.get(base(v2_project) + f'/test-cases/{original["id"]}').json()['title'] == original['title']
    body['changes'] = [{'test_case_id': original['id'], 'base_test_case_revision': 1, 'procedure': {'title': '새 절차'},
                        'execution': {'operation': 'update', 'execution_id': e['id'], 'base_version': 1, 'step_results': [result('step-1')]}}]
    r = client.post(url, json=body)
    assert r.status_code == 409 and r.json()['error']['code'] == 'EXECUTION_REVISION_CONFLICT'
    assert client.get(base(v2_project) + f'/test-cases/{original["id"]}').json()['revision'] == 1


def test_invalid_status_steps_and_scope(client, v2_project, v2_case):
    e = create_execution(client, v2_project, v2_case).json()
    url = base(v2_project) + f'/test-executions/{e["id"]}'
    for steps in [[{'step_key': 'step-1', 'status': 'passed'}], [result('missing')], [result('step-1'), result('step-1')], [{'step_key':'step-1','status':'on_hold','notes':''}]]:
        r = client.patch(url, json={'base_version': 1, 'client_edit_id': str(uuid4()), 'step_results': steps})
        assert r.status_code == 422
    other = client.post('/api/v2/projects', json={'name': '다른 프로젝트'}).json()['id']
    assert client.get(base(other) + f'/test-cases/{v2_case["id"]}').status_code == 404
    steps = deepcopy(v2_case['steps'])
    steps[1]['order'] = 1
    assert client.patch(base(v2_project) + f'/test-cases/{v2_case["id"]}/procedure', json={'base_revision': 1, 'steps': steps}).status_code == 422


def test_concurrent_same_execution_request_is_once(client, v2_project, v2_case):
    from concurrent.futures import ThreadPoolExecutor
    token = uuid4()
    with ThreadPoolExecutor(max_workers=2) as workers:
        calls = list(workers.map(lambda _: create_execution(client, v2_project, v2_case, token), range(2)))
    assert all(r.status_code == 201 for r in calls)
    assert calls[0].json() == calls[1].json()
    assert client.get(base(v2_project) + f'/test-cases/{v2_case["id"]}/executions').json()['total'] == 1


def test_generated_output_rejects_actual_fields_and_keeps_manual_case(client, v2_project, v2_case):
    import pytest
    from api.errors import APIError
    from api.v2.storage import resource, replace
    from api.v2.test_generation_adapter import ingest_test_generation
    candidate = {k: deepcopy(v) for k, v in v2_case.items() if k in ('export_section_key', 'subtopic_id', 'title', 'type', 'preconditions', 'requirement_links', 'steps')}
    with client.app.state.database.session() as db:
        spec = resource(db, v2_project, v2_case['test_specification_id'], 'test_specification')
        run = resource(db, v2_project, spec.data['verification_run_id'], 'verification_run')
        # Synthetic worker fixture, not a public route or real model result.
        replace(db, run, {**run.data, 'status': 'completed'})
        candidate.update(candidate_key='new-candidate', prediction={'status': 'unknown', 'reason': '예측 없음', 'verification_run_id': run.id, 'primary_chunk_ids': []}, assumptions=[], warnings=[])
        output = {'contract_version': '2.1', 'verification_run_id': run.id, 'test_candidates': [candidate], 'warnings': []}
        invalid = deepcopy(output)
        invalid['test_candidates'][0]['actual'] = 'AI가 통과를 생성'
        with pytest.raises(APIError):
            ingest_test_generation(db, v2_project, spec.id, invalid)
        ids = ingest_test_generation(db, v2_project, spec.id, output)
        assert len(ids) == 1
    assert client.get(base(v2_project) + f'/test-cases/{v2_case["id"]}').json()['revision'] == 1
    assert client.get(base(v2_project) + '/test-cases', params={'test_specification_id': v2_case['test_specification_id']}).json()['total'] == 2
