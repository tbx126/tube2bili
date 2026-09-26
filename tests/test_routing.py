import json
import time

import httpx
import pytest

from app import config, language, store, worker
from app.media import RetryLater, Waiting, Stopped


def task(client):
    return client.post('/api/tasks', json={'url':'https://youtu.be/abcdefghijk'}).json()['id']


def test_fallback_only_if_enabled(client, monkeypatch):
    task_id=task(client)
    settings=config.Settings()
    settings.translation.primary=config.Route(base_url='https://primary.test/v1',model='a')
    settings.translation.fallback=config.Route(base_url='https://backup.test/v1',model='b')
    seen=[]
    def handler(request):
        seen.append(request.url.host)
        if request.url.host=='primary.test':
            return httpx.Response(503)
        return httpx.Response(200,json={'choices':[{'message':{'content':'{"ok":true}'}}], 'usage':{'prompt_tokens':4,'completion_tokens':2}})
    original=httpx.Client
    monkeypatch.setattr(language.httpx,'Client',lambda **kwargs: original(transport=httpx.MockTransport(handler)))
    with pytest.raises(RuntimeError):
        language.chat(task_id,settings,'Translate',{})
    assert seen==['primary.test']
    settings.translation.fallback_enabled=True
    assert language.chat(task_id,settings,'Translate',{})=={'ok':True}
    assert seen==['primary.test','primary.test','backup.test']
    assert store.rows('SELECT tokens FROM usage')[0]['tokens']==6


def test_provider_429_has_safe_diagnostic_and_retry_delay(client, monkeypatch):
    task_id = task(client)
    settings = config.Settings()
    settings.translation.primary = config.Route(protocol='qwen', base_url='https://primary.test/v1',
        model='qwen-plus', api_key='private-test-key')
    original = httpx.Client
    def handler(request):
        assert request.headers['authorization'] == 'Bearer private-test-key'
        return httpx.Response(429, json={'error': {'message': 'quota reached'}}, headers={'Retry-After': '45'})
    monkeypatch.setattr(language.httpx, 'Client', lambda **kwargs: original(transport=httpx.MockTransport(handler)))

    with pytest.raises(RetryLater) as error:
        language.chat(task_id, settings, 'Translate', {'text': 'Example'})

    assert error.value.retry_after == 45
    events = ' '.join(x['message'] for x in store.rows('SELECT message FROM events WHERE task_id=?', (task_id,)))
    assert 'HTTP 429' in events
    assert 'private-test-key' not in events


def test_empty_provider_content_is_retryable(client, monkeypatch):
    settings = config.Settings()
    settings.translation.primary = config.Route(protocol='qwen', base_url='https://primary.test/v1', model='qwen-plus')
    original = httpx.Client
    monkeypatch.setattr(language.httpx, 'Client', lambda **kwargs: original(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, json={'choices': [{'message': {'content': None}}]}))))

    with pytest.raises(RetryLater, match='JSON 无效') as error:
        language.chat(None, settings, 'Translate', {})

    assert error.value.retry_after == 60


def test_asr_provider_429_is_retryable(client, monkeypatch, tmp_path):
    task_id = task(client)
    settings = config.Settings()
    settings.transcription.primary = config.Route(protocol='qwen_audio', base_url='https://qwen.test/api/v1',
        model='qwen-audio-3.0-asr-flash')
    def fake_run(args, *unused):
        from pathlib import Path
        Path(args[-1]).write_bytes(b'mock audio')
    def rate_limit(*args, **kwargs):
        request = httpx.Request('POST', 'https://qwen.test/asr')
        response = httpx.Response(429, headers={'Retry-After': '75'}, request=request)
        raise httpx.HTTPStatusError('rate limited', request=request, response=response)
    monkeypatch.setattr(language, 'run', fake_run)
    monkeypatch.setattr(language.qwen, 'transcribe_audio', rate_limit)
    folder = tmp_path / 'asr-retry'
    folder.mkdir()

    with pytest.raises(RetryLater) as error:
        language.transcribe(store.task(task_id), settings, folder, 30)

    assert error.value.retry_after == 75
    assert any('语音识别 API 返回 HTTP 429' in row['message']
               for row in store.rows('SELECT message FROM events WHERE task_id=?', (task_id,)))


def test_worker_retries_provider_failure_without_failing_translation(client, monkeypatch):
    task_id = task(client)
    second_id = store.enqueue('abcdefghijl', 'https://youtu.be/abcdefghijl')
    folder = store.DATA / 'media' / task_id
    folder.mkdir()
    (folder / 'source.json').write_text('{"title":"Example"}', 'utf-8')
    store.update(task_id, status='running', stage='translate')
    def retry(*args, **kwargs):
        raise RetryLater('翻译服务暂时不可用，稍后自动重试', 90)
    monkeypatch.setattr(worker, 'translate', retry)

    worker.process(task_id)

    current = store.task(task_id)
    state = store.get_runtime_state(worker.AI_BACKOFF_STATE)
    assert current['status'] == 'retrying'
    assert current['attempts'] == 1
    assert current['next_run'] > time.time() + 85
    assert store.task(second_id)['status'] == 'queued'
    assert state['until'] == current['next_run']


def test_invalid_translation_never_advances_to_publish(client, monkeypatch):
    task_id=task(client)
    folder=store.DATA/'media'/task_id
    folder.mkdir()
    (folder/'en.srt').write_text('1\n00:00:01,000 --> 00:00:03,000\nHello\n','utf-8')
    monkeypatch.setattr(language,'chat',lambda *args:{'lines':[{'id':99,'text':'错误'}]})
    with pytest.raises(Waiting):
        language.translate(store.task(task_id),config.get(),folder,{'title':'Example'})
    assert not (folder/'posting.json').exists()


def test_translation_checkpoints_skip_paid_calls(client, monkeypatch):
    task_id=task(client)
    folder=store.DATA/'media'/task_id
    folder.mkdir()
    (folder/'en.srt').write_text('1\n00:00:01,000 --> 00:00:03,000\nHello\n','utf-8')
    calls=[]
    def chat(*args):
        calls.append(1)
        return {'lines':[{'id':1,'text':'你好'}]} if len(calls)==1 else {'title':'标题','description_zh':'简介','description_en':'Description'}
    monkeypatch.setattr(language,'chat',chat)
    for _ in range(2):
        language.translate(store.task(task_id),config.get(),folder,{'title':'Example','uploader':'Creator'})
    assert len(calls)==2
    assert (folder/'bilingual.srt').read_text('utf-8').endswith('你好\nHello\n\n')


def test_confirmed_no_submission_can_reset(client):
    task_id=task(client)
    store.update(task_id,status='reconcile',stage='publish',payload={'publication_started':True})
    assert client.post(f'/api/tasks/{task_id}/action',json={'action':'resume'}).status_code==409
    assert client.post(f'/api/tasks/{task_id}/action',json={'action':'reset-publication'}).status_code==200
    assert not store.task(task_id)['payload'].get('publication_started')


def test_pause_during_translation_prevents_publish(client, monkeypatch):
    task_id=task(client)
    folder=store.DATA/'media'/task_id
    folder.mkdir()
    (folder/'source.json').write_text('{"title":"Example"}')
    store.update(task_id,status='running',stage='translate')
    def translate(*args):
        store.update(task_id,status='paused')
        raise Stopped()
    monkeypatch.setattr(worker,'translate',translate)
    worker.process(task_id)
    assert store.task(task_id)['status']=='paused'
    assert store.task(task_id)['stage']=='translate'


def test_non_english_asr_is_translated_and_cached(client, monkeypatch):
    task_id=task(client)
    folder=store.DATA/'media'/task_id
    folder.mkdir()
    settings=config.Settings()
    settings.transcription.primary=config.Route(base_url='https://asr.test/v1',model='asr')
    def fake_run(args, *unused):
        from pathlib import Path
        Path(args[-1]).write_bytes(b'mock audio')
    monkeypatch.setattr(language,'run',fake_run)
    original=httpx.Client
    def handler(request):
        assert b'name="language"' not in request.content
        return httpx.Response(200,json={'language':'japanese','segments':[{'start':0.1,'end':1.5,'text':'こんにちは'}]})
    monkeypatch.setattr(language.httpx,'Client',lambda **kwargs:original(transport=httpx.MockTransport(handler)))
    calls=[]
    def chat(*args):
        calls.append(1)
        return {'lines':[{'id':0,'text':'Hello'}]}
    monkeypatch.setattr(language,'chat',chat)
    language.transcribe(store.task(task_id),settings,folder,2)
    language.transcribe(store.task(task_id),settings,folder,2)
    assert len(calls)==1
    assert 'Hello' in (folder/'en.srt').read_text('utf-8')


def test_merged_translation_splits_and_preserves_checkpoints(client, monkeypatch, tmp_path):
    task_id = task(client)
    calls = []
    def reply(*args):
        ids = args[-1]['required_ids']
        calls.append(ids)
        if len(ids) > 2:
            return {'lines': [{'id': 1, 'text': 'merged'}]}
        return {'lines': [{'id': str(i), 'text': f'translation {i}'} for i in reversed(ids)]}
    monkeypatch.setattr(language, 'chat', reply)
    source = [{'id': i, 'text': 'fragment'} for i in range(41, 45)]
    path = tmp_path / 'translation.json'
    result = language.subtitle_lines(task_id, config.get(), path, source, 'Chinese')
    assert [x['id'] for x in result['lines']] == [41, 42, 43, 44]
    assert calls == [[41, 42, 43, 44], [41, 42], [43, 44]]
    path.unlink()
    language.subtitle_lines(task_id, config.get(), path, source, 'Chinese')
    assert len(calls) == 4  # Successful child batches are reused.


@pytest.mark.parametrize('lines', [
    [{'id': 1, 'text': 'a'}, {'id': 1, 'text': 'b'}],
    [{'id': 1, 'text': ''}, {'id': 2, 'text': 'b'}],
    [{'id': True, 'text': 'a'}, {'id': 2, 'text': 'b'}],
    [None, None],
])
def test_bad_mapping_never_cached(client, monkeypatch, tmp_path, lines):
    task_id = task(client)
    monkeypatch.setattr(language, 'chat', lambda *args: {'lines': lines})
    path = tmp_path / 'translation.json'
    with pytest.raises(Waiting):
        language.subtitle_lines(task_id, config.get(), path,
            [{'id': 1, 'text': 'a'}, {'id': 2, 'text': 'b'}], 'Chinese')
    assert not path.exists()


def test_rolling_captions_are_disjoint_and_clipped(tmp_path):
    path = tmp_path / 'en.srt'
    path.write_text('1\n00:00:01,000 --> 00:00:05,000\nFirst\n\n2\n00:00:03,000 --> 00:00:08,000\nSecond\n\n3\n00:00:06,000 --> 00:00:10,000\nThird\n', encoding='utf-8')
    cues = language.load_cues(path, duration=9)
    assert [c.content for c in cues] == ['First', 'Second', 'Third']
    assert [c.start.total_seconds() for c in cues] == [1, 3, 6]
    assert [c.end.total_seconds() for c in cues] == [3, 6, 9]


def test_simultaneous_cues_keep_both_texts(tmp_path):
    path = tmp_path / 'en.srt'
    path.write_text('1\n00:00:01,000 --> 00:00:03,000\nFirst\n\n2\n00:00:01,000 --> 00:00:04,000\nSecond\n', encoding='utf-8')
    cues = language.load_cues(path)
    assert len(cues) == 1
    assert cues[0].content == 'First\nSecond'
    assert cues[0].end.total_seconds() == 4
