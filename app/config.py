import json
import os
import threading
from typing import Literal
from urllib.parse import urlparse

from pydantic import BaseModel, Field, field_validator
from . import store

LOCK = threading.RLock()


class Route(BaseModel):
    name: str = '主服务'
    protocol: Literal['openai', 'qwen', 'qwen_asr'] = 'openai'
    base_url: str = ''
    api_key: str = ''
    model: str = ''
    input_per_million: float = Field(0, ge=0)
    output_per_million: float = Field(0, ge=0)
    per_minute: float = Field(0, ge=0)

    @field_validator('base_url')
    @classmethod
    def valid_url(cls, value):
        if value and (urlparse(value).scheme not in ('http', 'https') or not urlparse(value).hostname):
            raise ValueError('API 地址必须为 HTTP(S) URL')
        return value.rstrip('/')


class Routing(BaseModel):
    primary: Route = Field(default_factory=Route)
    fallback: Route = Field(default_factory=lambda: Route(name='备用服务'))
    fallback_enabled: bool = False


class Posting(BaseModel):
    tid: int = Field(171, gt=0)
    tags: str = 'YouTube,双语字幕'


class Settings(BaseModel):
    proxy: str = ''
    poll_minutes: int = Field(15, ge=5, le=1440)
    min_free_gb: float = Field(5, ge=1)
    monthly_budget: float = Field(0, ge=0)
    translation: Routing = Field(default_factory=Routing)
    transcription: Routing = Field(default_factory=Routing)
    posting: Posting = Field(default_factory=Posting)
    telegram_token: str = ''
    telegram_chat_id: str = ''
    subtitle_language: Literal['zh-CN'] = 'zh-CN'


def get():
    with LOCK:
        path = store.DATA / 'settings.json'
        return Settings.model_validate_json(path.read_text('utf-8')) if path.exists() else Settings()


def save(value: Settings):
    with LOCK:
        path = store.DATA / 'settings.json'
        tmp = path.with_suffix('.tmp')
        tmp.write_text(value.model_dump_json(indent=2), 'utf-8')
        os.chmod(tmp, 0o600)
        tmp.replace(path)


def public():
    value = get().model_dump()
    for purpose in ('translation', 'transcription'):
        for slot in ('primary', 'fallback'):
            route = value[purpose][slot]
            route['key_configured'] = bool(route.pop('api_key'))
    value['telegram_configured'] = bool(value.pop('telegram_token'))
    return value


def merge_public(value: dict):
    old = get().model_dump()
    for purpose in ('translation', 'transcription'):
        for slot in ('primary', 'fallback'):
            new = value.get(purpose, {}).get(slot, {})
            if not new.get('api_key'):
                new['api_key'] = old[purpose][slot]['api_key']
    if not value.get('telegram_token'):
        value['telegram_token'] = old['telegram_token']
    settings = Settings.model_validate(value)
    save(settings)
    return settings
