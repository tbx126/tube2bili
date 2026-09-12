import json

import httpx
import pytest

from app import config, language, qwen, store
from app.media import Stopped


def test_qwen_chat_disables_thinking(client, monkeypatch):
    settings = config.Settings()
    settings.translation.primary = config.Route(protocol='qwen', model='qwen-plus', base_url='https://qwen.test/v1')
    def handler(request):
        body = json.loads(request.content)
        assert body['enable_thinking'] is False
        assert body['response_format'] == {'type': 'json_object'}
        assert 'extra_body' not in body
        return httpx.Response(200, json={'choices': [{'message': {'content': '{"ok": true}'}}]})
    original = httpx.Client
    monkeypatch.setattr(httpx, 'Client', lambda **kwargs: original(transport=httpx.MockTransport(handler)))
    assert language.chat(None, settings, 'Translate', {}) == {'ok': True}


def test_qwen_upload_poll_resume_and_cache(client, monkeypatch, tmp_path):
    route = config.Route(protocol='qwen_asr', base_url='https://qwen.test/compatible-mode/v1',
                         model='qwen3-asr-flash-filetrans', api_key='private-key')
    audio = tmp_path / 'audio-0.mp3'
    audio.write_bytes(b'audio')
    calls = []
    stopped = [False]
    def handler(request):
        calls.append((request.method, request.url.path))
        if request.url.host == 'qwen.test':
            assert request.headers['authorization'] == 'Bearer private-key'
        else:
            assert 'authorization' not in request.headers
        if request.url.path == '/api/v1/uploads':
            assert request.url.params['model'] == route.model
            return httpx.Response(200, json={'data': {'upload_dir': 'private',
                'upload_host': 'https://storage.test/upload', 'oss_access_key_id': 'access',
                'signature': 'sig', 'policy': 'policy', 'x_oss_object_acl': 'private',
                'x_oss_forbid_overwrite': 'true'}})
        if request.url.path == '/upload':
            assert b'audio' in request.content
            return httpx.Response(200)
        if request.url.path == '/api/v1/services/audio/asr/transcription':
            assert request.headers['X-DashScope-OssResourceResolve'] == 'enable'
            assert request.headers['X-DashScope-Async'] == 'enable'
            body = json.loads(request.content)
            assert body['input']['file_url'].startswith('oss://private/')
            assert body['parameters']['channel_id'] == [0]
            stopped[0] = True
            return httpx.Response(200, json={'output': {'task_id': 'job-1'}})
        if request.url.path == '/api/v1/tasks/job-1':
            return httpx.Response(200, json={'output': {'task_status': 'SUCCEEDED',
                'result': {'transcription_url': 'https://storage.test/result'}}})
        if request.url.path == '/result':
            return httpx.Response(200, json={'transcripts': [{'channel_id': 0, 'sentences': [
                {'begin_time': 125, 'end_time': 1700, 'language': 'en', 'text': 'Hello'}]}]})
        raise AssertionError(request.url)
    def check(_):
        if stopped[0]:
            raise Stopped()
    monkeypatch.setattr(qwen, 'check', check)
    original = httpx.Client
    monkeypatch.setattr(httpx, 'Client', lambda **kwargs: original(transport=httpx.MockTransport(handler)))
    with pytest.raises(Stopped):
        qwen.transcribe('task', route, audio)
    stopped[0] = False
    result = qwen.transcribe('task', route, audio)
    assert result == {'language': 'en', 'segments': [{'start': .125, 'end': 1.7, 'text': 'Hello'}]}
    count = len(calls)
    assert qwen.transcribe('task', route, audio) == result
    assert len(calls) == count
    assert sum(path.endswith('/transcription') for _, path in calls) == 1
    assert 'private-key' not in next(tmp_path.glob('*qwen*.json')).read_text()


def test_qwen_mixed_languages_are_not_labelled_english():
    value = {'transcripts': [{'sentences': [
        {'begin_time': 0, 'end_time': 1000, 'text': 'Hello', 'language': 'en'},
        {'begin_time': 1000, 'end_time': 2000, 'text': '你好', 'language': 'zh'}]}]}
    assert qwen.parse_transcript(value)['language'] == ''


def test_wrong_qwen_asr_model_stops_before_http(tmp_path):
    with pytest.raises(language.Waiting, match='filetrans'):
        qwen.transcribe(None, config.Route(model='qwen3-asr-flash'), tmp_path / 'audio.mp3')
