#!/usr/bin/env python3
"""Run bounded failure fixtures only on the disposable nr99 node1 guest."""
import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile


def run(*args):
    return subprocess.check_output(args, text=True).strip()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('repository', type=Path)
    parser.add_argument('rpm_set')
    parser.add_argument('manifest_hash')
    args = parser.parse_args()
    if os.geteuid() or socket.gethostname() not in ('node1', 'node1.nr99.test') or run('systemd-detect-virt', '--vm') != 'kvm':
        raise SystemExit('Fixtures are restricted to the disposable nr99 node1 guest')
    repo = args.repository.resolve()
    config = json.loads((repo / 'examples/nr99-node1.json').read_text())
    assert [d['mount'] for d in config['disks']] == ['/storage/sdb1', '/storage/sdc1']
    assert all(d['by_id'].startswith('/dev/disk/by-id/virtio-nr99-node1-') for d in config['disks'])
    marker = Path('/var/lib/northrack/provisioned.json')
    initial_marker = marker.read_bytes()
    initial_uuids = [run('findmnt', '-n', '--mountpoint', d['mount'], '-o', 'UUID') for d in config['disks']]
    logs = Path('/var/lib/northrack/negative-test-logs')
    logs.mkdir(exist_ok=True)
    cases = []

    def reject(name, changed=None, rpm_set=None, manifest=None):
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json') as test_config:
            json.dump(changed or config, test_config)
            test_config.flush()
            result = subprocess.run(['bash', str(repo / 'provision.sh'), '--config', test_config.name,
                                     '--rpm-set', rpm_set or args.rpm_set,
                                     '--manifest-sha256', manifest or args.manifest_hash], capture_output=True, text=True)
        (logs / (name + '.log')).write_text(result.stdout + result.stderr)
        passed = result.returncode != 0 and marker.read_bytes() == initial_marker
        cases.append({'name': name, 'exit_code': result.returncode, 'passed': passed})
        if not passed:
            raise AssertionError(f'Failure fixture did not fail safely: {name}')

    reject('bad-manifest-hash', manifest='0' * 64)
    reject('unavailable-package-cache', rpm_set='/var/lib/nr99/intentionally-unavailable-repository')
    wrong = copy.deepcopy(config)
    wrong['disks'][0]['by_id'] = '/dev/disk/by-id/virtio-nr99-node1-os'
    reject('os-disk-is-not-a-brick', changed=wrong)
    wrong = copy.deepcopy(config)
    wrong['disks'][0]['mount'] = '/storage/missing-expected-mount'
    reject('wrong-storage-mount', changed=wrong)
    wrong = copy.deepcopy(config)
    wrong['storage_interface'] = 'eth0'
    reject('management-interface-is-not-storage', changed=wrong)
    run('umount', '/storage/sdb1')
    try:
        run('mount', '-t', 'tmpfs', '-o', 'size=1m', 'nr99-failure-fixture', '/storage/sdb1')
        try:
            reject('foreign-filesystem-at-brick-mount')
        finally:
            run('umount', '/storage/sdb1')
    finally:
        run('mount', '/storage/sdb1')
    final_uuids = [run('findmnt', '-n', '--mountpoint', d['mount'], '-o', 'UUID') for d in config['disks']]
    assert final_uuids == initial_uuids
    assert marker.read_bytes() == initial_marker
    print(json.dumps({'cases': cases, 'data_uuids_unchanged': True,
                      'last_success_sha256': hashlib.sha256(initial_marker).hexdigest(), 'passed': True}, indent=2))


if __name__ == '__main__':
    main()
