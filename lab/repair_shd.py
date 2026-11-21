#!/usr/bin/env python3
"""Explicitly recover only a stale self-heal daemon in the disposable nr99 lab."""
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import time


def main():
    if os.geteuid() or socket.gethostname() not in ('node1.nr99.test', 'node2.nr99.test', 'node3.nr99.test'):
        raise SystemExit('Restricted to root on the three private nr99 lab guests')
    def gluster(*args):
        return subprocess.check_output(['gluster', '--mode=script', *args], text=True)
    if set(gluster('volume', 'list').splitlines()) != {'nr99-data', 'nr99-io'}:
        raise RuntimeError('Unexpected volume pool')
    def bricks():
        result = set()
        for path in Path('/proc').glob('[0-9]*/cmdline'):
            try:
                if b'--process-name\x00brick\x00' in path.read_bytes():
                    result.add(path.parent.name)
            except FileNotFoundError:
                pass  # An unrelated short-lived process exited during enumeration.
        return result
    before = bricks()
    pids = {int(Path(f'/var/run/gluster/shd/{name}/{name}-shd.pid').read_text())
            for name in ('nr99-data', 'nr99-io')}
    for pid in pids:
        if b'--process-name\x00glustershd\x00' not in Path(f'/proc/{pid}/cmdline').read_bytes():
            raise RuntimeError('PID file does not identify a self-heal daemon')
    for pid in pids:
        os.kill(pid, signal.SIGTERM)
    for attempt in range(50):
        if not any(Path(f'/proc/{pid}').exists() for pid in pids):
            break
        time.sleep(0.2)
    else:
        raise RuntimeError('Self-heal daemon did not exit; no further signals sent')
    for name in ('nr99-data', 'nr99-io'):
        gluster('volume', 'start', name, 'force')
    if bricks() != before:
        raise RuntimeError('Unexpected brick process change during daemon recovery')
    print(json.dumps({'host': socket.gethostname(), 'recovered_shd_pids': sorted(pids),
                      'brick_processes_preserved': True}))


if __name__ == '__main__':
    main()
