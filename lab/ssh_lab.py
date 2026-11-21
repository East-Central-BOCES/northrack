"""Operator-side SSH transport restricted to the three private nr99 guests."""
from pathlib import Path
import shlex
import subprocess


def run(node, *arguments, known_hosts, timeout=120, check=True):
    if node not in (1, 2, 3):
        raise ValueError('Only the three nr99 guests are targets')
    if not Path(known_hosts).is_file():
        raise ValueError('Use the previously verified lab known_hosts file')
    result = subprocess.run([
        'ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10',
        '-o', 'StrictHostKeyChecking=yes', '-o', 'UserKnownHostsFile=' + str(known_hosts),
        '-J', 'jarredm@north31.maas', f'jarredm@172.31.231.{10 + node}',
        shlex.join(['sudo', '-n', *arguments]),
    ], text=True, capture_output=True, timeout=timeout)
    if check and result.returncode:
        raise RuntimeError(f'node{node}: {shlex.join(arguments)}: {result.stderr or result.stdout}')
    return result
