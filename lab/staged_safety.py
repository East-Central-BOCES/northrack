#!/usr/bin/env python3
"""Operator-paced, detached nr99 safety tests; run only as root on north31.

Draft until the documented bootstrap and first-stage review have passed.
No stage starts another stage. Network faults have independent server timers.
"""
import argparse
import contextlib
import errno
import fcntl
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time
import uuid
import xml.etree.ElementTree as ET
import shlex
from round2_probe import expected, valid_case

ROOT = Path('/var/lib/nr99/round2')
CODE = ROOT / 'code'
STEPS = ('checkpoint', 'checkpoint-finish', 'baseline', 'scenario', 'automatic-verify', 'targeted-recover', 'recover', 'verify', 'resume')
LIMITS = dict(zip(STEPS, (900, 900, 300, 900, 1200, 1500, 1500, 1200, 600)))
REJECTED = (errno.EIO, errno.EROFS, errno.ENOTCONN, errno.ETIMEDOUT)


def attempt_name(step, attempt):
    if not 1 <= attempt <= 9:
        raise ValueError('Attempt must be between 1 and 9')
    return step if attempt == 1 else step + '-attempt' + str(attempt)


class Inconclusive(RuntimeError):
    pass


def atomic(path, value):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, indent=2) + '\n')
    temporary.replace(path)


def arm_then_down(call, unit, watchdog, domain, mac):
    call(['systemd-run', '--unit=' + unit, '--on-active=360s',
          '--timer-property=AccuracySec=1s', *watchdog])
    call(['systemctl', 'is-active', '--quiet', unit + '.timer'])
    call(['virsh', 'domif-setlink', domain, mac, 'down'])


def classify(read, write, latest, copies):
    if read.get('status') == 'READ' and read.get('sha256') != latest:
        raise RuntimeError('CRITICAL: client returned stale or unexpected content')
    if write.get('status') == 'ACK' and copies < 2:
        raise RuntimeError('CRITICAL: acknowledged write lacks two matching available replicas')
    for result in (read, write):
        if result.get('status') == 'TIMEOUT':
            raise Inconclusive('A timed-out operation does not prove safe rejection')
        if result.get('status') == 'ERROR' and result.get('errno') not in REJECTED:
            raise Inconclusive('Unexpected I/O error: ' + str(result))
    if read.get('status') not in ('READ', 'ERROR') or write.get('status') not in ('ACK', 'ERROR'):
        raise Inconclusive('Unexpected probe result')


class Case:
    def __init__(self, case):
        self.id = valid_case(case)
        self.path = ROOT / 'cases' / case
        self.config = json.loads((self.path / 'config.json').read_text())
        self.oracle = json.loads((self.path / 'oracle.json').read_text()) if (self.path / 'oracle.json').exists() else {}
        self.observations = {}

    def call(self, argv, timeout=40, check=True):
        result = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
        print(json.dumps({'command': argv, 'rc': result.returncode,
                          'stdout': result.stdout, 'stderr': result.stderr}), flush=True)
        if check and result.returncode:
            raise RuntimeError(shlex.join(argv[:3]) + ': ' + result.stderr[-800:])
        return result

    def guest(self, node, argv, timeout=40, check=True):
        address = '172.31.231.' + str(10 + node)
        return self.call(['ssh', '-T', '-o', 'BatchMode=yes', '-o', 'IdentitiesOnly=yes',
                          '-o', 'StrictHostKeyChecking=yes', '-o', 'ConnectTimeout=5',
                          '-o', 'UserKnownHostsFile=' + str(ROOT / 'known_hosts'),
                          '-i', str(ROOT / 'id_ed25519'), 'jarredm@' + address,
                          shlex.join(['sudo', '-n', *argv])], timeout, check)

    def engine(self, action):
        return json.loads(self.guest(0, ['python3', str(CODE / 'round2_engine.py'), action], timeout=590).stdout)

    def quiescent(self):
        state = self.engine('status')
        if state['vm'] != 'down' or state['storage'] != 'maintenance':
            raise RuntimeError('Workload must be down and data domain in maintenance')

    def domain(self, node):
        name = f'nr99-node{node}'
        xml = ET.fromstring(self.call(['virsh', 'dumpxml', name]).stdout)
        if xml.findtext('uuid') != self.config['uuids'][str(node)]:
            raise RuntimeError('Lab VM UUID changed; refusing network mutation')
        mac = f'52:54:00:99:02:{10 + node:02x}'
        matches = [i for i in xml.findall('devices/interface')
                   if i.find('mac').get('address') == mac and i.find('source').get('network') == 'nr99-storage']
        if len(matches) != 1:
            raise RuntimeError('Expected isolated storage NIC is absent')
        return name, mac

    def restore(self, node):
        name, mac = self.domain(node)
        if self.call(['virsh', 'domstate', name]).stdout.strip() != 'running':
            raise RuntimeError('Lab VM is not running; inspect before restoration')
        self.call(['virsh', 'domif-setlink', name, mac, 'up'])
        if self.call(['virsh', 'domif-getlink', name, mac]).stdout.split()[-1] != 'up':
            raise RuntimeError('Storage link did not restore')

    @contextlib.contextmanager
    def fault(self, node):
        name, mac = self.domain(node)
        unit = f'nr99-r2-{self.id[:12]}-restore{node}'
        try:
            arm_then_down(self.call, unit, ['python3', str(CODE / 'staged_safety.py'),
                          'watchdog', '--case', self.id, '--node', str(node)], name, mac)
            yield
        finally:
            # Stop the timer only after independently verifying link restoration.
            self.restore(node)
            self.call(['systemctl', 'stop', unit + '.timer'], check=False)

    def probe(self, action, node=1, client='existing', file='record.bin', generation='g0', timeout=15):
        argv = ['timeout', '--kill-after=2s', str(timeout) + 's', 'python3', str(CODE / 'round2_probe.py'),
                action, '--case', self.id, '--client', client, '--file', file, '--generation', generation]
        result = self.guest(node, argv, timeout=timeout + 12, check=False)
        if result.returncode in (124, 137):
            return {'status': 'TIMEOUT'}
        if result.returncode:
            raise Inconclusive('Probe failed: ' + result.stderr[-600:])
        return json.loads(result.stdout)

    def write(self, generation, client='existing', file='record.bin', timeout=15):
        result = self.probe('write', client=client, file=file, generation=generation, timeout=timeout)
        if result.get('status') == 'ACK':
            if result['sha256'] != expected(self.id, generation):
                raise RuntimeError('Unexpected acknowledged checksum')
            self.oracle[file] = result['sha256']
            atomic(self.path / 'oracle.json', self.oracle)  # Outside Gluster, on north31.
        return result

    def hash(self, node, file='record.bin'):
        return self.probe('raw', node=node, file=file)

    def readiness(self, trigger=True):
        for volume in ('nr99-data', 'nr99-io'):
            if trigger and self.guest(1, ['gluster', '--mode=script', 'volume', 'heal', volume], check=False).returncode:
                return False
            for extra in ([], ['split-brain']):
                result = self.guest(1, ['gluster', '--xml', 'volume', 'heal', volume, 'info', *extra], check=False)
                if result.returncode:
                    return False
                root = ET.fromstring(result.stdout)
                bricks = root.findall('.//healInfo/bricks/brick')
                if root.findtext('opRet') != '0' or len(bricks) != 3 or any(
                        b.findtext('status') != 'Connected' or b.findtext('numberOfEntries') != '0' for b in bricks):
                    return False
        return True

    def require(self, step):
        receipts = [self.path / (attempt_name(step, n) + '.result.json') for n in range(1, 10)]
        receipt = json.loads(next(path for path in reversed(receipts) if path.exists()).read_text())
        if step == 'checkpoint' and receipt['status'] != 'PASS':
            self.require('checkpoint-finish')
            return
        if receipt['status'] != 'PASS':
            raise RuntimeError('Previous stage must pass: ' + step)

    def activate_hosts_ready(self):
        deadline = time.monotonic() + 180
        for node in (1, 2, 3):
            while self.guest(node, ['systemctl', 'is-active', '--quiet', 'vdsmd', 'supervdsmd'],
                             timeout=12, check=False).returncode:
                if time.monotonic() >= deadline:
                    raise RuntimeError('Host management services did not become active')
                time.sleep(5)
        self.observations['engine'] = self.engine('activate-hosts')
        if any(s != 'up' for s in self.observations['engine']['hosts'].values()):
            raise RuntimeError('Not all hosts returned to UP')
        self.quiescent()

    def checkpoint_finish(self):
        # Preserve the original failure. Resume only after all snapshots/start commands succeeded.
        original = json.loads((self.path / 'checkpoint.result.json').read_text())
        if original['status'] != 'FAILED':
            raise RuntimeError('Completion step requires the retained failed checkpoint receipt')
        checkpoint = json.loads((self.path / 'checkpoint.json').read_text())
        if checkpoint['uuids'] != self.config['uuids'] or checkpoint['label'] != 'nr99-r2-' + self.id:
            raise RuntimeError('Checkpoint identity mismatch')
        expected_images = {f'/var/lib/libvirt/images/nr99/node{n}/{disk}.qcow2'
                           for n in (1, 2, 3) for disk in ('os', 'data01', 'data02')}
        if set(checkpoint['images']) != expected_images or len(checkpoint['images']) != 9:
            raise RuntimeError('Unexpected checkpoint image inventory')
        records = [json.loads(line) for line in (self.path / 'checkpoint.log').read_text().splitlines()
                   if line.startswith('{')]
        for image in expected_images:
            matches = [r for r in records if r.get('command') == ['qemu-img', 'info', '--output=json', image]
                       and r.get('rc') == 0]
            if not matches or checkpoint['label'] not in [s['name'] for s in json.loads(matches[-1]['stdout']).get('snapshots', [])]:
                raise RuntimeError('Missing saved snapshot verification: ' + image)
        self.quiescent()
        for node in (1, 2, 3):
            name, _ = self.domain(node)
            if self.call(['virsh', 'domstate', name]).stdout.strip() != 'running':
                raise RuntimeError('Completion requires all three already restarted guests')
            xml = ET.fromstring(self.call(['virsh', 'dumpxml', name]).stdout)
            actual = [d.find('source').get('file') for d in xml.findall("devices/disk[@device='disk']")]
            if set(actual) != {p for p in expected_images if f'/node{node}/' in p} or len(actual) != 3:
                raise RuntimeError('Current disk inventory differs from checkpoint')
        self.activate_hosts_ready()
        if not self.readiness():
            raise RuntimeError('Post-checkpoint heal readiness failed')
        self.observations['checkpoint_label'] = checkpoint['label']
        self.observations['verified_snapshots'] = 9

    def checkpoint(self):
        self.engine('quiesce')
        self.quiescent()
        for client in ('existing', 'fresh'):
            self.probe('unmount', client=client, timeout=25)
        self.probe('legacy-unmount', timeout=25)
        if not self.readiness():
            raise RuntimeError('Restore healthy replicas before checkpointing')
        for network in ('nr99-mgmt', 'nr99-storage'):
            (self.path / (network + '.xml')).write_text(self.call(['virsh', 'net-dumpxml', network]).stdout)
        self.engine('maintain-hosts')
        for node in (1, 2, 3):
            self.domain(node)
            self.call(['virsh', 'shutdown', f'nr99-node{node}'])
        deadline = time.monotonic() + 180
        while any(self.call(['virsh', 'domstate', f'nr99-node{n}']).stdout.strip() != 'shut off' for n in (1, 2, 3)):
            if time.monotonic() > deadline:
                raise RuntimeError('Clean shutdown timed out; no forced power-off or snapshots attempted')
            time.sleep(3)
        images = []
        for node in (1, 2, 3):
            xml_text = self.call(['virsh', 'dumpxml', f'nr99-node{node}', '--inactive']).stdout
            (self.path / f'node{node}.xml').write_text(xml_text)
            disks = ET.fromstring(xml_text).findall("devices/disk[@device='disk']")
            if len(disks) != 3:
                raise RuntimeError('Unexpected OS/data disk inventory')
            for disk in disks:
                path = Path(disk.find('source').get('file', '')).resolve(strict=True)
                if path.parent != Path(f'/var/lib/libvirt/images/nr99/node{node}'):
                    raise RuntimeError('Disk is outside the approved lab directory')
                info = json.loads(self.call(['qemu-img', 'info', '--output=json', str(path)]).stdout)
                if info['format'] != 'qcow2':
                    raise RuntimeError('Only cold qcow2 internal checkpoints are supported')
                images.append(str(path))
        label = 'nr99-r2-' + self.id
        for path in images:
            self.call(['qemu-img', 'snapshot', '-c', label, path])
            info = json.loads(self.call(['qemu-img', 'info', '--output=json', path]).stdout)
            if label not in [s['name'] for s in info.get('snapshots', [])]:
                raise RuntimeError('Checkpoint verification failed')
        atomic(self.path / 'checkpoint.json', {'label': label, 'images': images, 'uuids': self.config['uuids']})
        for node in (1, 2, 3):
            self.call(['virsh', 'start', f'nr99-node{node}'])
        deadline = time.monotonic() + 180
        for node in (1, 2, 3):
            while self.guest(node, ['true'], timeout=12, check=False).returncode:
                if time.monotonic() > deadline:
                    raise RuntimeError('Lab guests did not regain SSH after checkpoint')
                time.sleep(3)
        self.activate_hosts_ready()

    def baseline(self):
        self.require('checkpoint')
        self.quiescent()
        versions = {}
        for node in (1, 2, 3):
            if self.guest(node, ['hostname']).stdout.strip() != f'node{node}.nr99.test':
                raise RuntimeError('Wrong guest identity')
            health = json.loads(self.guest(node, ['python3', str(CODE / 'check_host.py'),
                                f'/home/jarredm/nr99-node{node}-engine.json']).stdout)
            if not health.get('passed'):
                raise RuntimeError('Host health check failed')
            version = self.guest(node, ['rpm', '-q', '--qf', '%{NAME} %{VERSION}-%{RELEASE}\n',
                                       'glusterfs-server', 'glusterfs-fuse', 'glusterfs-cli']).stdout.splitlines()
            if len(version) != 3 or any(line.split()[-1] != '11.1-1.el9s' for line in version):
                raise RuntimeError('Gluster package set changed; review the experiment before proceeding')
            versions[str(node)] = version
        self.observations['packages'] = versions
        for volume in ('nr99-data', 'nr99-io'):
            for option, value in [('cluster.quorum-type', 'auto'), ('cluster.server-quorum-type', 'server')]:
                lines = self.guest(1, ['gluster', 'volume', 'get', volume, option]).stdout.splitlines()
                found = [line.split()[1] for line in lines if line.split() and line.split()[0] == option]
                if found != [value]:
                    raise RuntimeError('Quorum policy differs from the approved baseline')
        option = 'cluster.server-quorum-ratio'
        lines = self.guest(1, ['gluster', 'volume', 'get', 'all', option]).stdout.splitlines()
        values = [line.split()[1].rstrip('%') for line in lines if line.split() and line.split()[0] == option]
        if len(values) != 1 or float(values[0]) != 51:
            raise RuntimeError('Server quorum ratio is not 51 percent')
        if not self.readiness():
            raise RuntimeError('Baseline heal readiness failed')
        for client in ('existing', 'fresh'):
            self.probe('unmount', client=client, timeout=25)
        self.probe('mount', timeout=25)
        if self.write('g0').get('status') != 'ACK':
            raise RuntimeError('Baseline write was not acknowledged')
        if any(self.hash(n).get('sha256') != self.oracle['record.bin'] for n in (1, 2, 3)):
            raise RuntimeError('Baseline does not match on all three replicas')

    def scenario(self):
        self.require('baseline')
        self.quiescent()
        if not self.readiness() or any(self.hash(n).get('sha256') != expected(self.id, 'g0') for n in (1, 2, 3)):
            raise RuntimeError('Revalidate a clean baseline before fault injection')
        with self.fault(3):
            time.sleep(35)
            if self.write('g1', timeout=65).get('status') != 'ACK':
                raise Inconclusive('The outage write was not acknowledged')
            if any(self.hash(n).get('sha256') != expected(self.id, 'g1') for n in (1, 2)):
                raise RuntimeError('Acknowledged generation is missing from a healthy replica')
        # Do not issue heal commands here: the stale-replica window is the subject.
        deadline = time.monotonic() + 60
        while True:
            xml = ET.fromstring(self.guest(3, ['gluster', '--xml', 'volume', 'status', 'nr99-io']).stdout)
            bricks = [n for n in xml.findall('.//volStatus/volumes/volume/node') if n.findtext('path') == '/storage/sdc1/brick/nr99-io']
            if len(bricks) == 3 and all(n.findtext('status') == '1' for n in bricks):
                break
            if time.monotonic() > deadline:
                raise Inconclusive('Bricks did not return online; no second fault injected')
            time.sleep(2)
        stale = self.hash(3)
        self.observations['node3_before_second_fault'] = stale
        if stale.get('sha256') != expected(self.id, 'g0'):
            raise Inconclusive('Node3 is not demonstrably stale; no second fault injected')
        with self.fault(2):
            time.sleep(35)
            for client in ('existing', 'fresh'):
                if client == 'fresh':
                    if self.probe('mount', client=client, timeout=25).get('status') != 'OK':
                        raise Inconclusive('Fresh client mount unavailable')
                read = self.probe('read', client=client)
                write = self.write(client, client=client, file='probe-' + client + '.bin')
                copies = sum(self.hash(n, file='probe-' + client + '.bin').get('sha256') == write.get('sha256')
                             for n in (1, 3)) if write.get('status') == 'ACK' else 0
                self.observations[client] = {'read': read, 'write': write, 'available_matching_copies': copies,
                                             'node3_record_after_read': self.hash(3)}
                classify(read, write, expected(self.id, 'g1'), copies)
        if any(self.path.glob('watchdog-*.json')):
            raise Inconclusive('A watchdog restored a link; test timing is not controlled')

    def recover(self):
        self.quiescent()
        for n in (2, 3):
            self.restore(n)
        for volume in ('nr99-data', 'nr99-io'):
            xml = ET.fromstring(self.guest(1, ['gluster', '--xml', 'volume', 'heal', volume, 'info', 'split-brain']).stdout)
            bricks = xml.findall('.//healInfo/bricks/brick')
            if xml.findtext('opRet') != '0' or len(bricks) != 3 or any(
                    b.findtext('status') != 'Connected' or b.findtext('numberOfEntries') != '0' for b in bricks):
                raise RuntimeError('Split-brain or unknown state: manual investigation required; no source chosen')
        for client in ('existing', 'fresh'):
            self.probe('unmount', client=client, timeout=25)
        for n in (1, 2, 3):
            self.guest(n, ['python3', str(CODE / 'repair_shd.py')])
        for volume in ('nr99-data', 'nr99-io'):
            self.guest(1, ['gluster', '--mode=script', 'volume', 'heal', volume, 'full'])
        deadline = time.monotonic() + 900
        while not self.readiness():
            if time.monotonic() > deadline:
                raise RuntimeError('Manual recovery did not reach full heal readiness')
            time.sleep(10)

    def verify(self):
        self.require('recover')
        self.quiescent()
        if not self.readiness():
            raise RuntimeError('Final heal readiness failed')
        self.verify_contents()

    def automatic_verify(self):
        self.require('baseline')
        if not (self.path / 'scenario.result.json').exists():
            raise RuntimeError('A completed scenario receipt is required')
        self.quiescent()
        if not self.readiness(trigger=False):
            raise RuntimeError('Automatic recovery has not reached connected, zero-backlog, zero-split-brain state')
        self.verify_contents()
        self.observations['heal_triggered'] = False
        self.observations['heal_daemons_restarted'] = False

    def verify_contents(self):
        for file, digest in self.oracle.items():
            if any(self.hash(n, file=file).get('sha256') != digest for n in (1, 2, 3)):
                raise RuntimeError('An acknowledged write is missing or changed')
        trees = [self.probe('tree', node=n, timeout=250) for n in (1, 2, 3)]
        if any(not isinstance(tree, dict) or 'status' in tree for tree in trees):
            raise Inconclusive('Incomplete or timed-out replica checksum inventory')
        if not trees[0] == trees[1] == trees[2]:
            raise RuntimeError('Quiescent replica content mismatch')
        self.observations['matching_paths'] = len(trees[0])
        connections = {}
        for node in (1, 2, 3):
            state = json.loads(self.guest(node, ['python3', str(CODE / 'shd_state.py')]).stdout)
            connections[str(node)] = state
            self.observations['heal_clients'] = connections
            if len(state['active_clients']) != 6 or not all(state['active_clients'].values()):
                raise RuntimeError('Self-heal clients are not all connected')

    def targeted_recover(self):
        self.require('baseline')
        self.quiescent()
        if not self.readiness(trigger=False):
            raise RuntimeError('State changed: review backlog/split-brain before targeted recovery')
        before = {}
        for node in (1, 2, 3):
            before[str(node)] = json.loads(self.guest(node, ['python3', str(CODE / 'shd_state.py')]).stdout)
        self.observations['before'] = before
        expected_disconnected = {'nr99-data-client-2', 'nr99-io-client-2'}
        for node in (1, 2, 3):
            flags = before[str(node)]['active_clients']
            disconnected = {k for k, value in flags.items() if not value}
            if len(flags) != 6 or disconnected != (set() if node == 1 else expected_disconnected):
                raise RuntimeError('Connection pattern changed; review before restarting daemons')
        repairs = {}
        for node in (2, 3):
            repairs[str(node)] = json.loads(self.guest(node, ['python3', str(CODE / 'repair_shd.py')]).stdout)
            self.observations['repairs'] = repairs
            if not repairs[str(node)]['brick_processes_preserved']:
                raise RuntimeError('Brick processes changed')
        deadline = time.monotonic() + 180
        while True:
            after = {}
            for node in (1, 2, 3):
                result = self.guest(node, ['python3', str(CODE / 'shd_state.py')], check=False)
                if result.returncode == 0:
                    after[str(node)] = json.loads(result.stdout)
            self.observations['after'] = after
            if '1' in after and after['1']['shd_pids'] != before['1']['shd_pids']:
                raise RuntimeError('Node1 heal daemon changed unexpectedly')
            if len(after) == 3 and all(len(s['active_clients']) == 6 and all(s['active_clients'].values()) for s in after.values()):
                break
            if time.monotonic() >= deadline:
                raise RuntimeError('Targeted restart did not restore all 18 connections')
            time.sleep(5)
        if not self.readiness(trigger=False):
            raise RuntimeError('Final heal readiness failed after targeted restart')
        self.verify_contents()
        self.observations['node1_shd_preserved'] = True

    def resume(self):
        self.require('verify')
        self.observations['engine'] = self.engine('resume')
        if self.observations['engine']['vm'] != 'up' or self.observations['engine']['storage'] != 'active':
            raise RuntimeError('Workload was not restored')


def worker(case, step, attempt=1):
    with (ROOT / 'job.lock').open('w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        result = case.path / (attempt_name(step, attempt) + '.result.json')
        if result.exists():
            raise RuntimeError('Stage already has a receipt; retain it and review before retrying')
        started = time.time()
        outcome = {'case': case.id, 'step': step, 'attempt': attempt, 'status': 'FAILED'}
        atomic(case.path / 'progress.json', {'step': step, 'status': 'RUNNING'})
        try:
            getattr(case, step.replace('-', '_'))()
            outcome['status'] = 'PASS'
        except Inconclusive as error:
            outcome.update(status='INCONCLUSIVE', error=str(error))
        except Exception as error:
            outcome['error'] = str(error)[-1200:]
        finally:
            if step == 'scenario':
                errors = []
                for node in (2, 3):
                    try:
                        case.restore(node)
                    except Exception as error:
                        errors.append(str(error))
                if errors:
                    outcome.update(status='FAILED', cleanup_errors=errors)
            outcome.update(elapsed_seconds=round(time.time() - started, 1), observations=case.observations)
            atomic(result, outcome)
            atomic(case.path / 'progress.json', {'step': step, 'status': outcome['status']})
        if outcome['status'] != 'PASS':
            raise SystemExit(1)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['new', 'start', 'worker', 'show', 'wait', 'watchdog', 'restore-links'])
    parser.add_argument('--case', type=valid_case)
    parser.add_argument('--step', choices=STEPS)
    parser.add_argument('--attempt', type=int, choices=range(1, 10), default=1,
                        help='Explicit attempt number; retains earlier receipts and logs')
    parser.add_argument('--node', type=int, choices=[2, 3])
    args = parser.parse_args()
    if os.geteuid() or socket.gethostname().split('.')[0] != 'north31':
        raise SystemExit('Restricted to root on north31; drafting/importing does not execute tests')
    os.umask(0o077)
    if args.action == 'new':
        identifier = uuid.uuid4().hex
        path = ROOT / 'cases' / identifier
        path.mkdir(parents=True)
        uuids = {str(n): subprocess.check_output(['virsh', 'domuuid', f'nr99-node{n}'], text=True).strip() for n in (1, 2, 3)}
        atomic(path / 'config.json', {'case': identifier, 'uuids': uuids})
        print(json.dumps({'case': identifier, 'directory': str(path)}))
        return
    if not args.case:
        parser.error('--case is required')
    case = Case(args.case)
    if args.action in ('start', 'worker', 'show', 'wait') and not args.step:
        parser.error('--step is required')
    if args.action == 'start':
        name = attempt_name(args.step, args.attempt)
        if (case.path / (name + '.result.json')).exists():
            raise RuntimeError('Existing stage result retained; do not silently rerun')
        unit = f'nr99-r2-{case.id[:12]}-{name}'
        subprocess.run(['systemd-run', '--unit=' + unit, '--property=Type=exec',
                        '--property=RuntimeMaxSec=' + str(LIMITS[args.step]),
                        '--property=StandardOutput=append:' + str(case.path / (name + '.log')),
                        '--property=StandardError=append:' + str(case.path / (name + '.log')),
                        'python3', str(CODE / 'staged_safety.py'), 'worker', '--case', case.id, '--step', args.step,
                        '--attempt', str(args.attempt)], check=True)
        print(json.dumps({'started': args.step, 'attempt': args.attempt, 'unit': unit, 'result_file': str(case.path / (name + '.result.json'))}))
    elif args.action == 'worker':
        signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(InterruptedError('Service deadline or stop request')))
        worker(case, args.step, args.attempt)
    elif args.action in ('show', 'wait'):
        name = attempt_name(args.step, args.attempt)
        path = case.path / (name + '.result.json')
        if args.action == 'wait':
            deadline = time.monotonic() + LIMITS[args.step] + 90
            while not path.exists() and time.monotonic() < deadline:
                time.sleep(5)  # Optional terminal-side wait, never a chat polling loop.
        print(path.read_text() if path.exists() else json.dumps({'status': 'NO_RESULT_YET', 'log': str(case.path / (name + '.log'))}))
        if args.action == 'wait' and (not path.exists() or json.loads(path.read_text())['status'] != 'PASS'):
            raise SystemExit(1)
    elif args.action == 'watchdog':
        if not args.node:
            parser.error('--node is required')
        case.restore(args.node)
        atomic(case.path / f'watchdog-{args.node}.json', {'restored': True, 'time': time.time()})
    else:
        for node in (2, 3):
            case.restore(node)


if __name__ == '__main__':
    main()
