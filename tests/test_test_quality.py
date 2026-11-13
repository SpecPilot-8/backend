from testgen.quality import check, parse_limit


def req(key, conditions, original=''):
    return {'stable_key': key, 'original_text': original,
            'conditions': [{'id': f'{key}-c{i}', 'stable_key': f'{key}-C{i}', 'text': t} for i, t in enumerate(conditions, 1)]}


def step(key, cond_ids, action, value='', expected=''):
    return {'step_key': key, 'action': action, 'expected': expected,
            'test_data': {'input': value, 'notes': ''},
            'requirement_links': [{'requirement_id': 'r', 'requirement_revision': 1, 'condition_ids': cond_ids}]}


TITLE = req('NOTICE-002-1', ['제목은 50자까지 입력할 수 있고 50자가 넘어가면 입력을 할 수 없다.'])


def test_parse_limit_direction():
    assert parse_limit('비밀번호는 8자리 이상으로 구성되어야 한다').kind == 'min'
    assert parse_limit('제목은 50자까지 입력할 수 있다').kind == 'max'
    assert parse_limit('첨부파일은 50MB 이하만 가능').unit == 'MB'
    assert parse_limit('목록은 10개씩 표시한다') is None


def test_linked_step_without_boundary_values_is_reported():
    # #106 재현: 최대 50자 조건에 20자만 넣는 단계는 연결돼 있어도 경계값을 검증하지 않는다
    tests = [{'title': 't', 'steps': [step('s1', ['NOTICE-002-1-c1'], '제목에 20자를 입력한다', '제목 20자')]}]
    [b] = check([TITLE], tests).boundary
    assert b['missing'] == ['50자 (정확값)', '50자 초과 (바깥)']


def test_exact_and_outside_values_satisfy_boundary():
    tests = [{'title': 't', 'steps': [step('s1', ['NOTICE-002-1-c1'], '제목에 50자를 입력한다'),
                                      step('s2', ['NOTICE-002-1-c1'], '제목에 51자를 입력한다')]}]
    assert check([TITLE], tests).boundary[0]['missing'] == []


def test_boundary_is_checked_per_requirement_not_per_condition():
    pw = req('LOGIN-002-2', ['비밀번호는 8자리 이상이어야 한다', '최소 자릿수 미달 시 에러 메시지를 표시한다'])
    tests = [{'title': 't', 'steps': [step('s1', ['LOGIN-002-2-c2'], '7자리를 입력한다'),
                                      step('s2', ['LOGIN-002-2-c1'], '8자리를 입력한다')]}]
    assert check([pw], tests).boundary[0]['missing'] == []


def test_empty_input_counts_as_outside_of_minimum_one():
    r = req('NOTICE-002-8', ['제목은 1글자 이상이어야 한다'])
    tests = [{'title': 't', 'steps': [step('s1', ['NOTICE-002-8-c1'], '아무것도 입력하지 않은 상태를 확인한다'),
                                      step('s2', ['NOTICE-002-8-c1'], '제목을 1글자 입력한다')]}]
    assert check([r], tests).boundary[0]['missing'] == []


def test_repeated_step_in_same_test_and_identical_tests_are_duplicates():
    s = step('s1', ['NOTICE-002-1-c1'], '50자를 입력한다')
    report = check([TITLE], [{'title': 'a', 'steps': [s, dict(s, step_key='s2')]},
                             {'title': 'b', 'steps': [s]}, {'title': 'c', 'steps': [s]}])
    assert sorted(d['kind'] for d in report.duplicates) == ['same_test', 'whole_test']


def test_shared_step_across_different_tests_is_not_duplicate():
    close = step('s3', ['NOTICE-002-1-c1'], '팝업을 닫는다')
    other = step('s1', ['NOTICE-002-1-c1'], '51자를 입력한다')
    assert check([TITLE], [{'title': 'a', 'steps': [close]}, {'title': 'b', 'steps': [other, close]}]).duplicates == []


def test_quoted_message_not_in_spec_is_invented_and_other_requirement_is_borrowed():
    other = req('NOTICE-002-C', ['취소 팝업'], original='"작성한 내용이 있습니다." 팝업')
    tests = [{'title': 't', 'steps': [
        step('s1', ['NOTICE-002-1-c1'], '51자 입력', expected='"제목이 너무 깁니다" 메시지가 표시된다'),
        step('s2', ['NOTICE-002-1-c1'], '취소', expected='"작성한 내용이 있습니다." 팝업이 뜬다')]}]
    report = check([TITLE, other], tests)
    assert [u['message'] for u in report.invented] == ['제목이 너무 깁니다']
    assert report.borrowed[0]['found_in'] == ['NOTICE-002-C']
