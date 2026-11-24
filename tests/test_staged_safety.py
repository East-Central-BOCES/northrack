import errno
from pathlib import Path
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).parents[1] / 'lab'))
from staged_safety import Inconclusive, arm_then_down, classify
from round2_probe import expected, valid_case
from round2_engine import activate_ready


class SafetyVerdictTests(unittest.TestCase):
    def test_activation_waits_for_listener_and_retries_network_failure(self):
        service = Mock()
        state = lambda name: SimpleNamespace(status=SimpleNamespace(value=name))
        service.get.side_effect = [state('maintenance')] * 3 + [state('up')]
        service.activate.side_effect = [RuntimeError('Network error during communication with the Host.'), None]
        connection = Mock()
        connection.__enter__ = Mock()
        connection.__exit__ = Mock(return_value=False)
        connect = Mock(side_effect=[OSError('not ready'), connection, connection])
        activate_ready(service, 'node1.nr99.test', RuntimeError, 100,
                       clock=lambda: 0, sleep=lambda _: None, connect=connect)
        self.assertEqual(service.activate.call_count, 2)

    def test_activation_does_not_retry_unrelated_errors(self):
        service = Mock()
        service.get.return_value = SimpleNamespace(status=SimpleNamespace(value='maintenance'))
        service.activate.side_effect = RuntimeError('Certificate rejected')
        connection = Mock()
        connection.__enter__ = Mock()
        connection.__exit__ = Mock(return_value=False)
        with self.assertRaisesRegex(RuntimeError, 'Certificate rejected'):
            activate_ready(service, 'node1.nr99.test', RuntimeError, 100,
                           clock=lambda: 0, sleep=lambda _: None, connect=lambda *a, **k: connection)

    def test_activation_has_a_deadline(self):
        service = Mock()
        with self.assertRaisesRegex(RuntimeError, 'deadline'):
            activate_ready(service, 'node1.nr99.test', RuntimeError, 100, clock=lambda: 100)
        service.activate.assert_not_called()

    def test_stale_read_is_critical_even_when_writes_are_rejected(self):
        with self.assertRaisesRegex(RuntimeError, 'stale'):
            classify({'status': 'READ', 'sha256': 'old'}, {'status': 'ERROR', 'errno': errno.ENOTCONN}, 'latest', 0)

    def test_acknowledgement_requires_two_matching_available_replicas(self):
        with self.assertRaisesRegex(RuntimeError, 'two matching'):
            classify({'status': 'READ', 'sha256': 'latest'}, {'status': 'ACK'}, 'latest', 1)
        classify({'status': 'READ', 'sha256': 'latest'}, {'status': 'ACK'}, 'latest', 2)

    def test_critical_evidence_is_not_masked_by_another_operation_timeout(self):
        with self.assertRaisesRegex(RuntimeError, 'CRITICAL:.*stale'):
            classify({'status': 'READ', 'sha256': 'old'}, {'status': 'TIMEOUT'}, 'latest', 0)
        with self.assertRaisesRegex(RuntimeError, 'CRITICAL:.*two matching'):
            classify({'status': 'TIMEOUT'}, {'status': 'ACK'}, 'latest', 1)

    def test_safe_rejection_and_safe_healing_are_distinct_from_timeouts(self):
        rejected = {'status': 'ERROR', 'errno': errno.ENOTCONN}
        classify(rejected, rejected, 'latest', 0)
        with self.assertRaises(Inconclusive):
            classify({'status': 'TIMEOUT'}, rejected, 'latest', 0)
        with self.assertRaises(Inconclusive):
            classify({'status': 'ERROR', 'errno': errno.ENOENT}, rejected, 'latest', 0)

    def test_fault_is_not_injected_if_watchdog_cannot_be_verified(self):
        calls = []
        def call(argv):
            calls.append(argv)
            if argv[0] == 'systemctl':
                raise RuntimeError('Timer did not activate')
        with self.assertRaises(RuntimeError):
            arm_then_down(call, 'test-unit', ['python3', 'guard.py'], 'nr99-node3', 'test-mac')
        self.assertFalse(any(argv[0] == 'virsh' for argv in calls))

    def test_watchdog_is_armed_before_disconnection(self):
        calls = []
        arm_then_down(calls.append, 'test-unit', ['python3', 'guard.py'], 'nr99-node3', 'test-mac')
        self.assertEqual([argv[0] for argv in calls], ['systemd-run', 'systemctl', 'virsh'])

    def test_case_paths_cannot_escape_and_generation_checksums_differ(self):
        for value in ('../other', '', 'A' * 32, '0' * 31):
            with self.assertRaises(ValueError):
                valid_case(value)
        case = 'a' * 32
        self.assertEqual(valid_case(case), case)
        self.assertNotEqual(expected(case, 'g0'), expected(case, 'g1'))


if __name__ == '__main__':
    unittest.main()
