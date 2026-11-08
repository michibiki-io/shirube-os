"""Fixed Cargo cache integrity and SDK registry isolation."""
import hashlib
import io
import json
import os
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch

from producer.salt_cargo import prepare, patch_sdk_environment
from producer.sources import sha256


class SaltCargoTests(unittest.TestCase):
    def fixture(self, root, variant=None):
        cache = root / 'cache'
        cache.mkdir()
        archive = cache / 'sample-1.0.crate'
        with tarfile.open(archive, 'w:gz') as target:
            for name, content in [('Cargo.toml', b'[package]\nname="sample"\nversion="1.0"\n'),
                                  ('src/lib.rs', b'pub fn sample() {}\n')]:
                member = tarfile.TarInfo(('other' if variant == 'root' else 'sample-1.0') + '/' + name)
                member.size = len(content)
                target.addfile(member, io.BytesIO(content))
            if variant == 'link':
                member = tarfile.TarInfo('sample-1.0/alias')
                member.type = tarfile.SYMTYPE
                member.linkname = 'src/lib.rs'
                target.addfile(member)
        record = {'name': 'cargo:sample', 'version': '1.0', 'sha256': sha256(archive),
                  'url': 'https://static.crates.io/crates/sample/sample-1.0.crate'}
        lock = root / 'lock.json'
        lock.write_text(json.dumps({'components': [record]}))
        return cache, archive, lock, record

    def test_vendor_checksums_bind_every_file_and_package_without_registry_fallback(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cache, _, lock, record = self.fixture(root)
            with patch('producer.salt_cargo.download') as download:
                home = prepare(lock, root / 'result', cache)
                download.assert_not_called()
            vendor = root / 'result/vendor/sample-1.0'
            checksums = json.loads((vendor / '.cargo-checksum.json').read_text())
            self.assertEqual(checksums['package'], record['sha256'])
            self.assertEqual(checksums['files'], {str(p.relative_to(vendor)): sha256(p)
                             for p in vendor.rglob('*') if p.is_file() and p.name != '.cargo-checksum.json'})
            self.assertIn('offline = true', (home / 'config.toml').read_text())
            self.assertIn(str(vendor.parent.resolve()), (home / 'config.toml').read_text())

    def test_corrupt_or_linked_existing_cache_is_rejected_without_download(self):
        for variant in ['corrupt', 'link', 'dangling']:
            with self.subTest(variant=variant), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                cache, archive, lock, _ = self.fixture(root)
                if variant == 'corrupt':
                    archive.write_bytes(b'changed')
                else:
                    actual = archive.with_suffix('.original')
                    archive.rename(actual)
                    archive.symlink_to(actual.name if variant == 'link' else 'missing')
                with patch('producer.salt_cargo.download') as download:
                    with self.assertRaises(ValueError):
                        prepare(lock, root / 'result', cache)
                    download.assert_not_called()

    def test_wrong_archive_root_and_source_links_cannot_enter_vendor_tree(self):
        for variant in ['root', 'link']:
            with self.subTest(variant=variant), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                cache, _, lock, _ = self.fixture(root, variant)
                with self.assertRaises(ValueError):
                    prepare(lock, root / 'result', cache)

    def test_registry_policy_changes_only_the_two_sdk_environments_and_fails_on_drift(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / 'tools/pkg/build.py'
            path.parent.mkdir(parents=True)
            original = ('import os\n'
                        'def onedir_dependencies():\n    env = os.environ.copy()\n    return env\n'
                        'def salt_onedir():\n    env = os.environ.copy()\n    return env\n'
                        'def unrelated():\n    env = os.environ.copy()\n    return env\n')
            path.write_text(original)
            with patch.dict(os.environ, {}, clear=True):
                patch_sdk_environment(root, '/private-task/cargo-home')
                namespace = {}
                exec(compile(path.read_text(), str(path), 'exec'), namespace)
                for name in ['onedir_dependencies', 'salt_onedir']:
                    self.assertEqual(namespace[name](), {'CARGO_HOME': '/private-task/cargo-home',
                                                        'CARGO_NET_OFFLINE': 'true'})
                self.assertEqual(namespace['unrelated'](), {})
            path.write_text(original.replace('def salt_onedir():', 'def changed():'))
            with self.assertRaisesRegex(ValueError, 'function differs'):
                patch_sdk_environment(root, '/private-task/cargo-home')
            self.assertNotIn('Modified by', path.read_text())
