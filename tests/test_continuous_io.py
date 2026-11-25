import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).parents[1] / 'lab'))
from continuous_io import check_preserved, check_witness
from vm_transaction import checksum, record, verify_file

CASE = 'a' * 32
TRIAL = 'b' * 32


class TransactionTests(unittest.TestCase):
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
