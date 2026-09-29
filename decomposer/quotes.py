"""조건의 원문 인용을 기획서 원문과 대조하고, 어느 조건에도 인용되지 않은 구간을 찾는다.

PDF 추출 과정에서 띄어쓰기가 사라지거나 엉뚱한 곳에 줄바꿈이 들어가 있다
("메시 지"). 그래서 양쪽 모두 공백을 전부 지우고 비교한다. 기호 폰트의 글리프가
유니코드 사용자 정의 영역 문자(예: U+F0DF 화살표)로 섞여 나오기도 하는데, 화면에
보이지 않아 LLM이 인용할 때 빠뜨리므로 이것도 공백처럼 지운다. 비교는 공백을 지운
문자열에서 하지만, 누락 구간은 사람이 읽을 수 있게 원문 좌표로 되돌려 보여준다.
"""

import re
import unicodedata
from dataclasses import dataclass
_MEANINGFUL = re.compile(r"[0-9A-Za-z가-힣]")
# 기획서의 형식 표기: 번호 (1) 1), 불릿 * -, 라벨 error-msg: / msg:. 누락 의심은 이것을
# 지우고도 글자가 남는 구간만 센다. "(1)"만 덮이지 않은 것은 내용이 빠진 게 아니다.
_STRUCTURAL = re.compile(r"\(\d+\)|\d+\)|[*\-]|(?:error-)?msg\s*:", re.IGNORECASE)


def _ignored(ch: str) -> bool:
    return ch.isspace() or unicodedata.category(ch) == "Co"


def strip_ws(text: str) -> str:
    return "".join(ch for ch in text if not _ignored(ch))


class StrippedText:
    """공백을 지운 문자열과, 그 각 글자가 원문의 몇 번째 글자였는지의 대응표."""

    def __init__(self, original: str):
        self.original = original
        self.chars: list[str] = []
        self.index: list[int] = []  # stripped 위치 -> original 위치
        for i, ch in enumerate(original):
            if not _ignored(ch):
                self.chars.append(ch)
                self.index.append(i)
        self.text = "".join(self.chars)

    def original_span(self, start: int, end: int) -> str:
        """stripped [start, end) 구간을 원문 문자열로 되돌린다."""
        return self.original[self.index[start]:self.index[end - 1] + 1]


@dataclass
class QuoteMatch:
    quote: str
    found: bool
    start: int = -1  # stripped 좌표
    end: int = -1


def locate_quotes(body: StrippedText, quotes: list[str], covered: list[bool] | None = None) -> list[QuoteMatch]:
    """인용마다 원문에서의 위치를 찾고, 찾은 구간을 covered에 표시한다.

    같은 구절이 원문에 여러 번 나오면(예: 요약 줄과 상세 항목에 같은 소제목) 아직
    덮이지 않은 위치를 고른다. 한 요구사항의 조건들이 covered를 공유해야 한다.
    예전엔 조건마다 처음부터 찾아서, 두 조건이 같은 구절을 인용하면 둘 다 첫 위치에
    맞춰지고 두 번째 위치가 누락 의심으로 잘못 잡혔다.
    """
    if covered is None:
        covered = [False] * len(body.text)
    out = []
    for q in quotes:
        needle = strip_ws(q)
        pos = _find_uncovered(body.text, needle, covered) if needle else -1
        if pos < 0:
            out.append(QuoteMatch(q, False))
            continue
        end = pos + len(needle)
        for i in range(pos, end):
            covered[i] = True
        out.append(QuoteMatch(q, True, pos, end))
    return out


def _find_uncovered(text: str, needle: str, covered: list[bool]) -> int:
    """needle이 나오는 위치 중 아직 덮이지 않은 글자가 있는 첫 위치. 없으면 첫 위치."""
    first = text.find(needle)
    pos = first
    while pos >= 0:
        if not all(covered[pos:pos + len(needle)]):
            return pos
        pos = text.find(needle, pos + 1)
    return first


def uncovered_spans(body: StrippedText, covered: list[bool]) -> list[str]:
    """어떤 인용에도 덮이지 않은 원문 구간 (누락 의심). 원문 표기 그대로 돌려준다."""
    spans = []
    i = 0
    while i < len(covered):
        if covered[i]:
            i += 1
            continue
        j = i
        while j < len(covered) and not covered[j]:
            j += 1
        if _MEANINGFUL.search(_STRUCTURAL.sub("", body.text[i:j])):
            spans.append(body.original_span(i, j).strip())
        i = j
    return spans
