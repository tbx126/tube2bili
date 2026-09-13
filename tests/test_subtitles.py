import asyncio
import json

import pytest
from app import bili_bridge, store, publishing
from app.media import Waiting


@pytest.mark.parametrize('chinese', ['zh', 'zh-CN', 'zh-Hans'])
def test_live_language_and_existing_receipts(client, monkeypatch, chinese):
    task_id = client.post('/api/tasks', json={'url': 'https://youtu.be/abcdefghijk'}).json()['id']
    store.update(task_id, payload={'bvid': 'BV12gYZ64E97'})
    folder = store.DATA / 'media' / task_id
    folder.mkdir()
    for name in ('bilingual.srt', 'en.srt'):
        (folder / name).write_text('1\n00:00:01,000 --> 00:00:02,000\nHello\n', encoding='utf-8')
    monkeypatch.setattr(bili_bridge, 'cookies', lambda: {'SESSDATA': 'test', 'bili_jct': 'test', 'DedeUserID': '123'})
    class Video:
        def __init__(self, **kwargs): pass
        async def get_info(self):
            return {'pages': [{'cid': 1234}], 'owner': {'mid': 123}, 'desc': store.task(task_id)['url']}
        async def get_subtitle(self, **kwargs):
            return {'subtitles': [{'lan': chinese}, {'lan': 'en'}]}
    monkeypatch.setattr(bili_bridge.video, 'Video', Video)
    calls = []
    class Request:
        def __init__(self, **kwargs): pass
        def update_data(self, **kwargs):
            calls.append(kwargs)
            return self
        @property
        async def result(self): return {'subtitle_id': 1}
    monkeypatch.setattr(bili_bridge, 'Api', Request)
    for _ in range(2):
        assert asyncio.run(bili_bridge.perform('subtitles', task_id))['ok']
    assert [x['lan'] for x in calls] == ['zh', 'en']
    assert all(x['oid'] == 1234 and x['submit'] is True for x in calls)
    assert asyncio.run(bili_bridge.perform('verify', task_id))['ok']


def test_language_error_is_actionable(tmp_path, monkeypatch):
    monkeypatch.setattr(publishing, 'run', lambda *args, **kwargs: None)
    (tmp_path / 'subtitles-result.json').write_text(json.dumps({'ok': False, 'error_code': 79011}))
    with pytest.raises(Waiting, match='79011'):
        publishing.bridge({'id': 'test'}, tmp_path, 'subtitles')
