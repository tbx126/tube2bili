"""SQLite persistence. One scheduler/worker per installation; no external broker."""
import json
import os
import sqlite3
import time
import uuid
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()
DATA = Path(os.environ.get('DATA_DIR', './data')).resolve()


def connect():
    db = sqlite3.connect(DATA / 'tube2bili.db', timeout=30)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA busy_timeout=30000')
    return db


def init():
    DATA.mkdir(parents=True, exist_ok=True)
    (DATA / 'media').mkdir(exist_ok=True)
    with connect() as db:
        db.executescript('''
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS tasks (
          id TEXT PRIMARY KEY, video_id TEXT NOT NULL UNIQUE, url TEXT NOT NULL,
          title TEXT NOT NULL DEFAULT '', channel_id TEXT, status TEXT NOT NULL DEFAULT 'queued',
          stage TEXT NOT NULL DEFAULT 'download', progress REAL DEFAULT 0,
          created REAL NOT NULL, updated REAL NOT NULL, next_run REAL DEFAULT 0,
          attempts INTEGER DEFAULT 0, error TEXT DEFAULT '', payload TEXT DEFAULT '{}');
        CREATE TABLE IF NOT EXISTS channels (
          id TEXT PRIMARY KEY, url TEXT NOT NULL UNIQUE, name TEXT NOT NULL,
          enabled INTEGER DEFAULT 1, created REAL NOT NULL, last_poll REAL DEFAULT 0,
          initialized INTEGER DEFAULT 0, error TEXT DEFAULT '', options TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS seen (channel_id TEXT, video_id TEXT, PRIMARY KEY(channel_id,video_id));
        CREATE TABLE IF NOT EXISTS events (
          id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT, created REAL, message TEXT);
        CREATE TABLE IF NOT EXISTS usage (
          id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT, created REAL, route TEXT,
          tokens INTEGER, cost REAL, unit TEXT DEFAULT 'CNY');
        CREATE TABLE IF NOT EXISTS notices (
          id INTEGER PRIMARY KEY AUTOINCREMENT, created REAL, message TEXT,
          sent INTEGER DEFAULT 0, attempts INTEGER DEFAULT 0, next_run REAL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS runtime_state (
          key TEXT PRIMARY KEY, value TEXT NOT NULL, updated REAL NOT NULL);
        ''')
        if 'deleted' not in {row['name'] for row in db.execute('PRAGMA table_info(tasks)')}:
            db.execute('ALTER TABLE tasks ADD COLUMN deleted INTEGER NOT NULL DEFAULT 0')
        db.execute("UPDATE tasks SET status='queued' WHERE status='running' AND deleted=0")
        interrupted = db.execute("SELECT id FROM tasks WHERE status='paused' AND deleted=0 AND error='服务关闭时暂停，请继续任务'").fetchall()
        if interrupted:
            db.executemany('INSERT INTO events(task_id,created,message) VALUES(?,?,?)',
                [(row['id'], time.time(), '服务恢复后自动从已保存进度继续') for row in interrupted])
            db.execute("UPDATE tasks SET status='queued',next_run=0,error='',updated=? "
                       "WHERE status='paused' AND deleted=0 AND error='服务关闭时暂停，请继续任务'", (time.time(),))


def rows(sql, args=()):
    with connect() as db:
        return [dict(x) for x in db.execute(sql, args).fetchall()]


def execute(sql, args=()):
    with connect() as db:
        return db.execute(sql, args).rowcount


def task(task_id):
    found = rows('SELECT * FROM tasks WHERE id=?', (task_id,))
    if not found:
        raise KeyError(task_id)
    result = found[0]
    result['payload'] = json.loads(result['payload'])
    return result


def update(task_id, **values):
    allowed = {'title', 'status', 'stage', 'progress', 'next_run', 'attempts', 'error', 'payload'}
    if not set(values) <= allowed:
        raise ValueError('Invalid task field')
    if 'payload' in values:
        values['payload'] = json.dumps(values['payload'], ensure_ascii=False)
    values['updated'] = time.time()
    execute('UPDATE tasks SET ' + ','.join(f'{k}=?' for k in values) + ' WHERE id=?', (*values.values(), task_id))


def event(task_id, message):
    execute('INSERT INTO events(task_id,created,message) VALUES(?,?,?)', (task_id, time.time(), message))


def notice(message):
    execute('INSERT INTO notices(created,message) VALUES(?,?)', (time.time(), message))


def get_runtime_state(key, default=None):
    found = rows('SELECT value FROM runtime_state WHERE key=?', (key,))
    if not found:
        return default
    try:
        return json.loads(found[0]['value'])
    except (TypeError, ValueError):
        return default


def set_runtime_state(key, value):
    with connect() as db:
        db.execute('''INSERT INTO runtime_state(key,value,updated) VALUES(?,?,?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated=excluded.updated''',
            (key, json.dumps(value, ensure_ascii=False), time.time()))


def enqueue(video_id, url, channel_id=None, options=None, db=None):
    task_id = uuid.uuid4().hex
    now = time.time()
    query = '''INSERT OR IGNORE INTO tasks(id,video_id,url,channel_id,created,updated,payload)
               VALUES(?,?,?,?,?,?,?)'''
    args = (task_id, video_id, url, channel_id, now, now, json.dumps({'options': options or {}}))
    if db is not None:
        db.execute(query, args)
        return db.execute('SELECT id FROM tasks WHERE video_id=?', (video_id,)).fetchone()['id']
    with connect() as connection:
        return enqueue(video_id, url, channel_id, options, connection)
