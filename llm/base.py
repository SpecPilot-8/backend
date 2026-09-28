from dataclasses import dataclass, field
from typing import Protocol


@dataclass
class LLMResponse:
    # 스키마 검증 전의 파싱 결과. 로컬 LLM은 형식을 강제하지 못하므로(3.1)
    # 호출하는 쪽이 항상 내용 검증(예: chunk_id 화이트리스트)을 다시 해야 한다.
    data: dict | None
    raw_text: str
    model: str
    usage: dict = field(default_factory=dict)
    error: str | None = None  # JSON 파싱 실패, 거절 등


class LLMClient(Protocol):
    model: str

    def complete_json(self, *, instructions: str, context: str, prompt: str, schema: dict) -> LLMResponse:
        """JSON 하나를 돌려받는다.

        instructions + context는 여러 호출에 걸쳐 그대로 반복되는 앞부분이다
        (예: 레포 전체 청크 목록). 백엔드는 이 부분을 캐시할 수 있다.
        prompt는 호출마다 달라지는 부분이다.
        """
        ...
