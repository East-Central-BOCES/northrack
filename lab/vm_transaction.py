#!/usr/bin/env python3
"""Append/fsync deterministic transactions inside nr99-workload only."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import time
from round2_probe import valid_case


def record(case, trial, sequence):
    payload = hashlib.sha256(f'{case}:{trial}:{sequence}'.encode()).hexdigest() * 64
    return (json.dumps({'sequence': sequence, 'payload': payload}, sort_keys=True) + '\n').encode()


def checksum(data):
    return hashlib.sha256(data).hexdigest()


def verify_file(path, case, trial):
    hashes = []
    trailing = False
    with path.open('rb') as stream:
        for sequence, line in enumerate(stream):
            if not line.endswith(b'\n'):
                trailing = True  # An unacknowledged partial final write may exist.
                break
            if line != record(case, trial, sequence):
                raise RuntimeError('Transaction content/order mismatch at ' + str(sequence))
            hashes.append(checksum(line))
    return {'hashes': hashes, 'complete_records': len(hashes), 'partial_tail': trailing}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['write', 'verify', 'clock'])
    parser.add_argument('--case', required=True, type=valid_case)
    parser.add_argument('--trial', required=True, type=valid_case)
    args = parser.parse_args()
    if Path('/etc/hostname').read_text().strip() != 'nr99-workload' or os.getuid() == 0:
        raise SystemExit('Restricted to the named unprivileged nr99 workload user')
    if Path.home() != Path('/home/rocky'):
        raise SystemExit('Expected rocky home directory')
    if args.action == 'clock':
        print(json.dumps({'time': time.time()}), flush=True)
        return
    directory = Path.home() / 'nr99-continuous' / (args.case + '-' + args.trial)
    path = directory / 'transactions.jsonl'
    if args.action == 'verify':
        result = verify_file(path, args.case, args.trial)
        original = Path.home() / 'nr99-acceptance'
        saved = json.loads((original / 'state.json').read_text())
        result['original_payload_sha256'] = checksum((original / 'payload').read_bytes())
        result['original_payload_matches_state'] = result['original_payload_sha256'] == saved['sha256']
        entries = [json.loads(line) for line in (original / 'journal').read_text().splitlines()]
        result['original_journal_entries'] = len(entries)
        result['original_journal_contiguous'] = [e['sequence'] for e in entries] == list(range(1800))
        print(json.dumps(result), flush=True)
        return
    directory.mkdir(parents=True, mode=0o700, exist_ok=False)
    with path.open('xb') as stream:
        for parent in (directory, directory.parent, directory.parent.parent):
            fd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        deadline = time.monotonic() + 150
        sequence = 0
        while time.monotonic() < deadline:
            data = record(args.case, args.trial, sequence)
            started = time.time()
            try:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
                ended = time.time()
                # Verify the application's immediate read after its durable write.
                with path.open('rb') as reader:
                    reader.seek(stream.tell() - len(data))
                    if reader.read(len(data)) != data:
                        print(json.dumps({'status': 'CRITICAL', 'sequence': sequence,
                                          'error': 'Read-after-fsync returned unexpected content'}), flush=True)
                        raise RuntimeError('Read-after-fsync returned unexpected content')
            except OSError as error:
                print(json.dumps({'status': 'ERROR', 'errno': error.errno, 'sequence': sequence}), flush=True)
                raise SystemExit(2)
            print(json.dumps({'status': 'ACK', 'sequence': sequence, 'sha256': checksum(data),
                              'fsync_started': started, 'fsync_completed': ended}), flush=True)
            sequence += 1
            time.sleep(0.1)
    print(json.dumps({'status': 'DONE', 'records': sequence}), flush=True)


if __name__ == '__main__':
    main()
