#!/usr/bin/env python3
"""Download and verify the official Rocky 9.9 nested-test image on the Engine."""
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess

NAME = 'Rocky-9-GenericCloud-Base-9.9-20261112.0.x86_64.qcow2'
SHA256 = '4f88f4209de50813d8054ab97309e7169a580206a0c4ea04f375d36c4846d50e'


def checksum(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def main():
    if os.geteuid() or socket.gethostname() not in ('engine', 'engine.nr99.test'):
        raise SystemExit('Run as root on the nr99 Engine only')
    target = Path('/var/lib/nr99') / NAME
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        temporary = target.with_suffix('.partial')
        subprocess.run(['curl', '--fail', '--location', '--retry', '2', '--output', str(temporary),
                        'https://dl.rockylinux.org/pub/rocky/9/images/x86_64/' + NAME], check=True)
        if checksum(temporary) != SHA256:
            raise RuntimeError('Downloaded cloud image checksum mismatch')
        temporary.rename(target)
    if checksum(target) != SHA256:
        raise RuntimeError('Existing cloud image checksum mismatch')
    print(json.dumps({'image': str(target), 'sha256': SHA256, 'verified': True}))


if __name__ == '__main__':
    main()
