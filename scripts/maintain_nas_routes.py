#!/usr/bin/python3
"""Keep Tube2Bili bridge traffic ahead of NAS source-policy default routes."""
import argparse
import ipaddress
import json
import os
from pathlib import Path
import subprocess


PRIORITY = 9
STATE = Path('/var/lib/tube2bili-routing/subnets.json')


def run(*args):
    return subprocess.check_output(args, text=True).strip()


def rule_subnet(rule):
    destination = rule.get('dst')
    if not destination:
        return None
    if '/' not in destination:
        destination += '/' + str(rule.get('dstlen', 32))
    return str(ipaddress.ip_network(destination))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--remove', action='store_true')
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise SystemExit('Run as root')
    previous = set(json.loads(STATE.read_text()) if STATE.exists() else [])
    desired = set()
    if not args.remove:
        container = json.loads(run('docker', 'inspect', 'tube2bili-app-1'))[0]
        for network in container['NetworkSettings']['Networks'].values():
            info = json.loads(run('docker', 'network', 'inspect', network['NetworkID']))[0]
            if info['Driver'] != 'bridge':
                continue
            for config in info['IPAM']['Config']:
                subnet = ipaddress.ip_network(config['Subnet'])
                if subnet.version != 4 or not subnet.is_private:
                    continue
                routes = json.loads(run('ip', '-j', '-4', 'route', 'show', 'table', 'main', 'exact', str(subnet)))
                if not any(r.get('scope') == 'link' for r in routes):
                    raise SystemExit(f'No connected main-table route for {subnet}')
                desired.add(str(subnet))
        if not desired:
            raise SystemExit('No connected IPv4 bridge network found; keeping existing rules')
    rules = json.loads(run('ip', '-j', '-4', 'rule', 'show'))
    existing = set()
    for rule in rules:
        if rule.get('priority') != PRIORITY:
            continue
        subnet = rule_subnet(rule)
        if (subnet not in previous | desired or rule.get('table') not in ('main', 254)
                or rule.get('src', 'all') != 'all'
                or set(rule) - {'priority', 'src', 'dst', 'dstlen', 'table', 'protocol'}):
            raise SystemExit('Priority 9 is used by another rule; refusing to change it')
        existing.add(subnet)
    # Add new routes before removing obsolete ones; SSH and default routes stay intact.
    for subnet in sorted(desired - existing):
        run('ip', '-4', 'rule', 'add', 'priority', str(PRIORITY), 'to', subnet, 'lookup', 'main')
    for subnet in sorted(existing - desired):
        run('ip', '-4', 'rule', 'del', 'priority', str(PRIORITY), 'to', subnet, 'lookup', 'main')
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(sorted(desired)) + '\n')
    print('Tube2Bili main-table bridge routes: ' + ', '.join(sorted(desired)))


if __name__ == '__main__':
    main()
