"""North31 coordinator: fsync an external witness before accepting each ACK."""
import json
import errno
import os
from pathlib import Path
import shlex
import subprocess
import threading
import time
import uuid
from vm_transaction import checksum, record

ROOT = Path('/var/lib/nr99/round2')


class Inconclusive(RuntimeError):
    """Shared verdict type, including when staged_safety runs as __main__."""
    pass


def command(case, trial, action):
    return ['ssh', '-T', '-o', 'BatchMode=yes', '-o', 'IdentitiesOnly=yes',
            '-o', 'StrictHostKeyChecking=yes', '-o', 'ConnectTimeout=5',
            '-o', 'UserKnownHostsFile=' + str(ROOT / 'known_hosts'),
            '-i', str(ROOT / 'id_ed25519'), 'rocky@172.31.231.120',
            shlex.join(['timeout', '--kill-after=5s', '600s', 'python3',
                        '/home/rocky/nr99-round2-code/vm_transaction.py', action,
                        '--case', case, '--trial', trial])]


def check_witness(ack, case, trial, sequence):
    if ack.get('status') != 'ACK' or ack.get('sequence') != sequence:
        raise RuntimeError('Witness sequence/status mismatch')
    if ack.get('sha256') != checksum(record(case, trial, sequence)):
        raise RuntimeError('Witness received an unexpected transaction checksum')


def check_preserved(witness, inventory):
    hashes = inventory['hashes']
    for ack in witness:
        sequence = ack['sequence']
        if sequence >= len(hashes) or hashes[sequence] != ack['sha256']:
            raise RuntimeError('CRITICAL: witnessed committed transaction missing or changed: ' + str(sequence))


def unsafe_quorum_commits(witness, window):
    return [ack for ack in witness if ack['fsync_started'] > window['second_down'] + 2
            and ack['fsync_completed'] < window['second_up_requested'] - 2]


def exercise_loss(case, directory, atomic, two_node=False, active=lambda: True):
    window = {'two_node': two_node}
    with case.fault(3):
        window['down'] = time.time()
        atomic(directory / 'fault-window.json', window)
        time.sleep(45 if two_node else 35)
        if two_node:
            if not active():
                raise Inconclusive('Writer stopped before second fault; no second fault injected')
            name, mac = case.domain(3)
            if case.call(['virsh', 'domif-getlink', name, mac]).stdout.split()[-1] != 'down':
                raise Inconclusive('Node3 link is not down; refusing second fault')
            with case.fault(2):
                window['second_down'] = time.time()
                atomic(directory / 'fault-window.json', window)
                time.sleep(35)
                window['second_up_requested'] = time.time()
            window['second_up'] = time.time()
        window['up_requested'] = time.time()
    window['up'] = time.time()
    atomic(directory / 'fault-window.json', window)
    return window


def run_test(case, two_node=False):
    from staged_safety import CODE, atomic
    state = case.engine('status')
    if state['vm'] != 'up' or state['storage'] != 'active' or any(s != 'up' for s in state['hosts'].values()):
        raise RuntimeError('Live write test requires VM/domain/all hosts UP')
    if not case.readiness(trigger=False):
        raise RuntimeError('Heal readiness changed before live test')
    before = {}
    for node in (1, 2, 3):
        before[str(node)] = json.loads(case.guest(node, ['python3', str(CODE / 'shd_state.py')]).stdout)
        flags = before[str(node)]['active_clients']
        if len(flags) != 6 or not all(flags.values()):
            raise RuntimeError('Live test requires all 18 heal connections')
    trial = uuid.uuid4().hex
    directory = case.path / ('continuous-' + trial)
    directory.mkdir(mode=0o700)
    atomic(directory / 'config.json', {'case': case.id, 'trial': trial, 'writer_seconds': 150,
                                      'two_node': two_node, 'outage_seconds': 80 if two_node else 35, 'before': before})
    case.observations.update(trial=trial, evidence_directory=str(directory), before=before)
    sent = time.time()
    guest_time = json.loads(case.call(command(case.id, trial, 'clock'), timeout=20).stdout)['time']
    received = time.time()
    if received - sent > 3 or abs(guest_time - (sent + received) / 2) > 1:
        raise Inconclusive('Clock/latency check failed; cannot classify outage commits reliably')
    case.observations['clock_skew_seconds'] = round(guest_time - (sent + received) / 2, 3)
    witness = []
    errors = []
    writer_done = []
    io_errors = []
    with (directory / 'writer.stderr').open('w') as stderr, (directory / 'witness.jsonl').open('x') as output, (directory / 'attempts.jsonl').open('x') as attempts:
        os.fsync(output.fileno())
        fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
        os.fsync(fd)
        os.close(fd)
        process = subprocess.Popen(command(case.id, trial, 'write'), stdout=subprocess.PIPE,
                                   stderr=stderr, text=True, bufsize=1)
        def capture():
            try:
                for line in process.stdout:
                    ack = json.loads(line)
                    if ack.get('status') == 'BEGIN':
                        if ack.get('sequence') != len(witness):
                            raise RuntimeError('Transaction attempt sequence mismatch')
                        attempts.write(json.dumps(ack) + '\n')
                        attempts.flush()
                        os.fsync(attempts.fileno())
                        continue
                    if ack.get('status') == 'ERROR':
                        io_errors.append(ack)
                        continue
                    if ack.get('status') == 'DONE':
                        writer_done.append(ack)
                        continue
                    if ack.get('status') == 'CRITICAL':
                        raise RuntimeError('CRITICAL: ' + ack.get('error', 'guest reported corruption'))
                    check_witness(ack, case.id, trial, len(witness))
                    ack['witness_received'] = time.time()
                    output.write(json.dumps(ack) + '\n')
                    output.flush()
                    os.fsync(output.fileno())
                    witness.append(ack)
            except Exception as error:
                errors.append(str(error))
        thread = threading.Thread(target=capture, daemon=True)
        thread.start()
        try:
            deadline = time.monotonic() + 45
            while len(witness) < 10:
                if errors or process.poll() is not None or time.monotonic() > deadline:
                    raise Inconclusive('Writer/witness preflight failed; no fault injected: ' + str(errors))
                time.sleep(0.2)
            window = exercise_loss(case, directory, atomic, two_node,
                                   active=lambda: process.poll() is None and not errors and not io_errors)
            down, up = window['down'], window['up']
            case.observations['fault_window'] = window
            if two_node:
                state = case.engine('status')
                case.observations['engine_after_restore'] = state
                if state['vm'] == 'paused':
                    deadline = time.monotonic() + 120
                    while not case.readiness(trigger=False):
                        if time.monotonic() >= deadline:
                            raise Inconclusive('Links restored but storage is not ready; VM left paused')
                        time.sleep(5)
                    case.observations['pause_recovery'] = case.engine('resume-paused')
                elif state['vm'] != 'up':
                    raise Inconclusive('Unexpected VM state after restoration: ' + state['vm'])
            try:
                rc = process.wait(timeout=240)
            except subprocess.TimeoutExpired:
                raise Inconclusive('Writer did not finish after network restoration; investigate possible I/O stall')
            thread.join(timeout=10)

            if thread.is_alive():
                raise Inconclusive('External witness stream did not close')
            case.observations.update(writer_returncode=rc, witnessed_records=len(witness), witness_errors=errors, io_errors=io_errors)
            atomic(directory / 'writer-outcome.json', {'rc': rc, 'errors': errors, 'io_errors': io_errors, 'done': writer_done})
            result = case.call(command(case.id, trial, 'verify'), timeout=90)
            inventory = json.loads(result.stdout)
            atomic(directory / 'guest-inventory.json', inventory)
            check_preserved(witness, inventory)
            expected_original = '5d654a1dc48fda04b99586e20c6307d7cb7ef3f802525e062cd75e458a1522f1'
            if inventory['original_payload_sha256'] != expected_original or not inventory['original_payload_matches_state'] or not inventory['original_journal_contiguous']:
                raise RuntimeError('CRITICAL: original payload or retained journal changed')
            during = [ack for ack in witness if ack['fsync_started'] > down + 2 and ack['fsync_completed'] < up - 2
                      and ack['witness_received'] < up]
            case.observations.update(witnessed_records_preserved=True, guest_complete_records=inventory['complete_records'],
                                     outage_commits=len(during), partial_tail=inventory['partial_tail'],
                                     original_payload_preserved=True, original_journal_entries=inventory['original_journal_entries'],
                                     post_reboot_verified=False)
            after = {}
            for node in (1, 2, 3):
                after[str(node)] = json.loads(case.guest(node, ['python3', str(CODE / 'shd_state.py')]).stdout)
            case.observations['after'] = after
            case.observations['heal_connections_healthy'] = all(len(s['active_clients']) == 6 and all(s['active_clients'].values()) for s in after.values())
            if any('CRITICAL:' in error for error in errors):
                raise RuntimeError('CRITICAL: application observed unexpected data after fsync')
            if two_node:
                unsafe = unsafe_quorum_commits(witness, window)
                case.observations['commits_without_quorum'] = unsafe
                if unsafe:
                    raise RuntimeError('CRITICAL: new fsynced writes acknowledged with two storage links disconnected')
                accepted_errors = {errno.EIO, errno.EROFS, errno.ENOTCONN, errno.ETIMEDOUT}
                safe_error = rc == 2 and io_errors and all(e.get('errno') in accepted_errors for e in io_errors)
                if errors or (not safe_error and (rc or not writer_done)):
                    raise Inconclusive('Witnessed writes preserved but writer outcome requires investigation')
                blocked = [{'sequence': a['sequence'], 'fsync_seconds': round(a['fsync_completed'] - a['fsync_started'], 3)}
                           for a in witness if a['fsync_started'] < window['second_down'] and a['fsync_completed'] > window['second_up_requested']]
                case.observations['fsyncs_spanning_quorum_loss'] = blocked
                if not blocked and not safe_error and not case.observations.get('pause_recovery'):
                    raise Inconclusive('No unsafe commits, but no blocked operation or safe error demonstrated')
                case.observations['quorum_loss_safe_behavior'] = 'explicit_io_error' if safe_error else 'no_new_commit_with_one_reachable_replica'
            elif errors or io_errors or rc or not writer_done or not during:
                raise Inconclusive('Witnessed writes preserved, but writer/progress requirements were not met')
            if any(case.path.glob('watchdog-*.json')):
                raise Inconclusive('Watchdog fired; fault timing uncontrolled')
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
            thread.join(timeout=10)


def load_witness(case, source='continuous-write'):
    from round2_probe import valid_case
    if source not in ('continuous-write', 'two-node-write'):
        raise ValueError('Unexpected witness source')
    receipt = json.loads((case.path / (source + '.result.json')).read_text())
    observations = receipt['observations']
    if source == 'continuous-write' and (not observations.get('witnessed_records_preserved') or observations.get('writer_returncode') != 0
            or observations.get('witness_errors') or not observations.get('original_payload_preserved')):
        raise RuntimeError('Review live-write evidence before reboot verification')
    trial = valid_case(observations['trial'])
    directory = case.path / ('continuous-' + trial)
    if str(directory) != observations['evidence_directory']:
        raise RuntimeError('Trial evidence directory mismatch')
    if source == 'two-node-write':
        comparisons = sorted(p for p in directory.glob('recovered-comparison-*.json') if not p.name.endswith('-inventory.json'))
        if not comparisons:
            raise RuntimeError('Prior recovered data comparison is required')
        comparison = json.loads(comparisons[-1].read_text())
        if (comparison.get('trial') != trial or not comparison.get('witnessed_records_preserved')
                or not comparison.get('original_payload_preserved') or comparison.get('commits_without_quorum') != 0):
            raise RuntimeError('Review recovered data comparison before repair')
        observations = comparison
    witness = [json.loads(line) for line in (directory / 'witness.jsonl').read_text().splitlines()]
    if not witness or len(witness) != observations['witnessed_records']:
        raise RuntimeError('Witness receipt/count mismatch')
    for sequence, ack in enumerate(witness):
        check_witness(ack, case.id, trial, sequence)
    return trial, directory, witness


def check_original(inventory):
    expected = '5d654a1dc48fda04b99586e20c6307d7cb7ef3f802525e062cd75e458a1522f1'
    if (inventory['original_payload_sha256'] != expected or not inventory['original_payload_matches_state']
            or not inventory['original_journal_contiguous']):
        raise RuntimeError('CRITICAL: original payload or retained journal changed')


def run_reboot_check(case, source='continuous-write', repair=False):
    from staged_safety import CODE, atomic
    trial, directory, witness = load_witness(case, source)
    state = case.engine('status')
    if state['vm'] != 'up' or state['storage'] != 'active' or any(s != 'up' for s in state['hosts'].values()):
        raise RuntimeError('Reboot verification requires healthy live VM/domain/hosts')
    if not repair and not case.readiness(trigger=False):
        raise RuntimeError('Heal readiness failed before reboot')
    before = json.loads(case.call(command(case.id, trial, 'clock'), timeout=20).stdout)
    inventory = json.loads(case.call(command(case.id, trial, 'verify'), timeout=90).stdout)
    check_preserved(witness, inventory)
    check_original(inventory)
    atomic(directory / 'pre-reboot-inventory.json', inventory)
    case.observations.update(trial=trial, witnessed_records=len(witness), boot_id_before=before['boot_id'])
    older = None
    if repair:
        older = load_witness(case)
        previous_inventory = json.loads(case.call(command(case.id, older[0], 'verify'), timeout=90).stdout)
        check_preserved(older[2], previous_inventory)
        check_original(previous_inventory)
        capture_repair_evidence(case, directory)
    case.engine('quiesce')
    case.quiescent()
    if repair:
        case.recover()
        case.verify_contents()
        case.observations['replicas_verified_while_quiescent'] = True
    case.observations['engine'] = case.engine('resume')
    state = case.observations['engine']
    if state['vm'] != 'up' or state['storage'] != 'active' or any(s != 'up' for s in state['hosts'].values()):
        raise RuntimeError('VM/domain/hosts did not return after clean restart')
    deadline = time.monotonic() + 180
    while True:
        result = case.call(command(case.id, trial, 'clock'), timeout=12, check=False)
        if result.returncode == 0:
            after = json.loads(result.stdout)
            if after['boot_id'] != before['boot_id']:
                break
        if time.monotonic() >= deadline:
            raise Inconclusive('VM did not regain SSH with a new boot ID')
        time.sleep(5)
    inventory = json.loads(case.call(command(case.id, trial, 'verify'), timeout=90).stdout)
    atomic(directory / 'post-reboot-inventory.json', inventory)
    check_preserved(witness, inventory)
    check_original(inventory)
    if older:
        previous_inventory = json.loads(case.call(command(case.id, older[0], 'verify'), timeout=90).stdout)
        atomic(directory / 'older-trial-post-reboot-inventory.json', previous_inventory)
        check_preserved(older[2], previous_inventory)
        check_original(previous_inventory)
        case.observations['earlier_trial_commits_preserved'] = len(older[2])
    case.observations.update(boot_id_after=after['boot_id'], witnessed_records_preserved=True,
                             original_payload_preserved=True, original_journal_entries=inventory['original_journal_entries'],
                             post_reboot_verified=True, reboot_type='graceful_shutdown_and_start')
    connections = {}
    for node in (1, 2, 3):
        connections[str(node)] = json.loads(case.guest(node, ['python3', str(CODE / 'shd_state.py')]).stdout)
    case.observations['heal_clients'] = connections
    if not all(len(s['active_clients']) == 6 and all(s['active_clients'].values()) for s in connections.values()):
        raise RuntimeError('Post-reboot heal connections are not all healthy')
    if not case.readiness(trigger=False):
        raise RuntimeError('Post-reboot heal readiness failed')


def capture_repair_evidence(case, directory):
    from staged_safety import CODE, atomic
    evidence = {'engine': case.engine('status'), 'nodes': {}, 'volumes': {}}
    for node in (1, 2, 3):
        state = json.loads(case.guest(node, ['python3', str(CODE / 'shd_state.py')]).stdout)
        code = "from pathlib import Path; p=Path('/var/log/glusterfs/glustershd.log'); f=p.open('rb'); f.seek(0,2); end=f.tell(); f.seek(max(0,end-128000)); print(f.read().decode(errors='replace'))"
        log = case.guest(node, ['python3', '-c', code]).stdout
        evidence['nodes'][str(node)] = {'state': state, 'recent_shd_log': log}
    for volume in ('nr99-data', 'nr99-io'):
        evidence['volumes'][volume] = {}
        for action, argv in [('status', ['status']), ('backlog', ['heal', 'info']), ('split-brain', ['heal', 'info', 'split-brain'])]:
            arguments = ['gluster', '--xml', 'volume', argv[0], volume, *argv[1:]]
            result = case.guest(1, arguments, check=False)
            evidence['volumes'][volume][action] = {'rc': result.returncode, 'xml': result.stdout, 'stderr': result.stderr}
    atomic(directory / 'before-repair-evidence.json', evidence)
