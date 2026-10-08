import json
import time

from app import config, media, store, worker


def test_subtitle_429_does_not_block_video_or_asr_fallback(client, tmp_path, monkeypatch):
    task_id = store.enqueue('abcdefghijk', 'https://www.youtube.com/watch?v=abcdefghijk')
    folder = tmp_path / 'task-download'
    folder.mkdir()
    calls = []

    def execute(settings, args, target, requested_task_id):
        assert requested_task_id == task_id
        calls.append(args)
        if len(calls) == 1:
            assert '--write-subs' not in args
            (target / 'source.info.json').write_text(json.dumps({
                'title': 'Example video', 'description': '', 'uploader': 'Channel',
                'duration': 30, 'id': 'abcdefghijk'}), 'utf-8')
            (target / 'source.mp4').write_bytes(b'video')
            return ''
        assert '--skip-download' in args
        raise media.YouTubeError('rate')

    monkeypatch.setattr('app.youtube.execute', execute)
    result = media.download({'id': task_id, 'url': 'https://www.youtube.com/watch?v=abcdefghijk'},
                            config.Settings(), folder)

    assert result['title'] == 'Example video'
    assert (folder / 'source.mp4').is_file()
    assert (folder / 'source.json').is_file()
    assert len(calls) == 2
    assert any('下载英文字幕时 YouTube 返回 429' in row['message']
               for row in store.rows('SELECT message FROM events WHERE task_id=?', (task_id,)))


def test_video_429_still_uses_global_cooldown(client, monkeypatch):
    task_id = store.enqueue('abcdefghijk', 'https://www.youtube.com/watch?v=abcdefghijk')

    def download(*args, **kwargs):
        raise media.YouTubeError('rate')

    monkeypatch.setattr(worker, 'download', download)
    worker.process(task_id)

    assert store.task(task_id)['status'] == 'retrying'
    assert store.task(task_id)['error'].startswith('YouTube 返回 429')
    assert store.get_runtime_state(worker.YOUTUBE_RATE_STATE)['strikes'] == 1


def test_tls_eof_backs_off_all_downloads_until_proxy_recovery(client, monkeypatch):
    first = store.enqueue('abcdefghijk', 'url')
    second = store.enqueue('abcdefghijl', 'url')

    def download(*args, **kwargs):
        raise media.YouTubeError('network')

    monkeypatch.setattr(worker, 'download', download)
    worker.process(first)

    state = store.get_runtime_state(worker.YOUTUBE_NETWORK_STATE)
    assert state['strikes'] == 1
    assert state['until'] > time.time() + 4 * 60
    assert store.task(first)['status'] == 'retrying'
    assert store.task(second)['status'] == 'retrying'
    assert 'TLS/网络连接中断' in store.task(first)['error']

    worker.proxy_changed()
    assert store.task(first)['status'] == 'queued'
    assert store.task(second)['status'] == 'queued'
    assert store.get_runtime_state(worker.YOUTUBE_NETWORK_STATE)['strikes'] == 0
