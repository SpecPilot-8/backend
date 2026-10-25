"""LLM 호출은 이 패키지에만 둔다 (CLAUDE.md 12장).

파이프라인 코드는 `get_client()`가 돌려주는 `LLMClient`만 알고, 어떤 모델·서버를
쓰는지는 환경변수로 정한다. 운영 시 로컬 LLM으로 바꿀 때 이 패키지에 백엔드를
하나 추가하는 것으로 끝나야 한다.

    SPECPILOT_LLM_PROVIDER   anthropic (기본값) | vercel | local (미구현)
    SPECPILOT_LLM_MODEL      provider별 모델 이름
"""

import os
from pathlib import Path

from dotenv import load_dotenv

from llm.base import LLMClient, LLMResponse

# 레포 루트의 .env. 실행 위치와 무관하게 찾도록 경로를 고정한다.
# 셸에 이미 설정된 값이 있으면 그쪽이 이긴다 (override=False).
_ENV_FILE = Path(__file__).resolve().parent.parent / ".env"


def get_client() -> LLMClient:
    load_dotenv(_ENV_FILE, override=False)
    provider = os.environ.get("SPECPILOT_LLM_PROVIDER", "anthropic")
    if provider == "anthropic":
        from llm.anthropic_client import AnthropicClient
        return AnthropicClient(model=os.environ.get("SPECPILOT_LLM_MODEL", "claude-opus-5"))
    if provider == "vercel":
        from llm.vercel_client import VercelClient
        try:
            max_tokens = int(os.environ.get("SPECPILOT_LLM_MAX_TOKENS", "32000"))
            timeout = float(os.environ.get("SPECPILOT_LLM_TIMEOUT_SECONDS", "300"))
        except ValueError:
            raise ValueError("LLM의 토큰 한도/시간 제한 설정에 올바른 숫자를 입력하세요") from None
        return VercelClient(api_key=os.environ.get("AI_GATEWAY_API_KEY", ""),
                            model=os.environ.get("SPECPILOT_LLM_MODEL", ""),
                            max_tokens=max_tokens, timeout_seconds=timeout)
    if provider == "local":
        # 로컬 서버(vLLM/Ollama 등)는 사용자 PC 사양·모델이 정해진 뒤 붙인다 (11장 미정).
        raise NotImplementedError("local LLM 백엔드는 아직 없다. llm/base.py의 LLMClient를 구현해 추가할 것")
    raise ValueError("SPECPILOT_LLM_PROVIDER는 anthropic, vercel, local 중 하나여야 합니다")


__all__ = ["LLMClient", "LLMResponse", "get_client"]
