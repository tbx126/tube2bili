"""Read-only dashboard projections with one definition of visible records."""
import json
import time

from . import store


def service_health():
    path = store.DATA / 'network-health.json'
    try:
        value = json.loads(path.read_text())
        checked_at = float(value['checked_at'])
        fresh = 0 <= time.time() - checked_at <= 180
        network = {key: bool(value.get(key)) if fresh else None for key in ('proxy', 'tunnel', 'app')}
        network.update(checked_at=checked_at, stale=not fresh)
    except (OSError, ValueError, KeyError, TypeError):
        network = {}
    ai = store.get_runtime_state('language_api_backoff', {}) or {}
    telegram = store.get_runtime_state('telegram_delivery', {}) or {}
    return {'network': network,
            'translation_error': ai.get('reason', '') if ai.get('code') else '',
            'telegram_error': telegram.get('error', '') if telegram.get('until', 0) > time.time() else ''}


def cooldowns():
    now = time.time()
    result = []
    for key, name, stages in (
        ('youtube_rate_limit', 'YouTube 限流', ['download']),
        ('youtube_network_backoff', 'YouTube 网络', ['download']),
        ('language_api_backoff', '翻译服务', ['translate', 'publish']),
    ):
        state = store.get_runtime_state(key, {}) or {}
        until = float(state.get('until', 0))
        if until > now:
            result.append({'key': key, 'name': name, 'until': until, 'stages': stages,
                           'reason': state.get('reason', '')})
    return result


def task_view(task, holds=None):
    task = dict(task)
    payload = json.loads(task.pop('payload'))
    task.update(bvid=payload.get('bvid'), elapsed_seconds=payload.get('elapsed_seconds', 0),
                assets_deleted=payload.get('assets_deleted', False))
    holds = cooldowns() if holds is None else holds
    until = max([float(task.get('next_run') or 0)] + [h['until'] for h in holds if task['stage'] in h['stages']])
    task['retry_at'] = until if task['status'] in ('queued', 'retrying') else None
    task['retry_mode'] = 'automatic' if task['status'] == 'retrying' else 'manual' if task['status'] in ('waiting', 'failed') else 'none'
    task['channel_name'] = task.get('channel_name') or '手动导入'
    return task


def stats():
    counts = {row['status']: row['n'] for row in store.rows('SELECT status,COUNT(*) n FROM tasks WHERE deleted=0 GROUP BY status')}
    return {'total': sum(counts.values()), 'completed': counts.get('completed', 0),
            'attention': sum(counts.get(k, 0) for k in ('waiting', 'failed', 'reconcile')),
            'active': sum(counts.get(k, 0) for k in ('queued', 'running', 'retrying')),
            'counts': counts}
