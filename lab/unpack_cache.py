#!/usr/bin/env python3
"""Extract only a checksum-pinned lab RPM archive into a new cache directory."""
import argparse
import hashlib
import os
from pathlib import Path
import tarfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archive', type=Path)
    parser.add_argument('sha256')
    args = parser.parse_args()
    digest = hashlib.sha256()
    with args.archive.open('rb') as source:
        for block in iter(lambda: source.read(1024 * 1024), b''):
            digest.update(block)
    if digest.hexdigest() != args.sha256:
        raise SystemExit('RPM archive checksum mismatch')
    root = Path('/var/lib/nr99')
    root.mkdir(exist_ok=True)
    if (root / 'host-rpms-002').exists():
        raise SystemExit('Never overwrite an existing package cache')
    with tarfile.open(args.archive) as archive:
        members = archive.getmembers()
        for member in members:
            path = Path(member.name)
            if path.is_absolute() or '..' in path.parts or not path.parts or path.parts[0] != 'host-rpms-002' or not (member.isdir() or member.isfile()):
                raise SystemExit('Unsafe archive member')
            member.uid = member.gid = 0
            member.uname = member.gname = 'root'
        archive.extractall(root, members=members)
    print('Extracted checksum-verified host package cache')


if __name__ == '__main__':
    if os.geteuid():
        raise SystemExit('Root is required')
    main()
