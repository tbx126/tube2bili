"""DashScope recording ASR, with private upload and resumable remote jobs."""
import hashlib
import json
import os
import time
import uuid
from urllib.parse import quote, urlparse

import httpx

from .media import Waiting, check


def api_base(url):
    url = url.rstrip('/')
    if url.endswith('/compatible-mode/v1'):
        return url[:-len('/compatible-mode/v1')] + '/api/v1'
    if not url.endswith('/api/v1'):
        raise Waiting('Qwen 录音识别地址应以 /api/v1 或 /compatible-mode/v1 结尾')
    return url


def save(path, value):
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(value, ensure_ascii=False), 'utf-8')
    os.chmod(tmp, 0o600)
    tmp.replace(path)


def asset_url(value):
    # Signed storage URLs must not receive the model API key.
    parsed = urlparse(value)
    if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError('Invalid storage URL')
    return value


def upload(client, route, audio):
    response = client.get(api_base(route.base_url) + '/uploads',
        headers={'Authorization': 'Bearer ' + route.api_key},
        params={'action': 'getPolicy', 'model': route.model})
    response.raise_for_status()
    policy = response.json()['data']
    key = policy['upload_dir'] + '/' + uuid.uuid4().hex + '.mp3'
    fields = {
        'OSSAccessKeyId': policy['oss_access_key_id'], 'Signature': policy['signature'],
        'policy': policy['policy'], 'x-oss-object-acl': policy['x_oss_object_acl'],
        'x-oss-forbid-overwrite': policy['x_oss_forbid_overwrite'],
        'key': key, 'success_action_status': '200',
    }
    with audio.open('rb') as handle:
        response = client.post(asset_url(policy['upload_host']), data=fields,
            files={'file': (audio.name, handle, 'audio/mpeg')})
    response.raise_for_status()
    return 'oss://' + key


def parse_transcript(value):
    sentences = [s for track in value['transcripts'] if track.get('channel_id', 0) == 0
                 for s in track['sentences'] if str(s.get('text', '')).strip()]
    if not sentences:
        raise ValueError('ASR returned no timestamped speech')
    languages = {s.get('language', '').lower() for s in sentences}
    return {'language': 'en' if languages == {'en'} else '', 'segments': [
        {'start': float(s['begin_time']) / 1000, 'end': float(s['end_time']) / 1000,
         'text': s['text']} for s in sentences]}


def transcribe(task_id, route, audio):
    if not route.model.startswith('qwen3-asr-flash-filetrans'):
        raise Waiting('Qwen 录音识别请选择 qwen3-asr-flash-filetrans，普通 flash 的接口不相同')
    base = api_base(route.base_url)
    fingerprint = hashlib.sha256((base + '\n' + route.model).encode()).hexdigest()[:16]
    checkpoint = audio.with_name(audio.stem + '-qwen-' + fingerprint + '.json')
    state = json.loads(checkpoint.read_text('utf-8')) if checkpoint.exists() else {}
    if state.get('result'):
        return state['result']
    headers = {'Authorization': 'Bearer ' + route.api_key}
    with httpx.Client(timeout=60) as client:
        if not state.get('task_id'):
            check(task_id)
            file_url = upload(client, route, audio)
            check(task_id)
            response = client.post(base + '/services/audio/asr/transcription',
                headers={**headers, 'X-DashScope-Async': 'enable', 'X-DashScope-OssResourceResolve': 'enable'},
                json={'model': route.model, 'input': {'file_url': file_url},
                      'parameters': {'channel_id': [0], 'enable_itn': False}})
            response.raise_for_status()
            state = {'task_id': response.json()['output']['task_id'], 'submitted': time.time()}
            save(checkpoint, state)
        deadline = time.monotonic() + 600
        while time.monotonic() < deadline:
            check(task_id)
            response = client.get(base + '/tasks/' + quote(state['task_id'], safe=''), headers=headers)
            response.raise_for_status()
            output = response.json()['output']
            status = output['task_status']
            if status == 'SUCCEEDED':
                response = client.get(asset_url(output['result']['transcription_url']))
                response.raise_for_status()
                result = parse_transcript(response.json())
                state['result'] = result
                save(checkpoint, state)
                return result
            if status in ('FAILED', 'UNKNOWN'):
                # Preserve the provider receipt, but allow an explicit retry to submit anew.
                save(checkpoint.with_suffix('.failed.json'), {**state, 'status': status})
                checkpoint.unlink()
                raise ValueError('Qwen ASR task ' + status)
            if status not in ('PENDING', 'RUNNING'):
                raise ValueError('Unexpected Qwen ASR status')
            for _ in range(5):
                check(task_id)
                time.sleep(1)
    raise Waiting('Qwen 仍在处理录音，任务编号已保存；稍后继续将查询原任务，不会重新提交')
