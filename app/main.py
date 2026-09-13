import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import shutil
import sys
import time
import uuid
import httpx
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, ValidationError

from . import accounts, config, store, worker
from .media import Waiting, is_netscape_cookie_file, youtube_url

load_dotenv()
ROOT = Path(__file__).resolve().parent
LOGIN_FAILURES = {}


@asynccontextmanager
async def lifespan(app):
    password = os.environ.get('DASHBOARD_PASSWORD', '')
    if len(password) < 12:
        raise RuntimeError('请设置至少 12 位的 DASHBOARD_PASSWORD（.env 文件）')
    store.init()
    secret_file = store.DATA / 'session.key'
    if not secret_file.exists():
        secret_file.write_bytes(secrets.token_bytes(32))
        os.chmod(secret_file, 0o600)
    app.state.signing_key = hashlib.sha256(secret_file.read_bytes() + password.encode()).digest()
    app.state.password_hash = hashlib.sha256(password.encode()).digest()
    threads = [] if os.environ.get('DISABLE_WORKER') == '1' else worker.start()
    yield
    worker.STOP.set()
    for task in store.rows("SELECT id FROM tasks WHERE status='running'"):
        store.update(task['id'], status='paused', error='服务关闭时暂停，请继续任务')
    for thread in threads:
        thread.join(timeout=3)


app = FastAPI(title='Tube2Bili', lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)


def signed(value):
    return hmac.new(app.state.signing_key, value.encode(), hashlib.sha256).hexdigest()


def authenticated(request):
    token = request.cookies.get('tube_session', '')
    try:
        expiry, nonce, signature = token.split('.')
        return int(expiry) > time.time() and hmac.compare_digest(signed(expiry + '.' + nonce), signature)
    except (ValueError, AttributeError):
        return False


@app.middleware('http')
async def guard(request: Request, call_next):
    if request.url.path.startswith('/api/'):
        if request.method not in ('GET', 'HEAD') and request.headers.get('X-Requested-With') != 'Tube2Bili':
            return JSONResponse({'detail': '请求来源无效'}, status_code=403)
        if request.url.path != '/api/login' and not authenticated(request):
            return JSONResponse({'detail': '请先登录'}, status_code=401)
    response = await call_next(request)
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['Referrer-Policy'] = 'no-referrer'
    response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'self'"
    if request.url.path.startswith('/static/'):
        response.headers['Cache-Control'] = 'no-cache'
    if request.url.path.startswith('/api/'):
        response.headers['Cache-Control'] = 'no-store'
    return response


@app.exception_handler(RequestValidationError)
async def validation_error(request, exc):
    return JSONResponse({'detail': '提交内容格式不正确，请检查字段'}, status_code=422)


@app.exception_handler(KeyError)
async def missing(request, exc):
    return JSONResponse({'detail': '记录不存在'}, status_code=404)


class Login(BaseModel):
    password: str = Field(max_length=1000)


@app.post('/api/login')
def login(value: Login, request: Request, response: Response):
    peer = request.client.host if request.client else 'unknown'
    failures = [x for x in LOGIN_FAILURES.get(peer, []) if x > time.time() - 900]
    if len(failures) >= 8:
        raise HTTPException(429, '尝试过于频繁，请 15 分钟后再试')
    if not hmac.compare_digest(hashlib.sha256(value.password.encode()).digest(), app.state.password_hash):
        LOGIN_FAILURES[peer] = failures + [time.time()]
        raise HTTPException(401, '密码不正确')
    LOGIN_FAILURES.pop(peer, None)
    value = str(int(time.time() + 86400)) + '.' + secrets.token_hex(16)
    response.set_cookie('tube_session', value + '.' + signed(value), httponly=True, samesite='strict',
                        secure=os.environ.get('COOKIE_SECURE') == '1', max_age=86400)
    return {'ok': True}


@app.post('/api/logout')
def logout(response: Response):
    response.delete_cookie('tube_session')
    return {'ok': True}


@app.get('/healthz')
def health():
    store.rows('SELECT 1')
    return {'status': 'ok'}


@app.get('/api/overview')
def overview():
    tasks = store.rows('SELECT * FROM tasks WHERE deleted=0 ORDER BY created DESC LIMIT 300')
    for task in tasks:
        payload = json.loads(task.pop('payload'))
        task['bvid'] = payload.get('bvid')
        task['elapsed_seconds'] = payload.get('elapsed_seconds', 0)
        task['assets_deleted'] = payload.get('assets_deleted', False)
    disk = shutil.disk_usage(store.DATA)
    stats = store.rows("SELECT COUNT(*) total, SUM(status='completed') completed, SUM(status IN ('waiting','failed','reconcile')) attention FROM tasks")[0]
    usage = store.rows('SELECT COALESCE(SUM(cost),0) cost, COALESCE(SUM(tokens),0) tokens FROM usage')[0]
    daily = store.rows("SELECT date(created,'unixepoch') day, COUNT(*) count FROM tasks WHERE created>? GROUP BY day", (time.time() - 7 * 86400,))
    return {'tasks': tasks, 'stats': stats, 'usage': usage, 'daily': daily,
            'disk': {'total': disk.total, 'free': disk.free},
            'notices': store.rows('SELECT * FROM notices ORDER BY id DESC LIMIT 20'),
            'worker_enabled': os.environ.get('DISABLE_WORKER') != '1'}


class NewTask(BaseModel):
    url: str = Field(max_length=1000)


@app.post('/api/tasks')
def create_task(value: NewTask):
    try:
        video_id, url = youtube_url(value.url)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    task_id = store.enqueue(video_id, url, options=config.get().posting.model_dump())
    store.execute('UPDATE tasks SET deleted=0 WHERE id=?', (task_id,))
    return {'id': task_id}


@app.delete('/api/tasks/{task_id}')
def delete_task(task_id: str):
    # Keep the deduplication/publication receipt and media. Serialize with scheduler.
    with worker.ACTIVE_LOCK, store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        task = db.execute('SELECT status,payload FROM tasks WHERE id=?', (task_id,)).fetchone()
        if not task:
            raise HTTPException(404, '任务不存在')
        payload = json.loads(task['payload'])
        if task_id in worker.ACTIVE or task['status'] in ('running', 'reconcile') or (payload.get('publication_started') and not payload.get('bvid')):
            raise HTTPException(409, '任务正在执行或投稿结果待核对，请先暂停并等待停止，或核对投稿')
        db.execute("UPDATE tasks SET deleted=1, status=CASE WHEN status='completed' THEN status ELSE 'cancelled' END, updated=? WHERE id=?", (time.time(), task_id))
    store.event(task_id, '用户删除队列记录；保留本地文件、费用和投稿去重信息')
    return {'ok': True}


@app.get('/api/tasks/{task_id}')
def detail(task_id: str):
    task = store.task(task_id)
    folder = store.DATA / 'media' / task_id
    task['files'] = [{'name': p.name, 'size': p.stat().st_size} for p in folder.glob('*') if p.is_file() and p.name in FILES]
    task['events'] = store.rows('SELECT * FROM events WHERE task_id=? ORDER BY id DESC LIMIT 100', (task_id,))
    task['usage'] = store.rows('SELECT * FROM usage WHERE task_id=? ORDER BY id', (task_id,))
    return task


class Action(BaseModel):
    action: str
    bvid: str = ''


class CollectionTarget(BaseModel):
    season_id: int = Field(gt=0)
    section_id: int = Field(0, ge=0)


@app.put('/api/tasks/{task_id}/collection')
def task_collection(task_id: str, value: CollectionTarget):
    with worker.ACTIVE_LOCK:
        task = store.task(task_id)
        if task_id in worker.ACTIVE or task['status'] not in ('completed', 'waiting', 'failed', 'paused') or task['deleted']:
            raise HTTPException(409, '请先暂停任务，再设置合集')
        payload = task['payload']
        if not payload.get('bvid') or payload.get('assets_deleted'):
            raise HTTPException(409, '需要已投稿且保留本地处理文件的任务')
        if task['status'] != 'completed' and task['stage'] != 'collection':
            raise HTTPException(409, '请先完成视频与字幕步骤')
        receipt = store.DATA / 'media' / task_id / 'collection-receipt.json'
        if receipt.exists() and json.loads(receipt.read_text('utf-8')).get('target') != value.model_dump():
            raise HTTPException(409, '该任务已加入其他目标；请到 B 站手动调整合集')
        payload['collection_target'] = value.model_dump()
        store.update(task_id, payload=payload, stage='collection', status='queued', attempts=0, next_run=0, error='')
        store.event(task_id, '用户设置目标合集，排队执行加入步骤')
    return {'ok': True}


@app.post('/api/tasks/{task_id}/action')
def task_action(task_id: str, value: Action):
    task = store.task(task_id)
    if task['deleted']:
        raise HTTPException(409, '记录已删除；重新添加原视频链接可找回记录')
    with worker.ACTIVE_LOCK:
        active = task_id in worker.ACTIVE or task['status'] == 'running'
        if value.action in ('pause', 'cancel'):
            if task['status'] == 'completed':
                raise HTTPException(409, '已完成任务不能暂停或取消')
            if task['stage'] == 'publish' and active:
                raise HTTPException(409, '投稿提交中，请等待结果；此时停止可能造成结果不明确')
            store.update(task_id, status='paused' if value.action == 'pause' else 'cancelled')
        elif value.action in ('resume', 'retry'):
            if active or task['status'] in ('completed', 'reconcile') or task['payload'].get('assets_deleted'):
                raise HTTPException(409, '当前任务不能直接重试；请先核对状态')
            store.update(task_id, status='queued', attempts=0, next_run=0, error='')
        elif value.action == 'link':
            if active or task['status'] != 'reconcile' or not re.fullmatch(r'BV[0-9A-Za-z]{10}', value.bvid):
                raise HTTPException(409, '仅可为待核对任务填写有效 BV 号')
            payload = task['payload']
            payload['bvid'] = value.bvid
            store.update(task_id, payload=payload, status='queued', stage='subtitles', next_run=0, attempts=0, error='')
        elif value.action == 'reset-publication':
            if active or task['status'] != 'reconcile':
                raise HTTPException(409, '仅可重置待核对投稿')
            payload = task['payload']
            if payload.get('bvid'):
                raise HTTPException(409, '已有关联投稿，不能重置')
            payload.pop('publication_started', None)
            store.update(task_id, payload=payload, status='queued', stage='publish', next_run=0, attempts=0, error='')
        else:
            raise HTTPException(422, '操作无效')
    store.event(task_id, {'pause': '用户暂停任务', 'cancel': '用户取消任务', 'resume': '用户继续任务', 'retry': '用户重试任务', 'link': '用户关联投稿 BV 号', 'reset-publication': '用户确认未投稿，重置提交检查点'}[value.action])
    return {'ok': True}


FILES = {'source.mp4', 'source.jpg', 'en.srt', 'zh.srt', 'bilingual.srt', 'bilingual.ass', 'posting.json'}


@app.get('/api/tasks/{task_id}/files/{name}')
def file(task_id: str, name: str):
    store.task(task_id)
    if name not in FILES:
        raise HTTPException(404)
    path = store.DATA / 'media' / task_id / name
    if not path.is_file():
        raise HTTPException(404)
    return FileResponse(path, filename=name)


@app.delete('/api/tasks/{task_id}/files')
def delete_files(task_id: str):
    with worker.ACTIVE_LOCK:
        task = store.task(task_id)
        if task_id in worker.ACTIVE or task['status'] in ('running', 'queued', 'retrying', 'reconcile'):
            raise HTTPException(409, '请先暂停任务并等待当前步骤停止')
        root = (store.DATA / 'media').resolve()
        folder = (root / task_id).resolve()
        if not re.fullmatch(r'[a-f0-9]{32}', task_id) or folder.parent != root:
            raise HTTPException(400)
        if folder.exists():
            shutil.rmtree(folder)
        payload = task['payload']
        payload['assets_deleted'] = True
        store.update(task_id, payload=payload)
    store.event(task_id, '用户删除本地文件；投稿与去重记录保留')
    return {'ok': True}


class Channel(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    url: str = Field(max_length=500)
    enabled: bool = True
    options: config.Posting = Field(default_factory=config.Posting)


@app.get('/api/bilibili/collections')
def bili_collections():
    from .collections import list_collections
    try:
        return list_collections()
    except Waiting as exc:
        raise HTTPException(422, str(exc))
    except Exception:
        raise HTTPException(502, '无法读取 B 站合集，请检查网络与账号权限')


@app.get('/api/channels')
def channels():
    result = store.rows('SELECT * FROM channels ORDER BY created DESC')
    for item in result:
        item['options'] = json.loads(item['options'])
    return result


@app.post('/api/channels')
def add_channel(value: Channel):
    try:
        url = youtube_url(value.url, channel=True)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    if store.rows('SELECT id FROM channels WHERE url=?', (url,)):
        raise HTTPException(409, '频道已经存在')
    channel_id = uuid.uuid4().hex
    store.execute('INSERT INTO channels(id,url,name,enabled,created,options) VALUES(?,?,?,?,?,?)',
        (channel_id, url, value.name, int(value.enabled), time.time(), value.options.model_dump_json()))
    return {'id': channel_id}


@app.put('/api/channels/{channel_id}')
def edit_channel(channel_id: str, value: Channel):
    found = store.rows('SELECT * FROM channels WHERE id=?', (channel_id,))
    if not found:
        raise HTTPException(404)
    if youtube_url(value.url, channel=True) != found[0]['url']:
        raise HTTPException(422, '修改来源请新建订阅')
    store.execute('UPDATE channels SET name=?,enabled=?,options=? WHERE id=?',
        (value.name, int(value.enabled), value.options.model_dump_json(), channel_id))
    return {'ok': True}


@app.get('/api/settings')
def settings():
    value = config.public()
    value['bilibili_configured'] = (store.DATA / 'cookies.json').exists()
    value['youtube_configured'] = (store.DATA / 'youtube-cookies.txt').exists()
    value['tools'] = {name: bool(shutil.which(name)) for name in ('ffmpeg', 'node', 'biliup')}
    value['tools']['biliup'] = value['tools']['biliup'] or Path(sys.executable).with_name('biliup.exe' if sys.platform == 'win32' else 'biliup').is_file()
    return value


@app.put('/api/settings')
def update_settings(value: dict):
    try:
        config.merge_public(value)
    except ValidationError:
        raise HTTPException(422, '配置格式不正确，请检查地址、模型和数值范围')
    return {'ok': True}


@app.post('/api/routes/translation/{slot}/test')
def translation_test(slot: str):
    from .language import chat
    if slot not in ('primary', 'fallback'):
        raise HTTPException(404)
    settings = config.get()
    route = getattr(settings.translation, slot)
    settings.translation = config.Routing(primary=route)
    try:
        result = chat(None, settings, 'Translate the text into Simplified Chinese. Return {"text":"translation"}.', {'text': 'Hello, world.'})
        if not isinstance(result.get('text'), str) or not result['text'].strip():
            raise ValueError()
        return {'ok': True, 'text': result['text'][:200]}
    except (Waiting, RuntimeError, ValueError):
        raise HTTPException(422, '翻译测试失败，请检查 API Key、模型权限、地址和余额')


class Credentials(BaseModel):
    content: str = Field(max_length=2_000_000)


@app.put('/api/credentials/{provider}')
def credential_file(provider: str, value: Credentials):
    if provider == 'bilibili':
        try:
            data = json.loads(value.content)
            accounts.validate_login(data)
        except (ValueError, KeyError, TypeError):
            raise HTTPException(422, '需要 biliup 导出的完整 cookies.json（Cookie、token_info、sso）；仅网页 Cookie 无法用于当前上传工具。也可直接扫码登录。')
        path = store.DATA / 'cookies.json'
    elif provider == 'youtube':
        if not is_netscape_cookie_file(value.content):
            raise HTTPException(422, '需要 Netscape 格式的 cookies.txt')
        path = store.DATA / 'youtube-cookies.txt'
    else:
        raise HTTPException(404)
    tmp = path.with_suffix('.tmp')
    tmp.write_text(value.content, 'utf-8')
    os.chmod(tmp, 0o600)
    tmp.replace(path)
    return {'ok': True}


@app.post('/api/notifications/test')
def notification_test():
    settings = config.get()
    if not settings.telegram_token or not settings.telegram_chat_id:
        raise HTTPException(422, '请先保存 Telegram 配置')
    store.notice('Tube2Bili：Telegram 测试通知')
    return {'ok': True}


@app.post('/api/accounts/bilibili/qr')
def bilibili_qr():
    try:
        return accounts.create()
    except (httpx.HTTPError, ValueError, KeyError):
        raise HTTPException(502, '无法生成 B 站二维码，请检查网络后重试')


@app.post('/api/accounts/bilibili/qr/{session_id}')
def bilibili_qr_poll(session_id: str):
    try:
        return accounts.poll(session_id)
    except (httpx.HTTPError, ValueError, KeyError):
        raise HTTPException(502, 'B 站登录查询失败，请稍后重试')


@app.post('/api/accounts/bilibili/check')
def bilibili_check():
    try:
        return accounts.status()
    except (httpx.HTTPError, ValueError, KeyError, Waiting):
        raise HTTPException(422, 'B 站凭证缺失、失效或网络不可用，请重新登录')


app.mount('/static', StaticFiles(directory=ROOT / 'static'), name='static')


@app.get('/')
def index():
    return FileResponse(ROOT / 'static' / 'index.html')
