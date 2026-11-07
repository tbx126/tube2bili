import pytest
from fastapi.testclient import TestClient

from app import store
from app.main import app


@pytest.mark.parametrize('password', ['', None])
def test_passwordless_dashboard(tmp_path, monkeypatch, password):
    monkeypatch.setattr(store, 'DATA', tmp_path)
    monkeypatch.setenv('DISABLE_WORKER', '1')
    if password is None:
        monkeypatch.delenv('DASHBOARD_PASSWORD', raising=False)
    else:
        monkeypatch.setenv('DASHBOARD_PASSWORD', password)
    with TestClient(app) as client:
        assert client.get('/').status_code == 200
        response = client.get('/api/overview')
        assert response.status_code == 200
        assert response.json()['auth_required'] is False
        assert client.get('/api/settings').status_code == 200
        assert client.post('/api/logout').status_code == 403
        headers = {'X-Requested-With': 'Tube2Bili'}
        assert client.post('/api/logout', headers=headers).status_code == 200
        assert client.get('/api/overview').status_code == 200
        assert client.post('/api/youtube/extension-sync', json={}).status_code in (401, 403, 422)


def test_configured_password_still_required(client):
    assert client.get('/api/overview').json()['auth_required'] is True
    client.cookies.clear()
    assert client.get('/api/overview').status_code == 401


def test_short_password_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(store, 'DATA', tmp_path)
    monkeypatch.setenv('DISABLE_WORKER', '1')
    monkeypatch.setenv('DASHBOARD_PASSWORD', 'short')
    with pytest.raises(RuntimeError, match='DASHBOARD_PASSWORD'):
        with TestClient(app):
            pass
