"""Factory content failures must block publication before boot/source claims."""
from pathlib import Path
import tempfile
import unittest
from producer.factory_audit import inspect_filesystems
from producer.sanitize import HOSTS


class FactoryAuditTests(unittest.TestCase):
    def test_reusable_identity_password_and_initramfs_keys_are_rejected(self):
        for mutation in ['none', 'machine-id', 'password', 'initramfs-key', 'missing-init', 'completed-enrollment', 'initramfs-dropbear-key', 'build-hostname', 'build-hosts','enrollment-not-executable']:
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)/'root'
                initrd = Path(temporary)/'initramfs'
                for directory in ['etc/ssh', 'var/lib/dpkg/info', 'usr/share/shirube']:
                    (root/directory).mkdir(parents=True, exist_ok=True)
                initrd.mkdir()
                (initrd/'init').write_text('#!/bin/sh\n')
                (root/'etc/machine-id').write_text('')
                (root/'etc/hostname').write_text('shirube-os\n')
                (root/'etc/hosts').write_text(HOSTS)
                (root/'etc/shadow').write_text('root:!:20000:0:99999:7:::\n')
                (root/'usr/share/shirube/needs-enrollment').write_text('pending\n')
                enrollment=root/'usr/libexec/shirube/first-boot'
                enrollment.parent.mkdir(parents=True)
                enrollment.write_text('#!/usr/bin/python3\n')
                enrollment.chmod(0o644 if mutation=='enrollment-not-executable' else 0o755)
                if mutation == 'build-hostname':
                    (root/'etc/hostname').write_text('build-host-fixture\n')
                elif mutation == 'build-hosts':
                    (root/'etc/hosts').write_text('127.0.1.1 build-host-fixture\n')
                elif mutation == 'machine-id':
                    (root/'etc/machine-id').write_text('fixed-identity')
                elif mutation == 'password':
                    (root/'etc/shadow').write_text('root:$6$test$credential:20000:0:99999:7:::\n')
                elif mutation == 'initramfs-key':
                    (initrd/'unexpected-key').write_bytes(b'-----BEGIN PRIVATE KEY-----\n'+b'A'*64+b'\n-----END PRIVATE KEY-----')
                elif mutation == 'completed-enrollment':
                    state=root/'opt/vyatta/etc/config/shirube-enrolled'
                    state.parent.mkdir(parents=True);state.write_text('instanceadmin\n')
                elif mutation == 'initramfs-dropbear-key':
                    key=initrd/'etc/dropbear/initramfs/dropbear_rsa_host_key'
                    key.parent.mkdir(parents=True);key.write_bytes(b'binary-host-key')
                elif mutation == 'missing-init':
                    (initrd/'init').unlink()
                if mutation == 'none':
                    self.assertEqual(inspect_filesystems(root, initrd, 'amd64')['status'], 'PASS')
                else:
                    with self.assertRaises(ValueError):
                        inspect_filesystems(root, initrd, 'amd64')
