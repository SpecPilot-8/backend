from decomposer.numbers import numeric_issues, numeric_tokens


def test_tokens_read_numbers_glued_to_korean_text():
    assert numeric_tokens("비밀번호는8자리이상으로") == [("8", "char", "at_least", "8자리")]
    assert numeric_tokens("첨부파일은50MB이하") == [("50", "MB", "at_most", "50MB")]
    assert numeric_tokens("최대 2,000자") == [("2000", "char", "at_most", "2,000자")]


def test_same_values_with_paraphrase_pass():
    assert numeric_issues("제목은 최대 50자다.", ["제목은50자까지입력할수있다."]) == []
    assert numeric_issues("50자를 초과하면 입력할 수 없다.", ["50자가넘어가면입력을할수없으며"]) == []
    assert numeric_issues("비밀번호는 8글자 이상", ["비밀번호는8자리이상으로"]) == []
    assert numeric_issues("표시건수는 25, 50, 100 중 선택", ["표시건수는25, 50, 100 중에선택이가능하다."]) == []


def test_changed_value_unit_or_direction_is_reported():
    quote = ["제목은50자까지입력할수있다."]
    assert numeric_issues("제목은 100자까지", quote) == ["수치 '100자'가 원문 인용에 없음"]
    assert numeric_issues("제목은 50바이트까지", quote) == ["'50바이트'의 단위가 원문(50자)과 다름"]
    assert numeric_issues("제목은 50자 이상", quote) == ["'50자' 뒤의 비교 표현이 원문과 다름"]


def test_identifiers_inside_words_are_ignored():
    assert numeric_issues("예: !@abcde12345 로 표시", ["표시법: !@abcde12345"]) == []
    assert numeric_issues("로그인 화면(LOGIN-001)으로 이동", ["로그인화면(LOGIN-001)으로이동"]) == []
