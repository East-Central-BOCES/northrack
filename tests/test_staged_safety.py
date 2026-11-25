import errno
import json
from pathlib import Path
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).parents[1] / 'lab'))
from staged_safety import Case, Inconclusive, arm_then_down, classify, attempt_name
from round2_probe import expected, valid_case
from round2_engine import activate_ready


class SafetyVerdictTests(unittest.TestCase):
    def test_targeted_recovery_never_restarts_node1(self):
        case = Case.__new__(Case)
        case.observations = {}
        case.require = Mock()
        case.quiescent = Mock()
        case.readiness = Mock(return_value=True)
        case.verify_contents = Mock()
        repaired = set()
        def guest(node, argv, **kwargs):
            if argv[-1].endswith('repair_shd.py'):
                repaired.add(node)
                return SimpleNamespace(returncode=0, stdout=json.dumps({'brick_processes_preserved': True}))
            flags = {f'{volume}-client-{n}': not (node in (2, 3) and node not in repaired and n == 2)
                     for volume in ('nr99-data', 'nr99-io') for n in range(3)}
            return SimpleNamespace(returncode=0, stdout=json.dumps({'shd_pids': [100 + node], 'active_clients': flags}))
        case.guest = Mock(side_effect=guest)
        case.targeted_recover()
        self.assertEqual(repaired, {2, 3})
        case.verify_contents.assert_called_once()
        self.assertTrue(case.observations['node1_shd_preserved'])

    def test_targeted_recovery_refuses_changed_connection_pattern(self):
        case = Case.__new__(Case)
        case.observations = {}
        case.require = Mock()
        case.quiescent = Mock()
        case.readiness = Mock(return_value=True)
        flags = {f'{volume}-client-{n}': True for volume in ('nr99-data', 'nr99-io') for n in range(3)}
        case.guest = Mock(return_value=SimpleNamespace(stdout=json.dumps({'shd_pids': [123], 'active_clients': flags})))
        with self.assertRaisesRegex(RuntimeError, 'Connection pattern changed'):
            case.targeted_recover()
        self.assertFalse(any(c.args[1][-1].endswith('repair_shd.py') for c in case.guest.call_args_list))

    def test_observation_does_not_trigger_healing(self):
        case = Case.__new__(Case)
        brick = '<brick><status>Connected</status><numberOfEntries>0</numberOfEntries></brick>'
        xml = '<cliOutput><opRet>0</opRet><healInfo><bricks>' + brick * 3 + '</bricks></healInfo></cliOutput>'
        case.guest = Mock(return_value=SimpleNamespace(returncode=0, stdout=xml))
        self.assertTrue(case.readiness(trigger=False))
        self.assertEqual(case.guest.call_count, 4)
        for call in case.guest.call_args_list:
            self.assertIn('info', call.args[1])
            self.assertNotIn('--mode=script', call.args[1])

    def test_unassigned_is_tolerated_only_after_accepted_activation(self):
        service = Mock()
        state = lambda name: SimpleNamespace(status=SimpleNamespace(value=name))
        service.get.side_effect = [state('maintenance'), state('unassigned'), state('up')]
        connection = Mock()
        connection.__enter__ = Mock()
        connection.__exit__ = Mock(return_value=False)
        activate_ready(service, 'node2.nr99.test', RuntimeError, 100,
                       clock=lambda: 0, sleep=lambda _: None, connect=lambda *a, **k: connection)
        service.activate.assert_called_once()
        service.get.side_effect = [state('unassigned')]
        with self.assertRaisesRegex(RuntimeError, 'Unexpected host activation state'):
            activate_ready(service, 'node2.nr99.test', RuntimeError, 100, clock=lambda: 0)

    def test_attempts_preserve_original_result_names(self):
        self.assertEqual(attempt_name('checkpoint-finish', 1), 'checkpoint-finish')
        self.assertEqual(attempt_name('checkpoint-finish', 2), 'checkpoint-finish-attempt2')
        with self.assertRaises(ValueError):
            attempt_name('checkpoint-finish', 0)

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
