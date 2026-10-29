"""화면(소주제) 하나의 요구사항·조건으로 단계별 시험 시나리오를 만든다 (LLM 호출).

예상결과는 기획서에서만 도출한다. 코드와 판정은 보내지 않는다 — 코드에 맞춰 예상결과를 바꾸면
시험이 구현을 검증하지 못한다. 코드 기반 예측(prediction)은 판정 결과로 규칙에 따라 따로 붙인다
(adapters/v2_testgen.py).

조건은 stable_key(LOGIN-001-2-C1)로 주고받는다. 스택이 달라도 기획서와 조건 분해가 같으므로
같은 결과를 두 프로젝트에 쓸 수 있다. LLM은 조건 키를 후보 enum에서 고르기만 한다 (원칙 1).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from llm import LLMClient

PROMPT_VERSION = "testgen-v2-design"

INSTRUCTIONS = """\
당신은 화면 기획서로 수동 시험 사양서를 작성하는 QA 엔지니어다.
화면 하나의 요구사항과 하위 조건(수락 조건)이 주어진다. 사람이 브라우저에서 따라 할 시험 시나리오를 만든다.

시나리오와 단계
- 시나리오 하나는 시험번호 하나다. 같은 화면 흐름에서 이어지는 확인은 한 시나리오의 여러 단계로 묶는다.
- 단계마다 action(사람이 하는 동작 한 가지), expected(그 동작 직후 확인할 결과)를 쓴다.
- type: positive(정상 흐름), negative(잘못된 입력·거부), boundary(수치 경계: 최대 길이, 최대 크기 등).
- 같은 절차를 반복하는 중복 시나리오를 만들지 않는다.

예상결과
- 예상결과는 주어진 기획서 원문과 조건에서만 도출한다. 일반적인 웹 관행으로 결과를 지어내지 않는다.
- 원문이 잘렸거나 서로 모순되면 결과를 추정하지 말고, 확인 가능한 범위까지만 쓰고 warnings에 남긴다.
  code: SOURCE_TRUNCATED(잘림), SOURCE_CONTRADICTION(모순), AMBIGUOUS_SPEC(해석이 여러 가지).
- 안내 문구가 원문에 있으면 그 문구를 그대로 쓴다.
- 다른 화면으로 이동하는 조건은 이동한 화면이 표시되는지까지만 확인한다.

시험 데이터
- account: 필요한 사용자 역할만 쓴다(예: "로그인한 일반 회원", "관리자"). 필요 없으면 null.
  실제 이메일·비밀번호 같은 자격정보를 만들지 않는다.
- input: 입력값. 경계 시험은 경계 바로 안쪽과 바깥쪽 값을 구체적으로 쓴다(예: "제목 50자", "제목 51자").
  단위가 모호한 수치(MB 등)는 경계 정확값 대신 명확히 안쪽인 값을 쓰고 warnings에 단위 확인 필요를 남긴다.
- notes: 준비 방법 등 보충 설명. 없으면 빈 문자열.

조건 연결
- 각 단계의 condition_keys에는 그 단계가 확인하는 조건 키만 넣는다. 목록에 없는 키를 쓰지 않는다.
- 모든 조건이 최소 한 단계에 연결되게 한다. 수동 시험으로도 확인할 수 없는 조건만 uncovered에 이유와 함께 넣는다.
  색상·배치처럼 눈으로 확인하는 조건도 시험할 수 있으므로 단계로 만든다.

금지
- 실제 수행 결과, 통과/실패, O/X, 수행 날짜를 쓰지 않는다.
- 소스코드나 구현 상태를 추측해 쓰지 않는다.
"""


def build_schema(condition_keys: list[str], design_items: list[dict] | None = None) -> dict:
    key = {"type": "string", "enum": condition_keys}
    warning = {"type": "object", "properties": {"code": {"type": "string"}, "message": {"type": "string"}},
               "required": ["code", "message"], "additionalProperties": False}
    step = {
        "type": "object",
        "properties": {
            "action": {"type": "string"},
            "expected": {"type": "string"},
            "account": {"anyOf": [{"type": "string"}, {"type": "null"}]},
            "input": {"type": "string"},
            "notes": {"type": "string"},
            "condition_keys": {"type": "array", "items": key},
        },
        "required": ["action", "expected", "account", "input", "notes", "condition_keys"],
        "additionalProperties": False,
    }
    scenario = {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "type": {"type": "string", "enum": ["positive", "negative", "boundary"]},
            "preconditions": {"type": "string"},
            "steps": {"type": "array", "items": step},
            "assumptions": {"type": "array", "items": {"type": "string"}},
            "warnings": {"type": "array", "items": warning},
        },
        "required": ["title", "type", "preconditions", "steps", "assumptions", "warnings"],
        "additionalProperties": False,
    }
    uncovered = {"type": "object", "properties": {"condition_key": key, "reason": {"type": "string"}},
                 "required": ["condition_key", "reason"], "additionalProperties": False}
    schema = {
        "type": "object",
        "properties": {"scenarios": {"type": "array", "items": scenario},
                       "uncovered": {"type": "array", "items": uncovered}},
        "required": ["scenarios", "uncovered"],
        "additionalProperties": False,
    }
    if design_items is not None:
        intent = {"type": "string", "enum": [item['id'] for item in design_items]}
        step['properties']['design'] = {
            'type': 'object', 'additionalProperties': False,
            'properties': {'intent_ids': {'type': 'array', 'items': intent},
                           'rationale': {'type': 'string'}, 'input_rationale': {'type': 'string'},
                           'basis': {'type': 'string', 'enum': ['specification', 'review_needed']}},
            'required': ['intent_ids', 'rationale', 'input_rationale', 'basis']}
        step['required'].append('design')
        scenario['properties']['rationale'] = {'type': 'string'}
        scenario['required'].append('rationale')
        schema['properties']['uncovered_intents'] = {
            'type': 'array', 'items': {'type': 'object', 'additionalProperties': False,
                                     'properties': {'intent_id': intent, 'reason': {'type': 'string'}},
                                     'required': ['intent_id', 'reason']}}
        schema['required'].append('uncovered_intents')
    return schema


@dataclass(frozen=True)
class SpecRequirement:
    stable_key: str
    original_text: str
    review_reasons: tuple[str, ...]
    conditions: tuple[dict, ...]  # {stable_key, text, category, verifiable, constraint, ambiguity}


def render(subtopic_title: str, requirements: list[SpecRequirement]) -> str:
    lines = [f"화면: {subtopic_title}", ""]
    for r in requirements:
        lines.append(f"<requirement key=\"{r.stable_key}\">")
        lines.append(f"기획서 원문: {r.original_text.strip()}")
        for reason in r.review_reasons:
            lines.append(f"검토 메모: {reason}")
        for c in r.conditions:
            extra = []
            if c.get("constraint"):
                extra.append("수치: " + json.dumps(c["constraint"], ensure_ascii=False))
            if c.get("ambiguity"):
                extra.append("모호: " + "; ".join(map(str, c["ambiguity"])))
            if c.get("verifiable") == "not_statically_verifiable":
                extra.append("코드만으로 확인 어려움(실행해서 확인)")
            suffix = f" ({' / '.join(extra)})" if extra else ""
            lines.append(f"- {c['stable_key']} [{c['category']}] {c['text']}{suffix}")
        lines.append("</requirement>")
        lines.append("")
    return "\n".join(lines)


def input_hash(model: str, subtopic_title: str, requirements: list[SpecRequirement]) -> str:
    payload = json.dumps([model, PROMPT_VERSION, INSTRUCTIONS, render(subtopic_title, requirements)],
                         ensure_ascii=False)
    return hashlib.sha256(payload.encode()).hexdigest()


def validate(result: dict, condition_keys: list[str]) -> tuple[list[dict], list[dict]]:
    """LLM 출력을 다시 검사한다. 반환: (시나리오 목록, 경고 목록). 단계 없는 시나리오는 버린다."""
    known = set(condition_keys)
    warnings = []
    scenarios = []
    for s in result.get("scenarios", []):
        steps = []
        for st in s["steps"]:
            keys = [k for k in dict.fromkeys(st["condition_keys"]) if k in known]
            if not keys:
                warnings.append({"code": "STEP_WITHOUT_CONDITION",
                                 "message": f"'{s['title']}'의 단계 '{st['action'][:40]}'가 조건과 연결되지 않아 뺐다"})
                continue
            steps.append({**st, "condition_keys": keys})
        if steps:
            scenarios.append({**s, "steps": steps})
    covered = {k for s in scenarios for st in s["steps"] for k in st["condition_keys"]}
    explained = {u["condition_key"]: u["reason"] for u in result.get("uncovered", []) if u["condition_key"] in known}
    for k in condition_keys:
        if k in covered:
            continue
        reason = explained.get(k, "시험 단계에 연결되지 않았다")
        warnings.append({"code": "CONDITION_NOT_COVERED", "message": f"{k}: {reason}"})
    return scenarios, warnings


def generate(client: LLMClient, subtopic_title: str, requirements: list[SpecRequirement],
             design_items: list[dict] | None = None) -> dict:
    """반환: {scenarios, warnings, usage, model}. 실패하면 RuntimeError."""
    keys = [c["stable_key"] for r in requirements for c in r.conditions]
    instructions, prompt = INSTRUCTIONS, render(subtopic_title, requirements)
    if design_items is not None:
        instructions += '''
시험 설계 설명
- 아래 시험 관점 계획은 규칙 기반 제안이다. 명세에 없는 결과나 정책을 만들라는 지시가 아니다.
- 시나리오 rationale에는 확인할 위험·조건과 이 흐름을 고른 이유를 1~2문장으로 쓴다.
- 단계 design.rationale에는 연결 조건을 왜 이 절차로 확인하는지, input_rationale에는 입력값·역할 선정 근거를 간결하게 쓴다.
  입력이 필요 없으면 그 이유를 쓴다. 내부 사고 과정을 쓰지 않고 사용자가 원문과 비교할 수 있는 설계 설명만 쓴다.
- design.intent_ids는 이 단계가 실제 확인하는 관점만 선택한다. 그 관점들의 조건 키 집합은 condition_keys와 일치해야 한다.
- 명세에 근거한 설명이면 basis=specification이다. 예상 동작·역할·입력값에 원문 밖의 해석이 필요하면
  basis=review_needed로 두고 무엇을 확인해야 하는지 설명한다. 가정은 assumptions에도 남긴다.
- 각 관점은 구별되는 확인이어야 한다. 하나의 동작으로 서로 반대되는 허용·거부 시험을 모두 했다고 쓰지 않는다.
- 시험으로 만들지 않은 관점은 uncovered_intents에 이유를 남긴다. 작성한 관점을 미포함으로 중복 보고하지 않는다.
- 로그인·화면 열기 등 검증 전 준비는 preconditions에 적는다. 검증 조건과 무관한 단계를 억지로 연결하지 않는다.
'''
        prompt += '\n권장 시험 관점(명세 원문을 기준으로 검토):\n' + '\n'.join(
            f"- {i['id']} | 조건 {i['condition_key']} | {i['label']} | {i['reason']}" for i in design_items)
    res = client.complete_json(instructions=instructions, context="", prompt=prompt,
                               schema=build_schema(keys, design_items))
    if res.error or res.data is None:
        raise RuntimeError(f"{subtopic_title}: 시험 생성 실패 ({res.error})")
    scenarios, warnings = validate(res.data, keys)
    return {"scenarios": scenarios, "warnings": warnings, "usage": res.usage, "model": res.model,
            "uncovered_intents": res.data.get('uncovered_intents', [])}
