import datetime as dt
import json
import logging
import shutil
import threading
import time

import httpx

from . import config, store
from .provider_health import ProviderBlocked
from .language import translate
from .media import Reconcile, RetryLater, Stopped, Waiting, YouTubeError, check, download, run, ytdlp
from .youtube import LoginRequired, execute as youtube_execute, login_notice

STOP = threading.Event()
ACTIVE = set()
ACTIVE_LOCK = threading.Lock()
LOG = logging.getLogger('tube2bili.worker')
YOUTUBE_RATE_STATE = 'youtube_rate_limit'
YOUTUBE_NETWORK_STATE = 'youtube_network_backoff'
AI_BACKOFF_STATE = 'language_api_backoff'


def clear_youtube_rate_limit():
    store.set_runtime_state(YOUTUBE_RATE_STATE, {'strikes': 0, 'until': 0, 'last_at': 0})


def clear_youtube_backoffs():
    clear_youtube_rate_limit()
    store.set_runtime_state(YOUTUBE_NETWORK_STATE, {'strikes': 0, 'until': 0, 'last_at': 0})


def _hold_youtube_downloads(task_id=None, task=None):
    now = time.time()
    state = store.get_runtime_state(YOUTUBE_RATE_STATE, {}) or {}
    strikes = int(state.get('strikes', 0)) if now - float(state.get('last_at', 0)) < 7 * 86400 else 0
    strikes += 1
    needs_attention = strikes >= 3
    cooldown = 24 * 3600 if needs_attention else min(24 * 3600, 3600 * 2 ** (strikes - 1))
    until = now + cooldown
    proxy_hint = 'NAS 代理尚未配置，请先填写可供容器访问的代理地址' if not config.get().proxy else '请检查 NAS 代理出口'
    if needs_attention:
        message = f'YouTube 连续返回 429，已暂停全部待下载任务 24 小时；{proxy_hint}'
    else:
        message = f'YouTube 返回 429，全部视频下载冷却 {cooldown // 60} 分钟后再试；{proxy_hint}'
    store.set_runtime_state(YOUTUBE_RATE_STATE, {'strikes': strikes, 'until': until, 'last_at': now})

    if task is not None:
        attempts = task['attempts'] + 1
        store.update(task_id, status='waiting' if needs_attention else 'retrying',
                     attempts=attempts, next_run=until, error=message)
        store.event(task_id, message)
    rows = store.rows("SELECT id FROM tasks WHERE deleted=0 AND stage='download' "
                      "AND status IN ('queued','retrying') AND id<>COALESCE(?, '')", (task_id,))
    for row in rows:
        store.update(row['id'], status='waiting' if needs_attention else 'retrying',
                     next_run=until, error=message)
        store.event(row['id'], message)
    if strikes == 1 or needs_attention:
        store.notice(message)
    return message


def _hold_youtube_network(task_id=None, task=None):
    """Back off the shared download queue after a transient proxy/TLS failure."""
    now = time.time()
    state = store.get_runtime_state(YOUTUBE_NETWORK_STATE, {}) or {}
    strikes = int(state.get('strikes', 0)) if now - float(state.get('last_at', 0)) < 7 * 86400 else 0
    strikes += 1
    cooldown = min(3600, 300 * 2 ** min(strikes - 1, 4))
    until = now + cooldown
    message = (f'YouTube 与 NAS 代理的 TLS/网络连接中断，下载队列暂停 '
               f'{cooldown // 60} 分钟后自动重试；请检查代理节点健康状态')
    if strikes >= 6:
        message = 'YouTube 与 NAS 代理连续 6 次网络检查失败，已暂停视频重试；每五分钟检测网络，恢复后自动继续'
    store.set_runtime_state(YOUTUBE_NETWORK_STATE, {'strikes': strikes, 'until': until, 'last_at': now})

    if task is not None:
        payload = task['payload']
        payload['network_blocked'] = True
        store.update(task_id, status='waiting' if strikes >= 6 else 'retrying', attempts=task['attempts'] + 1,
                     next_run=until, error=message, payload=payload)
        store.event(task_id, message)
    rows = store.rows("SELECT id FROM tasks WHERE deleted=0 AND stage='download' "
                      "AND status IN ('queued','retrying') AND id<>COALESCE(?, '')", (task_id,))
    for row in rows:
        payload = store.task(row['id'])['payload']
        payload['network_blocked'] = True
        store.update(row['id'], status='waiting' if strikes >= 6 else 'retrying', next_run=until, error=message, payload=payload)
        store.event(row['id'], message)
    if strikes in (1, 6):
        store.notice(message)
    return message


def recover_network():
    """Probe the shared outage slowly, without needing cookies or retrying videos."""
    state = store.get_runtime_state(YOUTUBE_NETWORK_STATE, {}) or {}
    if not state.get('strikes') or time.time() - state.get('probe_at', 0) < 300:
        return
    state['probe_at'] = time.time()
    store.set_runtime_state(YOUTUBE_NETWORK_STATE, state)
    try:
        with httpx.Client(proxy=config.get().proxy or None, timeout=10) as client:
            response = client.get('https://www.youtube.com/generate_204')
            if response.status_code != 204:
                return
    except httpx.HTTPError:
        return
    # Do not clear the independent YouTube 429 cooldown.
    store.set_runtime_state(YOUTUBE_NETWORK_STATE, {})
    for row in store.rows("SELECT id FROM tasks WHERE deleted=0 AND stage='download' AND status IN ('waiting','retrying')"):
        task = store.task(row['id'])
        if not task['payload'].get('network_blocked'):
            continue
        task['payload'].pop('network_blocked', None)
        store.update(row['id'], status='queued', attempts=0, next_run=0, error='', payload=task['payload'])
        store.event(row['id'], 'YouTube 代理健康检查恢复，自动继续下载')
    store.execute("UPDATE channels SET last_poll=0,error='' WHERE enabled=1 AND error LIKE '%网络检查失败%'")
    store.notice('YouTube 代理连接已恢复，频道检查及网络中断的任务自动继续')


def proxy_changed():
    clear_youtube_backoffs()
    now = time.time()
    rows = store.rows("SELECT id FROM tasks WHERE deleted=0 AND stage='download' "
                      "AND status IN ('waiting','retrying') "
                      "AND (error LIKE '%429%' OR error LIKE '%请求限流%' OR error LIKE '%TLS%')")
    for row in rows:
        store.update(row['id'], status='queued', attempts=0, next_run=0, error='')
        store.event(row['id'], 'NAS 代理配置已更新，恢复 YouTube 下载队列')


def process(task_id):
    from .publishing import publish, subtitles, verify
    with ACTIVE_LOCK:
        ACTIVE.add(task_id)
    started = time.monotonic()
    try:
        task = store.task(task_id)
        settings = config.get()
        folder = store.DATA / 'media' / task_id
        folder.mkdir(exist_ok=True)
        if shutil.disk_usage(store.DATA).free < settings.min_free_gb * 1024**3:
            raise Waiting('磁盘剩余空间低于配置阈值，请手动清理文件')
        while True:
            check(task_id)
            settings = config.get()
            task = store.task(task_id)
            stage = task['stage']
            store.event(task_id, {'download': '下载视频与原字幕', 'translate': '生成双语字幕与投稿文案',
                'publish': '上传并提交转载稿件', 'subtitles': '提交 B 站播放器字幕', 'verify': '检查播放器字幕可见状态', 'collection': '加入 B 站合集'}[stage])
            payload = task['payload']
            if stage == 'download':
                source = download(task, settings, folder)
                clear_youtube_backoffs()
                store.update(task_id, title=source.get('title') or task['video_id'], stage='translate', progress=20, attempts=0)
            elif stage == 'translate':
                source = json.loads((folder / 'source.json').read_text('utf-8'))
                metadata = translate(task, settings, folder, source)
                store.set_runtime_state(AI_BACKOFF_STATE, {'until': 0, 'last_at': 0})
                store.update(task_id, title=metadata['title'], stage='publish', progress=65, attempts=0)
            elif stage == 'publish':
                publish(task, settings, folder)
                store.update(task_id, stage='subtitles', progress=85, attempts=0)
            elif stage == 'subtitles':
                subtitles(task, settings, folder)
                store.update(task_id, stage='verify', progress=95, attempts=0)
            elif stage == 'verify':
                verify(task, settings)
                store.update(task_id, stage='collection', progress=98, attempts=0)
            elif stage == 'collection':
                from .collections import add
                add(task, settings, folder)
                check(task_id)
                store.update(task_id, status='completed', progress=100, error='')
                store.event(task_id, '视频与双语播放器字幕已确认可访问')
                store.notice(f'任务完成：{task["title"]}\nhttps://www.bilibili.com/video/{store.task(task_id)["payload"]["bvid"]}')
                break
    except Stopped:
        store.event(task_id, '任务已停止；正在执行的 HTTP 请求可能在停止前完成')
    except Reconcile as exc:
        store.update(task_id, status='reconcile', error=str(exc))
        store.notice(f'需要核对投稿：{store.task(task_id)["title"]}\n{exc}')
    except ProviderBlocked as exc:
        task = store.task(task_id)
        if task['status'] not in ('paused', 'cancelled'):
            previous = store.get_runtime_state(AI_BACKOFF_STATE, {}) or {}
            message = f'{exc}（{exc.code}）；一小时后自动复查，保留处理进度'
            task['payload']['provider_blocked'] = True
            store.set_runtime_state(AI_BACKOFF_STATE, {'until': exc.until, 'reason': message, 'code': exc.code})
            store.update(task_id, status='retrying', next_run=exc.until, error=message, payload=task['payload'])
            store.event(task_id, message)
            if previous.get('code') != exc.code or time.time() - previous.get('notice_at', 0) >= 86400:
                store.notice(message)
                previous['notice_at'] = time.time()
            store.set_runtime_state(AI_BACKOFF_STATE, {'until': exc.until, 'reason': message,
                'code': exc.code, 'notice_at': previous.get('notice_at', 0)})
    except RetryLater as exc:
        task = store.task(task_id)
        if task['status'] not in ('paused', 'cancelled'):
            now = time.time()
            state = store.get_runtime_state(AI_BACKOFF_STATE, {}) or {}
            until = max(now + exc.retry_after, float(state.get('until', 0)))
            store.set_runtime_state(AI_BACKOFF_STATE, {'until': until, 'last_at': now})
            attempts = task['attempts'] + 1
            exhausted = attempts >= 12
            message = str(exc) if not exhausted else '翻译服务连续 12 次重试仍失败，请检查服务配置后继续'
            store.update(task_id, status='waiting' if exhausted else 'retrying', attempts=attempts,
                         next_run=0 if exhausted else until, error=message)
            delay = max(1, int(until - now))
            store.event(task_id, message if exhausted else f'{exc}；{delay} 秒后自动重试')
            if exhausted:
                store.notice(f'任务等待处理：{task["title"]}\n{message}')
            elif attempts == 1:
                store.notice(f'任务暂时等待：{task["title"]}\n{exc}；系统会自动重试')
    except Waiting as exc:
        if store.task(task_id)['status'] not in ('paused', 'cancelled'):
            store.update(task_id, status='waiting', error=str(exc))
            if isinstance(exc, LoginRequired):
                payload = store.task(task_id)['payload']
                payload['youtube_login_required'] = True
                store.update(task_id, payload=payload)
                login_notice()
            else:
                store.notice(f'任务等待处理：{store.task(task_id)["title"]}\n{exc}')
    except YouTubeError as exc:
        task = store.task(task_id)
        if task['status'] not in ('paused', 'cancelled'):
            if exc.kind == 'rate':
                _hold_youtube_downloads(task_id, task)
                return
            if exc.kind == 'network':
                _hold_youtube_network(task_id, task)
                return
            attempts = task['attempts'] + 1
            limit = 3
            status = 'retrying' if attempts < limit else ('waiting' if exc.kind == 'bot' else 'failed')
            payload = task['payload']
            payload['youtube_login_required'] = status == 'waiting'
            store.update(task_id, payload=payload, status=status, attempts=attempts, error=str(exc),
                         next_run=time.time() + min(3600, 300 * 2**(attempts - 1)))
            store.event(task_id, str(exc))
            if status == 'waiting':
                login_notice()
            elif status == 'failed':
                store.notice(f'YouTube 任务失败：{task["title"]}\n{exc}')
    except Exception as exc:
        LOG.warning('Task %s stopped with %s', task_id, type(exc).__name__)
        task = store.task(task_id)
        if task['status'] not in ('paused', 'cancelled'):
            attempts = task['attempts'] + 1
            # Processing and subtitle review can take hours on the platform.
            limit = 144 if task['stage'] in ('subtitles', 'verify') else 3
            message = '平台仍在处理或字幕不可用，稍后检查' if limit == 144 else '步骤执行失败，请检查服务配置和网络'
            if limit == 144:
                result_file = store.DATA / 'media' / task_id / f"{task['stage']}-result.json"
                try:
                    result = json.loads(result_file.read_text('utf-8'))
                    if result.get('error_code') == -404:
                        message = 'B 站稿件暂不可读取（API -404，可能仍在审核/转码或当前不可访问）；保留任务，稍后自动复查'
                except (OSError, ValueError, AttributeError):
                    pass
            if attempts >= limit:
                store.update(task_id, status='failed', attempts=attempts, error=message)
                store.notice(f'任务失败：{task["title"]}\n{message}')
            else:
                store.update(task_id, status='retrying', attempts=attempts, error=message,
                             next_run=time.time() + (600 if limit == 144 else 60 * 2**(attempts - 1)))
            store.event(task_id, message)
    finally:
        task = store.task(task_id)
        payload = task['payload']
        payload['elapsed_seconds'] = payload.get('elapsed_seconds', 0) + time.monotonic() - started
        store.update(task_id, payload=payload)
        with ACTIVE_LOCK:
            ACTIVE.discard(task_id)


def worker_loop():
    while not STOP.is_set():
        try:
            now = time.time()
            cooldown_until = float((store.get_runtime_state(YOUTUBE_RATE_STATE, {}) or {}).get('until', 0))
            network_until = float((store.get_runtime_state(YOUTUBE_NETWORK_STATE, {}) or {}).get('until', 0))
            ai_until = float((store.get_runtime_state(AI_BACKOFF_STATE, {}) or {}).get('until', 0))
            with store.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                task = db.execute("SELECT id FROM tasks WHERE deleted=0 AND status IN ('queued','retrying') "
                    "AND next_run<=? AND (stage!='download' OR ?<=?) AND (stage NOT IN ('translate','publish') OR ?<=?) "
                    "ORDER BY created LIMIT 1",
                    (now, max(cooldown_until, network_until), now, ai_until, now)).fetchone()
                if task:
                    db.execute("UPDATE tasks SET status='running',updated=? WHERE id=?", (time.time(), task['id']))
            if task:
                process(task['id'])
        except Exception as exc:
            # A scheduler iteration must never terminate the background service.
            LOG.error('Worker iteration failed: %s', type(exc).__name__)
        STOP.wait(2)


def ingest(channel, entries):
    """Baseline and seen IDs are committed with tasks, so failures cannot lose new videos."""
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        current = db.execute('SELECT * FROM channels WHERE id=?', (channel['id'],)).fetchone()
        if not current or not current['enabled']:
            return
        for entry in reversed(entries):
            video_id = entry.get('id', '')
            import re
            if not re.fullmatch(r'[\w-]{11}', video_id, flags=re.ASCII):
                continue
            existed = db.execute('SELECT 1 FROM seen WHERE channel_id=? AND video_id=?', (channel['id'], video_id)).fetchone()
            db.execute('INSERT OR IGNORE INTO seen VALUES(?,?)', (channel['id'], video_id))
            if current['initialized'] and not existed and entry.get('live_status') not in ('is_live', 'is_upcoming', 'was_live', 'post_live'):
                if '/shorts/' not in (entry.get('url') or ''):
                    store.enqueue(video_id, 'https://www.youtube.com/watch?v=' + video_id, channel['id'], json.loads(current['options']), db)
        db.execute("UPDATE channels SET initialized=1,last_poll=?,error='' WHERE id=?", (time.time(), channel['id']))


def channel_entries(settings, channel, folder):
    """Read the recent window; expand only until the previous baseline is found."""
    for limit in (50, 100, 200, 400):
        output = youtube_execute(settings, ['--flat-playlist', '--dump-single-json', '--skip-download',
            '--playlist-end', str(limit), channel['url']], folder, timeout=180, stop_event=STOP)
        entries = json.loads(output).get('entries')
        if not isinstance(entries, list):
            raise ValueError('Invalid channel response')
        if not channel['initialized'] or len(entries) < limit:
            return entries
        ids = [entry.get('id', '') for entry in entries]
        placeholders = ','.join('?' for _ in ids)
        if store.rows(f'SELECT 1 FROM seen WHERE channel_id=? AND video_id IN ({placeholders}) LIMIT 1',
                      (channel['id'], *ids)):
            return entries
    raise Waiting('频道近期 400 条视频仍未找到上次检查基线，请检查频道来源或手动补录更早的视频；未跳过未处理视频')


def poll_channels():
    recover_network()
    settings = config.get()
    for channel in store.rows('SELECT * FROM channels WHERE enabled=1 AND last_poll<?', (time.time() - settings.poll_minutes * 60,)):
        if STOP.is_set():
            return
        rate_until = float((store.get_runtime_state(YOUTUBE_RATE_STATE, {}) or {}).get('until', 0))
        network_until = float((store.get_runtime_state(YOUTUBE_NETWORK_STATE, {}) or {}).get('until', 0))
        if max(rate_until, network_until) > time.time():
            return
        folder = store.DATA / 'poll' / channel['id']
        folder.mkdir(parents=True, exist_ok=True)
        try:
            ingest(channel, channel_entries(settings, channel, folder))
        except Stopped:
            return
        except Waiting as exc:
            message = str(exc)
            if isinstance(exc, LoginRequired):
                login_notice()
            elif not channel['error']:
                store.notice(f'{channel["name"]}：{message}')
            store.execute('UPDATE channels SET error=?,last_poll=? WHERE id=?', (message, time.time(), channel['id']))
        except YouTubeError as exc:
            message = _hold_youtube_downloads() if exc.kind == 'rate' else _hold_youtube_network() if exc.kind == 'network' else str(exc)
            if not channel['error']:
                store.notice(f'{channel["name"]}：{message}')
            store.execute('UPDATE channels SET error=?,last_poll=? WHERE id=?', (message, time.time(), channel['id']))
        except Exception:
            message = '频道检查失败，请检查代理、频道地址或 YouTube Cookie'
            if not channel['error']:
                store.notice(f'{channel["name"]}：{message}')
            store.execute('UPDATE channels SET error=?,last_poll=? WHERE id=?', (message, time.time(), channel['id']))


def deliver_notices():
    settings = config.get()
    if not settings.telegram_token or not settings.telegram_chat_id:
        return
    now = time.time()
    hold = store.get_runtime_state('telegram_delivery', {}) or {}
    if hold.get('until', 0) > now:
        return
    for notice in store.rows('SELECT * FROM notices WHERE sent=0 AND attempts<5 AND next_run<=? ORDER BY id LIMIT 10', (time.time(),)):
        try:
            with httpx.Client(timeout=20, proxy=settings.proxy or None) as client:
                response = client.post(f'https://api.telegram.org/bot{settings.telegram_token}/sendMessage',
                    json={'chat_id': settings.telegram_chat_id, 'text': notice['message'][:4000]})
                response.raise_for_status()
                if not response.json().get('ok'):
                    raise ValueError()
            store.execute('UPDATE notices SET sent=1 WHERE id=?', (notice['id'],))
            store.set_runtime_state('telegram_delivery', {})
        except httpx.TransportError as exc:
            # An outage is shared, not ten independent failed messages. Preserve
            # delivery attempts and retry the transport later without dropping notices.
            store.set_runtime_state('telegram_delivery', {'until': now + 300,
                'error': f'Telegram 网络连接失败（{type(exc).__name__}），五分钟后自动继续'})
            break
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            if status == 429 or status >= 500:
                delay = 300
                if status == 429:
                    try:
                        delay = max(30, min(3600, int(exc.response.json()['parameters']['retry_after'])))
                    except (ValueError, TypeError, KeyError):
                        pass
                store.set_runtime_state('telegram_delivery', {'until': now + delay,
                    'error': f'Telegram 返回 HTTP {status}，稍后自动重试'})
                break
            store.execute('UPDATE notices SET attempts=attempts+1,next_run=? WHERE id=?', (now + 3600, notice['id']))
            store.set_runtime_state('telegram_delivery', {'until': now + 3600,
                'error': f'Telegram 返回 HTTP {status}，请检查 Bot 和会话权限'})
            break
        except (ValueError, KeyError, TypeError):
            store.execute('UPDATE notices SET attempts=attempts+1,next_run=? WHERE id=?', (time.time() + 60 * 2**notice['attempts'], notice['id']))


def scheduler_loop():
    while not STOP.is_set():
        try:
            poll_channels()
        except Exception as exc:
            LOG.error('Scheduler iteration failed: %s', type(exc).__name__)
        STOP.wait(15)


def notification_loop():
    while not STOP.is_set():
        try:
            deliver_notices()
        except Exception as exc:
            LOG.error('Notification iteration failed: %s', type(exc).__name__)
        STOP.wait(15)


def start():
    STOP.clear()
    threads = [threading.Thread(target=fn, daemon=True, name=fn.__name__) for fn in (worker_loop, scheduler_loop, notification_loop)]
    for thread in threads:
        thread.start()
    return threads
