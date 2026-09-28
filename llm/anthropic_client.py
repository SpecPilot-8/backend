"""개발용 백엔드: Anthropic API.

운영에서는 소스코드를 외부로 보낼 수 없으므로(3.1) 개발·평가 단계에서만 쓴다.
"""

import json

import anthropic

from llm.base import LLMResponse

# 스트리밍으로 받는다. 적응형 사고가 길어지면 비스트리밍 요청은 HTTP 타임아웃에 걸린다.
_MAX_TOKENS = 32000


class AnthropicClient:
    def __init__(self, model: str, effort: str = "high"):
        self.model = model
        self.effort = effort
        self._client = anthropic.Anthropic()

    def complete_json(self, *, instructions: str, context: str, prompt: str, schema: dict) -> LLMResponse:
        with self._client.messages.stream(
            model=self.model,
            max_tokens=_MAX_TOKENS,
            system=[
                {"type": "text", "text": instructions},
                # 화면마다 같은 청크 목록을 다시 보내므로 여기까지를 캐시한다.
                {"type": "text", "text": context, "cache_control": {"type": "ephemeral"}},
            ],
            messages=[{"role": "user", "content": prompt}],
            output_config={
                "effort": self.effort,
                "format": {"type": "json_schema", "schema": schema},
            },
        ) as stream:
            message = stream.get_final_message()

        usage = {
            "input_tokens": message.usage.input_tokens,
            "output_tokens": message.usage.output_tokens,
            "cache_read_input_tokens": message.usage.cache_read_input_tokens,
            "cache_creation_input_tokens": message.usage.cache_creation_input_tokens,
        }
        text = "".join(b.text for b in message.content if b.type == "text")

        if message.stop_reason == "refusal":
            return LLMResponse(None, text, message.model, usage, error="refusal")
        if message.stop_reason == "max_tokens":
            return LLMResponse(None, text, message.model, usage, error="max_tokens")
        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            return LLMResponse(None, text, message.model, usage, error=f"invalid_json: {e}")
        return LLMResponse(data, text, message.model, usage)
