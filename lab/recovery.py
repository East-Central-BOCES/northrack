#!/usr/bin/env python3
"""Inject and recover bounded faults only in the isolated nr99 replica-3 lab."""
import argparse
import errno
import hashlib
import json
import shlex
import subprocess
import time
import uuid
import xml.etree.ElementTree as ET
from ssh_lab import run


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--known-hosts', required=True)
    parser.add_argument('--stage', choices=['all', 'verify', 'network', 'quorum'], default='all')
    args = parser.parse_args()
    relative = 'acceptance-' + uuid.uuid4().hex
    expected = {}

    def guest(node, *argv, **kwargs):
        return run(node, *argv, known_hosts=args.known_hosts, **kwargs)

    def outer(*argv, check=True):
        return subprocess.run(['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10',
                               'jarredm@north31.maas', shlex.join(['sudo', '-n', 'virsh', *argv])],
                              check=check, text=True, capture_output=True, timeout=40)

    def link(node, state):
        # domif-setlink applies to the live domain by default (no --live option).
        outer('domif-setlink', f'nr99-node{node}', f'52:54:00:99:02:{10 + node:02x}', state)
        print(json.dumps({'storage_link': node, 'state': state}), flush=True)

    def healthy():
        started = time.monotonic()
        deadline = started + 900  # The vendor's default periodic heal interval is 600s.
        last_report = started
        while time.monotonic() < deadline:
            complete = True
            for volume in ('nr99-data', 'nr99-io'):
                # A restarting self-heal daemon can reject the trigger briefly.
                # Acceptance depends on complete, connected XML reports below.
                trigger = guest(1, 'gluster', '--mode=script', 'volume', 'heal', volume, check=False)
                if trigger.returncode:
                    complete = False
                for suffix in ([], ['split-brain']):
                    response = guest(1, 'gluster', '--xml', 'volume', 'heal', volume, 'info', *suffix, check=False)
                    if response.returncode:
                        complete = False
                        continue
                    root = ET.fromstring(response.stdout)
                    if root.findtext('opRet') != '0':
                        complete = False
                    bricks = root.findall('.//healInfo/bricks/brick')
                    if len(bricks) != 3 or any(b.findtext('status') != 'Connected' or b.findtext('numberOfEntries') != '0' for b in bricks):
                        complete = False
            if complete:
                print(json.dumps({'healing_complete': True, 'split_brain_entries': 0,
                                  'elapsed_seconds': round(time.monotonic() - started, 1)}), flush=True)
                return
            if time.monotonic() - last_report >= 30:
                print(json.dumps({'healing_pending': True, 'elapsed_seconds': round(time.monotonic() - started, 1)}), flush=True)
                last_report = time.monotonic()
            time.sleep(5)
        raise RuntimeError('Healing readiness failed: require successful heal triggers, connected bricks, zero backlog and zero split-brain entries')

    def write(name, reject=False):
        code = '''import errno,hashlib,json,os,sys
p=sys.argv[1]; reject=sys.argv[2]=='reject'
data=hashlib.sha256(p.encode()).digest()*32768
try:
    with open(p,'wb') as f:
        for i in range(16): f.write(data)
        f.flush(); os.fsync(f.fileno())
except OSError as e:
    if not reject or e.errno not in (errno.EIO,errno.EROFS,errno.ENOTCONN,errno.ETIMEDOUT): raise
    print(json.dumps({'write_rejected':True,'errno':e.errno})); sys.exit(0)
if reject: raise RuntimeError('Write unexpectedly succeeded without quorum')
print(json.dumps({'sha256':hashlib.sha256(data*16).hexdigest()}))
'''
        result = guest(1, 'timeout', '65', 'python3', '-c', code,
                       '/mnt/nr99-io/' + relative + '/' + name,
                       'reject' if reject else 'write', timeout=80)
        value = json.loads(result.stdout)
        print(json.dumps({'io': name, **value}), flush=True)
        if not reject:
            expected[name] = value['sha256']

    def verify():
        for name, digest in expected.items():
            for node in (1, 2, 3):
                result = guest(node, 'sha256sum', '/storage/sdc1/brick/nr99-io/' + relative + '/' + name)
                if result.stdout.split()[0] != digest:
                    raise RuntimeError(f'Replica checksum mismatch on node{node}: {name}')
            if guest(1, 'sha256sum', '/mnt/nr99-io/' + relative + '/' + name).stdout.split()[0] != digest:
                raise RuntimeError('Client checksum mismatch')
        print(json.dumps({'verified_files': len(expected), 'replicas_per_file': 3}), flush=True)

    for node in (1, 2, 3):
        if guest(node, 'hostname').stdout.strip() != f'node{node}.nr99.test':
            raise RuntimeError('Unexpected fault target')
        if 'running' != outer('domstate', f'nr99-node{node}').stdout.strip():
            raise RuntimeError('All three lab nodes must start running')
    guest(1, 'mkdir', '-p', '/mnt/nr99-io')
    mounted = guest(1, 'findmnt', '-n', '--mountpoint', '/mnt/nr99-io', '-o', 'SOURCE', check=False)
    if mounted.returncode:
        guest(1, 'mount', '-t', 'glusterfs', '-o', 'backup-volfile-servers=172.31.232.12:172.31.232.13',
              '172.31.232.11:/nr99-io', '/mnt/nr99-io')
    elif mounted.stdout.strip() != '172.31.232.11:/nr99-io':
        raise RuntimeError('Unexpected filesystem at the client test mount')
    guest(1, 'mkdir', '/mnt/nr99-io/' + relative)
    try:
        healthy()
        if args.stage == 'verify':
            print(json.dumps({'healing_verification': True, 'passed': True}), flush=True)
            return
        write('baseline')
        verify()
        if args.stage == 'all':
            outer('destroy', 'nr99-node3')
            print(json.dumps({'node3_power_loss': True}), flush=True)
            time.sleep(35)
            write('one-node-unavailable')
            outer('start', 'nr99-node3')
            time.sleep(30)
            healthy()
            verify()
        if args.stage in ('all', 'network'):
            link(3, 'down')
            time.sleep(35)
            write('one-storage-link-unavailable')
            link(3, 'up')
            healthy()
            verify()
        link(2, 'down')
        link(3, 'down')
        time.sleep(35)
        write('quorum-probe', reject=True)
        link(2, 'up')
        link(3, 'up')
        healthy()
        write('after-quorum-restored')
        healthy()
        verify()
        print(json.dumps({'recovery_tests': relative, 'passed': True}), flush=True)
    finally:
        if outer('domstate', 'nr99-node3').stdout.strip() == 'shut off':
            outer('start', 'nr99-node3')
            time.sleep(10)
        for node in (2, 3):
            link(node, 'up')


if __name__ == '__main__':
    main()
