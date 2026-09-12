import json

from app import accounts, store


def test_qr_login_saves_server_side_without_returning_secrets(client, monkeypatch):
    accounts.SESSIONS.clear()
    def request(action, auth_code=None):
        if action == 'auth_code':
            return {'code': 0, 'data': {'url': 'https://passport.bilibili.com/login?key=qr-secret', 'auth_code': 'qr-secret'}}
        assert auth_code == 'qr-secret'
        return {'code': 0, 'data': {'cookie_info': {'cookies': [
            {'name': 'SESSDATA', 'value': 'encoded%2Fsecret'}, {'name': 'bili_jct', 'value': 'csrf'},
            {'name': 'DedeUserID', 'value': '123'}]}, 'token_info': {'access_token': 'private-token'}, 'sso': []}}
    monkeypatch.setattr(accounts, 'tv_request', request)
    created = client.post('/api/accounts/bilibili/qr').json()
    assert created['image'].startswith('data:image/png;base64,')
    result = client.post('/api/accounts/bilibili/qr/' + created['id'])
    assert result.json() == {'status': 'done'}
    assert 'secret' not in result.text
    value = json.loads((store.DATA / 'cookies.json').read_text('utf-8'))
    jar = {c['name']: c['value'] for c in value['cookie_info']['cookies']}
    assert jar['SESSDATA'] == 'encoded%2Fsecret'
    assert jar['DedeUserID'] == '123'
    assert client.post('/api/accounts/bilibili/qr/' + created['id']).json() == {'status': 'done'}


def test_expired_qr_and_invalid_login_do_not_write(client, monkeypatch):
    assert client.post('/api/accounts/bilibili/qr/missing').json() == {'status': 'expired'}
    monkeypatch.setattr(accounts, 'tv_request', lambda *args, **kwargs: {'code': 0, 'data': {}})
    accounts.SESSIONS['invalid'] = {'expires': __import__('time').time() + 60, 'key': 'key'}
    assert client.post('/api/accounts/bilibili/qr/invalid').status_code == 502
    assert not (store.DATA / 'cookies.json').exists()
