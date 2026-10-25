import json

import httpx
import pytest

import llm
from llm.vercel_client import GATEWAY_URL, VercelClient

KEY = "test-gateway-key-never-send"
MODEL = "example/structured-model"
SCHEMA = {"type": "object", "properties": {"ok": {"type": "boolean"}},
          "required": ["ok"], "additionalProperties": False}


def event(value):
    return "data: " + json.dumps(value, ensure_ascii=False) + "\n\n"


def stream(raw='{"ok":true}', finish="stop", done=True):
    # JSON을 여러 이벤트/한글 조각으로 나눠 받더라도 올바르게 조립해야 한다.
    middle = len(raw) // 2
    content = ": heartbeat\n\n" + event({"choices": [{"index": 0, "delta": {"role": "assistant"}}]})
    for part in [raw[:middle], raw[middle:]]:
        content += event({"choices": [{"index": 0, "delta": {"content": part}}]})
    content += event({"choices": [{"index": 0, "delta": {}, "finish_reason": finish}]})
    content += event({"choices": [], "usage": {"prompt_tokens": 30, "completion_tokens": 4,
                                              "prompt_tokens_details": {"cached_tokens": 10}}})
    return content + ("data: [DONE]\n\n" if done else "")


def client_for(handler, **kwargs):
    return VercelClient(api_key=KEY, model=MODEL, transport=httpx.MockTransport(handler), **kwargs)


def complete(client, schema=SCHEMA, context="공유 문맥"):
    return client.complete_json(instructions="JSON만 반환", context=context, prompt="검증", schema=schema)


def test_streaming_request_uses_gateway_single_model_and_preserves_schema():
    seen = []

    def handler(request):
        seen.append(request)
        assert str(request.url) == GATEWAY_URL
        assert request.headers["Authorization"] == f"Bearer {KEY}"
        body = json.loads(request.content)
        assert body["model"] == MODEL and body["max_tokens"] == 512
        assert body["stream"] and body["stream_options"]["include_usage"]
        assert body["response_format"]["json_schema"]["schema"] == SCHEMA
        assert [m["content"] for m in body["messages"]] == ["JSON만 반환", "공유 문맥", "검증"]
        assert KEY not in request.content.decode()
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, text=stream())

    result = complete(client_for(handler, max_tokens=512))
    assert len(seen) == 1 and result.error is None and result.data == {"ok": True}
    assert result.model == MODEL
    assert result.usage == {"input_tokens": 30, "output_tokens": 4,
                            "cache_read_input_tokens": 10, "cache_creation_input_tokens": 0}


@pytest.mark.parametrize("raw,error", [('not json', 'invalid_json'), ('null', 'schema_validation_failed'),
                                     ('[]', 'schema_validation_failed'), ('{}', 'schema_validation_failed'),
                                     ('{"ok":"true"}', 'schema_validation_failed'),
                                     ('{"ok":true,"extra":1}', 'schema_validation_failed'),
                                     (json.dumps({"ok": KEY}), 'credential_in_response')])
def test_invalid_data_never_becomes_success(raw, error):
    client = client_for(lambda req: httpx.Response(200, headers={"content-type": "text/event-stream"},
                                                 text=stream(raw)))
    result = complete(client)
    assert result.data is None and result.error == error
    assert KEY not in repr(result)


@pytest.mark.parametrize("finish,done,error", [("length", True, "max_tokens"),
                                              ("content_filter", True, "refusal"),
                                              ("tool_calls", True, "incomplete_response"),
                                              (None, True, "incomplete_response"),
                                              ("stop", False, "incomplete_response")])
def test_truncated_or_refused_stream_is_not_accepted_even_if_json_is_valid(finish, done, error):
    result = complete(client_for(lambda req: httpx.Response(
        200, headers={"content-type": "text/event-stream"}, text=stream(finish=finish, done=done))))
    assert result.data is None and result.error == error
    assert result.usage["input_tokens"] == 30


@pytest.mark.parametrize("status,error", [(400, "invalid_request"), (401, "authentication_error"),
                                         (402, "insufficient_credits"), (403, "permission_denied"),
                                         (404, "model_not_found"), (429, "rate_limit"),
                                         (503, "http_error:503"), (307, "http_error:307")])
def test_http_errors_are_redacted_and_not_retried_or_redirected(status, error):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(status, headers={"location": "https://example.invalid"}, text=KEY)

    result = complete(client_for(handler))
    assert len(calls) == 1 and result.error == error and result.data is None
    assert KEY not in repr(result)


@pytest.mark.parametrize("kind,error", [(httpx.ReadTimeout, "timeout"), (httpx.ConnectError, "connection_error")])
def test_transport_errors_do_not_expose_exception_text(kind, error):
    def handler(request):
        raise kind(KEY, request=request)

    result = complete(client_for(handler))
    assert result.error == error and KEY not in repr(result)


@pytest.mark.parametrize("body,error", [
    ('data: not-json\n\n', 'invalid_response'),
    (event({"error": {"message": KEY}}), 'gateway_error'),
    (event({"choices": [{"delta": {"refusal": "denied"}, "finish_reason": "stop"}]}) + 'data: [DONE]\n\n',
     'refusal'),
    (event({"choices": [{"delta": {"content": ["wrong type"]}}]}), 'invalid_response'),
])
def test_protocol_failures_never_become_valid_json(body, error):
    result = complete(client_for(lambda req: httpx.Response(
        200, headers={"content-type": "text/event-stream"}, text=body)))
    assert result.error == error and result.data is None and KEY not in repr(result)


def test_non_sse_success_status_is_not_accepted():
    result = complete(client_for(lambda req: httpx.Response(200, json={"ok": True})))
    assert result.error == "invalid_response"


@pytest.mark.parametrize("schema", [True, {"type": "array"}, {"type": "object", "properties": []}])
def test_invalid_schema_is_rejected_before_sending_request(schema):
    def handler(request):
        pytest.fail("잘못된 스키마를 네트워크로 보내면 안 됩니다")
    assert complete(client_for(handler), schema=schema).error == "invalid_schema"


def test_empty_context_does_not_create_an_empty_system_message():
    def handler(request):
        assert len(json.loads(request.content)["messages"]) == 2
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, text=stream())
    assert complete(client_for(handler), context="").error is None


@pytest.fixture
def vercel_env(monkeypatch, tmp_path):
    monkeypatch.setattr(llm, "_ENV_FILE", tmp_path / ".env")
    monkeypatch.setenv("SPECPILOT_LLM_PROVIDER", "vercel")
    monkeypatch.setenv("SPECPILOT_LLM_MODEL", MODEL)
    monkeypatch.setenv("AI_GATEWAY_API_KEY", KEY)
    monkeypatch.delenv("SPECPILOT_LLM_MAX_TOKENS", raising=False)
    monkeypatch.delenv("SPECPILOT_LLM_TIMEOUT_SECONDS", raising=False)


def test_factory_loads_root_env_but_shell_values_win(vercel_env, monkeypatch):
    llm._ENV_FILE.write_text('SPECPILOT_LLM_MODEL=other/model\nSPECPILOT_LLM_MAX_TOKENS=1024\n')
    client = llm.get_client()
    assert isinstance(client, VercelClient) and client.model == MODEL
    assert client._max_tokens == 1024


@pytest.mark.parametrize("field,value", [("AI_GATEWAY_API_KEY", ""), ("SPECPILOT_LLM_MODEL", ""),
                                        ("SPECPILOT_LLM_MODEL", "no-provider"),
                                        ("SPECPILOT_LLM_MAX_TOKENS", "0"),
                                        ("SPECPILOT_LLM_MAX_TOKENS", KEY),
                                        ("SPECPILOT_LLM_TIMEOUT_SECONDS", "nan"),
                                        ("SPECPILOT_LLM_TIMEOUT_SECONDS", "inf")])
def test_invalid_configuration_is_explicit_and_redacted(vercel_env, monkeypatch, field, value):
    monkeypatch.setenv(field, value)
    with pytest.raises(ValueError) as error:
        llm.get_client()
    assert KEY not in str(error.value)


def test_existing_anthropic_factory_remains_available(vercel_env, monkeypatch):
    from types import SimpleNamespace
    import llm.anthropic_client
    monkeypatch.setenv("SPECPILOT_LLM_PROVIDER", "anthropic")
    monkeypatch.delenv("SPECPILOT_LLM_MODEL")
    monkeypatch.setattr(llm.anthropic_client, "AnthropicClient", lambda model: SimpleNamespace(model=model))
    assert llm.get_client().model == "claude-opus-5"


def test_gateway_output_still_goes_through_existing_chunk_whitelist():
    from matcher.data import ChunkRow, Requirement
    from matcher.select import select_for_screen
    payload = {"matches": [{"requirement_key": "REQ-1", "chunk_ids": [7, 999]}]}
    client = client_for(lambda req: httpx.Response(
        200, headers={"content-type": "text/event-stream"}, text=stream(json.dumps(payload))))
    result = select_for_screen(client, "LOGIN-001", "로그인", [Requirement(1, "REQ-1", "LOGIN-001", "제목 50자")],
                               [ChunkRow(7, "function", "screen.js", "valid", "return title.length <= 50")])
    assert result.error is None and result.picks == {1: [7]}
    assert any(kind == "invalid_chunk_id" for kind, _, _ in result.issues)


def test_gateway_works_with_dynamic_test_generation_schema_and_semantic_checks():
    from testgen.generate import SpecRequirement, generate
    key = "NOTICE-002-1-C1"
    step = {"action": "제목 입력", "expected": "입력 허용", "account": None, "input": "제목",
            "notes": "", "condition_keys": [key]}
    payload = {"scenarios": [{"title": "제목 입력", "type": "positive", "preconditions": "", "steps": [step],
                              "assumptions": [], "warnings": []}], "uncovered": []}

    def handler(request):
        schema = json.loads(request.content)["response_format"]["json_schema"]["schema"]
        assert schema["properties"]["scenarios"]["items"]["properties"]["steps"]["items"]["properties"][
            "condition_keys"]["items"]["enum"] == [key]
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, text=stream(json.dumps(payload)))

    result = generate(client_for(handler), "공지사항 작성", [SpecRequirement("NOTICE-002-1", "제목 50자", (),
        ({"stable_key": key, "text": "제목 50자", "category": "validation"},))])
    assert result["scenarios"][0]["steps"][0]["condition_keys"] == [key]
    assert not result["warnings"]
