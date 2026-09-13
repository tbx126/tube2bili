import datetime as dt
import json
import time

import pytest
import srt

from app import config, store, worker, publishing
from app.bili_bridge import subtitle_data
from app.language import load_cues, budget
from app.media import Waiting, Reconcile, is_netscape_cookie_file, is_youtube_auth_error, youtube_auth_waiting, youtube_url


VIDEO = 'https://www.youtube.com/watch?v=abcdefghijk'


def new_task(client):
    response = client.post('/api/tasks', json={'url': VIDEO})
    assert response.status_code == 200
    return response.json()['id']


@pytest.mark.parametrize('url', ['https://youtu.be/abcdefghijk?t=2', VIDEO, 'https://youtube.com/shorts/abcdefghijk'])
def test_normalize_video(url):
    assert youtube_url(url) == ('abcdefghijk', VIDEO)


@pytest.mark.parametrize('url', ['http://youtube.com/watch?v=abcdefghijk', 'https://youtube.com.evil.org/watch?v=abcdefghijk', 'https://127.0.0.1/test', 'https://user@youtube.com/watch?v=abcdefghijk', 'https://youtube.com:123/watch?v=abcdefghijk'])
def test_reject_non_youtube(url):
    with pytest.raises(ValueError):
        youtube_url(url)


def test_youtube_auth_detection_is_specific(tmp_path, monkeypatch):
    assert is_youtube_auth_error("Sign in to confirm you're not a bot. Use --cookies for the authentication.")
    assert not is_youtube_auth_error('WARNING: cookies were not found in the metadata')
    monkeypatch.setattr(store, 'DATA', tmp_path)
    with pytest.raises(Waiting, match='尚未配置 YouTube 登录 Cookie'):
        raise youtube_auth_waiting()
    (tmp_path / 'youtube-cookies.txt').write_text('# Netscape HTTP Cookie File\n', 'utf-8')
    with pytest.raises(Waiting, match='已失效或被轮换'):
        raise youtube_auth_waiting()


@pytest.mark.parametrize('header', ['# HTTP Cookie File', '# Netscape HTTP Cookie File'])
def test_netscape_cookie_header(header):
    assert is_netscape_cookie_file('\ufeff' + header + '\n.youtube.com\tTRUE\t/\tTRUE\t0\tSID\tvalue\n')
    assert not is_netscape_cookie_file('{"cookies": []}')


def test_auth_csrf_and_logout(client):
    assert client.get('/api/overview').status_code == 200
    assert client.post('/api/tasks', json={'url': VIDEO}, headers={'X-Requested-With': ''}).status_code == 403
    client.post('/api/logout')
    assert client.get('/api/overview').status_code == 401
    assert client.get('/healthz').status_code == 200


def test_duplicate_and_controls(client):
    one = new_task(client)
    two = new_task(client)
    assert one == two
    client.post(f'/api/tasks/{one}/action', json={'action': 'pause'})
    assert store.task(one)['status'] == 'paused'
    client.post(f'/api/tasks/{one}/action', json={'action': 'resume'})
    assert store.task(one)['status'] == 'queued'


def test_settings_redact_and_preserve_keys(client):
    settings = client.get('/api/settings').json()
    settings['translation']['primary'].update(base_url='http://model:8000/v1', model='local', api_key='SECRET')
    settings['telegram_token'] = 'TELEGRAM_SECRET'
    assert client.put('/api/settings', json=settings).status_code == 200
    public = client.get('/api/settings').json()
    assert 'SECRET' not in json.dumps(public)
    assert public['translation']['primary']['key_configured']
    assert client.put('/api/settings', json=public).status_code == 200
    assert config.get().translation.primary.api_key == 'SECRET'


def test_invalid_settings_secret_not_in_response(client):
    settings = client.get('/api/settings').json()
    settings['translation']['primary'].update(base_url='SECRET_BAD_URL', api_key='SECRET')
    response = client.put('/api/settings', json=settings)
    assert response.status_code == 422
    assert 'SECRET' not in response.text


def test_baseline_then_only_new_and_dedupe(client):
    response = client.post('/api/channels', json={'url':'https://youtube.com/@example', 'name':'Example'})
    channel = store.rows('SELECT * FROM channels WHERE id=?', (response.json()['id'],))[0]
    worker.ingest(channel, [{'id': 'abcdefghijk'}])
    assert store.rows('SELECT * FROM tasks') == []
    worker.ingest(channel, [{'id':'abcdefghijk'}, {'id':'lmnopqrstuv'}, {'id':'aaaaaaaaaaa','live_status':'is_live'}])
    worker.ingest(channel, [{'id':'lmnopqrstuv'}])
    assert [t['video_id'] for t in store.rows('SELECT * FROM tasks')] == ['lmnopqrstuv']


def test_paused_channel_cannot_enqueue(client):
    channel_id = client.post('/api/channels', json={'url':'https://youtube.com/@example', 'name':'Example'}).json()['id']
    channel = store.rows('SELECT * FROM channels WHERE id=?', (channel_id,))[0]
    worker.ingest(channel, [])
    store.execute('UPDATE channels SET enabled=0 WHERE id=?', (channel_id,))
    worker.ingest(channel, [{'id':'abcdefghijk'}])
    assert not store.rows('SELECT * FROM tasks')


def test_subtitle_conversion_preserves_timing(client, tmp_path):
    path = tmp_path / 'test.srt'
    path.write_text('1\n00:00:01,200 --> 00:00:03,450\n你好\nHello\n', 'utf-8')
    body = subtitle_data(path)['body']
    assert body == [{'from':1.2,'to':3.45,'location':2,'content':'你好\nHello'}]


def test_unknown_publish_never_reuploads(client, monkeypatch):
    task_id = new_task(client)
    store.update(task_id, stage='publish', payload={'publication_started':True})
    def forbidden(*args, **kwargs):
        pytest.fail('must not upload twice')
    monkeypatch.setattr(publishing, 'run', forbidden)
    with pytest.raises(Reconcile):
        publishing.publish(store.task(task_id), config.get(), store.DATA / 'media' / task_id)


def test_worker_full_pipeline_and_retry_subtitles_without_republishing(client, monkeypatch):
    task_id = new_task(client)
    folder = store.DATA / 'media' / task_id
    folder.mkdir()
    calls = []
    def download(task, settings, target):
        data = {'title':'A video'}
        (target / 'source.json').write_text(json.dumps(data))
        calls.append('download')
        return data
    def translate(*args):
        calls.append('translate')
        return {'title':'中文视频'}
    def publish(task,*args):
        calls.append('publish')
        store.update(task['id'],payload={'bvid':'BV1234567890'})
    def subtitles(*args):
        calls.append('subtitles')
        if calls.count('subtitles') == 1:
            raise RuntimeError('platform not ready')
    monkeypatch.setattr(worker,'download',download)
    monkeypatch.setattr(worker,'translate',translate)
    monkeypatch.setattr(publishing,'publish',publish)
    monkeypatch.setattr(publishing,'subtitles',subtitles)
    monkeypatch.setattr(publishing,'verify',lambda *args: calls.append('verify'))
    store.update(task_id,status='running')
    worker.process(task_id)
    assert store.task(task_id)['status'] == 'retrying'
    assert store.task(task_id)['stage'] == 'subtitles'
    store.update(task_id,status='running')
    worker.process(task_id)
    assert store.task(task_id)['status'] == 'completed'
    assert calls == ['download','translate','publish','subtitles','subtitles','verify']


def test_deletion_keeps_task_and_dedupe(client):
    task_id = new_task(client)
    folder = store.DATA / 'media' / task_id
    folder.mkdir()
    (folder/'source.mp4').write_bytes(b'video')
    assert client.delete(f'/api/tasks/{task_id}/files').status_code == 409
    store.update(task_id,status='paused')
    assert client.delete(f'/api/tasks/{task_id}/files').status_code == 200
    assert not folder.exists()
    assert new_task(client) == task_id
    assert client.post(f'/api/tasks/{task_id}/action',json={'action':'retry'}).status_code == 409


def test_queue_record_delete_hides_but_preserves_task_and_assets(client):
    task_id = new_task(client)
    folder = store.DATA / 'media' / task_id
    folder.mkdir()
    (folder / 'source.mp4').write_bytes(b'video')
    store.update(task_id, status='paused')
    assert client.delete(f'/api/tasks/{task_id}').status_code == 200
    assert not client.get('/api/overview').json()['tasks']
    assert store.task(task_id)['deleted'] == 1
    assert folder.exists() and (folder / 'source.mp4').exists()
    assert new_task(client) == task_id
    assert store.task(task_id)['deleted'] == 0


def test_queue_record_delete_rejects_active_and_reconcile(client):
    task_id = new_task(client)
    store.update(task_id, status='running')
    assert client.delete(f'/api/tasks/{task_id}').status_code == 409
    store.update(task_id, status='reconcile')
    assert client.delete(f'/api/tasks/{task_id}').status_code == 409


def test_queue_record_delete_rejects_unknown_publication(client):
    task_id = new_task(client)
    store.update(task_id, status='queued', stage='publish', payload={'publication_started': True})
    assert client.delete(f'/api/tasks/{task_id}').status_code == 409


def test_credentials_not_public_or_arbitrary_files(client):
    task_id = new_task(client)
    value={'cookie_info':{'cookies':[{'name':name,'value':'123' if name=='DedeUserID' else 'SECRET'} for name in ('SESSDATA','bili_jct','DedeUserID')]},
           'sso':[], 'token_info': {'access_token':'SECRET','refresh_token':'SECRET','expires_in':3600,'mid':123}}
    assert client.put('/api/credentials/bilibili',json={'content':json.dumps(value)}).status_code == 200
    assert client.get('/api/settings').json()['bilibili_configured']
    assert 'SECRET' not in client.get('/api/settings').text
    assert client.get(f'/api/tasks/{task_id}/files/command.log').status_code == 404


def test_restart_preserves_publishing_guard(client):
    task_id = new_task(client)
    store.update(task_id,status='running',stage='publish',payload={'publication_started':True})
    store.init()
    task = store.task(task_id)
    assert task['status'] == 'queued'
    assert task['payload']['publication_started']


def test_budget_blocks_at_threshold(client):
    store.execute('INSERT INTO usage(task_id,created,route,tokens,cost) VALUES(?,?,?,?,?)',('test',time.time(),'test',100,10))
    settings = config.get()
    settings.monthly_budget = 10
    with pytest.raises(Waiting):
        budget(settings)
