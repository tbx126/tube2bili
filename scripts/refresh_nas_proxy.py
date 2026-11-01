#!/usr/bin/python3
"""Refresh only NAS proxy nodes, retaining local DNS, routing and credentials."""
import argparse
import copy
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import time
import urllib.request

import yaml


ROOT = Path('/vol2/docker/mihomo-system/config')
CONFIG = ROOT / 'config.yaml'


def probe_candidate(value):
    """Test a separate listener before replacing a functioning NAS configuration."""
    with tempfile.TemporaryDirectory(prefix='tube2bili-proxy-') as folder:
        os.chmod(folder, 0o700)
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            port = listener.getsockname()[1]
        candidate = {'mixed-port': port, 'bind-address': '127.0.0.1', 'allow-lan': False,
            'mode': 'rule', 'log-level': 'error', 'interface-name': value.get('interface-name', ''),
            'proxies': value['proxies'], 'proxy-groups': value['proxy-groups'], 'rules': ['MATCH,PROXY']}
        path = Path(folder) / 'config.yaml'
        path.write_text(yaml.safe_dump(candidate, allow_unicode=True))
        os.chmod(path, 0o600)
        process = subprocess.Popen(['/usr/local/bin/mihomo', '-d', folder, '-f', str(path)],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            client = urllib.request.build_opener(urllib.request.ProxyHandler(
                {'https': f'http://127.0.0.1:{port}'}))
            for _ in range(5):
                if process.poll() is not None:
                    break
                try:
                    with client.open('https://www.youtube.com/generate_204', timeout=8) as response:
                        if response.status == 204:
                            return
                except Exception:
                    time.sleep(2)
            raise ValueError('No healthy subscription route; previous configuration retained')
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


def merge_nodes(current, incoming):
    nodes = []
    for value in incoming.get('proxies', []):
        if value.get('type') not in ('vless', 'trojan', 'hysteria2', 'ss', 'vmess'):
            continue
        if any(word in value.get('name', '') for word in ('剩余', '重置', '到期', '建议', '官网')):
            continue
        node = dict(value)
        node.pop('interface-name', None)
        node.pop('dialer-proxy', None)
        nodes.append(node)
    names = [node['name'] for node in nodes]
    if not names or len(names) != len(set(names)):
        raise ValueError('Empty subscription or duplicate node names')
    current['proxies'] = nodes
    current.pop('proxy-providers', None)
    current['proxy-groups'] = [{'name': 'PROXY', 'type': 'fallback', 'proxies': names,
        'url': 'https://www.gstatic.com/generate_204', 'interval': 180, 'lazy': False,
        'timeout': 8000}]
    # Keep local networks direct, but route Tunnel transport through working nodes.
    current['rules'] = [rule.replace('PROCESS-NAME,cloudflared,DIRECT', 'PROCESS-NAME,cloudflared,PROXY')
        .replace('DOMAIN-SUFFIX,argotunnel.com,DIRECT', 'DOMAIN-SUFFIX,argotunnel.com,PROXY')
        for rule in current['rules']]
    current['rules'].insert(0, 'DOMAIN-SUFFIX,cftunnel.com,PROXY')
    current['rules'] = list(dict.fromkeys(current['rules']))
    return current


def main():
    import fcntl
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--file', type=Path)
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise SystemExit('Run as root')
    with open('/run/tube2bili-proxy-refresh.lock', 'w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if args.file:
            raw = args.file.read_bytes()
        else:
            url = (ROOT / 'subscription.url').read_text().strip()
            if not url.startswith('https://'):
                raise ValueError('HTTPS subscription required')
            req = urllib.request.Request(url, headers={'User-Agent': 'clash.meta'})
            with urllib.request.urlopen(req, timeout=30) as response:
                raw = response.read(2_000_001)
        if len(raw) > 2_000_000:
            raise ValueError('Subscription too large')
        current = yaml.safe_load(CONFIG.read_text())
        previous = copy.deepcopy(current)
        value = merge_nodes(current, yaml.safe_load(raw))
        if value == previous:
            print(json.dumps({'nodes': len(value['proxies']), 'changed': False}))
            return
        tmp = CONFIG.with_suffix('.candidate.yaml')
        tmp.write_text(yaml.safe_dump(value, allow_unicode=True, sort_keys=False))
        os.chmod(tmp, 0o600)
        # Do not leak node credentials from validation output into journald.
        result = subprocess.run(['/usr/local/bin/mihomo', '-t', '-d', str(ROOT), '-f', str(tmp)],
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        if result.returncode:
            tmp.unlink(missing_ok=True)
            raise ValueError('Mihomo rejected subscription; previous config retained')
        probe_candidate(value)
        backup = CONFIG.with_suffix('.last-good.yaml')
        shutil.copy2(CONFIG, backup)
        os.chmod(backup, 0o600)
        tmp.replace(CONFIG)
        try:
            subprocess.run(['systemctl', 'restart', 'mihomo-nas.service'], check=True)
        except subprocess.CalledProcessError:
            shutil.copy2(backup, CONFIG)
            subprocess.run(['systemctl', 'restart', 'mihomo-nas.service'], check=True)
            raise RuntimeError('Reload failed; previous configuration restored') from None
        print(json.dumps({'nodes': len(value['proxies']), 'validation': 'passed'}))


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        # URLs and provider credentials must never appear in logs.
        raise SystemExit(f'Proxy refresh failed ({type(error).__name__}); configuration retained') from None
