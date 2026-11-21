#!/usr/bin/env python3
"""Preflight all declared data disks before any format operation."""
import argparse
import base64
import ipaddress
import json
from pathlib import Path
import stat
import subprocess


def run(*args, **kwargs):
    return subprocess.run(args, check=True, text=True, capture_output=True, **kwargs)


def flatten(tree):
    yield tree
    for child in tree.get('children', []):
        yield from flatten(child)


def inspect(config, allow_format):
    if config.get('role') not in ('host', 'engine'):
        raise ValueError('Configuration must select host or engine role')
    key = config.get('engine_public_key', '')
    if key:
        if '\n' in key:
            raise ValueError('One Engine public key is required')
        algorithm, encoded, *_ = key.split()
        if algorithm not in ('ssh-rsa', 'ssh-ed25519', 'ecdsa-sha2-nistp256', 'ecdsa-sha2-nistp384', 'ecdsa-sha2-nistp521'):
            raise ValueError('Unsupported Engine public key')
        base64.b64decode(encoded, validate=True)
        ipaddress.ip_address(config['engine_address'])
    disks = config.get('disks', [])
    if config['role'] == 'engine' and disks:
        raise ValueError('The independent Engine must not format storage bricks')
    devices, mounts, facts = set(), set(), []
    for disk in disks:
        identifier = Path(disk['by_id'])
        mount = Path(disk['mount'])
        if identifier.parent != Path('/dev/disk/by-id') or '..' in identifier.parts:
            raise ValueError('Select a stable disk identity under /dev/disk/by-id')
        if mount.parent != Path('/storage') or '..' in mount.parts or mount.name in ('', '.', '..'):
            raise ValueError('Data mounts must be direct children of /storage')
        if mount.is_symlink() or mount.resolve(strict=False) != mount:
            raise ValueError('Data mounts must not traverse symlinks')
        device = identifier.resolve(strict=True)
        if not stat.S_ISBLK(device.stat().st_mode) or str(device) in devices or str(mount) in mounts:
            raise ValueError('Disk identities and mount targets must be distinct block devices')
        devices.add(str(device))
        mounts.add(str(mount))
        tree = json.loads(run('lsblk', '-J', '-p', '-o', 'NAME,TYPE,FSTYPE,UUID,MOUNTPOINTS', str(device)).stdout)['blockdevices'][0]
        if tree['type'] != 'disk':
            raise ValueError('A whole data disk is required')
        rows = list(flatten(tree))
        for row in rows:
            for target in row.get('mountpoints') or []:
                if target and target != str(mount):
                    raise ValueError(f'Refusing a disk used by another mount: {target}')
        partitions = tree.get('children', [])
        ready = len(partitions) == 1 and partitions[0]['type'] == 'part' and partitions[0].get('fstype') == 'xfs'
        existing = subprocess.run(['findmnt', '-J', '--mountpoint', str(mount), '-o', 'UUID,FSTYPE'], text=True, capture_output=True)
        if existing.returncode == 0:
            mounted_uuid = json.loads(existing.stdout)['filesystems'][0].get('uuid')
            if not mounted_uuid or mounted_uuid not in {row.get('uuid') for row in rows if row.get('uuid')}:
                raise ValueError(f'Mount target is occupied by another filesystem: {mount}')
        elif existing.returncode != 1:
            raise RuntimeError(f'Cannot inspect mount target: {mount}')
        if not ready:
            if not allow_format or not config.get('allow_format', False):
                raise ValueError(f'Disk needs formatting but destructive mode is not enabled: {identifier}')
            is_lab = identifier.name.startswith('virtio-nr99-') and run('systemd-detect-virt', '--vm').stdout.strip() == 'kvm'
            if not is_lab:
                record = Path(config.get('backup_record') or '/nonexistent-northrack-backup-record')
                backup = json.loads(record.read_text())
                if backup.get('verified') is not True or not backup.get('archive_sha256') or identifier.name not in backup.get('disk_ids', []):
                    raise ValueError('Physical disk formatting requires matching verified-backup evidence')
        facts.append({'by_id': str(identifier), 'device': str(device), 'mount': str(mount), 'ready': ready,
                      'partition': partitions[0]['name'] if ready else str(device) + ('p1' if device.name[-1].isdigit() else '1'),
                      'uuid': partitions[0].get('uuid') if ready else None})
    return facts


def apply(facts):
    changed = False
    for disk in facts:
        if disk['ready']:
            continue
        mounted = run('findmnt', '-rn', '-o', 'SOURCE', '--target', disk['mount']).stdout.strip() if Path(disk['mount']).exists() else ''
        if mounted == disk['device'] or mounted.startswith(disk['device']):
            run('umount', disk['mount'])
        run('sfdisk', '--wipe', 'always', disk['device'], input='label: gpt\nstart=2048, type=L\n')
        run('blockdev', '--rereadpt', disk['device'])
        run('udevadm', 'settle')
        run('mkfs.xfs', '-f', disk['partition'])
        changed = True
    return changed


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('config', type=Path)
    parser.add_argument('--format-data-disks', action='store_true')
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    config = json.loads(args.config.read_text())
    facts = inspect(config, args.format_data_disks)
    changed = apply(facts) if args.apply else False
    if changed:
        facts = inspect(config, False)
    print(json.dumps({'changed': changed, 'disks': facts}))
