import json
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import store


class Waiting(Exception):
    pass


class Stopped(Exception):
    pass


class Reconcile(Exception):
    pass


def youtube_url(value, channel=False):
    parsed = urlparse(value.strip())
    if parsed.scheme != 'https' or parsed.username or parsed.password or parsed.port not in (None, 443):
        raise ValueError('请输入有效的 HTTPS YouTube 链接')
    host = (parsed.hostname or '').lower()
    if host not in ('www.youtube.com', 'youtube.com', 'm.youtube.com', 'youtu.be'):
        raise ValueError('仅支持 YouTube 链接')
    if channel:
        path = parsed.path.rstrip('/')
        if host == 'youtu.be' or not re.fullmatch(r'/(?:@[\w.\-]+|channel/UC[\w-]+|c/[\w.-]+|user/[\w.-]+)(?:/videos)?', path):
            raise ValueError('请输入频道首页或 /videos 链接')
        if not path.endswith('/videos'):
            path += '/videos'
        return 'https://www.youtube.com' + path
    video_id = parsed.path.strip('/') if host == 'youtu.be' else parse_qs(parsed.query).get('v', [''])[0]
    if not video_id and re.fullmatch(r'/(?:shorts|live)/[\w-]{11}/?', parsed.path):
        video_id = parsed.path.strip('/').split('/')[1]
    if not re.fullmatch(r'[\w-]{11}', video_id, flags=re.ASCII):
        raise ValueError('视频 ID 无效；请提供单个视频链接')
    return video_id, 'https://www.youtube.com/watch?v=' + video_id


def check(task_id):
    if task_id and store.task(task_id)['status'] in ('paused', 'cancelled'):
        raise Stopped()


def run(args, cwd, task_id=None, timeout=7200, stop_event=None):
    """No shell interpolation; drain output to a private file, support cancellation."""
    path = Path(cwd) / 'command.log'
    with path.open('wb') as output:
        env = {**os.environ, 'DATA_DIR': str(store.DATA), 'PYTHONPATH': str(Path(__file__).resolve().parent.parent)}
        process = subprocess.Popen(args, cwd=cwd, stdout=output, stderr=subprocess.STDOUT, env=env,
                                   start_new_session=os.name == 'posix')
        started = time.monotonic()
        try:
            while process.poll() is None:
                check(task_id)
                if stop_event is not None and stop_event.is_set():
                    raise Stopped()
                if time.monotonic() - started > timeout:
                    raise TimeoutError('命令执行超时')
                time.sleep(0.5)
        except BaseException:
            if os.name == 'posix':
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
            else:
                subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                if os.name == 'posix':
                    os.killpg(process.pid, signal.SIGKILL)
                else:
                    process.kill()
                process.wait()
            raise
    text = path.read_text('utf-8', errors='replace')
    if process.returncode:
        # Tool logs can contain credential-bearing URLs. Never expose them in API/log events.
        if any(marker in text.lower() for marker in ('sign in', 'login', 'cookies', '登录')):
            raise Waiting('下载或投稿需要更新登录凭证，请检查账号设置')
        raise RuntimeError(f'外部工具执行失败（退出码 {process.returncode}），请检查连接和配置')
    return text


def ytdlp(settings):
    args = [sys.executable, '-m', 'yt_dlp', '--no-warnings', '--socket-timeout', '30', '--retries', '3', '--js-runtimes', 'node']
    if settings.proxy:
        args += ['--proxy', settings.proxy]
    cookie = store.DATA / 'youtube-cookies.txt'
    if cookie.exists():
        args += ['--cookies', str(cookie)]
    return args


def download(task, settings, folder):
    if (folder / 'source.json').exists():
        return json.loads((folder / 'source.json').read_text('utf-8'))
    args = ytdlp(settings) + [
        '--no-playlist', '--write-info-json', '--write-thumbnail', '--convert-thumbnails', 'jpg',
        '--write-subs', '--write-auto-subs', '--sub-langs', 'en,en-US,en-GB,en-orig',
        '--sub-format', 'srt/best', '--convert-subs', 'srt', '--merge-output-format', 'mp4',
        '-f', 'bv*[height<=1080][ext=mp4][vcodec^=avc1]+ba[ext=m4a]/b[ext=mp4]/bv*[height<=1080]+ba/b',
        '--remux-video', 'mp4', '-o', 'source.%(ext)s', task['url']]
    run(args, folder, task['id'])
    info = json.loads((folder / 'source.info.json').read_text('utf-8'))
    if not (folder / 'source.mp4').exists():
        raise RuntimeError('下载完成但未找到 MP4')
    result = {k: info.get(k) for k in ('title', 'description', 'uploader', 'duration', 'id')}
    (folder / 'source.json').write_text(json.dumps(result, ensure_ascii=False), 'utf-8')
    return result
