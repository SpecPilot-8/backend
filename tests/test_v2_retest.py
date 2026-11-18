
def base(p):
    return f'/api/v2/projects/{p}'


def test_no_retest_without_changes(client, v2_project, v2_case):
    r = client.get(base(v2_project) + f'/test-specifications/{v2_case["test_specification_id"]}/retest')
    assert r.status_code == 200, r.text
    assert r.json()['retest_count'] == 0 and r.json()['compared_run_id'] is None


def test_requirement_revision_marks_case(client, v2_project, v2_case, v2_requirement):
    link = v2_case['requirement_links'][0]
    changed = client.patch(base(v2_project) + f'/requirements/{v2_requirement["id"]}',
                           json={'base_revision': link['requirement_revision'], 'title': '개정된 명세'})
    assert changed.status_code == 200, changed.text
    body = client.get(base(v2_project) + f'/test-specifications/{v2_case["test_specification_id"]}/retest').json()
    [item] = body['items']
    assert item['test_case_id'] == v2_case['id'] and item['retest_required']
    assert item['reasons'][0]['kind'] == 'requirement_revised'


def test_verdict_change_marks_case(monkeypatch, client, v2_project, v2_case):
    import api.v2.verification as verification
    calls = iter([({}, {'K-C1': {'status': 'implemented'}}), ({}, {'K-C1': {'status': 'mismatch'}})])
    monkeypatch.setattr(verification, '_run_states', lambda run: next(calls))
    spec = client.get(base(v2_project) + f'/test-specifications/{v2_case["test_specification_id"]}').json()
    run_id = client.get(base(v2_project) + '/verification-runs').json()['items'][-1]['id']
    import api.v2.testing as testing
    real = testing.historical

    def keyed(db, item, revision=None):
        data = real(db, item, revision)
        if item.kind == 'test_specification':
            for req in data['requirements']:
                for c in req['conditions']:
                    c['stable_key'] = 'K-C1'
        return data
    monkeypatch.setattr(testing, 'historical', keyed)
    body = client.get(base(v2_project) + f'/test-specifications/{spec["id"]}/retest',
                      params={'verification_run_id': run_id}).json()
    [item] = body['items']
    assert item['reasons'][0] == {'kind': 'verdict_changed', 'requirement_key': item['reasons'][0]['requirement_key'],
                                  'condition_key': 'K-C1', 'before_status': 'implemented', 'after_status': 'mismatch'}
