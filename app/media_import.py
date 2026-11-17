"""Attach an operator-supplied video to a stopped download task."""
import json
import math
import subprocess
import time

from fastapi import HTTPException

from . import store, worker

MAX_BYTES = 512 * 1024**2


def eligible(task):
    payload = task['payload']
    if (task['deleted'] or task['stage'] != 'download'
            or task['status'] not in ('failed', 'waiting', 'paused')
            or any(payload.get(key) for key in ('assets_deleted', 'publication_started', 'bvid'))):
        raise HTTPException(409, '只能给尚未转载且已停止的下载任务补交视频')


def mp4_duration(path):
    try:
        probe = subprocess.run(
            ['ffprobe', '-v', 'error', '-protocol_whitelist', 'file', '-f', 'mov', '-show_entries',
             'format=format_name,duration:stream=codec_type:stream_disposition=attached_pic',
             '-of', 'json', str(path)], capture_output=True, check=True, timeout=20)
        result = json.loads(probe.stdout)
        duration = float(result['format']['duration'])
        streams = {s['codec_type'] for s in result['streams']
                   if not s.get('disposition', {}).get('attached_pic')}
        if ('mp4' not in result['format']['format_name'].split(',')
                or not {'video', 'audio'} <= streams
                or not math.isfinite(duration) or duration <= 0):
            raise ValueError
    except FileNotFoundError as exc:
        raise HTTPException(503, 'NAS 缺少 ffprobe，暂时无法校验上传文件') from exc
    except (subprocess.SubprocessError, ValueError, KeyError, TypeError) as exc:
        raise HTTPException(422, '请选择同时包含画面和音轨的有效 MP4') from exc
    return duration


def install_source(task_id, path):
    duration = mp4_duration(path)
    with worker.ACTIVE_LOCK, store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT * FROM tasks WHERE id=?', (task_id,)).fetchone()
        if row is None:
            raise KeyError(task_id)
        task = dict(row)
        task['payload'] = json.loads(task['payload'])
        eligible(task)
        if task_id in worker.ACTIVE:
            raise HTTPException(409, '任务仍在停止，请稍后再导入')
        folder = store.DATA / 'media' / task_id
        folder.mkdir(exist_ok=True)
        video, checkpoint = folder / 'source.mp4', folder / 'source.json'
        if any(p.exists() for p in (video, checkpoint, folder / 'publish-receipt.json')):
            raise HTTPException(409, '任务已有视频或投稿记录')
        try:
            info = json.loads((folder / 'source.info.json').read_text('utf-8'))
        except (OSError, ValueError):
            info = {}
        if not isinstance(info, dict) or info.get('id') != task['video_id']:
            info = {}
        source = {k: info.get(k) for k in ('title', 'description', 'uploader')}
        source.update(id=task['video_id'], duration=duration)
        source['title'] = source['title'] or task['title'] or task['video_id']
        metadata = path.with_suffix('.json')
        metadata.write_text(json.dumps(source, ensure_ascii=False), 'utf-8')
        path.replace(video)
        try:
            metadata.replace(checkpoint)
            payload = task['payload']
            payload.pop('network_blocked', None)
            payload.pop('youtube_login_required', None)
            now = time.time()
            db.execute("UPDATE tasks SET stage='translate', status='queued', progress=20, "
                       "attempts=0, next_run=0, error='', updated=?, title=?, payload=? WHERE id=?",
                       (now, source['title'], json.dumps(payload, ensure_ascii=False), task_id))
            db.execute('INSERT INTO events(task_id,created,message) VALUES(?,?,?)',
                       (task_id, now, '已补交 MP4，等待字幕处理'))
            db.commit()
        except BaseException:
            checkpoint.unlink(missing_ok=True)
            video.replace(path)
            raise
    return {'ok': True}
