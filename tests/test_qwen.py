import json

import httpx
import pytest

from app import config, language, qwen, store
from app.media import Stopped


def test_qwen_chat_disables_thinking(client, monkeypatch):
    settings = config.Settings()
    settings.translation.primary = config.Route(protocol='qwen', model='qwen-plus', base_url='https://qwen.test/v1')
    def handler(request):
        body = json.loads(request.content)
        assert body['enable_thinking'] is False
        assert body['response_format'] == {'type': 'json_object'}
        assert 'extra_body' not in body
        return httpx.Response(200, json={'choices': [{'message': {'content': '{"ok": true}'}}]})
    original = httpx.Client
    monkeypatch.setattr(httpx, 'Client', lambda **kwargs: original(transport=httpx.MockTransport(handler)))
    assert language.chat(None, settings, 'Translate', {}) == {'ok': True}


def test_qwen_upload_poll_resume_and_cache(client, monkeypatch, tmp_path):
    route = config.Route(protocol='qwen_asr', base_url='https://qwen.test/compatible-mode/v1',
                         model='qwen3-asr-flash-filetrans', api_key='private-key')
    audio = tmp_path / 'audio-0.mp3'
    audio.write_bytes(b'audio')
    calls = []
    stopped = [False]
    def handler(request):
        calls.append((request.method, request.url.path))
        if request.url.host == 'qwen.test':
            assert request.headers['authorization'] == 'Bearer private-key'
        else:
            assert 'authorization' not in request.headers
        if request.url.path == '/api/v1/uploads':
            assert request.url.params['model'] == route.model
            return httpx.Response(200, json={'data': {'upload_dir': 'private',
                'upload_host': 'https://storage.test/upload', 'oss_access_key_id': 'access',
                'signature': 'sig', 'policy': 'policy', 'x_oss_object_acl': 'private',
                'x_oss_forbid_overwrite': 'true'}})
        if request.url.path == '/upload':
            assert b'audio' in request.content
            return httpx.Response(200)
        if request.url.path == '/api/v1/services/audio/asr/transcription':
            assert request.headers['X-DashScope-OssResourceResolve'] == 'enable'
            assert request.headers['X-DashScope-Async'] == 'enable'
            body = json.loads(request.content)
            assert body['input']['file_url'].startswith('oss://private/')
            assert body['parameters']['channel_id'] == [0]
            stopped[0] = True
            return httpx.Response(200, json={'output': {'task_id': 'job-1'}})
        if request.url.path == '/api/v1/tasks/job-1':
            return httpx.Response(200, json={'output': {'task_status': 'SUCCEEDED',
                'result': {'transcription_url': 'https://storage.test/result'}}})
        if request.url.path == '/result':
            return httpx.Response(200, json={'transcripts': [{'channel_id': 0, 'sentences': [
                {'begin_time': 125, 'end_time': 1700, 'language': 'en', 'text': 'Hello'}]}]})
        raise AssertionError(request.url)
    def check(_):
        if stopped[0]:
            raise Stopped()
    monkeypatch.setattr(qwen, 'check', check)
    original = httpx.Client
    monkeypatch.setattr(httpx, 'Client', lambda **kwargs: original(transport=httpx.MockTransport(handler)))
    with pytest.raises(Stopped):
        qwen.transcribe('task', route, audio)
    stopped[0] = False
    result = qwen.transcribe('task', route, audio)
    assert result == {'language': 'en', 'segments': [{'start': .125, 'end': 1.7, 'text': 'Hello'}]}
    count = len(calls)
    assert qwen.transcribe('task', route, audio) == result
    assert len(calls) == count
    assert sum(path.endswith('/transcription') for _, path in calls) == 1
    assert 'private-key' not in next(tmp_path.glob('*qwen*.json')).read_text()


def test_qwen_mixed_languages_are_not_labelled_english():
    value = {'transcripts': [{'sentences': [
        {'begin_time': 0, 'end_time': 1000, 'text': 'Hello', 'language': 'en'},
        {'begin_time': 1000, 'end_time': 2000, 'text': '你好', 'language': 'zh'}]}]}
    assert qwen.parse_transcript(value)['language'] == ''


def test_qwen_audio_cumulative_updates_use_word_timestamps_once():
    words = [
        {'begin_time': 200, 'end_time': 600, 'text': 'Hello', 'punctuation': ''},
        {'begin_time': 600, 'end_time': 1100, 'text': ' world', 'punctuation': '.'},
    ]
    value = {'transcripts': [{'channel_id': 0, 'sentences': [
        {'begin_time': 200, 'end_time': 600, 'text': 'Hello', 'language': 'en', 'words': words[:1]},
        {'begin_time': 200, 'end_time': 1100, 'text': 'Hello world.', 'language': 'en', 'words': words},
    ]}]}

    result = qwen.parse_transcript(value)

    assert result['language'] == 'en'
    assert result['segments'] == [{'start': .2, 'end': 1.1, 'text': 'Hello world.'}]


def test_qwen_word_timestamps_are_grouped_into_readable_cues():
    words = [
        {'begin_time': index * 500, 'end_time': index * 500 + 350, 'text': 'word', 'punctuation': ''}
        for index in range(20)
    ]
    result = qwen.parse_transcript({'transcripts': [{'channel_id': 0, 'sentences': [{
        'begin_time': 0, 'end_time': 10000, 'text': ' '.join(['word'] * 20),
        'language': 'en', 'words': words,
    }]}]})

    assert len(result['segments']) > 1
    assert all(len(segment['text']) <= 42 and segment['end'] - segment['start'] <= 5 for segment in result['segments'])
    assert ' '.join(segment['text'] for segment in result['segments']).split() == ['word'] * 20


def test_qwen_long_sentence_without_word_timestamps_is_paused():
    value = {'transcripts': [{'channel_id': 0, 'sentences': [{
        'begin_time': 0, 'end_time': 300000, 'text': 'word ' * 1000,
    }]}]}

    with pytest.raises(language.Waiting, match='词级时间戳'):
        qwen.parse_transcript(value)


def test_wrong_qwen_asr_model_stops_before_http(tmp_path):
    with pytest.raises(language.Waiting, match='filetrans'):
        qwen.transcribe(None, config.Route(model='qwen3-asr-flash'), tmp_path / 'audio.mp3')


@pytest.mark.parametrize('streaming', [True, False])
def test_audio_direct_upload_uses_final_timestamps(tmp_path, monkeypatch, streaming):
    audio = tmp_path / 'audio.mp3'
    audio.write_bytes(b'test-audio')
    route = config.Route(protocol='qwen_audio', base_url='https://qwen.test/api/v1', model='qwen-audio-3.0-asr-flash')
    sentence = {'sentence_id': 1, 'sentence_end': True, 'begin_time': 500, 'end_time': 2100,
        'text': 'Hello world!', 'words': [
            {'begin_time': 500, 'end_time': 1100, 'text': 'Hello', 'punctuation': ''},
            {'begin_time': 1100, 'end_time': 2100, 'text': ' world', 'punctuation': '!'},
        ]}
    def handler(request):
        assert request.url.path.endswith('/services/aigc/multimodal-generation/generation')
        body = json.loads(request.content)
        assert body['input']['messages'][0]['content'][0]['input_audio']['data'].startswith('data:audio/mpeg;base64,')
        assert body['parameters']['format'] == 'mp3'
        assert body['parameters']['enable_words'] is True
        if streaming:
            events = [{'output': {'sentence': {**sentence, 'sentence_end': False, 'text': 'partial'}}},
                      {'output': {'sentence': sentence}}]
            return httpx.Response(200, headers={'content-type': 'text/event-stream'},
                text=''.join('event:result\ndata:' + json.dumps(e) + '\n\n' for e in events))
        return httpx.Response(200, json={'output': {'sentence': sentence}})
    original = httpx.Client
    monkeypatch.setattr(httpx, 'Client', lambda **kwargs: original(transport=httpx.MockTransport(handler)))
    result = qwen.transcribe_audio(None, route, audio)
    assert result['segments'] == [{'start': .5, 'end': 2.1, 'text': 'Hello world!'}]


def test_direct_audio_segments_keep_offsets(client, monkeypatch):
    task_id = client.post('/api/tasks', json={'url': 'https://youtu.be/abcdefghijk'}).json()['id']
    folder = store.DATA / 'media' / task_id
    folder.mkdir()
    settings = config.Settings()
    settings.transcription.primary = config.Route(protocol='qwen_audio', base_url='https://qwen.test/api/v1', model='qwen-audio-3.0-asr-flash')
    cuts = []
    def cut(args, *unused):
        cuts.append((args[args.index('-ss') + 1], args[args.index('-t') + 1]))
    monkeypatch.setattr(language, 'run', cut)
    monkeypatch.setattr(qwen, 'transcribe_audio', lambda *args: {'language': 'en', 'segments': [{'start': 0, 'end': 1, 'text': 'Hello'}]})
    language.transcribe(store.task(task_id), settings, folder, 601)
    assert cuts == [('0', '300'), ('300', '300'), ('600', '300')]
    cues = language.load_cues(folder / 'en.srt')
    assert [cue.start.total_seconds() for cue in cues] == [0, 300, 600]
    assert json.loads((folder / 'asr-layout.json').read_text())['part_seconds'] == 300


def test_no_words_response_is_not_a_transport_failure(monkeypatch, tmp_path):
    audio = tmp_path / 'audio.mp3'
    audio.write_bytes(b'audio')
    original = httpx.Client
    def handler(request):
        return httpx.Response(200, headers={'content-type': 'text/event-stream'},
            text='event:error\ndata:{"code":"CLIENT_ERROR","message":"ASR_RESPONSE_HAVE_NO_WORDS"}\n\n')
    monkeypatch.setattr(httpx, 'Client', lambda **kwargs: original(transport=httpx.MockTransport(handler)))
    route = config.Route(protocol='qwen_audio', model='qwen-audio-3.0-asr-flash', base_url='https://qwen.test/api/v1')
    assert qwen.transcribe_audio(None, route, audio) == {'language': '', 'segments': [], 'no_speech': True}


def test_silent_part_keeps_later_speech_offset(client, monkeypatch):
    task_id = client.post('/api/tasks', json={'url': 'https://youtu.be/abcdefghijk'}).json()['id']
    folder = store.DATA / 'media' / task_id
    folder.mkdir()
    settings = config.Settings()
    settings.transcription.primary = config.Route(protocol='qwen_audio', base_url='https://qwen.test/api/v1', model='qwen-audio-3.0-asr-flash')
    monkeypatch.setattr(language, 'run', lambda *args: None)
    calls = []
    def recognize(_, route, audio):
        calls.append(audio.name)
        return {'language': '', 'segments': [], 'no_speech': True} if audio.name == 'audio-0.mp3' else {
            'language': 'en', 'segments': [{'start': 1, 'end': 2, 'text': 'Hello'}]}
    monkeypatch.setattr(qwen, 'transcribe_audio', recognize)
    language.transcribe(store.task(task_id), settings, folder, 305)
    assert language.load_cues(folder / 'en.srt')[0].start.total_seconds() == 301
    language.transcribe(store.task(task_id), settings, folder, 305)
    assert calls == ['audio-0.mp3', 'audio-1.mp3']


def test_all_silent_audio_pauses_without_invented_captions(client, monkeypatch):
    task_id = client.post('/api/tasks', json={'url': 'https://youtu.be/abcdefghijk'}).json()['id']
    folder = store.DATA / 'media' / task_id
    folder.mkdir()
    (folder / 'asr-layout.json').write_text('{"part_seconds":300}')
    (folder / 'asr-0.json').write_text('{"segments":[],"language":""}')
    settings = config.Settings()
    settings.transcription.primary = config.Route(base_url='https://qwen.test/api/v1', model='qwen-audio-3.0-asr-flash', protocol='qwen_audio')
    with pytest.raises(language.Waiting, match='全片未识别'):
        language.transcribe(store.task(task_id), settings, folder, 5)
    assert not (folder / 'en.srt').exists()
