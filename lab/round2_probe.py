#!/usr/bin/env python3
"""Small, bounded canary operations on the private nr99 guests only."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import subprocess

FILES = ('record.bin', 'probe-existing.bin', 'probe-fresh.bin')


def valid_case(value):
    if not re.fullmatch('[a-f0-9]{32}', value):
        raise ValueError('Case ID must be a complete UUID hex string')
    return value


def block(case, generation):
    return hashlib.sha256((case + ':' + generation).encode()).digest() * 32768


def expected(case, generation):
    return hashlib.sha256(block(case, generation) * 16).hexdigest()


def digest(path):
    result = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            result.update(chunk)
    return result.hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['mount', 'unmount', 'legacy-unmount', 'write', 'read', 'raw', 'tree'])
    parser.add_argument('--case', required=True, type=valid_case)
    parser.add_argument('--client', choices=['existing', 'fresh'], default='existing')
    parser.add_argument('--file', choices=FILES, default='record.bin')
    parser.add_argument('--generation', choices=['g0', 'g1', 'existing', 'fresh'], default='g0')
    args = parser.parse_args()
    hostname = socket.gethostname()
    if os.geteuid() or hostname not in [f'node{n}.nr99.test' for n in (1, 2, 3)]:
        raise SystemExit('Run as root on an nr99 replica only')
    if args.action in ('mount', 'unmount', 'legacy-unmount', 'write', 'read') and hostname != 'node1.nr99.test':
        raise SystemExit('Client operations are restricted to node1')
    mount = Path('/mnt') / ('nr99-r2-' + args.client)
    if args.action == 'legacy-unmount':
        mount = Path('/mnt/nr99-io')
    if mount.is_symlink() or mount.resolve(strict=False) != mount:
        raise RuntimeError('Canary mount must not traverse symlinks')
    directory = 'round2-' + args.case
    if args.action in ('mount', 'unmount', 'legacy-unmount'):
        found = subprocess.run(['findmnt', '-n', '--mountpoint', str(mount), '-o', 'SOURCE'],
                               capture_output=True, text=True)
        if found.returncode not in (0, 1):
            raise RuntimeError('Cannot inspect canary mount')
        if found.returncode == 0 and found.stdout.strip() != '172.31.232.11:/nr99-io':
            raise RuntimeError('Unexpected filesystem at canary mount')
        if args.action in ('unmount', 'legacy-unmount') and found.returncode == 0:
            subprocess.run(['umount', str(mount)], check=True, timeout=20)
        elif args.action == 'mount' and found.returncode == 1:
            mount.mkdir(exist_ok=True)
            if mount.is_symlink():
                raise RuntimeError('Canary mount is a symlink')
            subprocess.run(['mount', '-t', 'glusterfs', '-o',
                            'backup-volfile-servers=172.31.232.12:172.31.232.13',
                            '172.31.232.11:/nr99-io', str(mount)], check=True, timeout=20)
        print(json.dumps({'status': 'OK', 'action': args.action}))
        return
    if args.action == 'tree':
        result = {}
        for disk, volume in [('sdb1', 'nr99-data'), ('sdc1', 'nr99-io')]:
            root = Path('/storage') / disk / 'brick' / volume
            if not root.is_dir() or root.is_symlink():
                raise RuntimeError('Missing or unexpected brick root')
            count = len(result)
            def fail_walk(error):
                raise error
            for parent, dirs, files in os.walk(root, followlinks=False, onerror=fail_walk):
                dirs[:] = [d for d in dirs if d != '.glusterfs']
                for name in files:
                    path = Path(parent) / name
                    relative = volume + '/' + str(path.relative_to(root))
                    result[relative] = {'link': os.readlink(path)} if path.is_symlink() else {'sha256': digest(path), 'size': path.stat().st_size}
            if len(result) == count:
                raise RuntimeError('Unexpected empty volume inventory')
        print(json.dumps(result, sort_keys=True))
        return
    if args.action == 'raw':
        path = Path('/storage/sdc1/brick/nr99-io') / directory / args.file
    else:
        found = subprocess.check_output(['findmnt', '-n', '--mountpoint', str(mount), '-o', 'SOURCE'], text=True)
        if found.strip() != '172.31.232.11:/nr99-io':
            raise RuntimeError('Refusing canary I/O outside the Gluster mount')
        path = mount / directory / args.file
    try:
        if args.action == 'write':
            path.parent.mkdir(exist_ok=True)
            if path.parent.is_symlink() or path.is_symlink() or (path.exists() and not path.is_file()):
                raise RuntimeError('Unexpected canary file identity')
            with path.open('wb') as stream:
                for _ in range(16):
                    stream.write(block(args.case, args.generation))
                stream.flush()
                os.fsync(stream.fileno())
            result = {'status': 'ACK', 'sha256': expected(args.case, args.generation)}
        else:
            result = {'status': 'READ', 'sha256': digest(path)}
    except OSError as error:
        result = {'status': 'ERROR', 'errno': error.errno}
    print(json.dumps(result))


if __name__ == '__main__':
    main()
