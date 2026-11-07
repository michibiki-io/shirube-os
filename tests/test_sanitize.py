from pathlib import Path
import tempfile
import unittest
from producer.sanitize import sanitize, audit, path_in_root


class SanitizeTests(unittest.TestCase):
    def test_erases_credentials_identity_and_history(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for relative in ['etc/ssh', 'config/netbird', 'config/archive', 'home/test', 'var/log', 'root/.ssh']:
                (root/relative).mkdir(parents=True)
                (root/relative/'state').write_text('private-instance-data')
            (root/'etc/shadow').write_text('root:$6$reusable:1:0:99999:7:::\nvyos:$6$test:1:0:99999:7:::\n')
            (root/'etc/machine-id').write_text('fixed-machine-id')
            (root/'etc/ssh/ssh_host_rsa_key').write_text('private-key')
            result = sanitize(root, 'arm64')
            self.assertEqual(result['boot_validation'], 'NOT_RUN')
            self.assertIn('ttyAMA0', (root/'config/config.boot').read_text())
            self.assertFalse((root/'config/netbird').exists())
            self.assertFalse((root/'home/test').exists())
            self.assertEqual((root/'etc/shadow').read_text().split(':')[1], '!')
            audit(root)
            (root/'etc/machine-id').write_text('fixed-again')
            with self.assertRaises(ValueError):
                audit(root)

    def test_never_follows_offline_root_symlinks_outside_guest(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); (root/'etc').mkdir()
            (root/'config').symlink_to('/tmp')
            with self.assertRaises(ValueError):
                path_in_root(root, 'config/config.boot')
            with self.assertRaises(ValueError):
                path_in_root('/', 'etc/shadow')
