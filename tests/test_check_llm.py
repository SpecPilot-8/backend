from types import SimpleNamespace

import pytest

from llm.base import LLMResponse
from scripts import check_llm


@pytest.fixture
def diagnostic(monkeypatch):
    monkeypatch.setattr(check_llm, "load_dotenv", lambda *args, **kwargs: None)
    monkeypatch.setenv("SPECPILOT_LLM_PROVIDER", "vercel")
    monkeypatch.delenv("SPECPILOT_LLM_MAX_TOKENS", raising=False)
    calls = []

    def complete(**kwargs):
        calls.append(kwargs)
        return LLMResponse({"ok": True}, '{"ok":true}', "example/model", {"input_tokens": 8})

    client = SimpleNamespace(model="example/model", complete_json=complete)
    monkeypatch.setattr(check_llm, "get_client", lambda: client)
    return client, calls


def test_default_command_does_not_call_llm(diagnostic, capsys):
    assert check_llm.main([]) == 0
    assert diagnostic[1] == [] and "미검증" in capsys.readouterr().out


def test_explicit_call_sends_only_small_synthetic_prompt(diagnostic, monkeypatch, capsys):
    monkeypatch.setenv("SPECPILOT_LLM_MAX_TOKENS", "32000")
    assert check_llm.main(["--call"]) == 0
    assert len(diagnostic[1]) == 1 and diagnostic[1][0]["context"] == ""
    assert diagnostic[1][0]["schema"] == check_llm.SCHEMA
    assert check_llm.os.getenv("SPECPILOT_LLM_MAX_TOKENS") == "256"
    assert "JSON 검증 성공" in capsys.readouterr().out


@pytest.mark.parametrize("data,error", [(None, "authentication_error"), ({"ok": False}, None)])
def test_failed_call_exits_nonzero_without_printing_raw_output(diagnostic, capsys, data, error):
    diagnostic[0].complete_json = lambda **kwargs: LLMResponse(data, "never-print-raw", "example/model", error=error)
    assert check_llm.main(["--call"]) == 1
    captured = capsys.readouterr()
    assert "never-print-raw" not in captured.out + captured.err


def test_other_provider_is_rejected_before_client_creation(monkeypatch, diagnostic):
    monkeypatch.setenv("SPECPILOT_LLM_PROVIDER", "anthropic")
    assert check_llm.main([]) == 2 and diagnostic[1] == []
