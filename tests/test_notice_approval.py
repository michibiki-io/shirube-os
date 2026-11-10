"""Distributor file-list changes must not change an unchanged upstream grant."""
import hashlib
from pathlib import Path
import tempfile
import unittest

from producer.collect import approval_for_notice
from producer.notices import PACKAGE_MODIFICATIONS


class CoreNoticeApprovalTests(unittest.TestCase):
    def test_architecture_and_order_variations_keep_the_same_reviewed_upstream_notice(self):
        original = b'Fixture upstream copyright and complete GPL terms.\n'
        identity = 'a' * 64
        digest = hashlib.sha256(original).hexdigest()
        record = {'source_identity': identity, 'copyright_sha256': digest,
                  'license': 'GPL-2.0-or-later', 'redistributable': True,
                  'distributor_metadata_policy': 'shirube-core-modifications-v1'}
        approvals = {'vyos-1x@1.5dev0#' + identity + '#' + digest: record}
        with tempfile.TemporaryDirectory() as temporary:
            notice = Path(temporary) / 'copyright'
            notice.write_bytes(original)
            self.assertEqual(approval_for_notice(approvals, 'vyos-1x', '1.5dev0', identity, notice), record)
            for paths in [
                ['usr/libexec/vyos/op_mode/version.py', 'usr/share/vyos/templates/grub/grub.cfg.j2'],
                ['usr/share/vyos/templates/grub/grub.cfg.j2', 'usr/libexec/vyos/op_mode/version.py'],
                ['usr/share/vyos/templates/grub/arm64/grub.cfg.j2'],
            ]:
                notice.write_bytes(original + PACKAGE_MODIFICATIONS.encode()
                                   + ''.join('Modified: ' + p + '\n' for p in paths).encode())
                approved = approval_for_notice(approvals, 'vyos-1x', '1.5dev0', identity, notice)
                self.assertTrue(approved['redistributable'])
                self.assertEqual(approved['license'], record['license'])
                self.assertEqual(approved['copyright_sha256'], hashlib.sha256(notice.read_bytes()).hexdigest())
                self.assertEqual(approved['notice_derivation']['upstream_notice_sha256'], digest)
                self.assertEqual(approved['notice_derivation']['modified_files'], sorted(paths))
            self.assertEqual(record['copyright_sha256'], digest)

    def test_unknown_grants_changed_terms_and_non_generated_suffixes_still_fail(self):
        original = b'Fixture upstream copyright and complete GPL terms.\n'
        identity = 'a' * 64
        digest = hashlib.sha256(original).hexdigest()
        record = {'source_identity': identity, 'copyright_sha256': digest,
                  'license': 'GPL-2.0-or-later', 'redistributable': True,
                  'distributor_metadata_policy': 'shirube-core-modifications-v1'}
        approvals = {'vyos-1x@1.5dev0#' + identity + '#' + digest: record}
        marker = PACKAGE_MODIFICATIONS.encode()
        valid = original + marker + b'Modified: usr/share/version.py\n'
        with tempfile.TemporaryDirectory() as temporary:
            notice = Path(temporary) / 'copyright'
            for content in [
                valid.replace(b'complete GPL terms', b'different terms'),
                valid + b'Additional redistribution restrictions.\n',
                original + marker,
                original + marker + b'Modified: ../outside.py\n',
                original + marker + b'Modified: /outside.py\n',
                original + marker + b'Modified: .\n',
                valid + b'Modified: usr/share/version.py\n',
                valid.rstrip(b'\n'),
                valid.replace(b'2026-11-08', b'2026-11-09'),
                valid + marker + b'Modified: usr/share/another.py\n',
            ]:
                notice.write_bytes(content)
                self.assertEqual(approval_for_notice(approvals, 'vyos-1x', '1.5dev0', identity, notice), {})
            notice.write_bytes(valid)
            for name, version, source in [('other', '1.5dev0', identity),
                                          ('vyos-1x', 'different', identity),
                                          ('vyos-1x', '1.5dev0', 'b' * 64)]:
                self.assertEqual(approval_for_notice(approvals, name, version, source, notice), {})
            record['redistributable'] = False
            self.assertFalse(approval_for_notice(approvals, 'vyos-1x', '1.5dev0', identity, notice)['redistributable'])
            record.pop('distributor_metadata_policy')
            self.assertEqual(approval_for_notice(approvals, 'vyos-1x', '1.5dev0', identity, notice), {})

    def test_missing_required_original_notice_does_not_inherit_clearance(self):
        original = b'Fixture original copyright.\n'
        identity = 'a' * 64
        digest = hashlib.sha256(original).hexdigest()
        required = b'Complete separately retained original terms.\n'
        record = {'source_identity': identity, 'copyright_sha256': digest,
                  'license': 'GPL-2.0-or-later', 'redistributable': True,
                  'distributor_metadata_policy': 'shirube-core-modifications-v1',
                  'required_notice_files': [{'path': 'LICENSES/original.txt',
                                             'sha256': hashlib.sha256(required).hexdigest()}]}
        approvals = {'vyos-1x@1.5dev0#' + identity + '#' + digest: record}
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            notice = root / 'copyright'
            notice.write_bytes(original + PACKAGE_MODIFICATIONS.encode() + b'Modified: usr/share/version.py\n')
            self.assertEqual(approval_for_notice(approvals, 'vyos-1x', '1.5dev0', identity, notice, root), {})
            path = root / 'LICENSES/original.txt'
            path.parent.mkdir(); path.write_bytes(required)
            self.assertTrue(approval_for_notice(approvals, 'vyos-1x', '1.5dev0', identity, notice, root)['redistributable'])
            path.write_bytes(b'Incomplete terms')
            self.assertEqual(approval_for_notice(approvals, 'vyos-1x', '1.5dev0', identity, notice, root), {})
