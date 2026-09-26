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
    for name in ('zh.srt', 'en.srt'):
        (folder / name).write_text('1\n00:00:01,000 --> 00:00:02,000\n' + ('你好' if name == 'zh.srt' else 'Hello') + '\n', encoding='utf-8')
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
    assert [json.loads(x['data'])['font_color'] for x in calls] == ['#FFD54F', '#FFFFFF']
    assert [json.loads(x['data'])['body'][0]['content'] for x in calls] == ['你好', 'Hello']
    assert all(x['oid'] == 1234 and x['submit'] is True for x in calls)
    assert asyncio.run(bili_bridge.perform('verify', task_id))['ok']


@pytest.mark.parametrize(('code', 'message'), [
    (79011, '79011'),
    (79014, '79014'),
])
def test_subtitle_platform_errors_are_actionable(tmp_path, monkeypatch, code, message):
    monkeypatch.setattr(publishing, 'run', lambda *args, **kwargs: None)
    (tmp_path / 'subtitles-result.json').write_text(json.dumps({'ok': False, 'error_code': code}))
    with pytest.raises(Waiting, match=message):
        publishing.bridge({'id': 'test'}, tmp_path, 'subtitles')


def test_ass_style_timing_and_untrusted_text(tmp_path):
    import datetime as dt
    import srt
    from app.subtitle_style import write_ass
    start, end = dt.timedelta(seconds=32.48), dt.timedelta(seconds=36.719)
    zh = [srt.Subtitle(1, start, end, '五十六岁')]
    en = [srt.Subtitle(1, start, end, r'56 {\pos(0,0)}')]
    path = tmp_path / 'bilingual.ass'
    write_ass(path, zh, en)
    result = path.read_text('utf-8-sig')
    assert '0:00:32.48,0:00:36.72' in result
    assert '&H004FD5FF' in result
    assert r'五十六岁\N{\c&HFFFFFF&\fs34\b0}56' in result
    assert r'{\pos(0,0)}' not in result
    with pytest.raises(ValueError):
        write_ass(path, zh, [])
