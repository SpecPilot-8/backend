def test_manual_specification_without_model_keeps_verdict_blocked(client, v2_project, v2_run, v2_requirement):
    base = f'/api/v2/projects/{v2_project}'
    result = client.post(base + '/test-specifications', json={
        'verification_run_id': v2_run['id'], 'requirement_ids': [v2_requirement['id']]})
    assert result.status_code == 201, result.text
    spec = result.json()
    assert spec['status'] == 'draft' and spec['origin'] == 'manual'
    assert not spec['test_case_ids'] and 'job_id' not in spec
    assert client.get(base + f'/verification-runs/{v2_run["id"]}').json()['status'] == 'blocked'
    assert client.post(base + '/test-specifications', json={
        'verification_run_id': v2_run['id'], 'requirement_ids':['missing']}).status_code == 422
