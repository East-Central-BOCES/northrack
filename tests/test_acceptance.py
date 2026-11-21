import hashlib
import importlib.util
import json
from pathlib import Path
import stat
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch


def load(name):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).parents[1] / 'tools' / f'{name}.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


accepted = load('accepted_set')
storage = load('storage')


class AcceptanceTests(unittest.TestCase):
    def test_changed_rpm_or_key_is_rejected(self):
        for changed in ('rpms/package.rpm', 'keys/publisher'):
            with self.subTest(changed=changed), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                (root / 'rpms').mkdir()
                (root / 'keys').mkdir()
                for path in ('rpms/package.rpm', 'keys/publisher'):
                    (root / path).write_bytes(b'accepted bytes')
                entry = lambda name: {'filename': name, 'sha256': hashlib.sha256(b'accepted bytes').hexdigest()}
                data = {'role': 'host', 'signature_failures': [], 'packages': [entry('package.rpm')], 'keys': [entry('publisher')]}
                (root / 'manifest.json').write_text(json.dumps(data))
                expected = accepted.sha256(root / 'manifest.json')
                (root / 'ACCEPTED').write_text(expected)
                accepted.validate(root, expected, 'host')
                (root / changed).write_bytes(b'tampered')
                with self.assertRaises(ValueError):
                    accepted.validate(root, expected, 'host')

    def test_bad_stable_identity_or_mount_is_rejected_before_block_access(self):
        for disk in ({'by_id': '/dev/sda', 'mount': '/storage/test'},
                     {'by_id': '/dev/disk/by-id/test', 'mount': '/'}):
            with self.assertRaises(ValueError):
                storage.inspect({'role': 'host', 'disks': [disk]}, True)

    def test_system_mount_is_never_a_format_target(self):
        tree = {'blockdevices': [{'name': '/dev/vda', 'type': 'disk', 'mountpoints': [None],
                                'children': [{'name': '/dev/vda1', 'type': 'part', 'fstype': 'xfs', 'mountpoints': ['/']}]}]}
        config = {'role': 'host', 'allow_format': True, 'disks': [{'by_id': '/dev/disk/by-id/virtio-nr99-test', 'mount': '/storage/test'}]}
        original_resolve = Path.resolve
        resolve = lambda path, strict=False: Path('/dev/vda') if str(path).startswith('/dev/disk/') else original_resolve(path, strict=strict)
        with patch.object(Path, 'resolve', resolve), \
             patch.object(Path, 'stat', return_value=SimpleNamespace(st_mode=stat.S_IFBLK)), \
             patch.object(storage, 'run', return_value=SimpleNamespace(stdout=json.dumps(tree))):
            with self.assertRaisesRegex(ValueError, 'another mount'):
                storage.inspect(config, True)

    def test_independent_engine_cannot_format_data_disks(self):
        with self.assertRaises(ValueError):
            storage.inspect({'role': 'engine', 'disks': [{'by_id': '/dev/disk/by-id/test', 'mount': '/storage/test'}]}, True)

    def test_invalid_engine_access_is_rejected_before_disk_inspection(self):
        for key, address in [('ssh-rsa AAAA\ncommand=bad', '172.31.231.10'),
                             ('ssh-dss AAAA', '172.31.231.10'),
                             ('ssh-rsa %%%%', '172.31.231.10'),
                             ('ssh-rsa AAAA', '172.31.231.0/24')]:
            with self.subTest(key=key, address=address), self.assertRaises(ValueError):
                storage.inspect({'role': 'host', 'engine_public_key': key,
                                 'engine_address': address, 'disks': []}, False)


if __name__ == '__main__':
    unittest.main()
