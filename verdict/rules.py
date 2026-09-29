"""판정 기준. docs/label-definition.md(2026-09-29 잠정 결정)를 옮긴 것이다.

기준이 바뀌면 이 파일만 고친다. 규칙으로 정하는 단계(판정 순서 1~3), LLM에게
주는 판정 정책(4~6), 요구사항 단위 집계(Q0)가 모두 여기 있다.
"""

from dataclasses import dataclass

LLM_STATUSES = ("implemented", "partial", "mismatch", "not_found", "needs_review")


@dataclass(frozen=True)
class Evidence:
    """한 요구사항의 매칭 근거를 화면 범위 태그(match_scope_tag)로 나눈 것."""
    primary: tuple[int, ...]    # this_screen: 구현 여부 판단에 쓰는 근거
    reference: tuple[int, ...]  # other_screen: 다른 화면에서만 쓰이는 코드. 참고로만 (Q4-B)
    # unreachable(어느 화면에서도 쓰이지 않는 코드)은 근거로 인정하지 않아 아예 넘기지 않는다


@dataclass(frozen=True)
class RuleVerdict:
    status: str
    reason_kind: str
    reasoning: str


def pre_judge(verifiable: str, match_invalid: bool, ev: Evidence) -> RuleVerdict | None:
    """판정 순서 1~3단계. 규칙으로 결론이 나면 돌려주고, LLM이 봐야 하면 None."""
    # 1. 정적으로 검증할 수 없는 조건은 판정을 시도하지 않는다 (Q8-A)
    if verifiable == "not_statically_verifiable":
        return RuleVerdict("not_statically_verifiable", "rule_not_verifiable",
                           "시각 스타일이나 브라우저 제공 동작이라 코드만으로 확인할 수 없다")
    # 2. 매칭 단계 출력이 검증을 통과하지 못했으면 근거 목록을 믿을 수 없다
    if match_invalid:
        return RuleVerdict("needs_review", "rule_match_invalid", "근거 매칭 결과에 검증 이슈가 있다")
    # 3. 이 화면에서 도달하는 근거가 없음
    if not ev.primary:
        if ev.reference:
            # Q2(다른 화면에 같은 기능 구현)인지 Q4(비슷한 코드를 잘못 고름)인지 규칙으로는 못 가린다
            return RuleVerdict("needs_review", "rule_other_screen_only",
                               "이 화면 근거는 없고 다른 화면 코드만 있다")
        return RuleVerdict("not_found", "rule_no_evidence", "이 화면에서 쓰이는 관련 코드를 찾지 못했다")
    return None


# 판정 순서 4~6단계에서 LLM에게 주는 정책. 각 줄 끝의 Q 번호가 라벨 정의서 항목이다.
LLM_POLICY = """\
상태 (조건 하나에 대해)
- implemented: 조건이 기획서대로 구현됨.
- partial: 조건 안의 일부만 구현됨 (예: 8자 이상 검사는 있으나 조합 규칙 검사가 없음).
- mismatch: 구현됐으나 값·동작·위치가 기획서와 다름 (예: 날짜 형식, 이동 목적지). 일부가 다르고 일부가 없으면 mismatch.
- not_found: 이 화면 코드에 구현이 없음.
- needs_review: 코드만으로 판단할 수 없음 (값이 런타임에 정해지는 등). 이유를 reasoning에 쓴다.

정책
- 판정은 <primary> 코드로만 한다. <reference>는 다른 화면에서만 쓰이는 코드라 이 화면의 구현 근거가 아니다.
  primary에는 없고 reference에만 있으면 not_found로 두고 reasoning에 다른 화면에 있다고 쓴다. (Q4)
- 화면 이동 조건은 이 화면의 링크·버튼·리다이렉트가 올바른 목적지를 가리키는지만 본다. 목적지 화면의 구현은 보지 않는다.
  목적지가 다르면 mismatch. (Q1)
- 사용자 동작에 대한 조건(클릭하면 ~된다)은 그 동작을 실제로 수행하는 코드가 있어야 implemented다.
  링크·버튼·폼이 가리키는 경로를 처리하는 코드(라우트, 컨트롤러, 이벤트 핸들러, 프레임워크 설정)가 근거에 없으면
  동작이 없는 것이므로 not_found다. 이때 새 창 여부 같은 세부가 기획서와 다르다는 이유로 mismatch를 주지 않는다.
  mismatch는 동작이 있고 그 세부가 다를 때만 쓴다. (Q3)
- 메시지 문구: 상태는 동작 기준으로 정한다(조건이 맞을 때 메시지를 보여주는가). 문구가 기획서와 다르기만 하면
  implemented로 두고 message_match=differs. 조건에 문구가 없으면 not_applicable. (Q5)
- 입력 제한·검증 조건: 화면에서 기획서대로 동작하면 implemented. 서버 쪽 같은 검증이 있으면 server_validation=present,
  없으면 missing. 입력 검증과 무관한 조건은 not_applicable. (Q6)
- 기획서가 틀렸거나 옛 버전으로 의심되면(코드가 더 그럴듯함, 기획서 앞뒤 모순) 상태는 기획서 기준으로 정하고
  spec_suspect에 이유를 쓴다. 없으면 빈 문자열. (Q7)
"""


def aggregate(statuses: list[str]) -> str:
    """조건 판정들 → 요구사항 단위 상태 (Q0-B).

    정적 검증 불가 조건은 집계에서 뺀다. 남은 것 중 하나라도 mismatch면 mismatch,
    사람이 봐야 할 것이 있으면 needs_review, 전부 구현이면 implemented, 전부 없으면
    not_found, 섞여 있으면 partial.
    """
    judged = [s for s in statuses if s != "not_statically_verifiable"]
    if not statuses:
        return "needs_review"  # 조건 분해가 비었다
    if not judged:
        return "not_statically_verifiable"
    if "mismatch" in judged:
        return "mismatch"
    if "needs_review" in judged:
        return "needs_review"
    if all(s == "implemented" for s in judged):
        return "implemented"
    if all(s == "not_found" for s in judged):
        return "not_found"
    return "partial"
