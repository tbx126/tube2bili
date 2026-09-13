import datetime as dt
import json
import logging
import shutil
import threading
import time

import httpx

from . import config, store
from .language import translate
from .media import Reconcile, Stopped, Waiting, check, download, run, ytdlp

STOP = threading.Event()
ACTIVE = set()
ACTIVE_LOCK = threading.Lock()
LOG = logging.getLogger('tube2bili.worker')


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
                'publish': '上传并提交转载稿件', 'subtitles': '提交 B 站播放器字幕', 'verify': '检查播放器字幕可见状态'}[stage])
            payload = task['payload']
            if stage == 'download':
                source = download(task, settings, folder)
                store.update(task_id, title=source.get('title') or task['video_id'], stage='translate', progress=20, attempts=0)
            elif stage == 'translate':
                source = json.loads((folder / 'source.json').read_text('utf-8'))
                metadata = translate(task, settings, folder, source)
                store.update(task_id, title=metadata['title'], stage='publish', progress=65, attempts=0)
            elif stage == 'publish':
                publish(task, settings, folder)
                store.update(task_id, stage='subtitles', progress=85, attempts=0)
            elif stage == 'subtitles':
                subtitles(task, settings, folder)
                store.update(task_id, stage='verify', progress=95, attempts=0)
            elif stage == 'verify':
                verify(task, settings)
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
    except Waiting as exc:
        if store.task(task_id)['status'] not in ('paused', 'cancelled'):
            store.update(task_id, status='waiting', error=str(exc))
            store.notice(f'任务等待处理：{store.task(task_id)["title"]}\n{exc}')
    except Exception as exc:
        LOG.warning('Task %s stopped with %s', task_id, type(exc).__name__)
        task = store.task(task_id)
        if task['status'] not in ('paused', 'cancelled'):
            attempts = task['attempts'] + 1
            # Processing and subtitle review can take hours on the platform.
            limit = 144 if task['stage'] in ('subtitles', 'verify') else 3
            message = '平台仍在处理或字幕不可用，稍后检查' if limit == 144 else '步骤执行失败，请检查服务配置和网络'
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
            with store.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                task = db.execute("SELECT id FROM tasks WHERE deleted=0 AND status IN ('queued','retrying') AND next_run<=? ORDER BY created LIMIT 1", (time.time(),)).fetchone()
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


def poll_channels():
    settings = config.get()
    for channel in store.rows('SELECT * FROM channels WHERE enabled=1 AND last_poll<?', (time.time() - settings.poll_minutes * 60,)):
        if STOP.is_set():
            return
        folder = store.DATA / 'poll' / channel['id']
        folder.mkdir(parents=True, exist_ok=True)
        try:
            output = run(ytdlp(settings) + ['--flat-playlist', '--dump-single-json', '--skip-download', channel['url']], folder, timeout=900, stop_event=STOP)
            value = json.loads(output)
            if not isinstance(value.get('entries'), list):
                raise ValueError()
            ingest(channel, value['entries'])
        except Stopped:
            return
        except Exception:
            message = '频道检查失败，请检查代理、频道地址或 YouTube Cookie'
            if not channel['error']:
                store.notice(f'{channel["name"]}：{message}')
            store.execute('UPDATE channels SET error=?,last_poll=? WHERE id=?', (message, time.time(), channel['id']))


def deliver_notices():
    settings = config.get()
    if not settings.telegram_token or not settings.telegram_chat_id:
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
        except Exception:
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
