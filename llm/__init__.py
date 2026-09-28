"""LLM 호출은 이 패키지에만 둔다 (CLAUDE.md 12장).

파이프라인 코드는 `get_client()`가 돌려주는 `LLMClient`만 알고, 어떤 모델·서버를
쓰는지는 환경변수로 정한다. 운영 시 로컬 LLM으로 바꿀 때 이 패키지에 백엔드를
하나 추가하는 것으로 끝나야 한다.

    SPECPILOT_LLM_PROVIDER   anthropic (기본값) | local (미구현)
    SPECPILOT_LLM_MODEL      provider별 모델 이름
"""

import os

from llm.base import LLMClient, LLMResponse


def get_client() -> LLMClient:
    provider = os.environ.get("SPECPILOT_LLM_PROVIDER", "anthropic")
    if provider == "anthropic":
        from llm.anthropic_client import AnthropicClient
        return AnthropicClient(model=os.environ.get("SPECPILOT_LLM_MODEL", "claude-opus-5"))
    if provider == "local":
        # 로컬 서버(vLLM/Ollama 등)는 사용자 PC 사양·모델이 정해진 뒤 붙인다 (11장 미정).
        raise NotImplementedError("local LLM 백엔드는 아직 없다. llm/base.py의 LLMClient를 구현해 추가할 것")
    raise ValueError(f"알 수 없는 SPECPILOT_LLM_PROVIDER: {provider}")


__all__ = ["LLMClient", "LLMResponse", "get_client"]
