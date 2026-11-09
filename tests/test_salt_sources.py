import hashlib
import io
import json
from pathlib import Path
import shutil
import tarfile
import tempfile
import unittest
from unittest.mock import patch

from producer.salt_sources import installed_distributions, notice_members, retain, seed_sdk, pin_build, validate_cargo_sources, validate_bootstrap_python_sources
from producer.sources import sha256


class SaltSourceTests(unittest.TestCase):
    def test_scoped_source_export_replaces_raw_archive_and_requires_the_exact_export_hash(self):
        from tests.test_charset_source import CharsetSourceExportTests
        from producer.charset_source import write_export
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            original,members,policy=CharsetSourceExportTests().fixture(root)
            guest=root/'guest';(guest/'etc').mkdir(parents=True)
            metadata=guest/'opt/saltstack/salt/lib/python3.11/site-packages/charset_normalizer-3.2.0.dist-info/METADATA'
            metadata.parent.mkdir(parents=True);metadata.write_text('Name: charset-normalizer\nVersion: 3.2.0\n')
            digest=sha256(original)
            with patch('producer.charset_source.ORIGINAL',digest):
                policy['sha256']=write_export(original,root/'expected.tar.gz',policy)
                record={'name':'pypi:charset-normalizer','version':'3.2.0','url':'https://example.org/source.tar.gz',
                        'sha256':digest,'source_export':policy,'notices':[{'member':'charset-normalizer-3.2.0/LICENSE',
                        'sha256':hashlib.sha256(members['charset-normalizer-3.2.0/LICENSE']).hexdigest()}]}
                lock={'schema_version':1,'installed_distributions':{'charset-normalizer':'3.2.0'},'components':[record]}
                lock_path=root/'lock.json';lock_path.write_text(json.dumps(lock))
                with patch('producer.salt_sources.download',side_effect=lambda _,p:shutil.copyfile(original,p)):
                    records,children=retain(guest,root/'bundle.tar.gz',root/'work',lock_path)
                    self.assertEqual(children[0]['source_identity'],policy['sha256'])
                    self.assertEqual(children[0]['sources'][0]['sha256'],policy['sha256'])
                    with tarfile.open(root/'bundle.tar.gz') as bundle:
                        self.assertNotIn('salt-sdk/inputs/'+digest+'.source',bundle.getnames())
                        exported=bundle.extractfile(children[0]['sources'][0]['member']).read()
                        self.assertEqual(hashlib.sha256(exported).hexdigest(),policy['sha256'])
                    policy['sha256']='0'*64;lock_path.write_text(json.dumps(lock))
                    with self.assertRaisesRegex(ValueError,'export hash differs'):
                        retain(guest,root/'bad.tar.gz',root/'bad-work',lock_path)

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

    def test_embedded_sdk_notice_keeps_exact_header_and_checks_the_entire_source(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = root / 'source.tar.gz'
            header = b'/* Copyright upstream. Apache-2.0. */\n'
            content = header + b'int original_code;\n'
            with tarfile.open(archive, 'w:gz') as target:
                member = tarfile.TarInfo('source/header.h')
                member.size = len(content)
                target.addfile(member, io.BytesIO(content))
            declaration = {'member': member.name, 'sha256': hashlib.sha256(content).hexdigest(),
                           'prefix_bytes': len(header),
                           'notice_sha256': hashlib.sha256(header).hexdigest()}
            self.assertEqual(notice_members(archive, [declaration]), {member.name: header})
            for field, value in [('sha256', '0' * 64), ('notice_sha256', '0' * 64),
                                 ('prefix_bytes', len(content) + 1)]:
                changed = {**declaration, field: value}
                with self.assertRaises(ValueError): notice_members(archive, [changed])

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

    def test_rust_sources_are_bound_to_the_actual_parent_cargo_lock(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);content=('version = 3\n[[package]]\nname = "native-dependency"\n'
                'version = "1.2.3"\nsource = "registry+https://github.com/rust-lang/crates.io-index"\n'
                'checksum = "'+'a'*64+'"\n').encode()
            archive=root/'original.tar.gz'
            with tarfile.open(archive,'w:gz') as source:
                member=tarfile.TarInfo('parent/Cargo.lock');member.size=len(content)
                source.addfile(member,io.BytesIO(content))
            digest=sha256(archive);archive.rename(root/(digest+'.source'))
            lock={'cargo_parent':{'component':'pypi:parent','source_sha256':digest,
                                 'member':member.name,'lock_sha256':hashlib.sha256(content).hexdigest()},
                  'components':[{'name':'pypi:parent','sha256':digest},
                                {'name':'cargo:native-dependency','version':'1.2.3','sha256':'a'*64}]}
            validate_cargo_sources(lock,root)
            lock['components'][1]['sha256']='b'*64
            with self.assertRaisesRegex(ValueError,'coverage differs'):
                validate_cargo_sources(lock,root)

    def test_actual_rust_compiler_and_debian_source_drift_stop_retention(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            guest, _, lock, _ = self.fixture(root)
            lock['rust_compiler'] = {'version_output': 'rustc 1.63.0',
                                     'source_package': 'rustc',
                                     'source_version': '1.63.0+dfsg1-2'}
            lock_path = root / 'lock.json'
            lock_path.write_text(json.dumps(lock))
            for responses in [('rustc 1.64.0', 'rustc\t1.63.0+dfsg1-2'),
                              ('rustc 1.63.0', 'rustc\t1.63.0+dfsg1-3')]:
                with patch('producer.salt_sources.subprocess.check_output', side_effect=responses), \
                        patch('producer.salt_sources.download') as download:
                    with self.assertRaisesRegex(ValueError, 'compiler/source differs'):
                        retain(guest, root / 'result.tar.gz', root / 'work', lock_path)
                    download.assert_not_called()

    def test_supplemental_source_and_patch_hashes_are_part_of_one_component_identity(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);guest,original,lock,_=self.fixture(root)
            patch_archive=root/'patches.tar.gz';content=b'Original patch copyright and grant.\n'
            with tarfile.open(patch_archive,'w:gz') as source:
                member=tarfile.TarInfo('debian/copyright');member.size=len(content)
                source.addfile(member,io.BytesIO(content))
            extra={'url':'https://example.org/patches.tar.gz','sha256':sha256(patch_archive),
                   'notices':[{'member':member.name,'sha256':hashlib.sha256(content).hexdigest()}]}
            lock['components'][0]['extra_sources']=[extra]
            lock_path=root/'lock.json';lock_path.write_text(json.dumps(lock))
            def fetch(url,destination):
                shutil.copyfile(patch_archive if url==extra['url'] else original,destination)
            with patch('producer.salt_sources.download',side_effect=fetch):
                _,children=retain(guest,root/'result.tar.gz',root/'work',lock_path)
            self.assertEqual(len(children[0]['sources']),2)
            self.assertNotEqual(children[0]['source_identity'],sha256(original))
            self.assertIn(content,children[0]['notice'].read_bytes())
            patch_archive.write_bytes(b'changed patch')
            with patch('producer.salt_sources.download',side_effect=fetch):
                with self.assertRaisesRegex(ValueError,'Supplemental SDK source SHA256 mismatch'):
                    retain(guest,root/'bad.tar.gz',root/'bad-work',lock_path)


class BootstrapPythonBindingTests(unittest.TestCase):
    def test_same_version_does_not_hide_changed_added_or_linked_sdk_python_files(self):
        for variant in ['matching', 'changed', 'added', 'linked']:
            with self.subTest(variant=variant), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                guest = root / 'guest'
                (guest / 'etc').mkdir(parents=True)
                package = guest / 'opt/saltstack/salt/lib/python3.11/site-packages/sample'
                package.mkdir(parents=True)
                content = b'# Original package code.\nvalue = 1\n'
                installed = package / '__init__.py'
                installed.write_bytes(content)
                (package / 'subpackage').mkdir()
                (package / 'subpackage/__init__.py').write_bytes(b'')
                archive = root / 'source.tar.gz'
                with tarfile.open(archive, 'w:gz') as target:
                    member = tarfile.TarInfo('sample-1.0/src/sample/__init__.py')
                    member.size = len(content)
                    target.addfile(member, io.BytesIO(content))
                    empty = tarfile.TarInfo('sample-1.0/src/sample/subpackage/__init__.py')
                    empty.size = 0
                    target.addfile(empty, io.BytesIO(b''))
                digest = sha256(archive)
                archive.rename(root / (digest + '.source'))
                lock = {'components': [{'name': 'pypi:sample', 'sha256': digest}],
                        'bootstrap_python_sources': [{'component': 'pypi:sample',
                            'source_prefix': 'sample-1.0/src/sample/',
                            'installed_prefix': str(package.relative_to(guest))}]}
                if variant == 'changed': installed.write_bytes(b'value = 2\n')
                if variant == 'added': (package / 'extra.py').write_bytes(b'extra = True\n')
                if variant == 'linked':
                    installed.rename(package / 'original.py')
                    installed.symlink_to('original.py')
                if variant == 'matching': validate_bootstrap_python_sources(guest, lock, root)
                else:
                    with self.assertRaises(ValueError):
                        validate_bootstrap_python_sources(guest, lock, root)
