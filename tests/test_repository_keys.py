import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('repository_keys', Path(__file__).parents[1] / 'tools' / 'repository_keys.py')
keys = importlib.util.module_from_spec(spec)
spec.loader.exec_module(keys)


class PublisherKeyTests(unittest.TestCase):
    config = '[stable]\nenabled=1\ngpgcheck=1\nsslverify=1\ngpgkey=file:///etc/pki/rpm-gpg/publisher\n'

    def test_disabled_testing_key_is_not_trusted(self):
        text = self.config + '[testing]\nenabled=0\ngpgcheck=0\ngpgkey=file:///etc/pki/rpm-gpg/test-key\n'
        self.assertEqual(keys.referenced_keys(text), ['/etc/pki/rpm-gpg/publisher'])

    def test_native_dnf_report_banners(self):
        text = '================ repo: stable ================\n' + self.config
        self.assertEqual(keys.referenced_keys(text), ['/etc/pki/rpm-gpg/publisher'])

    def test_rejects_unchecked_packages_or_tls(self):
        for text in (self.config.replace('gpgcheck=1', 'gpgcheck=0'), self.config.replace('sslverify=1', 'sslverify=0')):
            with self.assertRaises(ValueError):
                keys.referenced_keys(text)

    def test_remote_or_unowned_key_locations_fail(self):
        for url in ('https://example.org/key', 'file:///tmp/key', 'file:///etc/pki/rpm-gpg/../../tmp/key'):
            with self.assertRaises(ValueError):
                keys.referenced_keys(self.config.replace('file:///etc/pki/rpm-gpg/publisher', url))

    def test_missing_signing_key_fails(self):
        with self.assertRaises(ValueError):
            keys.referenced_keys('[stable]\nenabled=1\ngpgcheck=1\n')


if __name__ == '__main__':
    unittest.main()
