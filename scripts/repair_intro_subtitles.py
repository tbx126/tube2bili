"""Resumable opening-phrase CC updates; never uploads or reposts a video."""
import argparse
import asyncio
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import shutil
import time
import uuid

import httpx
import srt
from bilibili_api import Credential, video

from app import config, qwen, store
from app.bili_bridge import perform, subtitle_data
from app.language import load_cues, subtitle_lines
from app.media import Waiting, RetryLater
from app.publishing import cookies
from app.subtitle_phrases import POLICY, prepare_intro, signature
from app.subtitle_style import write_ass

FILES = ('en.srt', 'zh.srt', 'bilingual.srt', 'bilingual.ass')


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


async def visible(task, folder):
    jar = cookies()
    credential = Credential(sessdata=jar['SESSDATA'], bili_jct=jar['bili_jct'],
        buvid3=jar.get('buvid3'), dedeuserid=jar.get('DedeUserID'))
    instance = video.Video(bvid=task['payload']['bvid'], credential=credential)
    info = await instance.get_info()
    tracks = (await instance.get_subtitle(cid=info['pages'][0]['cid'])).get('subtitles') or []
    with httpx.Client(timeout=25) as client:
        for language, name in [('zh', 'zh.srt'), ('en', 'en.srt')]:
            track = next((item for item in tracks if item['lan'] == language), None)
            if not track or not track.get('subtitle_url'):
                return False
            url = track['subtitle_url']
            url = 'https:' + url if url.startswith('//') else url
            response = client.get(url)
            response.raise_for_status()
            actual = response.json().get('body', [])
            expected = subtitle_data(folder / name)['body']
            if len(actual) != len(expected):
                return False
            if any(str(a.get('content')) != e['content'] or abs(float(a['from']) - e['from']) > .02
                   or abs(float(a['to']) - e['to']) > .02 for a, e in zip(actual, expected)):
                return False
    return True


async def repair(task_id):
    task = store.task(task_id)
    if task['deleted'] or task['status'] not in ('completed', 'failed', 'waiting') or (
            task['status'] != 'completed' and task['stage'] != 'collection'):
        return {'id': task_id, 'status': 'skipped_active'}
    if not task['payload'].get('bvid') or task['payload'].get('assets_deleted'):
        return {'id': task_id, 'status': 'skipped_no_assets'}
    folder = store.DATA / 'media' / task_id
    if not all((folder / name).exists() for name in ('source.mp4', 'en.srt', 'zh.srt')):
        return {'id': task_id, 'status': 'skipped_no_assets'}
    checkpoint = folder / 'intro-phrases-repair.json'
    state = json.loads(checkpoint.read_text()) if checkpoint.exists() else {}
    original_manifest = folder / 'intro-phrases.json'
    if original_manifest.exists() and not checkpoint.exists():
        original = json.loads(original_manifest.read_text())
        if original.get('policy') == POLICY and original.get('output_sha256') == signature(load_cues(folder / 'en.srt')):
            return {'id': task_id, 'status': 'already_short'}
    if state.get('policy') == POLICY and state.get('status') == 'verified':
        return {'id': task_id, 'status': 'verified_cached'}
    if state.get('status') not in ('prepared', 'installed', 'submitted', 'pending_review'):
        stage = folder / 'intro-phrases-work'
        stage.mkdir(exist_ok=True)
        for name in ('en.srt', 'zh.srt', 'asr-0.json'):
            if not (stage / name).exists() and (folder / name).exists():
                shutil.copy2(folder / name, stage / name)
        if not (stage / 'source.mp4').exists():
            os.link(folder / 'source.mp4', stage / 'source.mp4')
        old_en, old_zh = load_cues(folder / 'en.srt'), load_cues(folder / 'zh.srt')
        if len(old_en) != len(old_zh) or any(a.start != b.start or a.end != b.end for a, b in zip(old_en, old_zh)):
            return {'id': task_id, 'status': 'needs_review', 'reason': 'existing_tracks_not_paired'}
        settings = config.get()
        settings.intro_phrase_seconds = 20
        try:
            new_en = prepare_intro(task, settings, stage, load_cues(stage / 'en.srt'))
        finally:
            (stage / 'source.mp4').unlink(missing_ok=True)
        manifest_path = stage / 'intro-phrases.json'
        if not manifest_path.exists():
            return {'id': task_id, 'status': 'skipped_no_short_intro'}
        manifest = json.loads(manifest_path.read_text())
        prefix_count, old_count = manifest['prefix_count'], manifest['old_prefix_count']
        new_zh = []
        for offset in range(0, prefix_count, 8):
            batch = new_en[offset:min(offset + 8, prefix_count)]
            translated = subtitle_lines(task_id, settings, stage / f'intro-translation-{offset}.json',
                [{'id': cue.index, 'text': cue.content} for cue in batch],
                'concise natural Simplified Chinese', task['title'])
            new_zh.extend(srt.Subtitle(cue.index, cue.start, cue.end, line['text'].strip())
                          for cue, line in zip(batch, translated['lines']))
        new_zh.extend(srt.Subtitle(prefix_count + index + 1, cue.start, cue.end, cue.content)
                      for index, cue in enumerate(old_zh[old_count:]))
        if len(new_zh) != len(new_en):
            raise ValueError('Paired subtitle count mismatch')
        (stage / 'zh.srt').write_text(srt.compose(new_zh), 'utf-8')
        (stage / 'bilingual.srt').write_text(srt.compose([
            srt.Subtitle(en.index, en.start, en.end, zh.content + '\n' + en.content)
            for zh, en in zip(new_zh, new_en)]), 'utf-8')
        write_ass(stage / 'bilingual.ass', new_zh, new_en)
        for name in ('en.srt', 'zh.srt'):
            subtitle_data(stage / name)
        backup = folder / ('intro-published-backup-' + uuid.uuid4().hex)
        backup.mkdir()
        for name in (*FILES, 'subtitle-en-receipt.json', 'subtitle-zh-CN-receipt.json'):
            if (folder / name).exists():
                shutil.copy2(folder / name, backup / name)
        state = {'policy': POLICY, 'status': 'prepared', 'backup': backup.name,
                 'cutoff': manifest['cutoff'], 'old_prefix_count': old_count, 'prefix_count': prefix_count,
                 'files': {name: digest(stage / name) for name in FILES}}
        qwen.save(checkpoint, state)
    if state.get('status') == 'prepared':
        stage = folder / 'intro-phrases-work'
        for name in FILES:
            if digest(stage / name) != state['files'][name]:
                raise ValueError('Prepared subtitle artifact changed')
            temporary = folder / (name + '.phrase-tmp')
            shutil.copy2(stage / name, temporary)
            temporary.replace(folder / name)
        state['status'] = 'installed'
        qwen.save(checkpoint, state)
    task = store.task(task_id)
    task['payload']['subtitle_revision'] = POLICY
    store.update(task_id, payload=task['payload'])
    await perform('subtitles', task_id)
    state['status'] = 'submitted'
    qwen.save(checkpoint, state)
    state['status'] = 'verified' if await visible(store.task(task_id), folder) else 'pending_review'
    state['updated'] = time.time()
    qwen.save(checkpoint, state)
    store.event(task_id, '开头短语字幕已更新并验证' if state['status'] == 'verified' else '开头短语字幕已提交，等待 B 站审核显示')
    return {'id': task_id, 'bvid': task['payload']['bvid'], 'status': state['status'],
            'old_prefix_count': state.get('old_prefix_count'), 'prefix_count': state.get('prefix_count')}


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--task')
    parser.add_argument('--all', action='store_true')
    args = parser.parse_args()
    if not args.task and not args.all:
        parser.error('Choose one task or explicitly --all')
    ids = [args.task] if args.task else [row['id'] for row in store.rows(
        "SELECT id FROM tasks WHERE deleted=0 AND status IN ('completed','failed','waiting') ORDER BY updated DESC")]
    counts = {}
    for task_id in ids:
        try:
            result = await repair(task_id)
        except Exception as error:
            # Keep credentials, signed URLs and provider response bodies private.
            result = {'id': task_id, 'status': 'error', 'error': type(error).__name__}
            if isinstance(error, (Waiting, RetryLater)):
                result['detail'] = str(error)
        print(json.dumps(result, ensure_ascii=False), flush=True)
        counts[result['status']] = counts.get(result['status'], 0) + 1
    print(json.dumps({'summary': counts}), flush=True)
    if args.all:
        store.notice('开头短语字幕更新结果：' + json.dumps(counts, ensure_ascii=False))


if __name__ == '__main__':
    asyncio.run(main())
