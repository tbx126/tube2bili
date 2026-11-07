#!/usr/bin/python3
"""Bounded Tunnel recovery with local health telemetry, using only the stdlib."""
import json
import os
from pathlib import Path
import subprocess
import time
import urllib.request
import urllib.error

DATA = Path('/vol2/1000/media/tube2bili')
STATE = Path('/var/lib/tube2bili-network-watch/state.json')


def reachable(url, proxy=None, expected=200):
    client = urllib.request.build_opener(urllib.request.ProxyHandler(
        {'https': proxy} if proxy else {}))
    try:
        with client.open(url, timeout=10) as response:
            return response.status == expected
    except Exception:
        return False


def should_restart(state, tunnel_ok, proxy_ok, now):
    failures = 0 if tunnel_ok else state.get('failures', 0) + 1
    state['failures'] = failures
    return not tunnel_ok and proxy_ok and failures >= 3 and now - state.get('restart_at', 0) >= 900


def should_restart_proxy(state, proxy_ok, secondary_ok):
    if proxy_ok or secondary_ok:
        state['proxy_failures'] = 0
        state['proxy_restart_attempted'] = False
        return False
    state['proxy_failures'] = state.get('proxy_failures', 0) + 1
    return state['proxy_failures'] >= 3 and not state.get('proxy_restart_attempted', False)


def public_status():
    client = urllib.request.build_opener(urllib.request.ProxyHandler(
        {'https': 'http://127.0.0.1:17897'}))
    try:
        request = urllib.request.Request('https://tube2bili.tbx12.com/healthz',
            headers={'User-Agent': 'Tube2Bili-NetworkWatch/1.0', 'Cache-Control': 'no-cache'})
        with client.open(request, timeout=10) as response:
            return response.status, False
    except urllib.error.HTTPError as error:
        # A ready connector does not prove that the public hostname is routed.
        return error.code, error.code == 530 and b'1033' in error.read(4096)
    except Exception:
        return None, False


def should_restart_public(state, status, no_connector, tunnel_ok, proxy_ok, app_ok, now):
    if status == 200:
        state['public_failures'] = 0
        state['public_restart_attempted'] = False
        return False
    state['public_failures'] = state.get('public_failures', 0) + 1 if no_connector else 0
    return (no_connector and tunnel_ok and proxy_ok and app_ok
            and state['public_failures'] >= 3
            and not state.get('public_restart_attempted', False)
            and now - state.get('restart_at', 0) >= 900)


def main():
    state = json.loads(STATE.read_text()) if STATE.exists() else {}
    now = time.time()
    proxy = reachable('https://www.youtube.com/generate_204', 'http://127.0.0.1:17897', 204)
    secondary = True if proxy else reachable('https://www.gstatic.com/generate_204', 'http://127.0.0.1:17897', 204)
    tunnel = reachable('http://127.0.0.1:42081/ready')
    app = reachable('http://192.168.5.6:18080/healthz')
    status, no_connector = public_status()
    public_restart = should_restart_public(state, status, no_connector, tunnel, proxy, app, now)
    if should_restart_proxy(state, proxy, secondary):
        # Restart the existing validated configuration once per outage. Never
        # fetch a different subscription or loop restarts against a dead node.
        state['proxy_restart_attempted'] = True
        STATE.parent.mkdir(parents=True, exist_ok=True)
        STATE.write_text(json.dumps(state))
        subprocess.run(['systemctl', 'restart', 'mihomo-nas.service'], check=True, timeout=30)
        print('Proxy failed both probes three times; one recovery restart attempted')
    if should_restart(state, tunnel, proxy, now) or public_restart:
        # Persist the latch before invoking Docker, including failed restarts.
        state['restart_at'] = now
        if public_restart:
            state['public_restart_attempted'] = True
        STATE.parent.mkdir(parents=True, exist_ok=True)
        STATE.write_text(json.dumps(state))
        subprocess.run(['docker', 'restart', 'cloudflared-azuki'], check=True,
                       stdout=subprocess.DEVNULL, timeout=45)
        print('Tunnel readiness or public 1033 failed three checks; bounded recovery attempted')
    else:
        print(f'App={app} YouTubeProxy={proxy} Tunnel={tunnel} PublicHTTP={status}')
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(state))
    health = {'checked_at': now, 'proxy': proxy, 'tunnel': tunnel, 'app': app,
              'public': status == 200, 'public_status': status,
              'public_failures': state['public_failures'],
              'tunnel_failures': state['failures']}
    temporary = DATA / 'network-health.tmp'
    temporary.write_text(json.dumps(health))
    os.chmod(temporary, 0o644)
    temporary.replace(DATA / 'network-health.json')


if __name__ == '__main__':
    main()
