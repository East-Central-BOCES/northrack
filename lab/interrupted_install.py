#!/usr/bin/env python3
"""Interrupt only node2 after data formatting, then prove safe resume."""
import argparse
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('repository', type=Path)
    parser.add_argument('rpm_set')
    parser.add_argument('manifest_hash')
    args = parser.parse_args()
    if os.geteuid() or socket.gethostname() not in ('node2', 'node2.nr99.test'):
        raise SystemExit('Interruption fixture is restricted to the disposable node2 guest')
    if subprocess.check_output(['systemd-detect-virt', '--vm'], text=True).strip() != 'kvm':
        raise SystemExit('A disposable KVM guest is required')
    marker = Path('/var/lib/northrack/provisioned.json')
    if marker.exists():
        raise SystemExit('Use this fixture only for node2 initial provisioning')
    marker.parent.mkdir(exist_ok=True)
    command = ['bash', str(args.repository / 'provision.sh'), '--config', str(args.repository / 'examples/nr99-node2.json'),
               '--rpm-set', args.rpm_set, '--manifest-sha256', args.manifest_hash, '--format-data-disks']
    interrupted_log = marker.parent / 'node2-interrupted-install.log'
    deadline = time.monotonic() + 1200
    with interrupted_log.open('w') as output:
        process = subprocess.Popen(command, stdout=output, stderr=subprocess.STDOUT, start_new_session=True)
        while process.poll() is None:
            if 'TASK [Mount XFS data filesystems by UUID]' in interrupted_log.read_text(errors='replace'):
                assert process.pid != os.getpgrp()
                os.killpg(process.pid, signal.SIGTERM)
                process.wait(timeout=15)
                break
            if time.monotonic() > deadline:
                os.killpg(process.pid, signal.SIGTERM)
                raise RuntimeError('Timed out before the controlled interruption point')
            time.sleep(0.1)
        else:
            raise RuntimeError('Provisioning exited before the interruption point; inspect its log')
    assert process.returncode != 0 and not marker.exists()

    def uuids():
        return [subprocess.check_output(['blkid', '-s', 'UUID', '-o', 'value', device], text=True).strip()
                for device in ('/dev/disk/by-id/virtio-nr99-node2-data01-part1', '/dev/disk/by-id/virtio-nr99-node2-data02-part1')]

    before = uuids()
    with (marker.parent / 'node2-resumed-install.log').open('w') as output:
        subprocess.run(command, check=True, stdout=output, stderr=subprocess.STDOUT)
    assert marker.exists() and uuids() == before
    print(json.dumps({'interrupted_after_format': True, 'no_success_marker_after_interruption': True,
                      'resume_completed': True, 'filesystem_uuids_preserved': True, 'passed': True}, indent=2))


if __name__ == '__main__':
    main()
