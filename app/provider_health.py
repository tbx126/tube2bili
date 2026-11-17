"""Safe provider diagnostics and persistent, route-specific circuit breakers."""
import hashlib
import time

from . import store
from .media import Waiting


class ProviderBlocked(Waiting):
    def __init__(self, message, code, until):
        self.code, self.until = code, until
        super().__init__(message)


MESSAGES = {
    'Arrearage': '千问账户欠费或账户状态异常，请在阿里云费用与成本控制台处理',
    'InsufficientQuota': '模型账户额度不足，请检查余额或资源额度',
    'insufficient_quota': '模型账户额度不足，请检查余额或资源额度',
    'InvalidApiKey': '模型 API Key 无效，请更新登录凭证',
    'invalid_api_key': '模型 API Key 无效，请更新登录凭证',
    '401': '模型 API 身份验证失败，请检查 API Key',
    '403': '模型 API 拒绝访问，请检查账户状态及模型权限',
}


def route_key(route):
    # A key rotation or model change must not inherit an old circuit.
    value = '\0'.join((route.base_url, route.model, route.api_key, route.protocol))
    return 'provider_block_' + hashlib.sha256(value.encode()).hexdigest()


def blocked(route):
    state = store.get_runtime_state(route_key(route), {}) or {}
    if state.get('until', 0) > time.time() and state.get('code') in MESSAGES:
        return ProviderBlocked(MESSAGES[state['code']], state['code'], state['until'])
    return None


def classify(route, response):
    # Never expose arbitrary provider messages, response bodies or credentials.
    try:
        value = response.json()
        error = value.get('error', {})
        code = error.get('code') or error.get('type') if isinstance(error, dict) else None
        code = code or value.get('code')
    except (ValueError, AttributeError, TypeError):
        code = None
    if not isinstance(code, str) or code not in MESSAGES:
        code = str(response.status_code) if response.status_code in (401, 403) else None
    if not code:
        return None
    until = time.time() + 3600
    store.set_runtime_state(route_key(route), {'code': code, 'until': until})
    return ProviderBlocked(MESSAGES[code], code, until)


def clear(route):
    store.set_runtime_state(route_key(route), {})
