from pathlib import Path

import pytest

from app import config, media, store, worker, youtube

COOKIE = '# Netscape HTTP Cookie File\n.youtube.com\tTRUE\t/\tTRUE\t2000000000\tSID\told\n'
NEW = COOKIE.replace('\told', '\tnew')


def test_anonymous_success_never_uses_stored_login(client, monkeypatch):
    youtube.import_cookie(COOKIE)
    def run(args, *a, **kw):
        assert '--cookies' not in args
        return 'ok'
    monkeypatch.setattr(youtube, 'run', run)
    assert youtube.execute(config.Settings(), ['url'], store.DATA) == 'ok'


@pytest.mark.parametrize('kind', ['rate', 'network', 'token', 'other'])
def test_transient_errors_do_not_use_account(client, monkeypatch, kind):
    youtube.import_cookie(COOKIE)
    calls = []
    def run(args, *a, **kw):
        calls.append(args)
        raise media.YouTubeError(kind)
    monkeypatch.setattr(youtube, 'run', run)
    with pytest.raises(media.YouTubeError):
        youtube.execute(config.Settings(), ['url'], store.DATA)
    assert len(calls) == 1


def test_snapshot_cannot_overwrite_concurrent_import(client, monkeypatch):
    youtube.import_cookie(COOKIE)
    paths = []
    def run(args, *a, **kw):
        if '--cookies' not in args:
            raise media.YouTubeError('bot')
        path = Path(args[args.index('--cookies') + 1])
        paths.append(path)
        assert path.read_text() == COOKIE
        youtube.import_cookie(NEW)
        path.write_text('tool modified cookie')
        raise media.YouTubeError('bot')
    monkeypatch.setattr(youtube, 'run', run)
    with pytest.raises(media.YouTubeError, match='不能据此判定'):
        youtube.execute(config.Settings(), ['url'], store.DATA)
    assert media.youtube_cookie_path().read_text() == NEW
    assert not paths[0].exists()


def test_import_only_resumes_youtube_waits(client):
    ids = [store.enqueue(str(n).zfill(11), 'url') for n in range(3)]
    for task_id, status, required in zip(ids, ['waiting', 'paused', 'waiting'], [True, True, False]):
        store.update(task_id, status=status, payload={'youtube_login_required': required})
    assert youtube.import_cookie(COOKIE) == 1
    assert [store.task(i)['status'] for i in ids] == ['queued', 'paused', 'waiting']
    with pytest.raises(ValueError):
        youtube.import_cookie('# Netscape HTTP Cookie File\ninvalid')
    assert media.youtube_cookie_path().read_text() == COOKIE


def test_login_notice_deduplicated(client):
    youtube.login_notice()
    youtube.login_notice()
    assert len(store.rows('SELECT * FROM notices')) == 1


def test_session_cookies_without_expiry_are_accepted(client):
    assert youtube.import_cookie(COOKIE.replace('2000000000', '0')) == 0


@pytest.mark.parametrize('text,kind', [
    ("ERROR: Sign in to confirm you're not a bot. Use --cookies", 'bot'),
    ('WARNING: cookies are no longer valid\nERROR: HTTP Error 429', 'rate'),
    ('ERROR: HTTP Error 403: Forbidden', 'token'),
    ('ERROR: Private video. Sign in if granted access', 'auth'),
    ('ERROR: Unable to download: connection timed out', 'network'),
    ('ERROR: Unable to download API page: [SSL: UNEXPECTED_EOF_WHILE_READING] EOF occurred in violation of protocol', 'network'),
    ('ERROR: TLS handshake failed: remote end closed connection', 'network')])
def test_error_categories(text, kind):
    assert media.youtube_error_kind(text) == kind


def test_worker_login_pause_and_import_resume(client, monkeypatch):
    task_id = store.enqueue('abcdefghijk', 'url')
    monkeypatch.setattr(worker, 'download', lambda *a: (_ for _ in ()).throw(youtube.LoginRequired('login needed')))
    worker.process(task_id)
    assert store.task(task_id)['status'] == 'waiting'
    assert youtube.import_cookie(COOKIE) == 1
    assert store.task(task_id)['status'] == 'queued'


def test_provider_args_are_automatic(monkeypatch):
    monkeypatch.setenv('POT_PROVIDER_URL', 'http://pot-provider:4416')
    args = media.ytdlp(config.Settings())
    assert 'youtube:player_client=mweb' in args
    assert 'youtubepot-bgutilhttp:base_url=http://pot-provider:4416' in args
    assert '--cookies' not in args
