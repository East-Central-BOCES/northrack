#!/usr/bin/env python3
"""Bounded checksum and fsync workload inside the disposable nested VM."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

ROOT = Path.home() / 'nr99-acceptance'


def digest(path):
    result = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            result.update(block)
    return result.hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['prepare', 'run', 'verify', 'stop'])
    args = parser.parse_args()
    if Path('/etc/hostname').read_text().strip() != 'nr99-workload':
        raise SystemExit('This workload is restricted to nr99-workload')
    if args.action == 'run':
        with (ROOT / 'journal').open('a', buffering=1) as journal:
            for number in range(1800):
                journal.write(json.dumps({'sequence': number, 'time': time.time()}) + '\n')
                journal.flush()
                os.fsync(journal.fileno())
                time.sleep(1)
        return
    if args.action == 'prepare':
        ROOT.mkdir(mode=0o700)
        with (ROOT / 'payload').open('wb') as stream:
            for block in range(16):
                stream.write(os.urandom(1024 * 1024))
            stream.flush()
            os.fsync(stream.fileno())
        with (ROOT / 'writer.log').open('w') as output:
            process = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), 'run'],
                                       stdin=subprocess.DEVNULL, stdout=output, stderr=output, start_new_session=True)
        state = {'sha256': digest(ROOT / 'payload'), 'pid': process.pid,
                 'boot_id': Path('/proc/sys/kernel/random/boot_id').read_text().strip()}
        (ROOT / 'state.json').write_text(json.dumps(state) + '\n')
        print(json.dumps({'prepared': state}), flush=True)
        return
    state = json.loads((ROOT / 'state.json').read_text())
    if args.action == 'stop':
        cmdline = Path(f'/proc/{state["pid"]}/cmdline')
        if cmdline.exists() and b'guest_io.py' in cmdline.read_bytes():
            os.kill(state['pid'], signal.SIGTERM)
        return
    entries = [json.loads(line) for line in (ROOT / 'journal').read_text().splitlines()]
    checks = {
        'same_boot': Path('/proc/sys/kernel/random/boot_id').read_text().strip() == state['boot_id'],
        'checksum_unchanged': digest(ROOT / 'payload') == state['sha256'],
        'journal_contiguous': bool(entries) and [entry['sequence'] for entry in entries] == list(range(len(entries))),
        'writer_progressing': bool(entries) and time.time() - entries[-1]['time'] < 10,
    }
    print(json.dumps({'checks': checks, 'journal_entries': len(entries), 'sha256': state['sha256'],
                      'boot_id': state['boot_id'], 'passed': all(checks.values())}), flush=True)
    if not all(checks.values()):
        raise SystemExit(1)


if __name__ == '__main__':
    main()
