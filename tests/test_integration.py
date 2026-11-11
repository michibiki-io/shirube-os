"""Offline integration policy checks; no downloaded-byte or guest acceptance claim."""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from producer import integration
from producer.collect import approval_for, approval_for_notice

ROOT = Path(__file__).resolve().parents[1]


class IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.lock = json.loads((ROOT/'source-lock.json').read_text())
        self.approvals = json.loads((ROOT/'license-audit.json').read_text())['components']
        self.records = {key: value for key, value in self.approvals.items()
                        if key.startswith('shirube-integration@')}
        self.digest = '7638692ac8052c3b725ba53625d754da176323afae1382fd38c94dee3367e357'
        self.notice = Path('LICENSES/Module-client-original-notices/shirube-integration')/(self.digest+'.txt')
        self.identities = {'amd64': 'f65e1e073d6690d57a23833806bba9a069c9b0931feb0aac1a25ef0013c6ffe4',
                           'arm64': 'c9023518a77883781e78e42e8633035268e2140e359ef5931f49db102b5ab509'}

    def local_inputs(self, root):
        (root/'source-lock.json').write_text(json.dumps(self.lock))
        (root/'license-audit.json').write_text(json.dumps({'components': self.records}))
        (root/'LICENSE').write_bytes((ROOT/'LICENSE').read_bytes())
        for record in self.records.values():
            for notice in record['required_notice_files']:
                path = root/notice['path']
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes((ROOT/notice['path']).read_bytes())

    def test_current_inputs_generate_exact_reviewed_notice_and_both_source_identities(self):
        retained = (ROOT/self.notice).read_bytes()
        netbird = retained.split(integration.NETBIRD_MARKER)[1][:-len(integration.LINKED_NOTICES)]
        generated = integration.notice_bytes((ROOT/'LICENSE').read_bytes(), netbird)
        self.assertEqual(generated, retained)
        self.assertEqual(hashlib.sha256(generated).hexdigest(), self.digest)
        for arch, expected in self.identities.items():
            self.assertEqual(integration.source_identity(integration.provenance(
                self.lock, arch, (ROOT/'LICENSE').read_bytes())), expected)
            for version in ['2026.03.0', '2026.03.1', '2026.03.2', '2026.03.123']:
                record = approval_for_notice(self.approvals, 'shirube-integration', version,
                                             expected, ROOT/self.notice, ROOT)
                self.assertTrue(record['redistributable'])
                self.assertEqual(record['copyright_sha256'], self.digest)
        for version in ['v2026.03.1', 'v2026.03.2', 'v2026.03.123']:
            # A filesystem-only preflight must never execute external work.
            with patch('subprocess.run', side_effect=AssertionError('external work')), \
                    patch('subprocess.check_output', side_effect=AssertionError('external work')):
                with patch('producer.collect.approval_for', wraps=approval_for) as checked:
                    integration.preflight(version, ROOT)
                self.assertEqual([(call.args[2], call.args[3]) for call in checked.call_args_list],
                                 [(version[1:], identity) for identity in self.identities.values()])

    def test_versions_architecture_and_unrelated_components_remain_strict(self):
        identity = self.identities['amd64']
        for version in ['2026.3.1', '2026.03.01', 'v2026.03.1', '2026.13.0',
                        '2026.04.0', '2025.03.1', '', '2026.03.-1']:
            with self.subTest(version=version), self.assertRaises(ValueError):
                approval_for(self.approvals, 'shirube-integration', version, identity,
                             ROOT, copyright_sha256=self.digest)
        with self.assertRaises(ValueError):
            integration.provenance(self.lock, 'riscv64', b'license')
        key, record = next(iter(self.records.items()))
        unrelated = {key.replace('shirube-integration@', 'other@'): record}
        self.assertEqual(approval_for(unrelated, 'other', '2026.03.1', identity,
                                     ROOT, copyright_sha256=self.digest), {})
        # Even with the policy marker, children require their own exact version.
        unrelated = {key.replace('shirube-integration@', 'go:child@'): record}
        self.assertEqual(approval_for(unrelated, 'go:child', '2026.03.1', identity,
                                     ROOT, copyright_sha256=self.digest), {})

    def test_missing_changed_denied_and_non_opted_in_records_fail(self):
        identity = self.identities['amd64']
        for change in ['missing', 'identity', 'digest', 'policy', 'denied', 'explicit-denial',
                       'different-stream', 'malformed-approved-version', 'ambiguous']:
            records = copy.deepcopy(self.records)
            key = next(key for key in records if identity in key)
            record = records[key]
            if change == 'missing': del records[key]
            if change == 'identity': record['source_identity'] = 'a'*64
            if change == 'digest': record['copyright_sha256'] = 'b'*64
            if change == 'policy': record.pop('product_version_policy')
            if change == 'denied': record['redistributable'] = False
            if change == 'explicit-denial':
                records[key.replace('@2026.03.0', '@2026.03.1')] = {**record, 'redistributable': False}
            if change == 'different-stream':
                records[key.replace('@2026.03.0', '@2026.02.0')] = records.pop(key)
            if change == 'malformed-approved-version':
                records[key.replace('@2026.03.0', '@2026.03.00')] = records.pop(key)
            if change == 'ambiguous':
                records[key.replace('@2026.03.0', '@2026.03.2')] = copy.deepcopy(record)
            with self.subTest(change=change), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                self.local_inputs(root)
                (root/'license-audit.json').write_text(json.dumps({'components': records}))
                with self.assertRaises(ValueError): integration.preflight('v2026.03.1', root)

    def test_changed_inputs_and_missing_or_tampered_required_notices_fail(self):
        for change in ['LICENSE', 'source', 'asset-amd64', 'asset-arm64', 'geoip',
                       'retained-missing', 'retained-tampered', 'retained-symlink',
                       'required-missing', 'required-tampered', 'installed-notice']:
            with self.subTest(change=change), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                self.local_inputs(root)
                lock = copy.deepcopy(self.lock)
                if change == 'LICENSE': (root/'LICENSE').write_bytes(b'changed license')
                if change == 'source': lock['netbird']['source_sha256'] = 'a'*64
                if change.startswith('asset-'): lock['netbird']['assets'][change[6:]]['sha256'] = 'a'*64
                if change == 'geoip': lock['geoip']['notice_sha256'] = 'a'*64
                if change.startswith('retained-'):
                    path = root/self.notice
                    if change == 'retained-missing': path.unlink()
                    if change == 'retained-tampered': path.write_bytes(b'tampered')
                    if change == 'retained-symlink':
                        path.unlink(); path.symlink_to(ROOT/self.notice)
                if change.startswith('required-'):
                    path = root/'LICENSES/Zabbix-original-AGPL-3.0.txt'
                    if change == 'required-missing': path.unlink()
                    else: path.write_bytes(b'tampered')
                (root/'source-lock.json').write_text(json.dumps(lock))
                if change == 'installed-notice':
                    path = root/'installed-copyright'; path.write_bytes((ROOT/self.notice).read_bytes()+b'changed')
                    self.assertEqual(approval_for_notice(self.records, 'shirube-integration', '2026.03.1',
                                                        self.identities['amd64'], path, root), {})
                else:
                    with self.assertRaises(ValueError): integration.preflight('v2026.03.1', root)

    def test_pinned_stream_change_requires_new_approval(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); self.local_inputs(root)
            lock = copy.deepcopy(self.lock); lock['stream']['version'] = '2026.04'
            (root/'source-lock.json').write_text(json.dumps(lock))
            for version in ['v2026.03.1', 'v2026.04.0']:
                with self.subTest(version=version), self.assertRaises(ValueError):
                    integration.preflight(version, root)
