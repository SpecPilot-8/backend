from types import SimpleNamespace

from api.v2.verification import _diff, _run_states


def fake_run(condition_status):
    req = {'id': 'r1', 'stable_key': 'NOTICE-001-1', 'revision': 1, 'title': '제목 50자',
           'conditions': [{'id': 'c1', 'stable_key': 'NOTICE-001-1-C1', 'text': '제목은 50자까지'}]}
    result = {'status': condition_status, 'condition_verdicts': [
        {'condition_id': 'c1', 'status': condition_status, 'message_match': 'matches'}]}
    return SimpleNamespace(data={'requirements': [req], 'results': {'r1': result}})


def test_condition_status_change_is_reported():
    _, before = _run_states(fake_run('mismatch'))
    _, after = _run_states(fake_run('implemented'))
    [change] = _diff(before, after, ('status', 'message_match'))
    assert change['change'] == 'changed'
    assert (change['before_status'], change['after_status']) == ('mismatch', 'implemented')
    assert change['requirement_key'] == 'NOTICE-001-1'


def test_same_results_have_no_changes():
    _, a = _run_states(fake_run('implemented'))
    assert _diff(a, a, ('status', 'message_match')) == []


def test_added_and_removed():
    _, a = _run_states(fake_run('implemented'))
    changes = _diff({}, a, ('status',)) + _diff(a, {}, ('status',))
    assert [c['change'] for c in changes] == ['added', 'removed']


def test_compare_endpoint(client, v2_project, v2_run):
    url = f'/api/v2/projects/{v2_project}/verification-runs/{v2_run["id"]}/compare'
    r = client.get(url, params={'base': v2_run['id']})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body['summary']['conditions_status_changed'] == 0 and body['conditions'] == []
    assert client.get(url, params={'base': 'missing'}).status_code == 404
