"""서버 환경변수로 고정한 단일 모델의 짧은 실제 연결 검사."""
from hashlib import sha256
import json
import re

import anthropic
from jsonschema import Draft202012Validator

from api.errors import APIError
from api.models import now
from api.v2.storage import all_resources

SCHEMA = {'type': 'object', 'properties': {'ok': {'type': 'boolean'}},
          'required': ['ok'], 'additionalProperties': False}
ERRORS = {
    'authentication_error': ('LLM_AUTHENTICATION_FAILED', '서버 API 키가 유효하지 않습니다'),
    'permission_denied': ('LLM_PERMISSION_DENIED', '서버 키에 해당 모델의 접근 권한이 없습니다'),
    'model_not_found': ('LLM_MODEL_NOT_FOUND', '서버에 설정된 모델을 사용할 수 없습니다'),
    'insufficient_credits': ('LLM_INSUFFICIENT_CREDITS', '모델 제공 서비스의 크레딧을 확인하세요'),
    'rate_limit': ('LLM_RATE_LIMITED', '제공 서비스의 요청 한도를 초과했습니다. 나중에 다시 확인하세요'),
    'timeout': ('LLM_TIMEOUT', '모델 연결 확인 시간이 초과되었습니다'),
    'connection_error': ('LLM_CONNECTION_FAILED', '모델 제공 서비스에 연결하지 못했습니다'),
    'invalid_request': ('LLM_REQUEST_REJECTED', '모델이 JSON 형식 요청을 지원하는지 확인하세요'),
}


def server_profile(config):
    supported = config.llm_provider in ('anthropic', 'vercel')
    model_valid = bool(re.fullmatch(r'[A-Za-z0-9._:/-]{1,200}', config.llm_model))
    if config.llm_provider == 'vercel':
        model_valid = model_valid and '/' in config.llm_model
    key_valid = bool(config.llm_api_key and config.llm_api_key.isascii()
                     and not any(c.isspace() for c in config.llm_api_key))
    enabled = bool(supported and model_valid and key_valid)
    return {'id': 'primary', 'provider': config.llm_provider, 'model': config.llm_model,
            'display_name': f'{config.llm_model} (서버 설정)', 'adapter_status': 'configured' if enabled else 'not_configured',
            'enabled': enabled, 'capabilities': ['connection_check', 'requirement_extraction'],
            'credential_required': False, 'credential_source': 'server_env', 'credential_configured': key_valid,
            'limitations': ['web_implementation_judgment', 'web_test_generation']}


def settings_fingerprint(config):
    return sha256(json.dumps([config.llm_provider, config.llm_model, config.llm_api_key]).encode()).hexdigest()


def connection_state(db, p, version, config):
    matches = [r for r in all_resources(db, p, 'llm_connection')
               if r.data['config_version'] == version and r.data['fingerprint'] == settings_fingerprint(config)]
    return 'connected' if server_profile(config)['enabled'] and matches and matches[-1].data['status'] == 'connected' else 'not_connected'


def probe_client(config):
    if config.llm_provider == 'anthropic':
        from llm.anthropic_client import AnthropicClient
        return AnthropicClient(model=config.llm_model, effort='low', api_key=config.llm_api_key,
                               max_tokens=256, timeout_seconds=20, max_retries=0, base_url="https://api.anthropic.com")
    from llm.vercel_client import VercelClient
    return VercelClient(api_key=config.llm_api_key, model=config.llm_model, max_tokens=256, timeout_seconds=20)


def fail(error):
    code, message = ERRORS.get(error, ('LLM_INVALID_RESPONSE', '모델의 정상 JSON 응답을 확인하지 못했습니다'))
    raise APIError(504 if error == 'timeout' else 502, code, message)


def probe_connection(config):
    if not server_profile(config)['enabled']:
        raise APIError(409, 'LLM_NOT_CONFIGURED', '백엔드의 제공업체·모델·API 키 환경변수를 확인하세요')
    client = None
    try:
        client = probe_client(config)
        result = client.complete_json(
            instructions='Return only the requested JSON object.', context='',
            prompt='Return {"ok": true}.', schema=SCHEMA)
    except anthropic.AuthenticationError:
        fail('authentication_error')
    except anthropic.PermissionDeniedError:
        fail('permission_denied')
    except anthropic.NotFoundError:
        fail('model_not_found')
    except anthropic.RateLimitError:
        fail('rate_limit')
    except anthropic.APITimeoutError:
        fail('timeout')
    except anthropic.APIConnectionError:
        fail('connection_error')
    except anthropic.APIStatusError as error:
        fail('insufficient_credits' if error.status_code == 402 else 'invalid_request')
    except Exception:
        # 제공자 예외 문자열에 키/헤더/본문이 포함될 수 있어 로그나 응답에 되비치지 않는다.
        fail('connection_error')
    finally:
        if client is not None and hasattr(client, 'close'):
            client.close()
    if result.error:
        fail(result.error)
    if not Draft202012Validator(SCHEMA).is_valid(result.data) or result.data != {'ok': True}:
        fail('schema_validation_failed')
    return {'status': 'connected', 'checked_at': now(), 'provider': config.llm_provider, 'model': config.llm_model,
            'config_source': 'server_env', 'json_verified': True}
