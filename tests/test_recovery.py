import copy
import importlib.util
import json
from pathlib import Path
import time

import httpx
import pytest

from app import config, dashboard, language, media, provider_health, store, worker


def mock_http(monkeypatch, handler):
    original = httpx.Client
    monkeypatch.setattr(httpx, 'Client', lambda **kw: original(transport=httpx.MockTransport(handler)))


def test_arrearage_is_safe_cached_and_fallback_can_recover(client, monkeypatch):
    settings = config.Settings()
    settings.translation.primary = config.Route(protocol='qwen', base_url='https://primary.test/v1', model='qwen-plus', api_key='secret')
    seen = []
    def handler(request):
        seen.append(request.url.host)
        if request.url.host == 'primary.test':
            return httpx.Response(400, json={'error': {'code': 'Arrearage', 'message': 'secret unsafe text'}})
        return httpx.Response(200, json={'choices': [{'message': {'content': '{"ok":true}'}}]})
    mock_http(monkeypatch, handler)
    for _ in range(2):
        with pytest.raises(provider_health.ProviderBlocked, match='欠费') as error:
            language.chat(None, settings, 'Translate', {})
        assert 'secret' not in str(error.value)
    assert seen == ['primary.test']
    settings.translation.fallback_enabled = True
    settings.translation.fallback = config.Route(base_url='https://backup.test/v1', model='b')
    assert language.chat(None, settings, 'Translate', {}) == {'ok': True}
    assert seen == ['primary.test', 'backup.test']
    settings.translation.primary.api_key = 'new-secret'
    assert provider_health.blocked(settings.translation.primary) is None


def test_account_hold_does_not_consume_task_attempts_or_spam(client, monkeypatch):
    task_id = store.enqueue('abcdefghijk', 'url')
    folder = store.DATA / 'media' / task_id
    folder.mkdir()
    (folder / 'source.json').write_text('{}')
    store.update(task_id, status='running', stage='translate', attempts=2)
    def fail(*args):
        raise provider_health.ProviderBlocked('欠费', 'Arrearage', time.time() + 3600)
    monkeypatch.setattr(worker, 'translate', fail)
    worker.process(task_id)
    worker.process(task_id)
    task = store.task(task_id)
    assert task['status'] == 'retrying' and task['attempts'] == 2
    assert task['next_run'] > time.time() + 3500
    assert len(store.rows('SELECT * FROM notices')) == 1
    assert '欠费' in dashboard.service_health()['translation_error']


def test_network_recovery_preserves_rate_hold_and_manual_pause(client, monkeypatch):
    first = store.enqueue('abcdefghijk', 'url')
    second = store.enqueue('abcdefghijl', 'url')
    worker._hold_youtube_network(first, store.task(first))
    store.update(second, status='paused')
    store.set_runtime_state(worker.YOUTUBE_RATE_STATE, {'until': time.time() + 600})
    mock_http(monkeypatch, lambda request: httpx.Response(204))
    worker.recover_network()
    assert store.task(first)['status'] == 'queued'
    assert store.task(second)['status'] == 'paused'
    assert store.get_runtime_state(worker.YOUTUBE_RATE_STATE)['until'] > time.time()
    assert not store.get_runtime_state(worker.YOUTUBE_NETWORK_STATE)


def test_telegram_transport_outage_preserves_message_and_attempts(client, monkeypatch):
    settings = config.get()
    settings.telegram_token = 'secret-token'
    settings.telegram_chat_id = '123'
    config.save(settings)
    store.notice('test')
    healthy = [False]
    def fail(request):
        if healthy[0]:
            return httpx.Response(200, json={'ok': True})
        raise httpx.ConnectError('network unavailable', request=request)
    mock_http(monkeypatch, fail)
    worker.deliver_notices()
    notice = store.rows('SELECT * FROM notices')[0]
    assert notice['attempts'] == 0 and notice['sent'] == 0
    assert 'secret-token' not in store.get_runtime_state('telegram_delivery')['error']
    store.set_runtime_state('telegram_delivery', {})
    healthy[0] = True
    worker.deliver_notices()
    assert store.rows('SELECT sent FROM notices')[0]['sent'] == 1


def test_range_failure_preserves_partial_and_retries_once(client, monkeypatch):
    task_id = store.enqueue('abcdefghijk', 'url')
    folder = store.DATA / 'media' / task_id
    folder.mkdir(parents=True)
    (folder / 'source.f299.mp4.part').write_bytes(b'partial')
    (folder / 'unrelated.part').write_bytes(b'keep')
    calls = []
    def execute(settings, args, target, tid):
        calls.append(args)
        if len(calls) == 1:
            raise media.YouTubeError('range')
        if '--skip-download' in args:
            return ''
        assert '--no-continue' in args
        (target / 'source.mp4').write_bytes(b'video')
        (target / 'source.info.json').write_text('{"title":"Example"}')
    monkeypatch.setattr('app.youtube.execute', execute)
    media.download({'id': task_id, 'url': 'url'}, config.Settings(), folder)
    assert len(calls) == 3
    assert next(folder.glob('partial-recovery-*/source.f299.mp4.part')).read_bytes() == b'partial'
    assert (folder / 'unrelated.part').read_bytes() == b'keep'
    assert media.youtube_error_kind('ERROR: HTTP Error 416: Requested range not satisfiable') == 'range'
    assert media.youtube_error_kind('ERROR: API page: HTTP Error 500\n' + json.dumps(
        {'title': 'error in chess', 'view_count': 429, 'description': 'sign in proxy ssl 403'})) == 'other'


def load_script(name):
    path = Path(__file__).resolve().parents[1] / 'scripts' / (name + '.py')
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_subscription_cannot_override_nas_settings():
    refresh = load_script('refresh_nas_proxy')
    original = {'tun': {'enable': True}, 'dns': {'enable': True}, 'secret': 'private',
                'rules': ['PROCESS-NAME,cloudflared,DIRECT', 'GEOIP,CN,DIRECT', 'MATCH,PROXY']}
    incoming = {'tun': {'enable': False}, 'secret': 'attacker', 'proxies': [
        {'name': '剩余流量', 'type': 'trojan'},
        {'name': 'node', 'type': 'trojan', 'server': 'node.test', 'port': 443, 'password': 'secret'}]}
    value = refresh.merge_nodes(copy.deepcopy(original), incoming)
    assert value['tun'] == original['tun'] and value['secret'] == 'private'
    assert len(value['proxies']) == 1
    assert value['proxy-groups'][0]['type'] == 'fallback'
    assert 'GEOIP,CN,DIRECT' in value['rules']
    with pytest.raises(ValueError):
        refresh.merge_nodes(copy.deepcopy(original), {'proxies': []})


def test_tunnel_restart_is_bounded_and_requires_healthy_proxy():
    watch = load_script('watch_nas_network')
    state = {'failures': 2}
    assert not watch.should_restart(state, False, False, 10000)
    assert watch.should_restart(state, False, True, 10000)
    state['restart_at'] = 9999
    assert not watch.should_restart(state, False, True, 10000)
    assert not watch.should_restart(state, True, True, 10000)
    assert state['failures'] == 0


def test_asr_billing_error_is_classified(client, monkeypatch):
    settings = config.Settings()
    settings.transcription.primary = config.Route(base_url='https://asr.test/v1', model='asr')
    task_id = store.enqueue('abcdefghijk', 'url')
    folder = store.DATA / 'media' / task_id
    folder.mkdir(parents=True)
    def audio(*args):
        (folder / 'audio-0.mp3').write_bytes(b'audio')
    monkeypatch.setattr(language, 'run', audio)
    mock_http(monkeypatch, lambda request: httpx.Response(400, json={'error': {'code': 'Arrearage'}}))
    with pytest.raises(provider_health.ProviderBlocked, match='欠费'):
        language.transcribe(store.task(task_id), settings, folder, 30)


def test_stale_monitor_cannot_claim_healthy(client):
    (store.DATA / 'network-health.json').write_text(json.dumps({'checked_at': time.time() - 600,
        'proxy': True, 'tunnel': True, 'app': True}))
    health = dashboard.service_health()['network']
    assert health['stale'] and health['proxy'] is None


def test_audio_stream_billing_error_survives_context_close(client, monkeypatch):
    settings = config.Settings()
    settings.transcription.primary = config.Route(protocol='qwen_audio', base_url='https://asr.test/compatible-mode/v1', model='qwen-audio-3.0-asr-flash')
    task_id = store.enqueue('abcdefghijk', 'url')
    folder = store.DATA / 'media' / task_id
    folder.mkdir(parents=True)
    monkeypatch.setattr(language, 'run', lambda *args: (folder / 'audio-0.mp3').write_bytes(b'audio'))
    mock_http(monkeypatch, lambda request: httpx.Response(400, json={'code': 'Arrearage'}))
    with pytest.raises(provider_health.ProviderBlocked, match='欠费'):
        language.transcribe(store.task(task_id), settings, folder, 30)


def test_channel_poll_is_bounded_and_expands_to_known_baseline(client, monkeypatch):
    cid = client.post('/api/channels', json={'name': 'test', 'url': 'https://www.youtube.com/@test'}).json()['id']
    channel = store.rows('SELECT * FROM channels WHERE id=?', (cid,))[0]
    worker.ingest(channel, [{'id': 'abcdefghijk'}])
    channel['initialized'] = 1
    limits = []
    def execute(settings, args, folder, **kwargs):
        limit = int(args[args.index('--playlist-end') + 1])
        limits.append(limit)
        entries = [{'id': f'{i:011d}'} for i in range(limit)]
        if limit == 100:
            entries[-1] = {'id': 'abcdefghijk'}
        return json.dumps({'entries': entries})
    monkeypatch.setattr(worker, 'youtube_execute', execute)
    entries = worker.channel_entries(config.Settings(), channel, store.DATA)
    assert limits == [50, 100] and len(entries) == 100


def test_channel_gap_never_silently_marks_unseen_videos_seen(client, monkeypatch):
    channel = {'id': 'missing', 'initialized': 1, 'url': 'url'}
    def execute(settings, args, folder, **kwargs):
        limit = int(args[args.index('--playlist-end') + 1])
        return json.dumps({'entries': [{'id': f'{i:011d}'} for i in range(limit)]})
    monkeypatch.setattr(worker, 'youtube_execute', execute)
    with pytest.raises(media.Waiting, match='400'):
        worker.channel_entries(config.Settings(), channel, store.DATA)
    assert not store.rows('SELECT * FROM seen')
