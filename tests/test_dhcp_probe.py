import importlib.util
from pathlib import Path
import socket
import unittest

spec = importlib.util.spec_from_file_location('dhcp_probe', Path(__file__).parents[1] / 'lab/dhcp_dns_probe.py')
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


class DHCPProbeTests(unittest.TestCase):
    def setUp(self):
        self.mac = bytes.fromhex('525400990501')
        self.reply = bytearray(probe.packet(123, self.mac, 'nr99-test', 5, '10.11.0.100'))
        self.reply[0] = 2
        self.reply[16:20] = socket.inet_aton('10.11.0.100')

    def test_only_matching_maas_ack_is_accepted(self):
        address, options = probe.parse(self.reply, 123, self.mac)
        self.assertEqual(address, '10.11.0.100')
        self.assertEqual(options[53], b'\x05')
        self.assertIsNone(probe.parse(self.reply, 124, self.mac))
        self.assertIsNone(probe.parse(self.reply, 123, bytes.fromhex('525400990502')))
        foreign = self.reply.replace(socket.inet_aton(probe.SERVER), socket.inet_aton('10.11.0.1'))
        self.assertIsNone(probe.parse(foreign, 123, self.mac))

    def test_malformed_options_and_truncated_replies_are_ignored(self):
        self.assertIsNone(probe.parse(self.reply[:239], 123, self.mac))
        self.assertIsNone(probe.parse(self.reply[:-1] + b'\x01\x10\x01', 123, self.mac))


if __name__ == '__main__':
    unittest.main()
