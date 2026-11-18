# [decomposer/numbers.py]
# - 담당 파트: 백엔드 분석 파이프라인 / 요구사항 추출 사후 검증
# - 작성자: 정래원
# - 페이지 개요: 추출된 조건 문장의 숫자·단위·비교 표현을 원문 인용과 규칙으로 대조한다.
# - 주요 기능:
#   1) 숫자와 단위를 뽑아 단위 동의어를 하나로 맞춘다 (자·자리·글자 → 문자 수)
#   2) 조건 문장에만 있는 수치, 같은 숫자에 다른 단위를 찾는다 (50자 → 100자, 50자 → 50바이트)
#   3) 같은 수치의 비교 방향이 원문과 반대인지 찾는다 (이하 → 이상)
# 모델 인용이 원문에 존재하는지는 quotes.py가 확인한다. 여기서는 인용은 맞는데
# 조건 문장을 쓰면서 숫자가 바뀐 경우를 잡는다. 판단이 애매하면 검토로 넘길 뿐 조건을 고치지 않는다.
import re

# 긴 표기를 먼저 둬야 '자리'가 '자'로 잘리지 않는다.
_UNITS = [
    ('메가바이트', 'MB'), ('메가', 'MB'), ('MB', 'MB'), ('KB', 'KB'), ('GB', 'GB'),
    ('바이트', 'byte'), ('bytes', 'byte'), ('byte', 'byte'),
    ('자리', 'char'), ('글자', 'char'), ('문자', 'char'), ('자', 'char'),
    ('시간', 'hour'), ('초', 'sec'), ('분', 'min'), ('일', 'day'), ('개월', 'month'),
    ('페이지', 'page'), ('개', 'count'), ('건', 'count'), ('회', 'times'), ('번', 'nth'),
    ('명', 'person'), ('%', 'percent'), ('px', 'px'),
]
_UNIT_RE = '|'.join(re.escape(u) for u, _ in _UNITS)
_TOKEN = re.compile(rf'(?<![0-9A-Za-z.])(\d{{1,3}}(?:,\d{{3}})+|\d+(?:\.\d+)?)\s*({_UNIT_RE})?', re.IGNORECASE)
_UNIT_OF = {u.lower(): n for u, n in _UNITS}

# 같은 뜻의 비교 표현을 한 묶음으로 둔다. 문장을 다듬으며 표현이 바뀌어도 방향이 같으면 통과한다.
_AFTER = [
    ('at_most', ('이하', '까지', '이내', '넘지않', '넘을수없', '넘어서는안')),
    ('over', ('초과', '넘어가', '넘으면', '넘는', '넘을경우', '넘을시')),
    ('at_least', ('이상',)),
    ('under', ('미만', '보다작', '보다적')),
]
_BEFORE = [('at_most', '최대'), ('at_least', '최소')]


def _compact(text):
    return re.sub(r'\s+', '', text)


def _value(raw):
    number = raw.replace(',', '')
    return number.rstrip('0').rstrip('.') if '.' in number else str(int(number))


def numeric_tokens(text):
    """[(값, 정규화 단위 또는 None, 비교 방향 또는 None, 원래 표기)]"""
    out = []
    for m in _TOKEN.finditer(text):
        unit = _UNIT_OF.get((m.group(2) or '').lower())
        after = _compact(text[m.end():m.end() + 12])
        before = _compact(text[max(0, m.start() - 6):m.start()])
        direction = next((name for name, words in _AFTER if after.startswith(words)), None)
        if direction is None:
            direction = next((name for name, word in _BEFORE if before.endswith(word)), None)
        out.append((_value(m.group(1)), unit, direction, m.group(0).strip()))
    return out


def numeric_issues(statement, quotes):
    """조건 문장의 수치가 원문 인용과 어긋나는 사유 목록. 빈 목록이면 통과."""
    source = [t for q in quotes for t in numeric_tokens(q)]
    issues = []
    for value, unit, direction, raw in numeric_tokens(statement):
        same_value = [t for t in source if t[0] == value]
        if not same_value:
            issues.append(f"수치 '{raw}'가 원문 인용에 없음")
            continue
        # 단위를 생략해 쓴 경우(25, 50, 100 중 선택)는 숫자만 맞으면 통과시킨다.
        same_unit = [t for t in same_value if unit is None or t[1] is None or t[1] == unit]
        if not same_unit:
            found = ', '.join(dict.fromkeys(t[3] for t in same_value))
            issues.append(f"'{raw}'의 단위가 원문({found})과 다름")
            continue
        directions = {t[2] for t in same_unit}
        if direction and None not in directions and direction not in directions:
            issues.append(f"'{raw}' 뒤의 비교 표현이 원문과 다름")
    return list(dict.fromkeys(issues))
