#!/usr/bin/env python3
"""Check an accepted RPM set against the independently pinned manifest hash."""
import argparse
import hashlib
import json
from pathlib import Path
import re


def sha256(path):
    digest = hashlib.sha256()
    with path.open('rb') as source:
        for block in iter(lambda: source.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def validate(root, expected, role):
    if not re.fullmatch(r'[a-f0-9]{64}', expected):
        raise ValueError('Expected a pinned SHA-256 manifest hash')
    if sha256(root / 'manifest.json') != expected or (root / 'ACCEPTED').read_text().strip() != expected:
        raise ValueError('Package set acceptance/hash mismatch')
    manifest = json.loads((root / 'manifest.json').read_text())
    if manifest['role'] != role or manifest['signature_failures'] or not manifest['packages'] or not manifest.get('keys'):
        raise ValueError('Package set is not accepted for this role')
    for section, directory in (('packages', 'rpms'), ('keys', 'keys')):
        names = set()
        for entry in manifest[section]:
            name = entry['filename']
            if not name or Path(name).name != name or name in ('.', '..') or name in names:
                raise ValueError('Unsafe or duplicate manifest filename')
            names.add(name)
            path = root / directory / name
            if path.is_symlink() or not path.is_file() or sha256(path) != entry['sha256']:
                raise ValueError(f'Missing or altered accepted file: {name}')
        if section == 'packages' and {p.name for p in (root / directory).glob('*.rpm')} != names:
            raise ValueError('Unreviewed RPMs exist in the accepted repository')
    return manifest


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    parser.add_argument('expected_hash')
    parser.add_argument('role', choices=['host', 'engine'])
    args = parser.parse_args()
    result = validate(args.directory, args.expected_hash, args.role)
    print(f"Validated {len(result['packages'])} accepted {args.role} RPMs")
