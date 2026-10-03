"""Isolated async client lifecycle; credentials never travel in command arguments."""
import asyncio
import json
import sys
import re

from bilibili_api import Credential, video
from bilibili_api.utils.network import Api

from . import store
from .language import load_cues
from .publishing import cookies


def subtitle_data(path, color='#FFFFFF'):
    body = []
    for cue in load_cues(path):
        if re.search(r'(?:译者注|译注|按\s*glossary|根据.*glossary|（注：)', cue.content) or (path.name == 'en.srt' and re.search(r'[\u4e00-\u9fff]', cue.content)):
            raise ValueError('Subtitle contains translation commentary')
        chunks, chunk = [], ''
        for char in cue.content:
            if len((chunk + char).encode('utf-8')) > 80:
                chunks.append(chunk)
                chunk = ''
            chunk += char
        if chunk:
            chunks.append(chunk)
        start, end = cue.start.total_seconds(), cue.end.total_seconds()
        offset = 0
        for text in chunks:
            following = offset + len(text)
            body.append({'from': start + (end-start)*offset/len(cue.content),
                         'to': start + (end-start)*following/len(cue.content),
                         'location': 2, 'content': text})
            offset = following
    return {'font_size': 0.4, 'font_color': color, 'background_alpha': 0.5,
            'background_color': '#000000', 'Stroke': 'none',
            'body': body}


async def perform(action, task_id):
    task = store.task(task_id)
    folder = store.DATA / 'media' / task_id
    jar = cookies()
    credential = Credential(sessdata=jar['SESSDATA'], bili_jct=jar['bili_jct'],
        buvid3=jar.get('buvid3'), dedeuserid=jar.get('DedeUserID'))
    instance = video.Video(bvid=task['payload']['bvid'], credential=credential)
    info = await instance.get_info()
    cid = info['pages'][0]['cid']
    if str(info['owner']['mid']) != str(jar.get('DedeUserID')):
        raise ValueError('投稿不属于当前登录账号')
    if task['url'] not in info.get('desc', ''):
        raise ValueError('关联投稿的简介未包含该任务的来源链接')
    if action == 'subtitles':
        # CC language tracks contain only their own language.
        for language, filename, receipt_language in [('zh', 'zh.srt', 'zh-CN'), ('en', 'en.srt', 'en')]:
            checkpoint = folder / f'subtitle-{receipt_language}-receipt.json'
            if checkpoint.exists():
                continue
            # The pinned SDK's bundled language list predates Bilibili's zh code.
            # Retain its authenticated request/CSRF handling, bypass only that stale list.
            result = await Api(**video.API['operate']['submit_subtitle'], credential=credential).update_data(
                type=1, oid=cid, lan=language, data=json.dumps(subtitle_data(folder / filename, '#FFD54F' if language == 'zh' else '#FFFFFF')),
                submit=True, sign=False, bvid=task['payload']['bvid']).result
            checkpoint.write_text(json.dumps(result, ensure_ascii=False), 'utf-8')
        return {'ok': True, 'cid': cid}
    tracks = (await instance.get_subtitle(cid=cid) or {}).get('subtitles', [])
    languages = {track.get('lan') for track in tracks}
    return {'ok': bool({'zh', 'zh-CN', 'zh-Hans'} & languages) and 'en' in languages, 'cid': cid}


def main():
    action, task_id = sys.argv[1:]
    try:
        result = asyncio.run(asyncio.wait_for(perform(action, task_id), timeout=150))
    except Exception as exc:
        code = getattr(exc, 'code', None)
        result = {'ok': False, 'auth_error': code in (-101, -111),
                  'error_code': code if type(code) is int else None,
                  'error_type': type(exc).__name__}
    folder = store.DATA / 'media' / task_id
    (folder / f'{action}-result.json').write_text(json.dumps(result), 'utf-8')


if __name__ == '__main__':
    main()
