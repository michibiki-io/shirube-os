"""Restricted auxiliary assets never remove or replace runtime source bytes."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from producer.go_modules import module_hash
from producer.go_source_export import _write_export, export_source, replace_delivery_inputs
from producer.sources import sha256


class GoSourceExportTests(unittest.TestCase):
    module = 'example.test/library'
    version = 'v1.0.0'

    def original(self, root, extra=None):
        archive = root / 'original.zip'
        members = {'go.mod': b'module example.test/library\n',
                   'library.go': b'package library\nconst Answer = 42\n',
                   'LICENSE': b'Original complete fixture permission\n',
                   'assets/logo.svg': b'restricted fixture artwork'}
        members.update(extra or {})
        with zipfile.ZipFile(archive, 'x') as output:
            for relative, content in members.items():
                output.writestr(self.module + '@' + self.version + '/' + relative, content)
        return archive, members

    def policy(self, original):
        return {'upstream_sha256': sha256(original), 'exclude': ['assets/'],
                'reason': 'Exclude separately restricted fixture artwork.'}

    def test_deterministic_export_retains_every_other_original_byte(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            original, members = self.original(root)
            before = sha256(original)
            policy = self.policy(original)
            first = _write_export(original, self.module, self.version, policy, root / 'one')
            second = _write_export(original, self.module, self.version, policy, root / 'two')
            self.assertEqual(first['archive'].read_bytes(), second['archive'].read_bytes())
            self.assertEqual(sha256(original), before)
            with zipfile.ZipFile(first['archive']) as archive:
                prefix = self.module + '@' + self.version + '/'
                self.assertNotIn(prefix + 'assets/logo.svg', archive.namelist())
                for name in ['go.mod', 'library.go', 'LICENSE']:
                    self.assertEqual(archive.read(prefix + name), members[name])
                self.assertEqual(archive.read(prefix + 'SHIRUBE-SOURCE-EXPORT.json'),
                                 first['manifest'].read_bytes())
            self.assertEqual(json.loads(first['manifest'].read_text())['omitted_members'][0]['path'],
                             'assets/logo.svg')

    def test_runtime_source_inside_excluded_directory_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            original, _ = self.original(root, {'assets/runtime.go': b'package runtime\n'})
            with self.assertRaisesRegex(ValueError, 'runtime source'):
                _write_export(original, self.module, self.version, self.policy(original), root / 'out')
            self.assertFalse((root / 'out').exists())

    def test_real_entrypoint_checks_original_and_derived_pins_without_fallback(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            original, _ = self.original(root)
            policy = self.policy(original)
            baseline = _write_export(original, self.module, self.version, policy, root / 'baseline')
            policy['export_sha256'] = sha256(baseline['archive'])
            with patch('producer.go_source_export.POLICIES', {(self.module, self.version): policy}):
                self.assertIsNone(export_source(original, 'other.test/module', self.version, root / 'other'))
                policy['export_sha256'] = '0' * 64
                with self.assertRaisesRegex(ValueError, 'verified output'):
                    export_source(original, self.module, self.version, root / 'wrong-export')
                original.write_bytes(b'changed original')
                with self.assertRaisesRegex(ValueError, 'pinned policy'):
                    export_source(original, self.module, self.version, root / 'wrong-input')

    def test_delivery_replacement_keeps_original_binding_and_mandatory_manifest(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            original, _ = self.original(root)
            policy = self.policy(original)
            baseline = _write_export(original, self.module, self.version, policy, root / 'baseline')
            policy['export_sha256'] = sha256(baseline['archive'])
            original_sum = module_hash(original, self.module, self.version)
            child = {'name': 'go:' + self.module, 'version': self.version,
                     'source_identity': original_sum, 'source_sha256': sha256(original),
                     'source_member': 'inputs/library.zip'}
            inputs = [(original, child['source_member'], 'sources')]
            with patch('producer.go_source_export.POLICIES', {(self.module, self.version): policy}):
                replace_delivery_inputs(inputs, [child], root / 'delivery')
            self.assertEqual(child['source_identity'], policy['export_sha256'])
            self.assertEqual(child['source_export']['original_module_h1'], original_sum)
            self.assertNotIn(original, [item[0] for item in inputs])
            self.assertEqual(child['source_member'], 'inputs/library.preferred-source.zip')
            self.assertNotIn('inputs/library.zip', [item[1] for item in inputs])
            self.assertEqual(inputs[1][2], 'build_inputs')
            self.assertEqual(child['build_input_members'][0]['sha256'], sha256(inputs[1][0]))
            self.assertEqual(sha256(original), policy['upstream_sha256'])

    def test_unsafe_archive_and_missing_auxiliary_scope_fail_before_output(self):
        for extra, exclusions in [({'../outside': b'bad'}, ['assets/']), ({}, ['missing/'])]:
            with self.subTest(extra=extra, exclusions=exclusions), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                original, _ = self.original(root, extra)
                policy = self.policy(original)
                policy['exclude'] = exclusions
                with self.assertRaises(ValueError):
                    _write_export(original, self.module, self.version, policy, root / 'out')
                self.assertFalse((root / 'out').exists())
