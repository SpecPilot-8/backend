"""후보 청크 중 요구사항별 근거를 LLM이 고르게 한다 (원칙1).

두 매칭 방식(full_context / screen_scope)은 후보 집합을 어떻게 만드느냐만 다르고
선택 단계는 이 모듈을 공유한다. 그래야 둘의 차이가 후보 집합 차이로만 설명된다.
"""

from dataclasses import dataclass, field

from llm import LLMClient
from matcher.data import ChunkRow, Requirement

# 프롬프트를 바꾸면 올린다. match_run에 기록되어 결과 비교 시 기준이 된다.
PROMPT_VERSION = "select-v1"

INSTRUCTIONS = """\
당신은 화면 기획서의 요구사항과 소스코드를 대조하는 도구의 근거 검색 단계를 맡는다.

아래 <chunks>는 대조 대상 코드를 미리 잘라 둔 조각이다. 각 조각에는 id가 있다.
사용자가 한 화면의 요구사항 목록을 주면, 요구사항마다 그 요구사항과 관련된 코드 조각의 id를 고른다.

규칙
- 반드시 <chunks>에 있는 id만 쓴다. 파일 경로나 줄 번호를 만들어내지 않는다.
- 요구사항 하나의 근거는 여러 곳에 흩어져 있는 것이 정상이다. 화면 마크업, 화면 측 검증 스크립트,
  서버 라우트/컨트롤러, 서비스, 설정 파일 등 관련된 조각을 모두 고른다. 가장 관련 깊은 것부터 나열한다.
- 구현 여부는 판단하지 않는다. 기획서와 값·동작이 다르거나 일부만 구현되었거나, 버튼만 있고 동작이 없는
  경우에도 그 요구사항을 다루는 코드라면 고른다. 판정은 다음 단계가 한다.
- 관련 코드가 정말 없으면 빈 배열로 둔다. 억지로 채우지 않는다.
- 기획서 원문은 PDF에서 추출되어 띄어쓰기가 사라져 있고 오타가 있을 수 있다.
- 요청받은 요구사항 키마다 정확히 한 번씩 응답한다.
"""

SCHEMA = {
    "type": "object",
    "properties": {
        "matches": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "requirement_key": {"type": "string"},
                    "chunk_ids": {"type": "array", "items": {"type": "integer"}},
                },
                "required": ["requirement_key", "chunk_ids"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["matches"],
    "additionalProperties": False,
}


def render_chunks(chunks: list[ChunkRow]) -> str:
    # 줄 번호는 보여주지 않는다. LLM이 고를 대상은 id뿐이고, 줄 번호가 보이면
    # 응답에 좌표를 섞어 쓸 여지만 생긴다.
    parts = ["<chunks>"]
    for c in chunks:
        parts.append(
            f'<chunk id="{c.id}" type="{c.chunk_type}" file="{c.file_path}" symbol="{c.symbol_fqn or ""}">\n'
            f"{c.content}\n</chunk>"
        )
    parts.append("</chunks>")
    return "\n".join(parts)


def render_screen(screen_id: str, screen_name: str, reqs: list[Requirement]) -> str:
    lines = [f"화면 {screen_id} ({screen_name})의 요구사항이다.", ""]
    for r in reqs:
        lines.append(f"[{r.stable_key}]\n{r.body}\n")
    lines.append("각 요구사항 키마다 관련 chunk id를 골라라.")
    return "\n".join(lines)


@dataclass
class ScreenSelection:
    screen_id: str
    candidate_count: int
    # requirement_id -> 순서 있는 chunk_id 목록 (검증 통과분만)
    picks: dict[int, list[int]] = field(default_factory=dict)
    # (kind, requirement_id | None, detail)
    issues: list[tuple[str, int | None, dict]] = field(default_factory=list)
    error: str | None = None
    raw_output: str = ""
    usage: dict = field(default_factory=dict)


def select_for_screen(
    client: LLMClient,
    screen_id: str,
    screen_name: str,
    reqs: list[Requirement],
    candidates: list[ChunkRow],
    context: str | None = None,
) -> ScreenSelection:
    """context를 넘기면 그대로 쓴다 (여러 화면이 같은 후보 목록을 공유할 때 캐시 적중용)."""
    context = context if context is not None else render_chunks(candidates)
    resp = client.complete_json(
        instructions=INSTRUCTIONS,
        context=context,
        prompt=render_screen(screen_id, screen_name, reqs),
        schema=SCHEMA,
    )
    sel = ScreenSelection(screen_id, len(candidates), error=resp.error,
                          raw_output=resp.raw_text, usage=resp.usage)
    if resp.data is None:
        return sel

    # 로컬 LLM은 스키마·enum을 강제하지 못하므로(3.1) 형식이 맞아도 내용을 다시 검증한다.
    allowed = {c.id for c in candidates}
    by_key = {r.stable_key: r for r in reqs}
    for m in resp.data.get("matches", []):
        key = m.get("requirement_key")
        req = by_key.get(key)
        if req is None:
            sel.issues.append(("unknown_requirement", None, {"requirement_key": key}))
            continue
        ids = [i for i in m.get("chunk_ids", []) if isinstance(i, int)]
        invalid = [i for i in ids if i not in allowed]
        if invalid:
            sel.issues.append(("invalid_chunk_id", req.id, {"chunk_ids": invalid}))
        valid = list(dict.fromkeys(i for i in ids if i in allowed))  # 순서 유지 중복 제거
        sel.picks.setdefault(req.id, [])
        sel.picks[req.id].extend(i for i in valid if i not in sel.picks[req.id])

    for r in reqs:
        if r.id not in sel.picks:
            sel.issues.append(("omitted", r.id, {}))
    return sel
