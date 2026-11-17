import json
from types import SimpleNamespace

import httpx
import pytest

from app import config, media, media_import, ninebuddy, store, youtube


@pytest.mark.parametrize('status,attempts,expected', [
    (200, 0, ['9xbuddy']),
    (502, 0, ['9xbuddy', 'yt-dlp']),
    (200, 1, ['9xbuddy', 'yt-dlp probe']),
    (502, 1, ['9xbuddy', 'yt-dlp']),
])
def test_automatic_download_prefers_valid_9xbuddy_file_and_falls_back(client, monkeypatch, status, attempts, expected):
    task_id = store.enqueue('abcdefghijk', 'https://www.youtube.com/watch?v=abcdefghijk')
    folder = store.DATA / 'media' / task_id
    folder.mkdir()
    store.update(task_id, attempts=attempts)
    monkeypatch.setenv('NINEBUDDY_URL', 'http://ninebuddy:8061')
    monkeypatch.setattr(ninebuddy.shutil, 'disk_usage', lambda path: SimpleNamespace(free=10 * 1024**3))
    calls = []

    def service(request):
        calls.append('9xbuddy')
        assert json.loads(request.content) == {'id': 'abcdefghijk'}
        return httpx.Response(status, content=b'mp4', headers={'content-type': 'video/mp4'})

    http_client = httpx.Client(transport=httpx.MockTransport(service))
    monkeypatch.setattr(ninebuddy.httpx, 'Client', lambda **kwargs: http_client)
    monkeypatch.setattr(media_import, 'mp4_duration', lambda path: 30)

    def yt_dlp(settings, args, directory, identifier, **kwargs):
        if '--test' in args:
            calls.append('yt-dlp probe')
            assert directory.parent == folder
            assert kwargs['timeout'] == 45
            raise media.YouTubeError('token')
        if '--skip-download' not in args:
            calls.append('yt-dlp')
            (directory / 'source.mp4').write_bytes(b'fallback')
            (directory / 'source.info.json').write_text('{"id":"abcdefghijk","duration":30}')

    monkeypatch.setattr(youtube, 'execute', yt_dlp)
    media.download(store.task(task_id), config.Settings(min_free_gb=1), folder)
    assert calls == expected
    assert (folder / 'source.mp4').read_bytes() == (b'mp4' if status == 200 else b'fallback')
