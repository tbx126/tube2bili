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

FAILURES = {
    'browser': '浏览器启动或运行失败',
    'formats': '未找到可准备的 MP4 格式',
    'prepare': '网页未能准备下载文件',
    'transfer': '文件传输中断或超过大小限制',
    'timeout': '服务处理超时',
    'service': '服务执行出错',
}


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
    except (httpx.HTTPError, HTTPException, OSError, ValueError) as error:
        if isinstance(error, httpx.HTTPStatusError):
            detail = FAILURES.get(error.response.headers.get('x-ninebuddy-error'),
                                  f'HTTP {error.response.status_code}')
        elif isinstance(error, httpx.TimeoutException):
            detail = '连接或读取超时'
        elif isinstance(error, HTTPException):
            detail = '文件未通过音视频校验'
        elif isinstance(error, httpx.HTTPError):
            detail = '连接或传输失败'
        else:
            detail = '响应不完整、格式无效或磁盘不可写'
        store.event(task['id'], f'9xbuddy {detail}，改用 yt-dlp')
        return None
    store.event(task['id'], '9xbuddy 视频下载完成，使用原声生成字幕')
    return source
