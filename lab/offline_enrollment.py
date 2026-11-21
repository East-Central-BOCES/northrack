#!/usr/bin/env python3
"""Temporarily constrain lab host deployment to its accepted local package set."""
import argparse
import json
import os
from pathlib import Path
import socket
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['enter', 'restore'])
    args = parser.parse_args()
    if os.geteuid() or socket.gethostname().split('.')[0] not in ('node1', 'node2', 'node3'):
        raise SystemExit('This helper is restricted to the nr99 lab hosts')
    state = json.loads(Path('/var/lib/northrack/provisioned.json').read_text())
    if state['role'] != 'host':
        raise SystemExit('Host preparation is incomplete')
    original = Path('/etc/yum.repos.d')
    saved = Path('/var/lib/northrack/repositories-before-enrollment')
    if args.action == 'enter':
        if saved.exists():
            raise SystemExit('Enrollment repository isolation is already active')
        original.rename(saved)
        original.mkdir(mode=0o755)
        (original / 'nr99-accepted.repo').write_text(
            '[northrack-accepted]\nname=Accepted lab host package set\n'
            'baseurl=file:///var/lib/nr99/host-rpms-002/rpms\nenabled=1\ngpgcheck=1\nskip_if_unavailable=0\n'
        )
    else:
        if not saved.exists():
            raise SystemExit('No isolated enrollment repository state to restore')
        original.rename(saved.parent / ('repositories-used-during-enrollment-' + str(time.time_ns())))
        saved.rename(original)
    print(json.dumps({'repository_policy': args.action, 'host': socket.gethostname()}))


if __name__ == '__main__':
    main()
