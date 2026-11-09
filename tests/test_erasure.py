"""Fail-closed boundaries for offline erasure; real disk trials are separate."""
import unittest
from unittest.mock import Mock, call

from producer.erasure import erase_ext4_free_space


class ErasureTests(unittest.TestCase):
    def appliance(self):
        guest = Mock()
        guest.mountpoints.return_value = {}
        guest.vfs_type.return_value = 'ext4'
        guest.tune2fs_l.return_value = {'Filesystem features': 'has_journal extent'}
        return guest

    def test_mounted_wrong_filesystem_and_inline_data_abort_before_mutation(self):
        for setting in ['mounted', 'filesystem', 'inline']:
            with self.subTest(setting=setting):
                guest = self.appliance()
                if setting == 'mounted':
                    guest.mountpoints.return_value = {'/dev/sda3': '/'}
                elif setting == 'filesystem':
                    guest.vfs_type.return_value = 'xfs'
                else:
                    guest.tune2fs_l.return_value = {'Filesystem features': 'inline_data'}
                with self.assertRaises(ValueError):
                    erase_ext4_free_space(guest)
                guest.debug.assert_not_called()
                guest.zerofree.assert_not_called()
                guest.mount.assert_not_called()

    def test_missing_feature_aborts_before_journal_changes(self):
        guest = self.appliance()
        guest.available.side_effect = RuntimeError('missing optional appliance feature')
        with self.assertRaises(RuntimeError):
            erase_ext4_free_space(guest)
        guest.debug.assert_not_called()
        guest.zerofree.assert_not_called()

    def test_failure_does_not_fall_back_or_continue_to_success_steps(self):
        for operation in ['debug', 'zerofree', 'fstrim']:
            with self.subTest(operation=operation):
                guest = self.appliance()
                getattr(guest, operation).side_effect = RuntimeError('erasure failed')
                with self.assertRaises(RuntimeError):
                    erase_ext4_free_space(guest)
                guest.zero_free_space.assert_not_called()
                if operation in ['debug', 'zerofree']:
                    guest.mount.assert_not_called()
                self.assertNotIn(call.debug('sh', ['tune2fs -j /dev/sda3']),
                                 guest.mock_calls)

    def test_erasure_runs_unmounted_before_trim_and_journal_recreation(self):
        guest = self.appliance()
        erase_ext4_free_space(guest)
        self.assertEqual(guest.mock_calls, [
            call.mountpoints(), call.vfs_type('/dev/sda3'),
            call.tune2fs_l('/dev/sda3'), call.available(['zerofree', 'fstrim']),
            call.debug('sh', ['e2fsck -fy /dev/sda3']),
            call.debug('sh', ['tune2fs -O ^has_journal /dev/sda3']),
            call.zerofree('/dev/sda3'), call.mount('/dev/sda3', '/'),
            call.fstrim('/'), call.umount_all(),
            call.debug('sh', ['tune2fs -j /dev/sda3']),
            call.debug('sh', ['e2fsck -fy /dev/sda3']),
        ])
