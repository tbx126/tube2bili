"""Isolated async client lifecycle; credentials never travel in command arguments."""
import asyncio
import json
import sys

from bilibili_api import Credential, video

from . import store
from .language import load_cues
from .publishing import cookies


def subtitle_data(path):
    return {'font_size': 0.4, 'font_color': '#FFFFFF', 'background_alpha': 0.5,
            'background_color': '#000000', 'Stroke': 'none',
            'body': [{'from': c.start.total_seconds(), 'to': c.end.total_seconds(),
                      'location': 2, 'content': c.content} for c in load_cues(path)]}


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
        # Chinese track contains both languages; English-only track is also available.
        for language, filename in [('zh-CN', 'bilingual.srt'), ('en', 'en.srt')]:
            checkpoint = folder / f'subtitle-{language}-receipt.json'
            if checkpoint.exists():
                continue
            result = await instance.submit_subtitle(lan=language, data=subtitle_data(folder / filename),
                                                    submit=True, sign=False, cid=cid)
            checkpoint.write_text(json.dumps(result, ensure_ascii=False), 'utf-8')
        return {'ok': True, 'cid': cid}
    tracks = (await instance.get_subtitle(cid=cid) or {}).get('subtitles', [])
    languages = {track.get('lan') for track in tracks}
    return {'ok': {'zh-CN', 'en'} <= languages, 'cid': cid}


def main():
    action, task_id = sys.argv[1:]
    try:
        result = asyncio.run(asyncio.wait_for(perform(action, task_id), timeout=150))
    except Exception as exc:
        result = {'ok': False, 'auth_error': getattr(exc, 'code', None) in (-101, -111)}
    folder = store.DATA / 'media' / task_id
    (folder / f'{action}-result.json').write_text(json.dumps(result), 'utf-8')


if __name__ == '__main__':
    main()
