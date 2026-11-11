"""Embedded source notices are explicit inputs, not inferred license grants."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from producer.embedded_notices import retain


class EmbeddedNoticeTests(unittest.TestCase):
    def fixture(self, root):
        source = root / 'source'
        source.mkdir()
        code = source / 'source.c'
        header = b'/* SPDX-License-Identifier: MIT; Copyright upstream */\n'
        code.write_bytes(header + b'int sample;\n')
        common = root / 'common'
        common.mkdir()
        legal = common / 'MIT'
        legal.write_bytes(b'Complete fixture permission and disclaimer.\n')
        digest = lambda value: hashlib.sha256(value).hexdigest()
        lock = {'stream': {'sha256': 'a' * 64}, 'embedded_legal_notices': {'sample': {
            'stream_sha256': 'a' * 64, 'files': [{'path': 'source.c',
                'sha256': digest(code.read_bytes()), 'prefix_bytes': len(header),
                'notice_sha256': digest(header)}],
            'full_licenses': [{'source': 'common:MIT', 'sha256': digest(legal.read_bytes())}]}}}
        path = root / 'lock.json'
        path.write_text(json.dumps(lock))
        return source, code, header, common, legal, path, lock

    def test_original_header_and_full_license_are_preserved_without_code_or_relicensing(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, code, header, common, legal, lock, _ = self.fixture(root)
            original = code.read_bytes()
            notice = retain(source, 'sample', lock, common)
            self.assertIn(header, notice.read_bytes())
            self.assertIn(legal.read_bytes(), notice.read_bytes())
            self.assertNotIn(b'int sample;', notice.read_bytes())
            self.assertEqual(code.read_bytes(), original)
            self.assertIn(b'does not grant a license', notice.read_bytes())
            with self.assertRaises(FileExistsError):
                retain(source, 'sample', lock, common)

    def test_changed_source_or_full_license_cannot_inherit_a_notice_identity(self):
        for victim in ['source', 'license']:
            with self.subTest(victim=victim), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                source, code, _, common, legal, lock, _ = self.fixture(root)
                (code if victim == 'source' else legal).write_bytes(b'changed')
                with self.assertRaises(ValueError):
                    retain(source, 'sample', lock, common)
                self.assertFalse((source / 'NOTICE.shirube-upstream').exists())

    def test_baseline_path_links_and_notice_extent_drift_fail_before_publication(self):
        for variant in ['baseline', 'path', 'link', 'extent', 'notice_hash']:
            with self.subTest(variant=variant), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                source, code, _, common, _, path, lock = self.fixture(root)
                record = lock['embedded_legal_notices']['sample']['files'][0]
                if variant == 'baseline': lock['stream']['sha256'] = 'b' * 64
                if variant == 'path': record['path'] = '../source.c'
                if variant == 'extent': record['prefix_bytes'] = code.stat().st_size + 1
                if variant == 'notice_hash': record['notice_sha256'] = '0' * 64
                if variant == 'link':
                    original = source / 'original.c'
                    code.rename(original)
                    code.symlink_to(original.name)
                path.write_text(json.dumps(lock))
                with self.assertRaises(ValueError):
                    retain(source, 'sample', path, common)
                self.assertFalse((source / 'NOTICE.shirube-upstream').exists())
