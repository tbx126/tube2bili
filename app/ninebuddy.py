"""Optional private 9xbuddy browser service, with a bounded yt-dlp fallback."""
import json
import os
import shutil
import tempfile
import time
from pathlib import Path
from urllib.parse import unquote

import httpx
from fastapi import HTTPException

from . import store
from .media import check


def acquire(task, settings, folder):
    endpoint = os.getenv('NINEBUDDY_URL')
    if not endpoint or (folder / 'source.mp4').exists():
        return None

    from .media_import import MAX_BYTES, mp4_duration
    limit = min(MAX_BYTES, shutil.disk_usage(folder).free - settings.min_free_gb * 1024**3)
    if limit <= 0:
        return None
    deadline = time.monotonic() + 205
    store.event(task['id'], '正在尝试 9xbuddy 下载')
    try:
        with tempfile.TemporaryDirectory(prefix='.ninebuddy-', dir=folder) as temp:
            video = Path(temp) / 'source.mp4'
            with httpx.Client(timeout=httpx.Timeout(18, read=195), trust_env=False) as client:
                with client.stream('POST', endpoint + '/download', json={'id': task['video_id']}) as response:
                    response.raise_for_status()
                    size = int(response.headers.get('content-length', 0))
                    if response.headers.get('content-type') != 'video/mp4' or not 0 < size <= limit:
                        raise ValueError
                    received = 0
                    with video.open('wb') as output:
                        for chunk in response.iter_bytes(128 * 1024):
                            check(task['id'])
                            received += len(chunk)
                            if received > size or time.monotonic() > deadline:
                                raise ValueError
                            output.write(chunk)
                    if received != size:
                        raise ValueError
                    title = unquote(response.headers.get('x-video-title', ''))[:1000]
                    uploader = unquote(response.headers.get('x-video-uploader', ''))[:300]
            source = {'id': task['video_id'], 'title': title or task['title'] or task['video_id'],
                      'description': '', 'uploader': uploader, 'duration': mp4_duration(video),
                      'download_provider': '9xbuddy'}
            try:
                previous = json.loads((folder / 'source.info.json').read_text('utf-8'))
                if previous.get('id') == task['video_id']:
                    for key in ('title', 'description', 'uploader'):
                        source[key] = previous.get(key) or source[key]
            except (OSError, ValueError, AttributeError):
                pass
            check(task['id'])
            checkpoint = video.with_suffix('.json')
            checkpoint.write_text(json.dumps(source, ensure_ascii=False), 'utf-8')
            video.replace(folder / 'source.mp4')
            try:
                checkpoint.replace(folder / 'source.json')
            except OSError:
                (folder / 'source.mp4').replace(video)
                raise
    except (httpx.HTTPError, HTTPException, OSError, ValueError):
        store.event(task['id'], '9xbuddy 未返回可用的带声 MP4，改用 yt-dlp')
        return None
    store.event(task['id'], '9xbuddy 视频下载完成，使用原声生成字幕')
    return source
