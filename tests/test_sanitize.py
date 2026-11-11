from pathlib import Path
import tempfile
import unittest
from producer.sanitize import sanitize, audit, path_in_root


class SanitizeTests(unittest.TestCase):
    def test_recreates_only_empty_audit_log_directory_and_rejects_any_log_data(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            (root/'etc/audit').mkdir(parents=True)
            (root/'etc/audit/auditd.conf').write_text('log_file = /var/log/audit/audit.log\n')
            (root/'etc/shadow').write_text('root:!:1:0:99999:7:::\n')
            (root/'var/log/audit').mkdir(parents=True)
            (root/'var/log/audit/audit.log').write_text('old-instance-data')
            sanitize(root,'amd64')
            directory=root/'var/log/audit'
            self.assertTrue(directory.is_dir())
            self.assertEqual(directory.stat().st_mode & 0o777,0o700)
            self.assertEqual(list(directory.iterdir()),[])
            audit(root)
            (directory/'audit.log').write_text('new-instance-data')
            with self.assertRaisesRegex(ValueError,'log content'):audit(root)

    def test_erases_credentials_identity_and_history(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for relative in ['etc/ssh', 'config/netbird', 'config/archive', 'home/test', 'var/log', 'root/.ssh', 'opt/vyatta/etc/config/netbird', 'opt/vyatta/etc/config/auth', 'tmp/shirube-export', 'var/tmp/config-cache', 'etc/dropbear/initramfs']:
                (root/relative).mkdir(parents=True)
                (root/relative/'state').write_text('private-instance-data')
            cloud_paths=['var/lib/cloud/instances/test','config/cloud/instances/test',
                         'opt/vyatta/etc/config/cloud/instances/test']
            for relative in cloud_paths:
                (root/relative).mkdir(parents=True)
                (root/relative/'user-data.txt').write_text('instance-specific-initialization-data')
            (root/'etc/shadow').write_text('root:$6$reusable:1:0:99999:7:::\nvyos:$6$test:1:0:99999:7:::\n')
            (root/'etc/machine-id').write_text('fixed-machine-id')
            (root/'etc/hostname').write_text('build-host-fixture\n')
            (root/'etc/hosts').write_text('127.0.1.1 build-host-fixture\n')
            (root/'etc/ssh/ssh_host_rsa_key').write_text('private-key')
            native=root/'opt/vyatta/etc/config'
            (native/'config.boot').write_text('saved-private-config')
            (native/'shirube-enrolled').write_text('private-instance-admin')
            for backup in ['shadow-','gshadow-','passwd-','group-']:
                (root/'etc'/backup).write_text('private-account-backup')
            dropbear=root/'etc/dropbear/initramfs/dropbear_rsa_host_key'
            dropbear.write_bytes(b'binary-private-key')
            result = sanitize(root, 'arm64')
            self.assertEqual(result['boot_validation'], 'NOT_RUN')
            self.assertIn('ttyAMA0', (root/'config/config.boot').read_text())
            self.assertFalse((root/'config/netbird').exists())
            self.assertTrue(all(not (root/relative).exists() for relative in cloud_paths))
            self.assertFalse(dropbear.exists())
            self.assertFalse((root/'home/test').exists())
            self.assertEqual((native/'config.boot').read_text(),(root/'config/config.boot').read_text())
            self.assertEqual({p.name for p in native.iterdir()},{'config.boot','shirube-first-boot'})
            self.assertFalse(any((root/'etc'/name).exists() for name in ['shadow-','gshadow-','passwd-','group-']))
            self.assertFalse(any((root/'tmp').iterdir()))
            self.assertFalse(any((root/'var/tmp').iterdir()))
            self.assertEqual((root/'etc/shadow').read_text().split(':')[1], '!')
            self.assertEqual((root/'etc/hostname').read_text(),'shirube-os\n')
            self.assertNotIn('build-host-fixture',(root/'etc/hosts').read_text())
            audit(root)
            (root/'etc/machine-id').write_text('fixed-again')
            with self.assertRaises(ValueError):
                audit(root)

    def test_never_follows_offline_root_symlinks_outside_guest(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); (root/'etc').mkdir()
            (root/'config').symlink_to('/tmp')
            self.assertEqual(path_in_root(root, 'config/config.boot'), root/'tmp/config.boot')
            with self.assertRaises(ValueError):
                path_in_root('/', 'etc/shadow')
