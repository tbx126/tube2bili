"""External publishing is checkpointed before a non-idempotent submit."""
import json
import re
import shutil
import sys

from . import store
from .media import Reconcile, Waiting, check, run


def cookies():
    path = store.DATA / 'cookies.json'
    if not path.exists():
        raise Waiting('请先在设置中导入 biliup cookies.json 或在容器内扫码登录')
    value = json.loads(path.read_text('utf-8'))
    from .accounts import validate_login
    try:
        return validate_login(value)
    except (ValueError, KeyError, TypeError):
        raise Waiting('B 站登录文件缺少完整上传凭据，请在设置页重新扫码登录')


def publish(task, settings, folder):
    payload = task['payload']
    if payload.get('bvid'):
        return payload['bvid']
    receipt = folder / 'publish-receipt.json'
    if receipt.exists():
        payload['bvid'] = json.loads(receipt.read_text('utf-8'))['bvid']
        store.update(task['id'], payload=payload)
        return payload['bvid']
    if payload.get('publication_started'):
        raise Reconcile('上次投稿结果不明确；请到创作中心核对并填写 BV 号，避免重复发布')
    cookies()
    executable = shutil.which('biliup') or str(__import__('pathlib').Path(sys.executable).with_name('biliup.exe' if sys.platform == 'win32' else 'biliup'))
    metadata = json.loads((folder / 'posting.json').read_text('utf-8'))
    options = {**settings.posting.model_dump(), **payload.get('options', {})}
    args = [executable, '-u', str(store.DATA / 'cookies.json'), 'upload', '--submit', 'web',
            '--copyright', '2', '--source', task['url'], '--tid', str(options['tid']),
            '--tag', options['tags'], '--title', metadata['title'], '--desc', metadata['description'],
            '--limit', '2', '--extra-fields', '{"open_subtitle":true,"subtitle":{"open":1,"lan":"en"}}']
    if (folder / 'source.jpg').exists():
        args += ['--cover', str(folder / 'source.jpg')]
    args += [str(folder / 'source.mp4')]
    check(task['id'])
    # Mark before spawning. Unknown result MUST NOT automatically submit again.
    payload['publication_started'] = True
    store.update(task['id'], payload=payload)
    try:
        output = run(args, folder, task['id'])
        matches = set(re.findall(r'BV[0-9A-Za-z]{10}', output))
        if len(matches) != 1:
            raise ValueError('No unambiguous BVID')
        bvid = matches.pop()
    except BaseException as exc:
        raise Reconcile('投稿结果不明确；请核对创作中心并关联 BV 号，系统不会自动重复投稿') from exc
    tmp = receipt.with_suffix('.tmp')
    tmp.write_text(json.dumps({'bvid': bvid}), 'utf-8')
    tmp.replace(receipt)
    payload['bvid'] = bvid
    store.update(task['id'], payload=payload)
    return bvid


def bridge(task, folder, action):
    run([sys.executable, '-m', 'app.bili_bridge', action, task['id']], folder, task['id'], timeout=180)
    result = json.loads((folder / f'{action}-result.json').read_text('utf-8'))
    if result.get('auth_error'):
        raise Waiting('B 站登录已失效，请更新凭证后继续任务')
    if not result.get('ok'):
        raise RuntimeError('B 站仍在处理或字幕接口暂不可用')
    return result


def subtitles(task, settings, folder):
    cookies()
    result = bridge(task, folder, 'subtitles')
    payload = store.task(task['id'])['payload']
    payload['subtitle_submitted'] = True
    payload['cid'] = result['cid']
    store.update(task['id'], payload=payload)


def verify(task, settings):
    bridge(task, store.DATA / 'media' / task['id'], 'verify')
