from api.v2.testing import step_evidence, step_evidence_text

RUN = {
    'requirements': [{'id': 'r1', 'conditions': [{'id': 'c1', 'text': '제목 50자'}, {'id': 'c2', 'text': '첨부 50MB'}]}],
    'results': {'r1': {
        'evidence': [{'id': 'e1', 'chunk_id': 'k1', 'file_path': 'NoticeForm.java', 'start_line': 10, 'end_line': 12},
                     {'id': 'e2', 'chunk_id': 'k2', 'file_path': 'application.yml', 'start_line': 3, 'end_line': 4}],
        'condition_verdicts': [{'condition_id': 'c1', 'status': 'implemented', 'evidence_ids': ['e1']},
                               {'condition_id': 'c2', 'status': 'mismatch', 'evidence_ids': ['e2']}]}},
}


def test_step_gets_only_its_condition_evidence():
    step = {'requirement_links': [{'requirement_id': 'r1', 'requirement_revision': 1, 'condition_ids': ['c1']}]}
    [item] = step_evidence(RUN, step)
    assert item['status'] == 'implemented' and [e['file_path'] for e in item['evidence']] == ['NoticeForm.java']
    assert 'application.yml' not in step_evidence_text([item])


def test_condition_without_evidence_is_explicit():
    run = {**RUN, 'results': {'r1': {'evidence': [], 'condition_verdicts': [
        {'condition_id': 'c2', 'status': 'not_found', 'evidence_ids': []}]}}}
    step = {'requirement_links': [{'requirement_id': 'r1', 'requirement_revision': 1, 'condition_ids': ['c2']}]}
    assert step_evidence_text(step_evidence(run, step)) == '[not_found] 첨부 50MB\n  근거 코드 없음'


def test_case_evidence_endpoint(client, v2_project, v2_case):
    r = client.get(f'/api/v2/projects/{v2_project}/test-cases/{v2_case["id"]}/evidence')
    assert r.status_code == 200, r.text
    assert [s['step_key'] for s in r.json()['steps']] == [s['step_key'] for s in v2_case['steps']]
