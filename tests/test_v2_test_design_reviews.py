from copy import deepcopy
from uuid import uuid4


def endpoint(p, case):
    return f'/api/v2/projects/{p}/test-cases/{case["id"]}/design-review'


def body(case, decision='adequate'):
    link = case['requirement_links'][0]
    return {'test_case_revision': case['revision'], 'base_version': 0, 'client_review_id': str(uuid4()),
            'items': [{'requirement_id': link['requirement_id'], 'requirement_revision': link['requirement_revision'],
                       'condition_id': link['condition_ids'][0], 'decision': decision,
                       'reason': '명세 상한의 정확값과 초과값을 입력하고 저장 허용 여부를 비교하도록 설계했다.'}]}


def test_review_is_explicit_manual_record_and_does_not_execute_test(client, v2_project, v2_case):
    url = endpoint(v2_project, v2_case)
    empty = client.get(url).json()
    assert empty['status'] == 'pending' and empty['version'] == 0 and empty['items'] == []
    request = body(v2_case)
    saved = client.put(url, json=request)
    assert saved.status_code == 200, saved.text
    data = saved.json()
    assert data['status'] == 'adequate' and data['origin'] == 'manual' and data['version'] == 1
    assert client.put(url, json=request).json() == data
    assert client.get(url).json() == data
    assert client.get(url.replace('/design-review', '/executions')).json()['items'] == []
    assert client.get(url.replace('/design-review', '')).json()['revision'] == 1
    request.update(client_review_id=str(uuid4()), base_version=1)
    request['items'][0].update(decision='needs_changes', reason='거부 결과가 구체적이지 않아 보완이 필요하다.')
    assert client.put(url, json=request).json()['status'] == 'needs_changes'


def test_review_is_pinned_to_case_revision_and_historical_record_survives(client, v2_project, v2_case):
    url = endpoint(v2_project, v2_case)
    request = body(v2_case)
    assert client.put(url, json=request).status_code == 200
    changed = client.patch(url.replace('/design-review', '/procedure'), json={'base_revision': 1, 'preconditions': '수정한 시험 준비'}).json()
    assert changed['revision'] == 2
    current = client.get(url).json()
    assert current['test_case_revision'] == 2 and current['status'] == 'pending' and current['items'] == []
    assert client.get(url + '?revision=1').json()['status'] == 'adequate'
    request.update(client_review_id=str(uuid4()))
    assert client.put(url, json=request).json()['error']['code'] == 'CASE_REVISION_CHANGED'
    assert client.put(url, json=body(changed, 'uncertain')).json()['status'] == 'uncertain'


def test_review_rejects_foreign_condition_revision_duplicate_and_blank_reason(client, v2_project, v2_case):
    url = endpoint(v2_project, v2_case)
    for field, value in [('condition_id', 'unlinked'), ('requirement_revision', 999), ('requirement_id', 'other'), ('reason', ' '), ('decision', 'passed')]:
        invalid = body(v2_case)
        invalid['items'][0][field] = value
        assert client.put(url, json=invalid).status_code == 422
    invalid = body(v2_case)
    invalid['items'].append(deepcopy(invalid['items'][0]))
    assert client.put(url, json=invalid).status_code == 422
    assert client.get(url).json()['items'] == []


def test_review_version_retry_conflict_and_project_isolation(client, v2_project, v2_case):
    url = endpoint(v2_project, v2_case)
    request = body(v2_case)
    assert client.put(url, json=request).status_code == 200
    changed = deepcopy(request)
    changed['items'][0]['reason'] = '다른 설명'
    assert client.put(url, json=changed).json()['error']['code'] == 'IDEMPOTENCY_CONFLICT'
    changed['client_review_id'] = str(uuid4())
    assert client.put(url, json=changed).json()['error']['code'] == 'VERSION_CONFLICT'
    other = client.post('/api/v2/projects', json={'name': '다른 프로젝트'}).json()['id']
    other_url = endpoint(other, v2_case)
    assert client.get(other_url).status_code == 404
    assert client.put(other_url, json=body(v2_case)).status_code == 404
