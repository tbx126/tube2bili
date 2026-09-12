"""Bilibili QR login. Login secrets stay on the server and expire in memory."""
import base64
import io
import hashlib
import secrets
import threading
import time
from urllib.parse import urlencode

import httpx
import qrcode
from fastapi import HTTPException

from . import store
from .qwen import save

LOCK = threading.RLock()
SESSIONS = {}
HEADERS = {'User-Agent': 'Mozilla/5.0', 'Referer': 'https://www.bilibili.com/'}


def validate_login(value):
    """Validate the complete LoginInfo consumed by biliup, not just web cookies."""
    jar = {cookie['name']: cookie['value'] for cookie in value['cookie_info']['cookies']}
    if not all(isinstance(jar.get(k), str) and jar[k] for k in ('SESSDATA', 'bili_jct', 'DedeUserID')):
        raise ValueError('Incomplete cookies')
    if not jar['DedeUserID'].isdigit():
        raise ValueError('Invalid account ID')
    token = value['token_info']
    if not all(isinstance(token.get(k), str) and token[k] for k in ('access_token', 'refresh_token')):
        raise ValueError('Incomplete uploader token')
    if not isinstance(token.get('mid'), int) or token['mid'] != int(jar['DedeUserID']):
        raise ValueError('Token and cookie accounts differ')
    if not isinstance(token.get('expires_in'), int) or token['expires_in'] < 0:
        raise ValueError('Invalid token expiry')
    if not isinstance(value.get('sso'), list) or not all(isinstance(s, str) for s in value['sso']):
        raise ValueError('Incomplete LoginInfo')
    return jar


def tv_request(action, auth_code=None):
    # Public BiliTV application identifiers used by biliup's QR login protocol.
    fields = {'appkey': '4409e2ce8ffd12b8', 'local_id': '0', 'ts': int(time.time())}
    if auth_code:
        fields['auth_code'] = auth_code
    body = urlencode(sorted(fields.items()))
    signature = hashlib.md5((body + '59b43e04ad6965f34319062b478f83dd').encode()).hexdigest()
    with httpx.Client(timeout=20, headers=HEADERS) as client:
        response = client.post('https://passport.bilibili.com/x/passport-tv-login/qrcode/' + action,
            content=body + '&sign=' + signature, headers={'Content-Type': 'application/x-www-form-urlencoded'})
        response.raise_for_status()
        return response.json()


def request(path, params=None, cookies=None):
    with httpx.Client(timeout=20, headers=HEADERS) as client:
        response = client.get(path, params=params, cookies=cookies)
        response.raise_for_status()
        value = response.json()
        if value.get('code') != 0:
            raise HTTPException(502, 'B 站接口暂不可用，请稍后重试')
        return value['data']


def create():
    with LOCK:
        for key in list(SESSIONS):
            if SESSIONS[key]['expires'] < time.time():
                del SESSIONS[key]
        if len(SESSIONS) >= 5:
            raise HTTPException(429, '已有登录二维码，请等待过期后重试')
        response = tv_request('auth_code')
        if response.get('code') != 0:
            raise HTTPException(502, 'B 站二维码接口暂不可用，请稍后重试')
        value = response['data']
        session_id = secrets.token_urlsafe(24)
        SESSIONS[session_id] = {'key': value['auth_code'], 'expires': time.time() + 180}
        output = io.BytesIO()
        qrcode.make(value['url']).save(output, format='PNG')
        return {'id': session_id, 'image': 'data:image/png;base64,' + base64.b64encode(output.getvalue()).decode()}


def poll(session_id):
    with LOCK:
        state = SESSIONS.get(session_id)
        if not state or state['expires'] < time.time():
            SESSIONS.pop(session_id, None)
            return {'status': 'expired'}
        if state.get('done'):
            return {'status': 'done'}
        value = tv_request('poll', state['key'])
        code = value['code']
        if code in (86039, 86090, 86038):
            return {'status': {86039: 'scan', 86090: 'confirm', 86038: 'expired'}[code]}
        if code != 0:
            raise HTTPException(502, 'B 站登录未成功，请重新生成二维码')
        login = value['data']
        validate_login(login)
        login['platform'] = 'BiliTV'
        save(store.DATA / 'cookies.json', login)
        state['done'] = True
        state.pop('key', None)
        return {'status': 'done'}


def status():
    from .publishing import cookies
    value = request('https://api.bilibili.com/x/web-interface/nav', cookies=cookies())
    if not value.get('isLogin'):
        return {'valid': False}
    return {'valid': True, 'name': value.get('uname'), 'mid': value.get('mid')}
