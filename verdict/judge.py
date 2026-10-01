"""판정 순서 4~6단계: 이 화면 근거 코드를 보고 LLM이 조건별 상태를 정한다.

요구사항 하나 = LLM 호출 하나 (그 요구사항의 조건들이 같은 근거를 공유하므로).
화면의 근거 코드는 그 화면의 모든 요구사항 호출에서 똑같이 쓰이므로 context로 보내 캐시한다.
LLM은 근거를 후보(primary) 중에서 고르기만 한다 (원칙1). 출력은 형식이 맞아도
다시 검증하고, 통과하지 못한 조건은 needs_review로 내린다.
"""

import hashlib
import json
from dataclasses import dataclass, field

from llm import LLMClient
from matcher.data import ChunkRow
from verdict.rules import LLM_POLICY, LLM_STATUSES

PROMPT_VERSION = "judge-v5"

INSTRUCTIONS = """\
당신은 화면 기획서의 요구사항 조건이 소스코드에 구현되었는지 판정한다.
요구사항 하나와 그 하위 조건들, 그리고 근거 코드 조각이 주어진다. 조건마다 상태를 정한다.

""" + LLM_POLICY + """
출력
- 조건 id마다 정확히 한 번씩 응답한다.
- evidence_chunk_ids: 판정 근거가 된 <primary> 조각의 id만. <reference> id나 목록에 없는 id를 쓰지 않는다.
  not_found이면 빈 배열이어도 된다.
- reasoning: 한두 문장. 근거 코드의 어떤 부분을 보고 판단했는지 쓴다.
"""

SCHEMA = {
    "type": "object",
    "properties": {
        "conditions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "condition_id": {"type": "integer"},
                    "status": {"type": "string", "enum": list(LLM_STATUSES)},
                    "evidence_chunk_ids": {"type": "array", "items": {"type": "integer"}},
                    "reasoning": {"type": "string"},
                    "message_match": {"type": "string", "enum": ["match", "differs", "not_applicable"]},
                    "server_validation": {"type": "string", "enum": ["present", "missing", "not_applicable"]},
                    "spec_suspect": {"type": "string"},
                },
                "required": ["condition_id", "status", "evidence_chunk_ids", "reasoning",
                             "message_match", "server_validation", "spec_suspect"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["conditions"],
    "additionalProperties": False,
}


@dataclass(frozen=True)
class ConditionInput:
    id: int
    statement: str
    quotes: tuple[str, ...]


@dataclass
class ConditionJudgment:
    status: str
    reason_kind: str  # llm | llm_invalid_output
    reasoning: str
    evidence: list[int] = field(default_factory=list)
    message_match: str | None = None
    server_validation: str | None = None
    spec_suspect: str | None = None


@dataclass(frozen=True)
class ScreenContext:
    """한 화면의 판정 공통 입력. 이 화면 근거 코드는 순서까지 고정해야 캐시가 맞는다."""
    screen_id: str
    screen_name: str
    primary: tuple[int, ...]


def input_hash(model: str, screen: ScreenContext, cond: ConditionInput, own: tuple[int, ...],
               reference: tuple[int, ...], chunks: dict[int, ChunkRow]) -> str:
    """같은 조건·같은 근거 코드·같은 프롬프트·같은 모델이면 같은 값. 판정 재사용의 키."""
    h = hashlib.sha256()
    payload = {
        "prompt": PROMPT_VERSION, "model": model, "screen": [screen.screen_id, screen.screen_name],
        "statement": cond.statement, "quotes": list(cond.quotes), "own": sorted(own),
        "primary": [[i, chunks[i].content] for i in sorted(screen.primary)],
        "reference": [[i, chunks[i].content] for i in sorted(reference)],
    }
    h.update(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8"))
    return h.hexdigest()


def _render_chunk(tag: str, c: ChunkRow, note: str = "") -> str:
    return (f'<{tag} id="{c.id}" file="{c.file_path}" symbol="{c.symbol_fqn or ""}"{note}>\n'
            f"{c.content}\n</{tag}>")


def render_context(screen: ScreenContext, chunks: dict[int, ChunkRow]) -> str:
    parts = [f"화면 {screen.screen_id} ({screen.screen_name})에서 쓰이는 근거 코드:"]
    parts += [_render_chunk("primary", chunks[i]) for i in screen.primary]
    return "\n".join(parts)


def render(screen: ScreenContext, req_key: str, req_body: str, conds: list[ConditionInput], own: tuple[int, ...],
           reference: tuple[int, ...], chunks: dict[int, ChunkRow], other_screens: dict[int, list[str]]) -> str:
    # 화면 이름이 있어야 LLM이 "삭제 팝업 화면인데 문장은 등록 화면 이야기"처럼 기획서 오류를 알아챈다 (Q7).
    parts = [f"화면 {screen.screen_id} ({screen.screen_name})의 요구사항 [{req_key}] 원문:\n{req_body}\n", "하위 조건:"]
    for c in conds:
        parts.append(f"- id {c.id}: {c.statement}")
    parts.append(f"\n이 요구사항에 매칭된 근거 id (먼저 볼 것): {list(own)}")
    parts.append("그 밖의 <primary>도 같은 화면 코드라 근거로 쓸 수 있다.")
    if reference:
        parts.append("\n참고: 다른 화면에서만 쓰이는 코드 (이 화면의 구현 근거가 아님):")
        parts += [_render_chunk("reference", chunks[i], f' screens="{",".join(other_screens.get(i, []))}"')
                  for i in reference]
    return "\n".join(parts)


def judge_requirement(client: LLMClient, screen: ScreenContext, req_key: str, req_body: str,
                      conds: list[ConditionInput], own: tuple[int, ...], reference: tuple[int, ...],
                      chunks: dict[int, ChunkRow],
                      other_screens: dict[int, list[str]]) -> tuple[dict[int, ConditionJudgment], dict, str | None]:
    resp = client.complete_json(
        instructions=INSTRUCTIONS, context=render_context(screen, chunks),
        prompt=render(screen, req_key, req_body, conds, own, reference, chunks, other_screens), schema=SCHEMA,
    )
    out: dict[int, ConditionJudgment] = {}
    if resp.data is None:
        for c in conds:
            out[c.id] = ConditionJudgment("needs_review", "llm_invalid_output", f"LLM 호출 실패: {resp.error}")
        return out, resp.usage, resp.error

    allowed_chunks = set(screen.primary)
    wanted = {c.id for c in conds}
    for item in resp.data.get("conditions", []):
        cid = item.get("condition_id")
        if cid not in wanted or cid in out:
            continue
        status = item.get("status")
        ids = [i for i in item.get("evidence_chunk_ids", []) if isinstance(i, int)]
        bad = [i for i in ids if i not in allowed_chunks]
        # 로컬 LLM은 enum·후보를 강제하지 못한다(3.1). 후보 밖 id나 모르는 상태면 사람에게 넘긴다.
        if status not in LLM_STATUSES or bad:
            why = f"후보에 없는 근거 id {bad}" if bad else f"알 수 없는 상태 {status!r}"
            out[cid] = ConditionJudgment("needs_review", "llm_invalid_output",
                                         f"LLM 출력 검증 실패: {why}. 원래 판단: {item.get('reasoning', '')}")
            continue
        out[cid] = ConditionJudgment(
            status=status, reason_kind="llm", reasoning=item.get("reasoning", ""),
            evidence=list(dict.fromkeys(ids)),
            message_match=item.get("message_match"), server_validation=item.get("server_validation"),
            spec_suspect=item.get("spec_suspect") or None,
        )
    for c in conds:
        if c.id not in out:
            out[c.id] = ConditionJudgment("needs_review", "llm_invalid_output", "LLM이 이 조건을 빠뜨렸다")
    return out, resp.usage, None
