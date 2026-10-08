"""Anonymous-first requests; the stored login cookie is never written by yt-dlp."""
import os
import re
import tempfile
import threading
from pathlib import Path

import httpx

from . import store
from .media import Waiting, YouTubeError, check, run, ytdlp, youtube_cookie_path

COOKIE_LOCK = threading.RLock()
NOTICE = 'YouTube 登录需要处理：请检查账号权限并重新导入 Cookie；请求验证也可能与代理出口有关。'
UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36'


class LoginRequired(Waiting):
    pass


def login_notice():
    with COOKIE_LOCK:
        since = youtube_cookie_path().stat().st_mtime if youtube_cookie_path().exists() else 0
        if not store.rows('SELECT id FROM notices WHERE message=? AND created>=? LIMIT 1', (NOTICE, since)):
            store.notice(NOTICE)


def execute(settings, args, folder, task_id=None, **kwargs):
    try:
        return run(ytdlp(settings) + args, folder, task_id, **kwargs)
    except YouTubeError as exc:
        if exc.kind not in ('auth', 'bot'):
            raise
        first = exc
    with COOKIE_LOCK:
        cookie = youtube_cookie_path()
        snapshot = cookie.read_bytes() if cookie.exists() else None
    if snapshot is None:
        if first.kind == 'bot':
            raise first
        raise LoginRequired('尚未配置 YouTube 登录 Cookie，此视频需要登录或相应访问权限')
    check(task_id)
    if task_id:
        store.event(task_id, '匿名请求需要验证，使用独立 Cookie 副本尝试一次登录请求')
    # A unique private directory avoids racing downloads, polling, or credential imports.
    with tempfile.TemporaryDirectory(prefix='youtube-session-', dir=store.DATA) as directory:
        path = Path(directory) / 'cookies.txt'
        path.write_bytes(snapshot)
        os.chmod(path, 0o600)
        try:
            return run(ytdlp(settings) + ['--cookies', str(path)] + args, folder, task_id, **kwargs)
        except YouTubeError as exc:
            if exc.kind == 'auth':
                raise LoginRequired(str(exc)) from exc
            # Bot challenges/403/rate limits do not prove that the login expired.
            raise


def import_cookie(content):
    import http.cookiejar
    from .media import is_netscape_cookie_file
    if not is_netscape_cookie_file(content):
        raise ValueError('需要 Netscape 格式的 cookies.txt')
    for line in content.splitlines():
        if line.startswith('#HttpOnly_'):
            line = line[len('#HttpOnly_'):]
        elif not line.strip() or line.startswith('#') or line.startswith('\ufeff#'):
            continue
        parts = line.split('\t')
        if len(parts) != 7 or parts[1] not in ('TRUE', 'FALSE') or parts[3] not in ('TRUE', 'FALSE') or (parts[4] and not parts[4].isdigit()):
            raise ValueError('Cookie 记录格式无效，需要 Netscape 的七列制表符格式')
    with COOKIE_LOCK:
        path = youtube_cookie_path()
        tmp = path.with_suffix('.tmp')
        try:
            tmp.write_text(content.lstrip('\ufeff'), 'utf-8')
            os.chmod(tmp, 0o600)
            jar = http.cookiejar.MozillaCookieJar(str(tmp))
            jar.load(ignore_discard=True, ignore_expires=True)
            if not any((c.domain.lstrip('.') == 'youtube.com' or c.domain.endswith('.youtube.com'))
                       and (c.expires in (None, 0) or not c.is_expired()) for c in jar):
                raise ValueError('文件没有未过期的 youtube.com Cookie，请重新导出')
            tmp.replace(path)
        except (http.cookiejar.LoadError, OSError) as exc:
            raise ValueError('Cookie 文件无法解析或保存，请检查 Netscape 格式') from exc
        finally:
            tmp.unlink(missing_ok=True)
    # Only resume tasks explicitly waiting for YouTube login, never paused/cancelled tasks.
    with store.connect() as db:
        count = db.execute("UPDATE tasks SET status='queued',attempts=0,next_run=0,error='' "
            "WHERE deleted=0 AND status='waiting' AND stage='download' "
            "AND json_extract(payload,'$.youtube_login_required')=1").rowcount
    return count


def validate_cookie_session(content, proxy=''):
    """Accept syncs only when YouTube itself confirms the session is logged in."""
    import http.cookiejar
    from .media import is_netscape_cookie_file
    if not is_netscape_cookie_file(content):
        raise ValueError('Cookie 文件格式无效')
    with tempfile.TemporaryDirectory(prefix='youtube-cookie-check-', dir=store.DATA) as directory:
        path = Path(directory) / 'cookies.txt'
        path.write_text(content.lstrip('\ufeff'), 'utf-8')
        jar = http.cookiejar.MozillaCookieJar(str(path))
        try:
            jar.load(ignore_discard=True, ignore_expires=True)
        except (http.cookiejar.LoadError, OSError) as exc:
            raise ValueError('Cookie 文件无法解析') from exc
        if not any((c.domain.lstrip('.').endswith('youtube.com')) and
                   (c.expires in (None, 0) or not c.is_expired()) for c in jar):
            raise ValueError('没有可用的 youtube.com Cookie')
        with httpx.Client(proxy=proxy or None, follow_redirects=True, timeout=25,
                          trust_env=False, headers={'User-Agent': UA}) as client:
            response = client.get('https://www.youtube.com/', cookies=jar)
            response.raise_for_status()
        flags = re.findall(r'"LOGGED_IN"\s*:\s*(true|false)', response.text, flags=re.I)
        if not flags or flags[-1].lower() != 'true':
            raise ValueError('YouTube 未确认此登录会话有效；旧 Cookie 保持不变，请先在浏览器重新登录')
    return import_cookie(content)
