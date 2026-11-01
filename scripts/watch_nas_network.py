#!/usr/bin/python3
"""Bounded Tunnel recovery with local health telemetry, using only the stdlib."""
import json
import os
from pathlib import Path
import subprocess
import time
import urllib.request

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


def main():
    state = json.loads(STATE.read_text()) if STATE.exists() else {}
    now = time.time()
    proxy = reachable('https://www.youtube.com/generate_204', 'http://127.0.0.1:17897', 204)
    tunnel = reachable('http://127.0.0.1:42081/ready')
    app = reachable('http://192.168.5.6:18080/healthz')
    if should_restart(state, tunnel, proxy, now):
        subprocess.run(['docker', 'restart', 'cloudflared-azuki'], check=True,
                       stdout=subprocess.DEVNULL, timeout=45)
        state['restart_at'] = now
        print('Tunnel not ready for three checks; restarted (15 minute restart limit)')
    else:
        print(f'App={app} YouTubeProxy={proxy} Tunnel={tunnel}')
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(state))
    health = {'checked_at': now, 'proxy': proxy, 'tunnel': tunnel, 'app': app,
              'tunnel_failures': state['failures']}
    temporary = DATA / 'network-health.tmp'
    temporary.write_text(json.dumps(health))
    os.chmod(temporary, 0o644)
    temporary.replace(DATA / 'network-health.json')


if __name__ == '__main__':
    main()
