import json
from pathlib import Path
import sys
import tempfile
import unittest
import contextlib
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).parents[1] / 'lab'))
from continuous_io import check_preserved, check_witness, check_original, load_witness, Inconclusive, unsafe_quorum_commits, exercise_loss
from staged_safety import Inconclusive as RunnerInconclusive
from vm_transaction import checksum, record, verify_file

CASE = 'a' * 32
TRIAL = 'b' * 32


class TransactionTests(unittest.TestCase):
    def test_quorum_detector_excludes_preexisting_and_restored_writes(self):
        window = {'second_down': 100, 'second_up_requested': 135}
        safe = [{'fsync_started': 99, 'fsync_completed': 110},
                {'fsync_started': 105, 'fsync_completed': 136}]
        unsafe = {'fsync_started': 105, 'fsync_completed': 120}
        self.assertEqual(unsafe_quorum_commits(safe, window), [])
        self.assertEqual(unsafe_quorum_commits(safe + [unsafe], window), [unsafe])

    def test_two_faults_restore_in_reverse_order_on_exception(self):
        events = []
        @contextlib.contextmanager
        def fault(node):
            events.append(('down', node))
            try:
                yield
            finally:
                events.append(('up', node))
        case = SimpleNamespace(fault=fault, domain=lambda _: ('nr99-node3', 'mac'),
                               call=Mock(return_value=SimpleNamespace(stdout='mac down')))
        def sleep(seconds):
            if seconds == 35:
                raise RuntimeError('simulated worker interruption')
        with patch('continuous_io.time.sleep', side_effect=sleep):
            with self.assertRaisesRegex(RuntimeError, 'interruption'):
                exercise_loss(case, Path('/unused'), Mock(), two_node=True)
        self.assertEqual(events, [('down', 3), ('down', 2), ('up', 2), ('up', 3)])

    def test_second_fault_is_refused_if_writer_exited(self):
        events = []
        @contextlib.contextmanager
        def fault(node):
            events.append(node)
            yield
        case = SimpleNamespace(fault=fault)
        with patch('continuous_io.time.sleep'):
            with self.assertRaisesRegex(Inconclusive, 'no second fault'):
                exercise_loss(case, Path('/unused'), Mock(), two_node=True, active=lambda: False)
        self.assertEqual(events, [3])

    def test_inconclusive_verdict_uses_one_shared_type(self):
        self.assertIs(RunnerInconclusive, Inconclusive)

    def test_reboot_witness_is_validated_independently_and_count_must_match(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root)
            directory = path / ('continuous-' + TRIAL)
            directory.mkdir()
            ack = {'status': 'ACK', 'sequence': 0, 'sha256': checksum(record(CASE, TRIAL, 0))}
            (directory / 'witness.jsonl').write_text(json.dumps(ack) + '\n')
            observations = {'trial': TRIAL, 'evidence_directory': str(directory), 'witnessed_records': 1,
                            'witnessed_records_preserved': True, 'writer_returncode': 0, 'witness_errors': [],
                            'original_payload_preserved': True}
            receipt = path / 'continuous-write.result.json'
            receipt.write_text(json.dumps({'observations': observations}))
            case = SimpleNamespace(path=path, id=CASE)
            self.assertEqual(load_witness(case)[2], [ack])
            (directory / 'witness.jsonl').write_text(json.dumps(dict(ack, sha256='bad')) + '\n')
            with self.assertRaisesRegex(RuntimeError, 'checksum'):
                load_witness(case)
            (directory / 'witness.jsonl').write_text('')
            with self.assertRaisesRegex(RuntimeError, 'count mismatch'):
                load_witness(case)

    def test_original_payload_is_compared_with_the_independent_saved_hash(self):
        inventory = {'original_payload_sha256': 'changed', 'original_payload_matches_state': True,
                     'original_journal_contiguous': True}
        with self.assertRaisesRegex(RuntimeError, 'CRITICAL'):
            check_original(inventory)

    def test_witness_rejects_wrong_sequence_and_false_checksums(self):
        ack = {'status': 'ACK', 'sequence': 0, 'sha256': checksum(record(CASE, TRIAL, 0))}
        check_witness(ack, CASE, TRIAL, 0)
        with self.assertRaisesRegex(RuntimeError, 'sequence'):
            check_witness(ack, CASE, TRIAL, 1)
        with self.assertRaisesRegex(RuntimeError, 'checksum'):
            check_witness(dict(ack, sha256='0' * 64), CASE, TRIAL, 0)

    def test_loss_or_change_of_any_witnessed_commit_is_critical(self):
        hashes = [checksum(record(CASE, TRIAL, n)) for n in range(3)]
        witness = [{'sequence': n, 'sha256': digest} for n, digest in enumerate(hashes)]
        check_preserved(witness, {'hashes': hashes})
        for actual in (hashes[:2], [hashes[0], 'changed', hashes[2]]):
            with self.assertRaisesRegex(RuntimeError, 'CRITICAL'):
                check_preserved(witness, {'hashes': actual})

    def test_valid_unwitnessed_tail_is_allowed_but_bad_complete_record_is_not(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'data'
            path.write_bytes(record(CASE, TRIAL, 0) + record(CASE, TRIAL, 1) + b'partial')
            inventory = verify_file(path, CASE, TRIAL)
            self.assertEqual(inventory['complete_records'], 2)
            self.assertTrue(inventory['partial_tail'])
            check_preserved([{'sequence': 0, 'sha256': inventory['hashes'][0]}], inventory)
            path.write_bytes(record(CASE, TRIAL, 0) + record(CASE, TRIAL, 2))
            with self.assertRaisesRegex(RuntimeError, 'content/order mismatch'):
                verify_file(path, CASE, TRIAL)

    def test_generation_is_case_trial_and_sequence_specific(self):
        values = [record(CASE, TRIAL, 0), record(CASE, TRIAL, 1), record(TRIAL, CASE, 0)]
        self.assertEqual(len(set(values)), 3)
        self.assertEqual(json.loads(values[0])['sequence'], 0)
