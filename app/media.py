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


class RetryLater(RuntimeError):
    """A provider error that should be retried without losing task progress."""

    def __init__(self, message, retry_after=300):
        self.retry_after = max(30, min(3600, int(retry_after)))
        super().__init__(message)


class Stopped(Exception):
    pass


class Reconcile(Exception):
    pass


class YouTubeError(RuntimeError):
    def __init__(self, kind):
        self.kind = kind
        super().__init__({'bot': 'YouTube 要求验证请求，可能与代理出口或会话有关，不能据此判定 Cookie 失效',
            'auth': '此视频需要有效登录或访问权限，请检查 YouTube Cookie 与账号权限',
            'rate': 'YouTube 请求限流，稍后自动重试，请保持代理出口稳定',
            'token': 'YouTube 令牌或媒体请求被拒绝，请检查 PO Token 服务及代理出口',
            'network': 'YouTube 网络请求失败，请检查代理与网络连接',
            'other': 'YouTube 下载失败，请检查视频可用性及工具日志'}[kind])


def youtube_error_kind(text):
    low = text.casefold()
    errors = '\n'.join(line for line in low.splitlines() if 'error' in line)
    if any(x in errors for x in ('429', 'too many requests', "this content isn't available, try again later")):
        return 'rate'
    if 'not a bot' in errors:
        return 'bot'
    if any(x in errors for x in ('sign in', 'members-only', 'private video', 'confirm your age', 'cookies are no longer valid')):
        return 'auth'
    if any(x in errors for x in ('po token', 'po_token', '403')):
        return 'token'
    if any(x in errors for x in (
            'timed out', 'connection', 'resolve', 'proxy', 'network',
            'ssl', 'tls', 'unexpected_eof', 'eof occurred', 'handshake',
            'connection reset', 'connection refused', 'remote end closed')):
        return 'network'
    return 'other'


YOUTUBE_AUTH_MARKERS = (
    'sign in to confirm',
    'use --cookies-from-browser',
    'use --cookies for the authentication',
    'confirm you\u2019re not a bot',
    "confirm you're not a bot",
)


def youtube_cookie_path():
    return store.DATA / 'youtube-cookies.txt'


def is_youtube_auth_error(output):
    """Match explicit yt-dlp YouTube auth failures without false positives."""
    lowered = output.casefold()
    return any(marker in lowered for marker in YOUTUBE_AUTH_MARKERS)


def youtube_auth_waiting():
    if youtube_cookie_path().is_file():
        return Waiting('YouTube 登录或请求验证未通过，请检查 Cookie、账号权限和代理出口；不能仅据此判定 Cookie 失效')
    return Waiting('尚未配置 YouTube 登录 Cookie，请在「服务设置 → YouTube」导入 Netscape 格式 cookies.txt')


def is_netscape_cookie_file(content):
    """Validate the strict header used by browser cookie exports."""
    content = content.lstrip('\ufeff')
    first = next((line.strip() for line in content.splitlines() if line.strip()), '')
    return first in ('# HTTP Cookie File', '# Netscape HTTP Cookie File')


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
        if 'yt_dlp' in args:
            raise YouTubeError(youtube_error_kind(text))
        raise RuntimeError(f'外部工具执行失败（退出码 {process.returncode}），请检查连接和配置')
    return text


def ytdlp(settings):
    args = [sys.executable, '-m', 'yt_dlp', '--no-warnings', '--socket-timeout', '30', '--retries', '3', '--js-runtimes', 'node',
            '--sleep-requests', '1', '--sleep-interval', str(settings.youtube_sleep_seconds),
            '--max-sleep-interval', str(settings.youtube_sleep_seconds + 5)]
    if settings.proxy:
        args += ['--proxy', settings.proxy]
    provider = os.environ.get('POT_PROVIDER_URL', '')
    if provider:
        args += ['--extractor-args', 'youtubepot-bgutilhttp:base_url=' + provider,
                 '--extractor-args', 'youtube:player_client=mweb']
    return args


def download(task, settings, folder):
    if (folder / 'source.json').exists():
        return json.loads((folder / 'source.json').read_text('utf-8'))
    video_args = [
        '--no-playlist', '--write-info-json', '--write-thumbnail', '--convert-thumbnails', 'jpg',
        '--merge-output-format', 'mp4',
        '-f', 'bv*[height<=1080][ext=mp4][vcodec^=avc1]+ba[ext=m4a]/b[ext=mp4]/bv*[height<=1080]+ba/b',
        '--remux-video', 'mp4', '-o', 'source.%(ext)s', task['url']]
    from .youtube import execute
    # Keep subtitle endpoints out of the critical video download. YouTube can
    # rate-limit captions independently; yt-dlp otherwise exits nonzero and
    # prevents a usable video from reaching the ASR fallback.
    execute(settings, video_args, folder, task['id'])
    info = json.loads((folder / 'source.info.json').read_text('utf-8'))
    if not (folder / 'source.mp4').exists():
        raise RuntimeError('下载完成但未找到 MP4')
    result = {k: info.get(k) for k in ('title', 'description', 'uploader', 'duration', 'id')}
    (folder / 'source.json').write_text(json.dumps(result, ensure_ascii=False), 'utf-8')
    subtitle_args = [
        '--no-playlist', '--skip-download', '--write-subs', '--write-auto-subs',
        '--sub-langs', 'en,en-US,en-GB,en-orig', '--sub-format', 'srt/best',
        '--convert-subs', 'srt', '-o', 'source.%(ext)s', task['url']]
    try:
        execute(settings, subtitle_args, folder, task['id'])
    except (Waiting, YouTubeError) as exc:
        reason = ('下载英文字幕时 YouTube 返回 429' if isinstance(exc, YouTubeError) and exc.kind == 'rate'
                  else '英文字幕暂不可用')
        store.event(task['id'], f'{reason}；视频已保存，将尝试从音频识别字幕')
    return result
