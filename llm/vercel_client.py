"""Vercel AI Gateway의 공통 Chat Completions API를 기존 분석 규격으로 변환한다."""

import json
import math
import re
from collections.abc import Iterator

import httpx
from jsonschema import Draft202012Validator, SchemaError

from llm.base import LLMResponse

GATEWAY_URL = "https://ai-gateway.vercel.sh/v1/chat/completions"


def _events(lines: Iterator[str]) -> Iterator[str]:
    """주석/heartbeat는 건너뛰고 여러 data 줄로 된 SSE 이벤트도 보존한다."""
    data = []
    for line in lines:
        if not line:
            if data:
                yield "\n".join(data)
                data = []
        elif line.startswith("data:"):
            data.append(line[5:].removeprefix(" "))
    if data:
        yield "\n".join(data)


def _usage(value: dict) -> dict:
    """Chat Completions의 사용량을 기존 CLI 집계 필드로 바꾼다."""
    details = value.get("prompt_tokens_details") or {}
    result = {"input_tokens": value.get("prompt_tokens") or 0,
              "output_tokens": value.get("completion_tokens") or 0,
              "cache_read_input_tokens": details.get("cached_tokens") or 0,
              "cache_creation_input_tokens": 0}
    if any(not isinstance(v, int) or isinstance(v, bool) or v < 0 for v in result.values()):
        raise ValueError("invalid usage")
    return result


class VercelClient:
    def __init__(self, *, api_key: str, model: str, max_tokens: int = 32000,
                 timeout_seconds: float = 300, transport: httpx.BaseTransport | None = None):
        if not api_key or not api_key.isascii() or any(c.isspace() for c in api_key):
            raise ValueError("AI_GATEWAY_API_KEY에 유효한 Gateway 키를 설정하세요")
        if not re.fullmatch(r"[A-Za-z0-9._-]+/[A-Za-z0-9._:/-]+", model):
            raise ValueError("SPECPILOT_LLM_MODEL에 Gateway의 제공자/모델 ID를 설정하세요")
        if isinstance(max_tokens, bool) or not isinstance(max_tokens, int) or max_tokens <= 0:
            raise ValueError("SPECPILOT_LLM_MAX_TOKENS는 양의 정수여야 합니다")
        if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ValueError("SPECPILOT_LLM_TIMEOUT_SECONDS는 유한한 양수여야 합니다")
        self.model = model
        self._api_key = api_key
        self._max_tokens = max_tokens
        self._timeout = httpx.Timeout(timeout_seconds, connect=min(10, timeout_seconds))
        self._transport = transport

    def _failure(self, error: str, raw: str = "", usage: dict | None = None) -> LLMResponse:
        # HTTP 오류 본문/헤더/예외 문자열에는 제공자가 키를 되비칠 수 있어 반환하지 않는다.
        return LLMResponse(None, raw, self.model, usage or {}, error=error)

    def complete_json(self, *, instructions: str, context: str, prompt: str, schema: dict) -> LLMResponse:
        if not isinstance(schema, dict) or schema.get("type") != "object":
            return self._failure("invalid_schema")
        try:
            Draft202012Validator.check_schema(schema)
        except SchemaError:
            return self._failure("invalid_schema")

        messages = [{"role": "system", "content": instructions}]
        if context:
            messages.append({"role": "system", "content": context})
        messages.append({"role": "user", "content": prompt})
        body = {"model": self.model, "messages": messages, "max_tokens": self._max_tokens,
                "stream": True, "stream_options": {"include_usage": True},
                "response_format": {"type": "json_schema", "json_schema": {
                    "name": "specpilot_response", "schema": schema, "strict": True}}}
        try:
            # 자동 재호출/다른 모델 fallback을 하지 않는다. 한 번의 요청만 보낸다.
            with httpx.Client(timeout=self._timeout, transport=self._transport) as client:
                with client.stream("POST", GATEWAY_URL, json=body,
                                   headers={"Authorization": f"Bearer {self._api_key}",
                                            "Accept": "text/event-stream"}) as response:
                    if response.status_code != 200:
                        error = {400: "invalid_request", 401: "authentication_error", 402: "insufficient_credits",
                                 403: "permission_denied", 404: "model_not_found", 429: "rate_limit"}.get(
                                     response.status_code, f"http_error:{response.status_code}")
                        return self._failure(error)
                    if "text/event-stream" not in response.headers.get("content-type", ""):
                        return self._failure("invalid_response")
                    return self._consume(_events(response.iter_lines()), schema)
        except httpx.TimeoutException:
            return self._failure("timeout")
        except httpx.RequestError:
            return self._failure("connection_error")

    def _consume(self, events: Iterator[str], schema: dict) -> LLMResponse:
        text, usage, finish, refused, done = [], {}, None, False, False
        try:
            for event in events:
                if event == "[DONE]":
                    done = True
                    break
                chunk = json.loads(event)
                if chunk.get("error"):
                    return self._failure("gateway_error")
                if chunk.get("usage"):
                    usage = _usage(chunk["usage"])
                for choice in chunk.get("choices", []):
                    if choice.get("index", 0) != 0:
                        continue
                    delta = choice.get("delta") or {}
                    if delta.get("content") is not None:
                        if not isinstance(delta["content"], str):
                            return self._failure("invalid_response")
                        text.append(delta["content"])
                    refused = refused or bool(delta.get("refusal"))
                    finish = choice.get("finish_reason") or finish
        except (ValueError, TypeError, AttributeError):
            return self._failure("invalid_response")

        raw = "".join(text)
        if self._api_key in raw:
            return self._failure("credential_in_response", usage=usage)
        if refused or finish == "content_filter":
            return self._failure("refusal", usage=usage)
        if finish == "length":
            return self._failure("max_tokens", usage=usage)
        if not done or finish != "stop":
            return self._failure("incomplete_response", usage=usage)
        try:
            data = json.loads(raw)
        except ValueError:
            return self._failure("invalid_json", raw, usage)
        if not isinstance(data, dict) or not Draft202012Validator(schema).is_valid(data):
            return self._failure("schema_validation_failed", raw, usage)
        return LLMResponse(data, raw, self.model, usage)
