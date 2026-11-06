"""Short opening phrases based on measured ASR word times, never guessed offsets."""
import datetime as dt
import difflib
import hashlib
import json
import math
import re
import shutil
import uuid

import srt

from . import qwen, store
from .media import Waiting, check, run

POLICY = 'intro-word-phrases-v1'


def group_words(words):
    groups, current = [], []
    def flush():
        if current:
            text = ' '.join(word['text'].strip() for word in current)
            text = re.sub(r'\s+([,.;:!?])', r'\1', text)
            text = re.sub(r"\b([A-Za-z]+)\s*'\s*(ll|re|ve|s|d|t|m)\b", r"\1'\2", text)
            groups.append({'start': current[0]['start'], 'end': current[-1]['end'], 'text': text})
            current.clear()
    previous = -1
    for word in words:
        start, end, text = float(word['start']), float(word['end']), str(word['text']).strip()
        if not text or not (math.isfinite(start) and math.isfinite(end) and 0 <= start < end) or start < previous:
            raise Waiting('开头字幕的词级时间戳无效，保留原字幕等待检查')
        previous = start
        boundary = text.casefold().strip(',.!?') in {'and', 'but', 'or', 'to', 'from', 'with', 'for'}
        if current and (len(current) >= 4 or end - current[0]['start'] > 1.6
                or len(' '.join(w['text'] for w in current)) + len(text) > 28
                or start - current[-1]['end'] > .35 or (boundary and len(current) >= 2)):
            flush()
        current.append({'start': start, 'end': end, 'text': text})
        if text.endswith(('.', '!', '?', ';')) or (text.endswith(',') and len(current) >= 2):
            flush()
    flush()
    for index in range(len(groups) - 1):
        groups[index]['end'] = min(groups[index]['end'], groups[index + 1]['start'])
    return [group for group in groups if group['end'] > group['start']]


def signature(cues):
    return hashlib.sha256(srt.compose(cues).encode()).hexdigest()


def prepare_intro(task, settings, folder, cues):
    """Keep the remainder of the video untouched and checkpoint paid recognition."""
    seconds = settings.intro_phrase_seconds
    if not cues or not seconds:
        return cues
    manifest = folder / 'intro-phrases.json'
    state = json.loads(manifest.read_text()) if manifest.exists() else {}
    if state.get('policy') == POLICY and state.get('output_sha256') == signature(cues):
        return cues
    prefix = [cue for cue in cues if cue.end.total_seconds() <= seconds]
    if not prefix or prefix[0] != cues[0]:
        return cues
    cutoff = prefix[-1].end.total_seconds()
    tail = cues[len(prefix):]
    cache = folder / 'intro-word-timestamps.json'
    if cache.exists():
        value = json.loads(cache.read_text())
    else:
        asr = folder / 'asr-0.json'
        value = json.loads(asr.read_text()) if asr.exists() else {}
        if not value.get('words'):
            route = next((route for route in [settings.transcription.primary,
                *([settings.transcription.fallback] if settings.transcription.fallback_enabled else [])]
                if route.protocol == 'qwen_audio' and route.base_url and route.model), None)
            if route is None:
                # A timestamped provider is necessary; never interpolate captions.
                raise Waiting('开头短语字幕需要千问音频直传的词级时间戳，请配置语音识别路由')
            audio = folder / 'intro-audio.mp3'
            run(['ffmpeg', '-y', '-i', 'source.mp4', '-t', str(cutoff), '-vn', '-ac', '1', '-ar', '16000', '-b:a', '48k', str(audio)], folder, task['id'])
            check(task['id'])
            value = qwen.transcribe_audio(task['id'], route, audio)
            from .language import record
            record(task['id'], route, minutes=cutoff / 60)
        qwen.save(cache, value)
    words = [dict(word) for word in value.get('words', []) if float(word['start']) < cutoff]
    if not words:
        raise Waiting('语音服务未返回开头词级时间戳，保留原字幕等待检查')
    for word in words:
        word['end'] = min(float(word['end']), cutoff)
    # Independently recognized audio must agree with the source transcript before
    # changing any files. A mismatch is reviewable, never silently dropped text.
    normalize = lambda text: re.sub(r'[^a-z0-9]+', '', text.casefold())
    old_text = normalize(' '.join(cue.content for cue in prefix))
    new_text = normalize(' '.join(word['text'] for word in words))
    if difflib.SequenceMatcher(None, old_text, new_text, autojunk=False).ratio() < .85:
        raise Waiting('开头语音识别与原字幕文本差异较大，保留原字幕等待核对')
    groups = group_words(words)
    updated = [srt.Subtitle(index + 1, dt.timedelta(seconds=group['start']),
        dt.timedelta(seconds=group['end']), group['text']) for index, group in enumerate(groups)]
    prefix_count = len(updated)
    updated.extend(srt.Subtitle(prefix_count + index + 1, cue.start, cue.end, cue.content)
                   for index, cue in enumerate(tail))
    backup = folder / ('intro-backup-' + uuid.uuid4().hex)
    backup.mkdir()
    for name in ('en.srt', 'zh.srt', 'bilingual.srt', 'bilingual.ass',
                 'subtitle-zh-CN-receipt.json', 'subtitle-en-receipt.json'):
        path = folder / name
        if path.exists():
            shutil.copy2(path, backup / name)
    # Existing per-batch Chinese translations refer to the old cue numbering.
    for path in folder.glob('translation-*.json'):
        if re.fullmatch(r'translation-\d+\.json', path.name):
            path.rename(backup / path.name)
    qwen.save(manifest, {'policy': POLICY, 'cutoff': cutoff, 'prefix_count': len(groups),
        'old_prefix_count': len(prefix), 'input_sha256': signature(cues),
        'output_sha256': signature(updated), 'backup': backup.name})
    (folder / 'en.srt').write_text(srt.compose(updated), 'utf-8')
    store.event(task['id'], f'开头 {cutoff:.1f} 秒改为词级短语字幕，原字幕已备份；其余时间轴保持不变')
    return updated
