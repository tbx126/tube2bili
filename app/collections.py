"""Creator collections: check membership before writes and after uncertain responses."""
import json
import httpx

import httpx

from . import store
from .media import Waiting, CollectionRetry, check
from .publishing import cookies

BASE = 'https://member.bilibili.com/x2/creative/web/'


def request(client, method, path, **kwargs):
    response = client.request(method, BASE + path, **kwargs)
    response.raise_for_status()
    try:
        value = response.json()
    except ValueError:
        raise Waiting('B 站合集接口返回非 JSON 内容，请检查接口及登录；已投稿视频保留') from None
    if not isinstance(value, dict):
        raise Waiting('B 站合集接口响应格式变化，已停止写入并保留稿件')
    if value.get('code') in (-101, -111):
        raise Waiting('B 站登录已失效，请更新凭证后重试合集步骤')
    if value.get('code') == 20111:
        raise CollectionRetry('B 站合集操作暂未完成（20111），稍后先核对成员再继续', 300)
    if value.get('code') != 0:
        raise Waiting(f'B 站合集接口拒绝请求（代码 {value.get("code")}），请检查合集权限及稿件审核状态')
    return value.get('data') or {}


def client_for(jar):
    return httpx.Client(cookies=jar, timeout=30, trust_env=False,
                        headers={'Referer': 'https://member.bilibili.com/', 'User-Agent': 'Mozilla/5.0'})


def owned(client):
    result = []
    for page in range(1, 101):
        data = request(client, 'GET', 'seasons', params={'pn': page, 'ps': 30})
        items = data.get('seasons') or []
        result.extend(items)
        if len(result) >= data.get('total', 0) or not items:
            return result
    raise Waiting('合集数量超出读取范围，请检查账号')


def list_collections():
    with client_for(cookies()) as client:
        return [{'id': item['season']['id'], 'title': item['season']['title'],
                 'sections': [{'id': s['id'], 'title': s['title']}
                              for s in (item.get('sections') or {}).get('sections') or []]}
                for item in owned(client)]


def add(task, settings, folder):
    try:
        return _add(task, settings, folder)
    except httpx.HTTPError as error:
        status = error.response.status_code if isinstance(error, httpx.HTTPStatusError) else None
        if status is not None and status not in (408, 425, 429, 500, 502, 503, 504):
            raise Waiting(f'B 站合集接口返回 HTTP {status}，请检查账号及合集权限；已投稿视频保留') from None
        detail = f'HTTP {status}' if status else type(error).__name__
        raise CollectionRetry(f'加入合集暂时失败（{detail}），稍后先检查成员再继续；不会重新投稿', 300) from None


def _add(task, settings, folder):
    payload = store.task(task['id'])['payload']
    target = payload.get('collection_target')
    if target is None:
        options = {**settings.posting.model_dump(), **payload.get('options', {})}
        target = {key: options[key] for key in ('season_id', 'section_id')}
        payload['collection_target'] = target
        store.update(task['id'], payload=payload)
    season_id, section_id = target['season_id'], target['section_id']
    if not season_id:
        return
    receipt = folder / 'collection-receipt.json'
    if receipt.exists() and json.loads(receipt.read_text('utf-8')).get('target') == target:
        return
    jar = cookies()
    with client_for(jar) as client:
        collection = next((i for i in owned(client) if i['season']['id'] == season_id), None)
        if collection is None:
            raise Waiting('当前账号找不到目标合集，请在 B 站创作中心检查合集及权限')
        sections = (collection.get('sections') or {}).get('sections') or []
        if not section_id and len(sections) == 1:
            section_id = sections[0]['id']
        if section_id not in {s['id'] for s in sections}:
            raise Waiting('合集小节无效或不唯一，请在订阅设置中选择小节；已有任务需保留原目标或重新建任务')
        response = client.get('https://api.bilibili.com/x/web-interface/view', params={'bvid': payload['bvid']})
        response.raise_for_status()
        info = response.json()
        if info.get('code') != 0:
            raise CollectionRetry('投稿尚不可读取，稍后重试合集；不会重新投稿', 300)
        info = info['data']
        if str(info['owner']['mid']) != str(jar.get('DedeUserID')) or task['url'] not in info.get('desc', ''):
            raise Waiting('目标稿件账号归属或来源链接不匹配，停止加入合集')
        aid = info['aid']

        def contains():
            data = request(client, 'GET', 'season/section', params={'id': section_id})
            if 'episodes' not in data or (data['episodes'] is not None and not isinstance(data['episodes'], list)):
                raise Waiting('合集小节响应格式变化，无法安全确认是否已加入')
            return any(str(e.get('aid')) == str(aid) for e in data['episodes'] or [])

        if not contains():
            check(task['id'])
            request(client, 'POST', 'season/section/episodes/add', params={'csrf': jar['bili_jct']},
                    json={'sectionId': section_id, 'episodes': [
                        {'aid': aid, 'cid': info['pages'][0]['cid'], 'title': info['title']}]})
            if not contains():
                raise CollectionRetry('合集更新已受理但成员尚不可见，稍后先检查成员；不会重新投稿', 300)
        tmp = receipt.with_suffix('.tmp')
        tmp.write_text(json.dumps({'target': target, 'aid': aid, 'section_id': section_id}), 'utf-8')
        tmp.replace(receipt)
        store.event(task['id'], '已确认加入 B 站合集：' + collection['season']['title'])
