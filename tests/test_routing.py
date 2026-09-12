import json

import httpx
import pytest

from app import config, language, store, worker
from app.media import Waiting, Stopped


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
