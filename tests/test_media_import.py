import json
from types import SimpleNamespace

from app import media_import, store


def test_import_resumes_same_task_only_after_validating_both_streams(client, monkeypatch):
    task_id = store.enqueue('abcdefghijk', 'https://www.youtube.com/watch?v=abcdefghijk',
                            options={'season_id': 42})
    endpoint = f'/api/tasks/{task_id}/source-video'
    store.update(task_id, status='failed', attempts=3)
    folder = store.DATA / 'media' / task_id
    folder.mkdir()
    (folder / 'source.info.json').write_text(
        json.dumps({'id': 'abcdefghijk', 'title': 'Original title', 'uploader': 'Creator'}))
    monkeypatch.setenv('MIN_FREE_GB', '0')
    monkeypatch.setattr('app.main.shutil.disk_usage', lambda path: SimpleNamespace(free=100 * 1024**3))
    streams = [{'codec_type': 'video'}]
    monkeypatch.setattr(media_import.subprocess, 'run', lambda *args, **kwargs: SimpleNamespace(
        stdout=json.dumps({'format': {'format_name': 'mov,mp4', 'duration': '12.5'}, 'streams': streams})))

    assert client.put(endpoint, content=b'test mp4').status_code == 422
    assert store.task(task_id)['status'] == 'failed'
    assert not (folder / 'source.mp4').exists()

    streams.append({'codec_type': 'audio'})
    assert client.put(endpoint, content=b'test mp4').status_code == 200
    task = store.task(task_id)
    assert (task['status'], task['stage'], task['attempts']) == ('queued', 'translate', 0)
    assert task['payload']['options']['season_id'] == 42
    assert json.loads((folder / 'source.json').read_text())['duration'] == 12.5
    assert client.put(endpoint, content=b'other file').status_code == 409
    assert (folder / 'source.mp4').read_bytes() == b'test mp4'
