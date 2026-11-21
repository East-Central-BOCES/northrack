#!/usr/bin/env python3
"""Capture only active self-heal client connection flags from a lab statedump."""
import json
import os
from pathlib import Path
import re
import signal
import socket
import time


def main():
    if os.geteuid() or socket.gethostname() not in (
            'node1.nr99.test', 'node2.nr99.test', 'node3.nr99.test'):
        raise SystemExit('Restricted to root on an nr99 lab replica')
    pids = {int(Path(f'/var/run/gluster/shd/{volume}/{volume}-shd.pid').read_text())
            for volume in ('nr99-data', 'nr99-io')}
    connections = {}
    for pid in sorted(pids):
        connections.update(capture(pid))
    if len(connections) != 6:
        raise RuntimeError('Incomplete client set across the two volume daemons')
    print(json.dumps({'host': socket.gethostname(), 'shd_pids': sorted(pids),
                      'active_clients': connections}))


def capture(pid):
    if b'--process-name\x00glustershd\x00' not in Path(f'/proc/{pid}/cmdline').read_bytes():
        raise RuntimeError('Unexpected self-heal process identity')
    root = Path('/var/run/gluster')
    pattern = f'glusterdump.{pid}.dump.*'
    before = set(root.glob(pattern))
    os.kill(pid, signal.SIGUSR1)
    for attempt in range(50):
        candidates = set(root.glob(pattern)) - before
        if candidates:
            latest = max(candidates, key=lambda path: path.stat().st_mtime_ns)
            content = latest.read_text().split('[oldgraph[0]]')[0]
            connections = re.findall(r'\[xlator\.protocol\.client\.(nr99-[^.]+)\.priv\]\nconnected=(\d)', content)
            if len(connections) in (3, 6):
                return {name: bool(int(value)) for name, value in connections}
        time.sleep(0.1)
    raise RuntimeError('Did not receive a complete active-client statedump')


if __name__ == '__main__':
    main()
