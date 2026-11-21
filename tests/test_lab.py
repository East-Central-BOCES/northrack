import importlib.util
import ast
from pathlib import Path
import unittest
import xml.etree.ElementTree as ET

spec = importlib.util.spec_from_file_location('north31', Path(__file__).parents[1] / 'lab' / 'north31.py')
lab = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lab)


class LabBoundaryTests(unittest.TestCase):
    def test_installer_can_clear_only_guest_os_disk(self):
        config = lab.kickstart('node1', 'ssh-ed25519 AAAA operator')
        self.assertIn('ignoredisk --only-use=vda', config)
        self.assertIn('clearpart --all --initlabel --drives=vda', config)
        self.assertNotIn('--drives=vdb', config)
        self.assertIn('rootpw --lock', config)
        self.assertNotIn('--bootproto=none', config)
        self.assertIn("config['connection']['autoconnect'] = 'true'", config)

    def test_storage_has_no_forwarding_or_dhcp(self):
        tree = ET.fromstring(lab.storage_xml())
        self.assertIsNone(tree.find('forward'))
        self.assertIsNone(tree.find('ip'))
        self.assertEqual(tree.find('mtu').get('size'), '9000')

    def test_management_addresses_have_private_dns(self):
        tree = ET.fromstring(lab.management_xml())
        self.assertEqual(tree.find('forward').get('mode'), 'nat')
        self.assertEqual(tree.find('domain').get('name'), 'nr99.test')
        addresses = {h.find('hostname').text: h.get('ip') for h in tree.findall('dns/host')}
        self.assertEqual(addresses['engine.nr99.test'], '172.31.231.10')
        self.assertEqual(addresses['node3.nr99.test'], '172.31.231.13')

    def test_engine_is_independent_of_storage_network(self):
        config = lab.kickstart('engine', 'ssh-ed25519 AAAA')
        self.assertNotIn('--device=eth1', config)

    def test_offline_profile_setup_is_valid_python(self):
        config = lab.kickstart('node1', 'ssh-ed25519 AAAA')
        script = config.split("python3 - <<'NETWORK_PROFILES'\n", 1)[1].split('\nNETWORK_PROFILES', 1)[0]
        ast.parse(script)

    def test_private_keys_and_multiline_input_are_rejected(self):
        for key in ('-----BEGIN PRIVATE KEY-----', 'ssh-ed25519 AAAA\nreboot'):
            with self.assertRaises(ValueError):
                lab.kickstart('node1', key)

    def test_key_comment_cannot_inject_kickstart_syntax(self):
        config = lab.kickstart('node1', 'ssh-ed25519 AAAA "malicious-comment"')
        self.assertNotIn('malicious-comment', config)
        self.assertIn('sshkey --username=jarredm "ssh-ed25519 AAAA"', config)


if __name__ == '__main__':
    unittest.main()
