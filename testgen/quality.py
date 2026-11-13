"""저장된 시험 사양서가 요구사항을 실제로 검증하는지 규칙으로 점검한다 (LLM 호출 없음, #106).

design.py는 단계가 '경계값 관점'에 연결됐는지만 센다. 그래서 최대 50자 조건에 20자만 넣는 단계도
연결만 되어 있으면 통과한다. 여기서는 단계의 실제 내용(절차·입력값·기대 결과)을 본다.

  boundary   수치 제한 조건(최대 50자, 8자리 이상, 50MB 이하)에 연결된 단계들이
             경계 정확값과 경계 바깥 값을 실제로 다루는가
  duplicate  같은 조건에 연결된 같은 절차·입력·기대 결과가 반복되는가
  unquoted   기대 결과에 따옴표로 적힌 안내 문구가 기획서 원문에 없는가 (지어낸 문구 의심)

규칙으로 확정할 수 없는 것은 '의심'으로만 표시한다. 사람이 시험 설계 검토에서 판단한다.
"""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field

QUALITY_VERSION = 'quality-check-v1'

_UNIT = r'(자리|글자|자|MB|KB|GB)'
_LIMIT = re.compile(r'(\d+)\s*' + _UNIT)
_MIN_WORDS = re.compile(r'이상|최소|상회|미달')
_MAX_WORDS = re.compile(r'이하|이내|까지|최대|초과|넘어가면|넘으면')
_VALUE = re.compile(r'(\d+(?:\.\d+)?)\s*' + _UNIT)
_EMPTY = re.compile(r'빈 ?값|비워|비운|공백|입력하지 ?않|미입력|0자')
_QUOTED = re.compile(r'"([^"]{4,})"|“([^”]{4,})”')


def _unit(u: str) -> str:
    return '자' if u in ('자리', '글자', '자') else u.upper()


@dataclass(frozen=True)
class Limit:
    value: int
    unit: str          # '자' | 'MB' | 'KB' | 'GB'
    kind: str          # 'max' | 'min'

    def required(self) -> dict[str, str]:
        """경계 점검에 필요한 값과 설명. 바깥 값은 경계를 넘는 아무 값이나 인정한다."""
        if self.kind == 'max':
            return {'exact': f'{self.value}{self.unit} (정확값)', 'outside': f'{self.value}{self.unit} 초과 (바깥)'}
        return {'exact': f'{self.value}{self.unit} (정확값)', 'outside': f'{self.value}{self.unit} 미만 (바깥)'}


def parse_limit(text: str) -> Limit | None:
    m = _LIMIT.search(text)
    if not m:
        return None
    if _MIN_WORDS.search(text):
        kind = 'min'
    elif _MAX_WORDS.search(text):
        kind = 'max'
    else:
        return None
    return Limit(int(m.group(1)), _unit(m.group(2)), kind)


def step_text(step: dict) -> str:
    data = step.get('test_data') or {}
    return ' '.join(str(x) for x in (step.get('action'), data.get('input'), data.get('notes'), step.get('expected')) if x)


def covered(limit: Limit, texts: list[str]) -> set[str]:
    found: set[str] = set()
    for text in texts:
        for raw, unit in _VALUE.findall(text):
            if _unit(unit) != limit.unit:
                continue
            v = float(raw)
            if v == limit.value:
                found.add('exact')
            elif (limit.kind == 'max' and v > limit.value) or (limit.kind == 'min' and v < limit.value):
                found.add('outside')
        if limit.kind == 'min' and limit.value == 1 and _EMPTY.search(text):
            found.add('outside')  # 1글자 이상의 바깥은 빈 값
    return found


def _norm(s) -> str:
    return re.sub(r'\s+', '', str(s or ''))


@dataclass
class Report:
    boundary: list[dict] = field(default_factory=list)       # 요구사항·수치 제한별 경계 점검 결과
    duplicates: list[dict] = field(default_factory=list)     # 같은 시험 안 반복 단계, 통째로 같은 시험
    invented: list[dict] = field(default_factory=list)       # 기획서 어디에도 없는 안내 문구
    borrowed: list[dict] = field(default_factory=list)       # 연결된 요구사항이 아닌 다른 요구사항의 문구
    test_count: int = 0
    step_count: int = 0

    def summary(self) -> dict:
        return {
            'version': QUALITY_VERSION,
            'tests': self.test_count, 'steps': self.step_count,
            'boundary_limits': len(self.boundary),
            'boundary_missing': sum(1 for b in self.boundary if b['missing']),
            'boundary_untested': sum(1 for b in self.boundary if not b['step_count']),
            'duplicates': len(self.duplicates),
            'invented_messages': len(self.invented),
            'borrowed_messages': len(self.borrowed),
        }


def check(requirements: list[dict], tests: list[dict]) -> Report:
    """requirements: v2 요구사항 리소스(data), tests: 같은 사양서의 시험 리소스(data).

    경계값은 요구사항 단위로 본다. 한 요구사항이 '8자리 이상'과 '미달 시 에러'처럼 같은 제한을 여러
    조건으로 나눠 갖는 것이 보통이라, 조건 하나에 연결된 단계만 보면 멀쩡한 시험도 누락으로 잡힌다.
    """
    req_of, req_text = {}, {}
    for req in requirements:
        req_text[req['stable_key']] = _norm(req.get('original_text')) + ''.join(
            _norm(c.get('text')) for c in req.get('conditions', []))
        for c in req.get('conditions', []):
            req_of[c['id']] = req['stable_key']
    whole_spec = ''.join(req_text.values())

    report = Report(test_count=len(tests))
    steps_by_req: dict[str, list[dict]] = defaultdict(list)
    whole_tests: dict[tuple, list[str]] = defaultdict(list)
    for test in tests:
        title = test.get('title', '')
        in_test: dict[tuple, list[str]] = defaultdict(list)
        for step in test.get('steps', []):
            report.step_count += 1
            where = f"{title} / {step.get('step_key')}"
            linked = sorted({req_of[cid] for link in step.get('requirement_links', [])
                             for cid in link['condition_ids'] if cid in req_of})
            for key in linked:
                steps_by_req[key].append(step)
            data = step.get('test_data') or {}
            in_test[(_norm(step.get('action')), _norm(data.get('input')), _norm(step.get('expected')))].append(where)

            for groups in _QUOTED.findall(step.get('expected') or ''):
                raw = groups[0] or groups[1]
                message = _norm(raw)
                if not linked or any(message in req_text[k] for k in linked):
                    continue
                item = {'requirements': linked, 'message': raw, 'where': where}
                if message in whole_spec:
                    item['found_in'] = sorted(k for k, t in req_text.items() if message in t)
                    report.borrowed.append(item)
                else:
                    report.invented.append(item)
        for wheres in in_test.values():
            if len(wheres) > 1:
                report.duplicates.append({'kind': 'same_test', 'count': len(wheres), 'where': wheres})
        signature = tuple((tuple(sorted(cid for link in s.get('requirement_links', []) for cid in link['condition_ids'])),
                           _norm(s.get('action')), _norm((s.get('test_data') or {}).get('input')), _norm(s.get('expected')))
                          for s in test.get('steps', []))
        if signature:
            whole_tests[signature].append(title)
    for titles in whole_tests.values():
        if len(titles) > 1:
            report.duplicates.append({'kind': 'whole_test', 'count': len(titles), 'where': titles})

    for req in requirements:
        key = req['stable_key']
        limits = {limit for c in req.get('conditions', []) if (limit := parse_limit(c.get('text', '')))}
        for limit in sorted(limits, key=lambda l: (l.unit, l.value, l.kind)):
            steps = steps_by_req.get(key, [])
            found = covered(limit, [step_text(s) for s in steps])
            need = limit.required()
            report.boundary.append({'requirement': key, 'limit': f"{limit.kind} {limit.value}{limit.unit}",
                                    'step_count': len(steps), 'missing': [need[k] for k in need if k not in found]})
    return report
