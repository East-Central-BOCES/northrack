"""North31 coordinator: fsync an external witness before accepting each ACK."""
import json
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


def run_test(case):
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
                                      'outage_seconds': 35, 'before': before})
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
    with (directory / 'writer.stderr').open('w') as stderr, (directory / 'witness.jsonl').open('x') as output:
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
            with case.fault(3):
                down = time.time()
                atomic(directory / 'fault-window.json', {'down': down})
                time.sleep(35)
                up = time.time()
            atomic(directory / 'fault-window.json', {'down': down, 'up': up})
            try:
                rc = process.wait(timeout=240)
            except subprocess.TimeoutExpired:
                raise Inconclusive('Writer did not finish after network restoration; investigate possible I/O stall')
            thread.join(timeout=10)

            if thread.is_alive():
                raise Inconclusive('External witness stream did not close')
            case.observations.update(writer_returncode=rc, witnessed_records=len(witness), witness_errors=errors)
            atomic(directory / 'writer-outcome.json', {'rc': rc, 'errors': errors, 'done': writer_done})
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
            if errors or rc or not writer_done or not during:
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


def load_witness(case):
    from round2_probe import valid_case
    receipt = json.loads((case.path / 'continuous-write.result.json').read_text())
    observations = receipt['observations']
    if (not observations.get('witnessed_records_preserved') or observations.get('writer_returncode') != 0
            or observations.get('witness_errors') or not observations.get('original_payload_preserved')):
        raise RuntimeError('Review live-write evidence before reboot verification')
    trial = valid_case(observations['trial'])
    directory = case.path / ('continuous-' + trial)
    if str(directory) != observations['evidence_directory']:
        raise RuntimeError('Trial evidence directory mismatch')
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


def run_reboot_check(case):
    from staged_safety import CODE, atomic
    trial, directory, witness = load_witness(case)
    state = case.engine('status')
    if state['vm'] != 'up' or state['storage'] != 'active' or any(s != 'up' for s in state['hosts'].values()):
        raise RuntimeError('Reboot verification requires healthy live VM/domain/hosts')
    if not case.readiness(trigger=False):
        raise RuntimeError('Heal readiness failed before reboot')
    before = json.loads(case.call(command(case.id, trial, 'clock'), timeout=20).stdout)
    inventory = json.loads(case.call(command(case.id, trial, 'verify'), timeout=90).stdout)
    check_preserved(witness, inventory)
    check_original(inventory)
    atomic(directory / 'pre-reboot-inventory.json', inventory)
    case.observations.update(trial=trial, witnessed_records=len(witness), boot_id_before=before['boot_id'])
    case.engine('quiesce')
    case.quiescent()
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
