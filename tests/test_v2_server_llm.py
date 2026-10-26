from dataclasses import replace
from types import SimpleNamespace

import anthropic
import httpx
import pytest
from fastapi.testclient import TestClient

from api.main import create_app
from api.v2 import server_llm
from llm.base import LLMResponse

KEY = 'test-server-key-not-for-production'


@pytest.fixture
def server(config, monkeypatch):
    config = replace(config, llm_provider='anthropic', llm_model='test-model', llm_api_key=KEY)
    calls = []
    response = SimpleNamespace(value=LLMResponse({'ok': True}, '{"ok":true}', 'test-model'))

    def complete(**kwargs):
        calls.append(kwargs)
        if isinstance(response.value, Exception):
            raise response.value
        return response.value

    monkeypatch.setattr(server_llm, 'probe_client', lambda _: SimpleNamespace(complete_json=complete))
    with TestClient(create_app(config)) as client:
        p = client.post('/api/v2/projects', json={'name': '연결'}).json()['id']
        yield client, p, response, calls, config


def bind(client, p, version=0):
    return client.put(f'/api/v2/projects/{p}/llm', json={'profile_id': 'primary', 'base_version': version})


def test_fixed_server_profile_and_settings_never_expose_key(server):
    client, p, _, calls, config = server
    r = client.get('/api/v2/model-profiles')
    item = r.json()['items'][0]
    assert item['model'] == 'test-model' and item['provider'] == 'anthropic'
    assert item['enabled'] and not item['credential_required']
    assert item['capabilities'] == ['connection_check', 'requirement_extraction', 'implementation_judgment', 'call_flow']
    assert KEY not in r.text and KEY not in repr(config) and calls == []
    assert bind(client, p).json()['config_version'] == 1
    assert bind(client, p).status_code == 409
    assert KEY not in client.get(f'/api/v2/projects/{p}/llm').text
    assert client.post(f'/api/v2/projects/{p}/credentials', json={'profile_id': 'primary', 'api_key': KEY}).status_code == 501
    assert client.put(f'/api/v2/projects/{p}/llm', json={'profile_id': 'primary', 'base_version': 1, 'credential_ref': KEY}).status_code == 422


def test_actual_json_check_is_explicit_and_survives_restart(server):
    client, p, _, calls, config = server
    assert calls == []
    assert bind(client, p).status_code == 200
    url = f'/api/v2/projects/{p}/llm'
    assert client.get(url).json()['connection_status'] == 'not_connected'
    result = client.post(url + '/connection-check', json={'config_version': 1})
    assert result.status_code == 200 and result.json()['json_verified'] is True
    assert len(calls) == 1 and calls[0]['context'] == '' and calls[0]['schema'] == server_llm.SCHEMA
    assert client.get(url).json()['connection_status'] == 'connected'
    assert KEY not in result.text
    with TestClient(create_app(config)) as reopened:
        assert reopened.get(url).json()['connection_status'] == 'connected'
    with TestClient(create_app(replace(config, llm_api_key='changed-key'))) as changed:
        assert changed.get(url).json()['connection_status'] == 'not_connected'
    with TestClient(create_app(replace(config, llm_model='other-model'))) as changed:
        assert changed.get(url).json()['connection_status'] == 'not_connected'
    assert client.post(url + '/connection-check', json={'config_version': 0}).status_code == 409
    assert len(calls) == 1


@pytest.mark.parametrize('error,code,status', [
    ('authentication_error', 'LLM_AUTHENTICATION_FAILED', 502),
    ('permission_denied', 'LLM_PERMISSION_DENIED', 502),
    ('insufficient_credits', 'LLM_INSUFFICIENT_CREDITS', 502),
    ('model_not_found', 'LLM_MODEL_NOT_FOUND', 502),
    ('timeout', 'LLM_TIMEOUT', 504),
    ('max_tokens', 'LLM_INVALID_RESPONSE', 502),
])
def test_failed_check_invalidates_previous_success_and_hides_provider_body(server, error, code, status):
    client, p, response, calls, _ = server
    bind(client, p)
    url = f'/api/v2/projects/{p}/llm'
    assert client.post(url + '/connection-check', json={'config_version': 1}).status_code == 200
    response.value = LLMResponse(None, KEY, 'test-model', error=error)
    result = client.post(url + '/connection-check', json={'config_version': 1})
    assert result.status_code == status and result.json()['error']['code'] == code
    assert KEY not in result.text and len(calls) == 2
    assert client.get(url).json()['connection_status'] == 'not_connected'


@pytest.mark.parametrize('data', [{'ok': False}, {'ok': True, 'extra': KEY}, {'ok': 1}, None])
def test_wrong_json_is_not_connected(server, data):
    client, p, response, _, _ = server
    bind(client, p)
    response.value = LLMResponse(data, KEY, 'test-model')
    r = client.post(f'/api/v2/projects/{p}/llm/connection-check', json={'config_version': 1})
    assert r.status_code == 502 and KEY not in r.text


def test_sdk_exception_is_sanitized(server):
    client, p, response, _, _ = server
    bind(client, p)
    response.value = anthropic.AuthenticationError(KEY, response=httpx.Response(401, request=httpx.Request('POST', 'https://api.anthropic.com')), body={'key': KEY})
    r = client.post(f'/api/v2/projects/{p}/llm/connection-check', json={'config_version': 1})
    assert r.status_code == 502 and r.json()['error']['code'] == 'LLM_AUTHENTICATION_FAILED' and KEY not in r.text


def test_unconfigured_server_does_not_call_provider(client, v2_project, monkeypatch):
    monkeypatch.setattr(server_llm, 'probe_client', lambda _: pytest.fail('must not send request'))
    assert client.get('/api/v2/model-profiles').json()['items'][0]['enabled'] is False
    assert bind(client, v2_project).status_code == 409


def test_changed_config_during_network_call_does_not_save_old_success(server, monkeypatch):
    client, p, _, _, _ = server
    bind(client, p)

    def complete(**kwargs):
        assert bind(client, p, 1).status_code == 200
        return LLMResponse({'ok': True}, '', 'test-model')

    monkeypatch.setattr(server_llm, 'probe_client', lambda _: SimpleNamespace(complete_json=complete))
    r = client.post(f'/api/v2/projects/{p}/llm/connection-check', json={'config_version': 1})
    assert r.status_code == 409
    assert client.get(f'/api/v2/projects/{p}/llm').json()['connection_status'] == 'not_connected'


def test_anthropic_probe_limits_and_key_are_fixed_by_server(config, monkeypatch):
    from llm import anthropic_client
    captured = {}
    monkeypatch.setattr(anthropic_client.anthropic, 'Anthropic', lambda **kwargs: captured.update(kwargs))
    probe = server_llm.probe_client(replace(config, llm_api_key=KEY))
    assert captured['api_key'] == KEY and captured['timeout'] == 20 and captured['max_retries'] == 0
    assert captured['base_url'] == 'https://api.anthropic.com'
    assert probe._max_tokens == 256 and probe.effort == 'low'
