import json

import httpx
import pytest

from app import collections, config, language, store, worker
from app.media import Waiting


def setup_task():
    task_id = store.enqueue('abcdefghijk', 'https://www.youtube.com/watch?v=abcdefghijk',
                            options={'season_id': 12, 'section_id': 34})
    task = store.task(task_id)
    task['payload']['bvid'] = 'BV1234567890'
    store.update(task_id, payload=task['payload'], stage='collection')
    folder = store.DATA / 'media' / task_id
    folder.mkdir()
    return store.task(task_id), folder


def transport(monkeypatch, task, *, uncertain=False, owner=99, section=34):
    state = {'added': False, 'posts': 0}
    def handler(req):
        path = req.url.path
        if path.endswith('/seasons'):
            data = {'total': 1, 'seasons': [{'season': {'id': 12, 'title': '课堂'},
                    'sections': {'sections': [{'id': section, 'title': '默认'}]}}]}
        elif path.endswith('/view'):
            data = {'aid': 56, 'owner': {'mid': owner}, 'desc': task['url'],
                    'pages': [{'cid': 78}], 'title': '测试'}
        elif path.endswith('/episodes/add'):
            assert json.loads(req.content)['section_id'] == 34
            state['posts'] += 1
            state['added'] = True
            if uncertain:
                raise httpx.ReadTimeout('ambiguous result')
            data = {}
        else:
            data = {'episodes': [{'aid': 56}] if state['added'] else []}
        return httpx.Response(200, json={'code': 0, 'data': data})
    monkeypatch.setattr(collections, 'cookies', lambda: {'SESSDATA': 'test', 'bili_jct': 'test', 'DedeUserID': '99'})
    monkeypatch.setattr(collections, 'client_for', lambda jar: httpx.Client(transport=httpx.MockTransport(handler)))
    return state


def test_uncertain_add_checks_remote_before_retry(client, monkeypatch):
    task, folder = setup_task()
    state = transport(monkeypatch, task, uncertain=True)
    with pytest.raises(httpx.ReadTimeout):
        collections.add(task, config.Settings(), folder)
    collections.add(store.task(task['id']), config.Settings(), folder)
    collections.add(store.task(task['id']), config.Settings(), folder)
    assert state['posts'] == 1
    assert (folder / 'collection-receipt.json').exists()


@pytest.mark.parametrize('owner,section', [(100, 34), (99, 35)])
def test_invalid_owner_or_section_never_posts(client, monkeypatch, owner, section):
    task, folder = setup_task()
    state = transport(monkeypatch, task, owner=owner, section=section)
    with pytest.raises(Waiting):
        collections.add(task, config.Settings(), folder)
    assert state['posts'] == 0


def test_collection_retry_does_not_republish(client, monkeypatch):
    task, folder = setup_task()
    state = transport(monkeypatch, task, uncertain=True)
    worker.process(task['id'])
    assert store.task(task['id'])['status'] == 'retrying'
    assert store.task(task['id'])['stage'] == 'collection'
    worker.process(task['id'])
    assert store.task(task['id'])['status'] == 'completed'
    assert state['posts'] == 1


def test_glossary_snapshot_survives_settings_change(client, monkeypatch):
    task, _ = setup_task()
    settings = config.Settings()
    settings.translation.primary = config.Route(base_url='https://translation.test', model='test')
    prompts = []
    def handler(req):
        prompts.append(json.loads(req.content)['messages'][0]['content'])
        return httpx.Response(200, json={'choices': [{'message': {'content': '{"ok": true}'}}]})
    original = httpx.Client
    monkeypatch.setattr(httpx, 'Client', lambda **kw: original(transport=httpx.MockTransport(handler)))
    language.chat(task['id'], settings, 'Translate', {})
    settings.translation_notes = 'changed glossary'
    language.chat(task['id'], settings, 'Translate', {})
    assert prompts[0] == prompts[1]
    assert '辛达诺夫' in prompts[0]


def test_channel_options_roundtrip(client):
    value = {'name': 'chess', 'url': 'https://www.youtube.com/@chess',
             'options': {'season_id': 12, 'section_id': 34, 'translation_notes': 'pin=牵制'}}
    assert client.post('/api/channels', json=value).status_code == 200
    assert client.get('/api/channels').json()[0]['options']['season_id'] == 12
    value['options']['season_id'] = -1
    assert client.post('/api/channels', json=value).status_code == 422


def test_completed_task_can_join_without_restarting_upload(client):
    task, _ = setup_task()
    store.update(task['id'], status='completed', stage='verify')
    result = client.put(f'/api/tasks/{task["id"]}/collection', json={'season_id': 12, 'section_id': 34})
    assert result.status_code == 200
    updated = store.task(task['id'])
    assert updated['stage'] == 'collection'
    assert updated['status'] == 'queued'
    store.update(task['id'], status='running')
    assert client.put(f'/api/tasks/{task["id"]}/collection', json={'season_id': 13}).status_code == 409


def test_account_with_no_collections_returns_empty_list(client, monkeypatch):
    monkeypatch.setattr(collections, 'cookies', lambda: {})
    monkeypatch.setattr(collections, 'client_for', lambda jar: httpx.Client(transport=httpx.MockTransport(
        lambda req: httpx.Response(200, json={'code': 0, 'data': {'seasons': None, 'total': 0}}))))
    response = client.get('/api/bilibili/collections')
    assert response.status_code == 200
    assert response.json() == []
