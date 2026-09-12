import pytest
from fastapi.testclient import TestClient

from app import store, worker
from app.main import app, LOGIN_FAILURES


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(store, 'DATA', tmp_path)
    monkeypatch.setenv('DASHBOARD_PASSWORD', 'test-password-long-enough')
    monkeypatch.setenv('DISABLE_WORKER', '1')
    LOGIN_FAILURES.clear()
    worker.ACTIVE.clear()
    with TestClient(app) as test_client:
        test_client.headers['X-Requested-With'] = 'Tube2Bili'
        assert test_client.post('/api/login', json={'password': 'test-password-long-enough'}).status_code == 200
        yield test_client
