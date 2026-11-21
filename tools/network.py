#!/usr/bin/env python3
"""Reconcile only the declared VLAN 106 storage profile and its parent MTU."""
import argparse
import json
from pathlib import Path
import re
import subprocess


def run(*args):
    return subprocess.check_output(args, text=True).strip()


def configure(config, apply=False):
    vlan = config['storage_interface']
    parent = config['storage_parent']
    link = json.loads(run('ip', '-d', '-j', 'link', 'show', vlan))[0]
    parent_link = json.loads(run('ip', '-j', 'link', 'show', parent))[0]
    info = link.get('linkinfo', {})
    if info.get('info_kind') != 'vlan' or info.get('info_data', {}).get('id') != 106 or link.get('link') != parent:
        raise ValueError('Storage must be VLAN 106 on the explicitly declared parent')
    changed = False
    for interface, current_link in ((parent, parent_link), (vlan, link)):
        uuid = run('nmcli', '-g', 'GENERAL.CON-UUID', 'device', 'show', interface)
        active = bool(re.fullmatch(r'[a-fA-F0-9-]{36}', uuid))
        if not active:
            candidates = [candidate for candidate in run('nmcli', '-g', 'UUID', 'connection', 'show').splitlines()
                          if run('nmcli', '-g', 'connection.interface-name', 'connection', 'show', 'uuid', candidate) == interface]
            if len(candidates) != 1:
                raise ValueError(f'Expected one NetworkManager profile for {interface}')
            uuid = candidates[0]
        current_mtu = run('nmcli', '-g', '802-3-ethernet.mtu', 'connection', 'show', 'uuid', uuid)
        autoconnect = run('nmcli', '-g', 'connection.autoconnect', 'connection', 'show', 'uuid', uuid)
        zone_changed = interface == vlan and run('nmcli', '-g', 'connection.zone', 'connection', 'show', 'uuid', uuid) != 'storage'
        if current_mtu != '9000' or current_link['mtu'] != 9000 or zone_changed or not active or autoconnect != 'yes':
            changed = True
            if apply:
                command = ['nmcli', 'connection', 'modify', 'uuid', uuid, '802-3-ethernet.mtu', '9000', 'connection.autoconnect', 'yes']
                if interface == vlan:
                    command += ['connection.zone', 'storage']
                run(*command)
                if active:
                    run('nmcli', 'device', 'reapply', interface)
                else:
                    run('nmcli', '-w', '15', 'connection', 'up', 'uuid', uuid)
                actual = json.loads(run('ip', '-j', 'link', 'show', interface))[0]['mtu']
                if actual != 9000:
                    raise RuntimeError(f'Storage MTU did not apply on {interface}: {actual}')
    return changed


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('config', type=Path)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    print(json.dumps({'changed': configure(json.loads(args.config.read_text()), args.apply)}))
