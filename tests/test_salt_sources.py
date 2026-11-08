import hashlib
import io
import json
from pathlib import Path
import shutil
import tarfile
import tempfile
import unittest
from unittest.mock import patch

from producer.salt_sources import installed_distributions, notice_members, retain, seed_sdk, pin_build
from producer.sources import sha256


class SaltSourceTests(unittest.TestCase):
    def fixture(self, root):
        guest = root / 'guest'
        (guest / 'etc').mkdir(parents=True)
        site = guest / 'opt/saltstack/salt/lib/python3.11/site-packages'
        metadata = site / 'sample-1.2.dist-info/METADATA'
        metadata.parent.mkdir(parents=True)
        metadata.write_text('Name: sample\nVersion: 1.2\n')
        archive = root / 'sample.tar.gz'
        content = b'Copyright sample contributors. Permission to redistribute.\n'
        with tarfile.open(archive, 'w:gz') as output:
            info = tarfile.TarInfo('sample-1.2/LICENSE')
            info.size = len(content)
            output.addfile(info, io.BytesIO(content))
        lock = {'schema_version': 1, 'installed_distributions': {'sample': '1.2'},
                'components': [{'name': 'pypi:sample', 'version': '1.2',
                                'url': 'https://example.org/sample.tar.gz',
                                'sha256': sha256(archive),
                                'notices': [{'member': info.name,
                                             'sha256': hashlib.sha256(content).hexdigest()}]}]}
        return guest, archive, lock, metadata

    def test_retained_archive_and_notice_bind_actual_installed_version(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            guest, original, lock, metadata = self.fixture(root)
            lock_path = root / 'lock.json'
            lock_path.write_text(json.dumps(lock))
            def fetch(url, destination):
                self.assertEqual(url, lock['components'][0]['url'])
                shutil.copyfile(original, destination)
            with patch('producer.salt_sources.download', side_effect=fetch):
                records, children = retain(guest, root / 'result.tar.gz', root / 'work', lock_path)
            self.assertEqual(children[0]['sources'][0]['sha256'], sha256(original))
            with tarfile.open(root / 'result.tar.gz') as output:
                notice = output.extractfile(children[0]['license_files'][0]['member']).read()
                self.assertIn(b'Permission to redistribute.', notice)
                self.assertIn(b'Original notice: sample-1.2/LICENSE', notice)
            self.assertEqual(len(records['build_inputs']), 1)
            metadata.write_text('Name: sample\nVersion: 1.3\n')
            with self.assertRaisesRegex(ValueError, 'distributions differ'):
                retain(guest, root / 'changed.tar.gz', root / 'changed', lock_path)

    def test_missing_dependency_and_mismatched_source_version_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            guest, original, lock, _ = self.fixture(root)
            lock_path = root / 'lock.json'
            lock['components'][0]['version'] = '0.9'
            lock_path.write_text(json.dumps(lock))
            with self.assertRaisesRegex(ValueError, 'source version differs'):
                retain(guest, root / 'bad.tar.gz', root / 'work', lock_path)
            lock['components'] = []
            lock_path.write_text(json.dumps(lock))
            with self.assertRaisesRegex(ValueError, 'coverage is incomplete'):
                retain(guest, root / 'empty.tar.gz', root / 'empty-work', lock_path)

    def test_new_or_changed_bytes_do_not_inherit_source_or_notice_approval(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            guest, original, lock, _ = self.fixture(root)
            declaration = lock['components'][0]['notices']
            declaration[0]['sha256'] = '0' * 64
            with self.assertRaisesRegex(ValueError, 'notice hash differs'):
                notice_members(original, declaration)
            lock_path = root / 'lock.json'
            lock_path.write_text(json.dumps(lock))
            def fetch(url, destination):
                Path(destination).write_bytes(b'changed public source')
            with patch('producer.salt_sources.download', side_effect=fetch):
                with self.assertRaisesRegex(ValueError, 'source SHA256 mismatch'):
                    retain(guest, root / 'changed.tar.gz', root / 'work', lock_path)

    def test_notice_symlinks_and_duplicate_members_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _, _, lock, _ = self.fixture(root)
            for variant in ('symlink', 'duplicate'):
                archive = root / (variant + '.tar.gz')
                with tarfile.open(archive, 'w:gz') as output:
                    info = tarfile.TarInfo('sample-1.2/LICENSE')
                    if variant == 'symlink':
                        info.type = tarfile.SYMTYPE
                        info.linkname = '/etc/shadow'
                        output.addfile(info)
                    else:
                        for _ in range(2):
                            content = b'notice'
                            info.size = len(content)
                            output.addfile(info, io.BytesIO(content))
                declaration = [{'member': info.name, 'sha256': hashlib.sha256(b'notice').hexdigest()}]
                with self.assertRaisesRegex(ValueError, 'Invalid or duplicate'):
                    notice_members(archive, declaration)

    def test_duplicate_installed_metadata_is_not_silently_collapsed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            guest, _, _, metadata = self.fixture(root)
            duplicate = metadata.parent.with_name('other-1.2.dist-info') / 'METADATA'
            duplicate.parent.mkdir()
            duplicate.write_bytes(metadata.read_bytes())
            with self.assertRaisesRegex(ValueError, 'duplicate Salt SDK'):
                installed_distributions(guest)

    def test_sdk_architecture_hash_and_existing_cache_are_checked_without_fallback(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            lock = {'python': '3.11.14', 'relenv': '0.22.3', 'sdk_assets': []}
            for arch, triplet in [('amd64', 'x86_64-linux-gnu'), ('arm64', 'aarch64-linux-gnu')]:
                content = ('architecture-specific ' + arch).encode()
                name = '3.11.14-' + triplet + '.tar.xz'
                lock['sdk_assets'].append({'name': name, 'size': len(content),
                    'digest': 'sha256:' + hashlib.sha256(content).hexdigest(),
                    'url': 'https://github.com/saltstack/relenv/releases/download/v0.22.3/' + name})
            lock_path = root / 'lock.json'
            lock_path.write_text(json.dumps(lock))
            def fetch(url, destination):
                arch = 'amd64' if 'x86_64' in url else 'arm64'
                Path(destination).write_bytes(('architecture-specific ' + arch).encode())
            with patch('producer.salt_sources.download', side_effect=fetch) as download:
                for arch in ('amd64', 'arm64'):
                    destination = seed_sdk(arch, root / arch, lock_path)
                    self.assertIn(arch.encode(), destination.read_bytes())
                    seed_sdk(arch, root / arch, lock_path)
                    destination.write_bytes(b'changed SDK archive')
                    with self.assertRaisesRegex(ValueError, 'asset hash/size mismatch'):
                        seed_sdk(arch, root / arch, lock_path)
                self.assertEqual(download.call_count, 2)
                cache=root/'cache';cache.mkdir()
                (cache/lock['sdk_assets'][0]['name']).write_bytes(b'changed cached SDK')
                with self.assertRaisesRegex(ValueError,'asset hash/size mismatch'):
                    seed_sdk('amd64',root/'bad-cache',lock_path,cache)
                self.assertEqual(download.call_count,2)
            with self.assertRaisesRegex(ValueError, 'Unsupported'):
                seed_sdk('riscv64', root, lock_path)

    def test_patch_preserves_upstream_features_and_fails_on_recipe_drift(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'pkg/debian').mkdir(parents=True)
            (root / 'requirements').mkdir()
            rules = root / 'pkg/debian/rules'
            rules.write_text('#!/usr/bin/make -f\n'
                             '\tbuild/onedir/venv/bin/relenv fetch --python=$${SALT_PYTHON_VERSION}\n'
                             '\tbuild/onedir/venv/bin/tools pkg build salt-onedir\n')
            constraints = root / 'requirements/constraints.txt'
            constraints.write_text('pip==25.2\n')
            lock = root / 'lock.json'
            lock.write_text(json.dumps({'installed_distributions': {
                'salt': '3006.21', 'pip': '25.2', 'certifi': '2024.7.4'}}))
            pin_build(root, 'arm64', lock)
            self.assertIn('--architecture arm64', rules.read_text())
            self.assertIn('tools pkg build salt-onedir', rules.read_text())
            self.assertIn('ppbt==0.1.8', constraints.read_text())
            self.assertNotIn('salt==', constraints.read_text())
            self.assertNotIn('certifi==', constraints.read_text())
            with self.assertRaisesRegex(ValueError, 'recipe differs'):
                pin_build(root, 'arm64', lock)
