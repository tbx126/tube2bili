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
PROBE_URL = 'https://www.youtube.com/generate_204'


def probe_running(value):
    port = int(value['mixed-port'])
    client = urllib.request.build_opener(urllib.request.ProxyHandler(
        {'https': f'http://127.0.0.1:{port}'}))
    # A large fallback group may still be checking cold routes after reload.
    for _ in range(8):
        try:
            with client.open(PROBE_URL, timeout=8) as response:
                if response.status == 204:
                    return
        except Exception:
            time.sleep(2)
    raise ValueError('Production proxy health check failed')


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
                    with client.open(PROBE_URL, timeout=8) as response:
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
    if not isinstance(current, dict) or not isinstance(incoming, dict) or not isinstance(incoming.get('proxies'), list):
        raise ValueError('Proxy configuration must contain a node list')
    nodes = []
    for value in incoming.get('proxies', []):
        if not isinstance(value, dict):
            raise ValueError('Invalid proxy node')
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
        'url': PROBE_URL, 'interval': 60, 'lazy': False,
        'timeout': 6000, 'expected-status': 204, 'max-failed-times': 2}]
    # Keep local networks direct, but route Tunnel transport through working nodes.
    current['rules'] = [rule.replace('PROCESS-NAME,cloudflared,DIRECT', 'PROCESS-NAME,cloudflared,PROXY')
        .replace('DOMAIN-SUFFIX,argotunnel.com,DIRECT', 'DOMAIN-SUFFIX,argotunnel.com,PROXY')
        for rule in current['rules']]
    current['rules'].insert(0, 'DOMAIN-SUFFIX,cftunnel.com,PROXY')
    current['rules'] = list(dict.fromkeys(current['rules']))
    return current


def local_source(args_file):
    if args_file:
        return args_file
    descriptor = ROOT / 'source.json'
    if descriptor.exists():
        value = json.loads(descriptor.read_text())
        if value.get('mode') != 'file' or not isinstance(value.get('path'), str):
            raise ValueError('Invalid proxy source; refusing subscription fallback')
        path = Path(value['path']).resolve()
        if path.parent != ROOT.resolve() or not path.is_file():
            raise ValueError('Proxy source must be an existing private configuration file')
        return path
    return None


def fetch_subscription(path):
    url = path.read_text().strip()
    if not url.startswith('https://'):
        raise ValueError('HTTPS subscription required')
    req = urllib.request.Request(url, headers={'User-Agent': 'clash.meta'})
    with urllib.request.urlopen(req, timeout=30) as response:
        raw = response.read(2_000_001)
    if len(raw) > 2_000_000:
        raise ValueError('Subscription too large')
    return raw


def clear_fallback_pin(value):
    controller = value.get('external-controller', '')
    port = controller.rpartition(':')[2]
    if not port.isdecimal() or not 0 < int(port) <= 65535:
        return
    url = f'http://127.0.0.1:{port}/proxies/PROXY'
    headers = {'Authorization': 'Bearer ' + str(value.get('secret', ''))}
    client = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with client.open(urllib.request.Request(url, headers=headers), timeout=5) as response:
        state = json.load(response)
    if state.get('fixed'):
        with client.open(urllib.request.Request(url, headers=headers, method='DELETE'), timeout=5) as response:
            if response.status != 204:
                raise ValueError('Could not clear fixed proxy policy')
        print('Cleared manual selection from PROXY fallback')


def main():
    import fcntl
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--file', type=Path)
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise SystemExit('Run as root')
    with open('/run/tube2bili-proxy-refresh.lock', 'w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        source = local_source(args.file)
        if source:
            raw = source.read_bytes()
        else:
            raw = fetch_subscription(ROOT / 'subscription.url')
        if len(raw) > 2_000_000:
            raise ValueError('Subscription too large')
        current = yaml.safe_load(CONFIG.read_text())
        previous = copy.deepcopy(current)
        incoming = yaml.safe_load(raw)
        backup_url = ROOT / 'backup-subscription.url'
        if source and backup_url.is_file():
            # A broken subscription must never replace a working configuration.
            data = yaml.safe_load(fetch_subscription(backup_url))
            if not isinstance(data, dict) or not isinstance(data.get('proxies'), list):
                raise ValueError('Invalid backup subscription')
            unique = {}
            for node in data['proxies']:
                if isinstance(node, dict) and isinstance(node.get('name'), str):
                    unique.setdefault(node['name'], node)
            backup = merge_nodes({'rules': []}, {'proxies': list(unique.values())})
            primary = incoming['proxies']
            names = {node['name'] for node in primary}
            additions = [node for node in backup['proxies'] if node['name'] not in names]
            if not additions:
                raise ValueError('No distinct backup nodes')
            incoming = {'proxies': [*primary, *additions]}
        value = merge_nodes(current, incoming)
        if source and backup_url.is_file():
            # Resolve new proxy hostnames without depending on an already
            # working proxy or a single HTTPS resolver during startup.
            dns = value.get('dns') or {}
            bootstrap = dns.get('default-nameserver') or []
            if dns.get('enable') and bootstrap:
                dns['proxy-server-nameserver'] = list(dict.fromkeys(
                    [*bootstrap, *dns.get('proxy-server-nameserver', [])]))
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
            if source and backup_url.is_file():
                time.sleep(2)
                clear_fallback_pin(value)
            probe_running(value)
        except (subprocess.CalledProcessError, OSError, ValueError):
            shutil.copy2(backup, CONFIG)
            subprocess.run(['systemctl', 'restart', 'mihomo-nas.service'], check=True)
            raise RuntimeError('Reload or health check failed; previous configuration restored') from None
        print(json.dumps({'nodes': len(value['proxies']), 'validation': 'passed'}))


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        # URLs and provider credentials must never appear in logs.
        raise SystemExit(f'Proxy refresh failed ({type(error).__name__}); configuration retained') from None
