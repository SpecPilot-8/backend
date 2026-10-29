from copy import deepcopy

from testgen.design import build_plan, design_review


def requirement():
    return {'id': 'r', 'revision': 2, 'title': '제목 입력', 'conditions': [
        {'id': 'c', 'stable_key': 'title-c1', 'text': '제목은 50자 이하이다.',
         'category': 'validation', 'verifiable': 'code'}]}


def test_plan_requires_explicit_numeric_or_role_basis():
    req = requirement()
    assert [i['perspective'] for i in build_plan([req])['items']] == ['valid_input', 'invalid_input', 'boundary']
    req['conditions'][0].update(text='제목을 입력한다.', stable_key='SCREEN-999-C1')
    items = build_plan([req])['items']
    assert {i['perspective'] for i in items} == {'valid_input', 'invalid_input'}
    assert all(i['requirement_revision'] == 2 and i['condition_id'] == 'c' for i in items)
    assert 'permission' not in {i['perspective'] for i in items}
    req['conditions'][0].update(text='관리자 권한으로 조회할 수 있다.', category='business_logic')
    assert 'permission' in {i['perspective'] for i in build_plan([req])['items']}


def test_visual_condition_gets_execution_view_without_inventing_policy():
    req = requirement()
    req['conditions'][0].update(text='입력창은 우측에 표시된다.', verifiable='not_statically_verifiable')
    assert [i['perspective'] for i in build_plan([req])['items']] == ['runtime_check']


def test_review_does_not_invent_historical_rationale_or_classify_manual_test():
    req = requirement()
    case = {'id': 't', 'test_no': 'TC-1', 'revision': 1, 'steps': [
        {'step_key': 's', 'order': 1, 'requirement_links': [
            {'requirement_id': 'r', 'requirement_revision': 2, 'condition_ids': ['c']}]}]}
    spec = {'requirements': [req], 'revision': 1}
    legacy = design_review(spec, [case])
    assert not legacy['plan_available'] and not legacy['items']
    assert legacy['summary']['conditions_linked'] == 1
    assert 'rationale' not in str(legacy)
    spec['design_plan'] = build_plan([req])
    reviewed = design_review(spec, [case])
    assert reviewed['summary']['intents_written'] == 0
    assert reviewed['summary']['intents_missing'] == 3
    assert reviewed['summary']['conditions_linked'] == 1
    # 다른 개정에 연결된 시험을 이 조건의 시험으로 세지 않는다.
    old = deepcopy(case)
    old['steps'][0]['requirement_links'][0]['requirement_revision'] = 1
    assert design_review(spec, [old])['summary']['conditions_linked'] == 0
