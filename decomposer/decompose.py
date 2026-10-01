"""요구사항을 판정 단위인 하위 조건으로 나누고, 조건마다 verifiable을 붙인다.

LLM은 조건 문장을 쓰지만, 조건마다 기획서 원문을 그대로 인용하게 하고 그 인용을
코드가 원문과 대조한다. 매칭에서 LLM이 chunk_id를 고르기만 하는 것(원칙1)과 같은
발상이다. 원문에 없는 인용은 지어낸 조건 의심, 어느 인용에도 안 걸린 원문은 누락
의심으로 검수 큐에 남긴다.
"""

from dataclasses import dataclass, field

from decomposer.quotes import StrippedText, locate_quotes, uncovered_spans
from llm import LLMClient
from matcher.data import Requirement

# 프롬프트를 바꾸면 올린다. decompose_run에 기록된다.
PROMPT_VERSION = "decompose-v3"

# verifiable 판단 기준. 분해와 재분류(scripts/reclassify_verifiable.py)가 같은 문장을 쓴다.
# v3: 렌더링 결과로만 드러나는 위치·배치와, CSS가 상태에 따라 그리는 모양 변화(토글 스위치 손잡이 이동 등)를
#     not_statically_verifiable로 옮겼다. 코드에 속성·클래스가 있어도 실제로 어떻게 그려지는지는 화면을 봐야 안다.
VERIFIABLE_CRITERIA = """\
verifiable (소스코드를 읽는 것만으로 확인할 수 있는가)
- not_statically_verifiable: 화면을 봐야 알 수 있는 조건.
  - 시각 스타일: 색상·정렬·크기·높이·폰트.
  - 화면상 위치·배치: 요소가 다른 요소의 어느 쪽에 놓이는가 (예: 입력창 오른쪽 아래에 글자수가 표시된다).
  - CSS가 상태에 따라 그리는 모양 변화: 토글 스위치를 클릭하면 손잡이가 좌우로 이동한다, 스위치의 왼쪽·오른쪽이
    각각 무엇을 뜻하는가처럼 체크 상태가 화면에 어떻게 그려지는지에 관한 것.
  - 브라우저나 OS가 제공하는 동작: 날짜 선택 캘린더가 열리고 닫힘, 파일 탐색기가 열림.
  - 성능·시간.
- code: 그 밖의 것. 요소가 있는지와 표시 조건(버튼, 로고 이미지, 아이콘이 있는가), 문구, 값·길이 제한,
  입력 검증, 버튼 활성화 조건, 화면 이동, 확인 팝업이 있는지와 그 문구, 데이터 조회·저장 조건, 권한.
- 클릭 등 사용자 조작에 따라 스크립트가 값이나 속성을 바꾸는 동작(비밀번호 보이기/가리기, 아이콘 이미지 교체)은
  이벤트 처리 코드로 확인하므로 code다. 비밀번호가 ●처럼 가려지는지(type=password)·그대로 보이는지(type=text)와
  그때 어떤 아이콘(감은눈/뜬눈)이 나오는지도 속성과 이미지 경로로 정해지므로 code다.
- 조건이 "무엇이 표시되는가"(어떤 글자·문구·아이콘·값)와 "어떻게 보이는가"를 함께 말하면 code로 둔다.
  예: 이름의 첫 글자가 원 안에 표시된다 → 어떤 글자를 넣는지는 코드로 확인하므로 code.
  아이콘 그림의 모양 자체, 원·박스의 모양 자체만 다루는 조건일 때만 not_statically_verifiable이다.
- verifiable_reason에 판단 이유를 한 문장으로 쓴다.
"""

INSTRUCTIONS = """\
당신은 화면 기획서의 요구사항을 검증 가능한 하위 조건으로 나누는 일을 맡는다.
나눈 조건 하나하나가 이후 소스코드와 대조되어 구현 여부가 판정된다.

조건 나누기
- 조건 하나는 코드에서 하나의 사실로 확인할 수 있는 단위다. 예: 요소가 표시되는가, 입력 길이 제한,
  입력 검증 규칙, 버튼 활성화 조건, 특정 상황의 에러 메시지, 화면 이동, 확인 팝업과 그 문구, 목록 정렬·필터 조건.
- 에러 메시지는 그 메시지가 나오는 상황과 묶어 한 조건으로 둔다.
- "다음과 같다", 소제목 같은 도입 문장은 따로 조건으로 만들지 말고, 그 아래 조건들의 맥락으로 쓴다.
- 원문에 없는 조건·값을 추가하지 않는다. 해석이 필요한 부분도 원문 범위 안에서만 쓴다.

statement
- 띄어쓰기를 복원한 한국어 한 문장. 원문은 PDF에서 추출되어 띄어쓰기가 사라져 있다.
- 오타로 보이는 표현도 고치지 말고 원문 표현을 유지한다.

source_quotes
- 그 조건의 근거가 되는 원문 구절을 **그대로 복사**한다. 요약·바꿔쓰기·말줄임(…)을 하지 않는다.
  띄어쓰기는 달라도 된다(비교할 때 공백을 무시한다).
- 조건이 원문 여러 곳에 걸치면(예: 소제목 + 항목) 구절을 여러 개 넣는다.
- 원문의 모든 내용이 어떤 조건의 인용에든 들어가야 한다. 도입 문장·소제목은 관련 조건의 인용에 함께 넣는다.
  어느 인용에도 들어가지 않은 원문은 조건을 빠뜨린 것으로 간주된다.

""" + VERIFIABLE_CRITERIA + """
요청받은 요구사항 키마다 정확히 한 번씩 응답한다.
"""

SCHEMA = {
    "type": "object",
    "properties": {
        "requirements": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "requirement_key": {"type": "string"},
                    "conditions": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "statement": {"type": "string"},
                                "source_quotes": {"type": "array", "items": {"type": "string"}},
                                "verifiable": {"type": "string", "enum": ["code", "not_statically_verifiable"]},
                                "verifiable_reason": {"type": "string"},
                            },
                            "required": ["statement", "source_quotes", "verifiable", "verifiable_reason"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["requirement_key", "conditions"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["requirements"],
    "additionalProperties": False,
}

_VERIFIABLE = {"code", "not_statically_verifiable"}


def render_screen(screen_id: str, screen_name: str, reqs: list[Requirement]) -> str:
    lines = [f"화면 {screen_id} ({screen_name})의 요구사항이다. 각각을 하위 조건으로 나눠라.", ""]
    for r in reqs:
        lines.append(f"[{r.stable_key}]\n{r.body}\n")
    return "\n".join(lines)


@dataclass
class Condition:
    statement: str
    quotes: list[dict]  # [{quote, found}]
    verified: bool
    verifiable: str
    verifiable_reason: str


@dataclass
class ScreenDecomposition:
    screen_id: str
    conditions: dict[int, list[Condition]] = field(default_factory=dict)  # requirement_id -> 조건
    issues: list[tuple[str, int | None, dict]] = field(default_factory=list)
    error: str | None = None
    usage: dict = field(default_factory=dict)


def decompose_screen(client: LLMClient, screen_id: str, screen_name: str, reqs: list[Requirement]) -> ScreenDecomposition:
    resp = client.complete_json(
        instructions=INSTRUCTIONS, context="", prompt=render_screen(screen_id, screen_name, reqs), schema=SCHEMA,
    )
    out = ScreenDecomposition(screen_id, error=resp.error, usage=resp.usage)
    if resp.data is None:
        out.issues.append(("llm_error", None, {"screen_id": screen_id, "error": resp.error, "raw": resp.raw_text[:2000]}))
        return out

    by_key = {r.stable_key: r for r in reqs}
    for item in resp.data.get("requirements", []):
        req = by_key.get(item.get("requirement_key"))
        if req is None:
            out.issues.append(("unknown_requirement", None, {"requirement_key": item.get("requirement_key")}))
            continue
        body = StrippedText(req.body)
        conds: list[Condition] = []
        covered = [False] * len(body.text)  # 한 요구사항의 조건들이 공유
        for c in item.get("conditions", []):
            quotes = [q for q in c.get("source_quotes", []) if isinstance(q, str)]
            matches = locate_quotes(body, quotes, covered)
            verified = bool(matches) and all(m.found for m in matches)
            missing = [m.quote for m in matches if not m.found]
            if missing or not matches:
                out.issues.append(("quote_not_found", req.id, {
                    "statement": c.get("statement"), "quotes": missing or [],
                }))
            # 로컬 LLM은 enum을 강제하지 못한다(3.1). 값이 틀리면 조건은 남기되 판정 대상에 두고 검수로 보낸다.
            verifiable = c.get("verifiable")
            if verifiable not in _VERIFIABLE:
                verifiable = "code"
            conds.append(Condition(
                statement=c.get("statement", ""),
                quotes=[{"quote": m.quote, "found": m.found} for m in matches],
                verified=verified,
                verifiable=verifiable,
                verifiable_reason=c.get("verifiable_reason", ""),
            ))
        for gap in uncovered_spans(body, covered):
            out.issues.append(("uncovered_text", req.id, {"text": gap}))
        out.conditions[req.id] = conds

    for r in reqs:
        if r.id not in out.conditions:
            out.issues.append(("omitted", r.id, {}))
    return out
