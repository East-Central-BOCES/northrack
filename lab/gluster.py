#!/usr/bin/env python3
"""Create and verify only the two replica-3 Gluster volumes in the nr99 lab."""
import argparse
import json
import time
import xml.etree.ElementTree as ET
from ssh_lab import run

VOLUMES = {'nr99-data': '/storage/sdb1', 'nr99-io': '/storage/sdc1'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--known-hosts', required=True)
    parser.add_argument('--clear-incomplete-staging', action='store_true',
                        help='Clear only empty, unregistered lab brick staging metadata')
    args = parser.parse_args()

    def command(node, *argv, **kwargs):
        return run(node, *argv, known_hosts=args.known_hosts, **kwargs)

    def gluster(*argv):
        return command(1, 'gluster', '--mode=script', *argv).stdout

    for node in (1, 2, 3):
        if command(node, 'hostname').stdout.strip() != f'node{node}.nr99.test':
            raise RuntimeError('Unexpected target hostname')
        for mount in VOLUMES.values():
            if command(node, 'findmnt', '-n', '--mountpoint', mount, '-o', 'FSTYPE').stdout.strip() != 'xfs':
                raise RuntimeError('Brick filesystem is not mounted XFS')
        if command(node, 'firewall-cmd', '--zone=public', '--query-service=glusterfs', check=False).returncode != 1:
            raise RuntimeError('Gluster must be closed on public before volume creation')
        for name, mount in VOLUMES.items():
            command(node, 'mkdir', '-p', mount + '/brick/' + name)
            command(node, 'restorecon', '-RF', mount + '/brick/' + name)
    # A new peer advertises its system hostname. Explicitly probe every storage
    # address in both directions so no peer depends on management-side DNS.
    for node in (1, 2, 3):
        for peer in (1, 2, 3):
            if node != peer:
                command(node, 'gluster', '--mode=script', 'peer', 'probe', f'172.31.232.{10 + peer}')
    for attempt in range(30):
        ready = True
        for node in (1, 2, 3):
            peers = ET.fromstring(command(node, 'gluster', '--xml', 'peer', 'status').stdout).findall('.//peerStatus/peer')
            expected = {f'172.31.232.{10 + peer}' for peer in (1, 2, 3) if peer != node}
            if len(peers) != 2 or not all(p.findtext('connected') == '1' and p.findtext('state') == '3' for p in peers):
                ready = False
                continue
            if {p.findtext('hostname') for p in peers} != expected:
                raise RuntimeError('Unexpected Gluster peer address; management identities must not carry storage traffic')
        if ready:
            break
        time.sleep(2)
    else:
        raise RuntimeError('Storage-network peers did not connect')
    gluster('volume', 'set', 'all', 'cluster.server-quorum-ratio', '51%')
    existing = set(gluster('volume', 'list').splitlines())
    if existing - VOLUMES.keys():
        raise RuntimeError('Unexpected existing lab volume')
    for name, mount in VOLUMES.items():
        bricks = [f'172.31.232.{10 + node}:{mount}/brick/{name}' for node in (1, 2, 3)]
        if name not in existing:
            if args.clear_incomplete_staging:
                for node in (1, 2, 3):
                    if name in command(node, 'gluster', '--mode=script', 'volume', 'list').stdout.splitlines():
                        raise RuntimeError('A registered volume must never have its brick metadata cleared')
                cleanup = (
                    'import errno,os,pathlib,sys; p=pathlib.Path(sys.argv[1]); '
                    'assert p.is_dir() and not p.is_symlink(); '
                    'assert {x.name for x in p.iterdir()} <= {".glusterfs"}; '
                    'g=p/".glusterfs"; '
                    'assert not g.exists() or (g.is_dir() and not g.is_symlink() and not any(g.iterdir())); '
                    'g.rmdir() if g.exists() else None; '
                    'os.removexattr(p,"trusted.glusterfs.volume-id") '
                    'if "trusted.glusterfs.volume-id" in os.listxattr(p) else None'
                )
                for node in (1, 2, 3):
                    command(node, 'python3', '-c', cleanup, mount + '/brick/' + name)
            gluster('volume', 'create', name, 'replica', '3', 'transport', 'tcp', *bricks)
        volume = ET.fromstring(command(1, 'gluster', '--xml', 'volume', 'info', name).stdout).find('.//volInfo/volumes/volume')
        if volume is None or volume.findtext('replicaCount') != '3' or {b.findtext('name') for b in volume.findall('bricks/brick')} != set(bricks):
            raise RuntimeError('Existing volume does not match the approved replica-3 topology')
        gluster('volume', 'set', name, 'group', 'virt')
        for key, value in {
            'performance.strict-o-direct': 'on', 'network.remote-dio': 'off',
            'storage.owner-uid': '36', 'storage.owner-gid': '36', 'network.ping-timeout': '30',
            'cluster.quorum-type': 'auto', 'cluster.server-quorum-type': 'server',
        }.items():
            gluster('volume', 'set', name, key, value)
        if volume.findtext('status') != '1':
            gluster('volume', 'start', name)
        print(gluster('volume', 'status', name), flush=True)
    print(json.dumps({'volumes': list(VOLUMES), 'replicas': 3, 'storage_network_only': True, 'passed': True}))


if __name__ == '__main__':
    main()
