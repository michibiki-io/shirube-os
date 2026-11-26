"""Independent signed kernel acquisition and graph-bound corresponding source."""
import base64
import copy
import hashlib
import io
import json
import lzma
from pathlib import Path
import shutil
import tarfile
import tempfile
import unittest
from unittest.mock import patch

from tests.component_fixture import component_project, ROOT
from producer import kernel_compile_cache as cache
from producer import kernel_source as kernel
from producer.component_inputs import manifest
from producer.bundles import bundle
from producer.release import verify_source_integrity
from producer.kernel_seed import key
from producer.collect import approval_for_notice
from producer.sources import sha256

LOCK = json.loads((ROOT / 'source-lock.json').read_text())
STREAM = {'archive_sha256': LOCK['stream']['sha256'],
          'nested_member': 'vyos-2026.03-sources/vyos-tarballs/vyos_tarballs.tar.gz',
          'nested_sha256': 'e' * 64}


class IdentityTests(unittest.TestCase):
    def test_stream_catalog_selection_and_abi_are_distinct(self):
        jobs = json.loads((ROOT / 'containers/native/packages.json').read_text())['jobs']
        roots, selected = kernel.source_paths(jobs, LOCK)
        self.assertIn('vyos-build', roots)
        self.assertNotIn('linux-6.6.163', roots)
        self.assertEqual(selected['source_root'], 'linux-6.6.163')
        self.assertEqual(kernel.build_environment(selected), {
            'SHIRUBE_KERNEL_VERSION': '6.6.163',
            'SHIRUBE_KERNEL_RELEASE': '6.6.163-vyos',
            'SHIRUBE_KERNEL_PACKAGE_VERSION': '6.6.163-1',
        })
        for change in ({'source': 'linux-kernel-6.6.128/linux-6.6.128'},
                       {'source_origin': 'stream'}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                kernel.source_paths([{**jobs[-1], **change}], LOCK)
        for name in ('source_root', 'kernel_release', 'package_version'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                kernel.validate({**selected, name: selected[name].replace('6.6.163', '6.6.128')})
        self.assertEqual(kernel.source_paths(jobs[:-1], {'stream': LOCK['stream']})[1], None)

    def test_kernel_source_lock_only_invalidates_kernel_scope_and_seed_both_arches(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = component_project(Path(tmp) / 'project')
            stages = ('native', 'kea', 'vpp', 'runtime', 'salt', 'kernel')
            arches = ('amd64', 'arm64')
            before = {(stage, arch): manifest(project, stage, arch)
                      for stage in stages for arch in arches}
            seed = {arch: key(project, arch) for arch in arches}
            changed = copy.deepcopy(LOCK)
            changed['kernel']['sha256'] = 'b' * 64
            (project / 'source-lock.json').write_text(json.dumps(changed))
            for arch in arches:
                self.assertNotEqual(key(project, arch), seed[arch])
                for stage in stages:
                    if stage == 'kernel':
                        self.assertNotEqual(manifest(project, stage, arch), before[stage, arch])
                        self.assertEqual(before[stage, arch]['source_values']['kernel'], LOCK['kernel'])
                    else:
                        self.assertEqual(manifest(project, stage, arch), before[stage, arch])

    def test_compiler_namespace_binds_source_even_for_identical_tree_and_tools(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = root / 'src'
            mount = root / 'mount'
            src.mkdir()
            mount.mkdir()
            (src / 'test.c').write_text('int k = 1;\n')
            first, second = root / 'v1', root / 'v2'
            first.mkdir()
            second.mkdir()
            actual = kernel.compiler_source_identity()
            with patch.object(cache, 'tools', return_value='1' * 64), patch.object(cache.subprocess, 'run'):
                with patch.object(cache, 'compiler_source_identity', return_value=actual):
                    old = cache.prepare(src, mount, 'amd64', state=first / 'state.json')
                with patch.object(cache, 'compiler_source_identity', return_value={**actual, 'sha256': 'a' * 64}):
                    new = cache.prepare(src, mount, 'amd64', state=second / 'state.json')
            self.assertEqual(old['scope'], new['scope'])
            self.assertNotEqual(old['namespace'], new['namespace'])

    def test_exact_license_tuple_both_historical_and_new(self):
        approvals = json.loads((ROOT / 'license-audit.json').read_text())['components']
        copyright_file = (ROOT / 'LICENSES/Native-original-notices/linux-upstream'
                          / 'b309c172b26dc93d33be9fb4ff6a12e6f1eb5c64e63e59a4fe7ab6691b74d112.txt')
        old = ('6.6.128-1', LOCK['stream']['sha256'])
        new = ('6.6.163-1', LOCK['kernel']['sha256'])
        for version, origin in (old, new):
            self.assertIs(approval_for_notice(
                approvals, 'linux-upstream', version, origin, copyright_file, ROOT)['redistributable'], True)
        for version, origin in ((old[0], new[1]), (new[0], old[1])):
            self.assertFalse(approval_for_notice(
                approvals, 'linux-upstream', version, origin, copyright_file, ROOT).get('redistributable', False))


# Detached signatures and public key for the deterministic tar fixtures below;
# their disposable RSA signing private key is not retained. No agent or network.
PUBLIC_KEY = (
    'xv8AAAENBGd0hYABCADSEwJfzn47lgjD/b0Oc4qXJQevBOpefS8cDa0CD4AjEJTGh+8E7VGJ4Hw19jH2/QuccujLgMiJz'
    'LLl93uDW0YlMHO5QvxeWQ6+SUUYb25/hfQhiRGAJ9BOUI1RRnUD/UpKkQbJT8gfKyQHyvXkNm4Yasv6c/8s0rukdthNt4'
    'TCEEqr7kCu0TYEFBq34UMnqdvBqXYk3Njj1umekoCfu9w3AA/LmmyIfz/1cdQCN19jQr/UfyqLhvhwGxcbYHEjvcNK9oW'
    'p9uA+wAix/BwxiOTW6NNEbLV+PZkLjfGw5HbW+QH8T5OhfsuJOwYuYPkUu8aq8lNbbl3e/a0+omc2gDfXABEBAAHN/wAA'
    'ADBTaGlydWJlIE9mZmxpbmUgRml4dHVyZSA8a2VybmVsQGV4YW1wbGUuaW52YWxpZD7C/wAAATMEEwEIAB0FAmd0hYAWI'
    'QTghz3N2A0TaM8SlVRzNxRp+F9x7wAKCRBzNxRp+F9x72nwB/9KnqsvHO0sSQakXlzR160EImtnjJONb/usCVq9IVahzM'
    'QBYe4kB/pmyoE1nUWw56q1raL2nV1I7sgdERWrAwdPUt+4Ut5NB7l47hrQWw+nmo7N5RLjilUY/Ps8ih538ktBTkXTSka'
    'fZ63B31m/o20312B+6n2ohB9RA1gzBwBU5XaO/q9ZBBt65nc3WPh0mdVC/T5UDSgSHzzmCmNN37wWh9p1TOKGdsmlsAHQ'
    '9BMrnyFh8VdNEVAz0EfxxLCdsnBlWbV8bSWqDSSplQonruQU2uPdSDv0QBtMUFhc0nqVYSwS0ziZHUfM/rXRRH1UTRdV5'
    'kkU3wlZqAdSidbmeN2T'
)
SIG_163 = (
    'wv8AAAEzBAABCAAdBQJndIWAFiEE4Ic9zdgNE2jPEpVUczcUafhfce8ACgkQczcUafhfce/BrQgAmOMaGZt5dafUhLeG3'
    'JIK4t7K7oCHSbWKg0WXvg1ilj2TsJE9EV0sd8K2xX2EdBOO/hSA+jPJW+BngGRS9OhjnQ6M1POEvEu5OFTK6YtA/yr9mj'
    'wWTo0TMkpX6Q2Sa2SC4HO2ErTO1wYGA3rFmjyqS+6XSwiV3soVdOkiO3SYt8CWFFbGK5LpfVbkWwQrkyEuGtpec90qQ90'
    '5pchZ+/APEEtPISjQ+eZM5Z1S35fgOOwPPW5wRerkebSdBDHjn6nYuAaQxSeWaPbw9zUGOxWi8w08rqqRQrIYd/KPwDAJ'
    'ljg/9ofmsepJaHkwHE/JGeb05qnBrE9Sm7yMAzdkrihz7g=='
)
SIG_128 = (
    'wv8AAAEzBAABCAAdBQJndIWAFiEE4Ic9zdgNE2jPEpVUczcUafhfce8ACgkQczcUafhfce9SlwgAzPXC7vetYUgHUzhYm'
    '/wO/x7LR40mkD1EZMGvsX5xVX3teU1oSMZg+YIjte6dy+B13LPRnVIrI10/eRQuX/3Ef5idhr7huMVMPrbXij2WG6V/Ro'
    'yavCRbIh6aU69nQncA9AAYEYsrIWTK8/1dh5ZIxc37gHHdvPOkheHcK8ICHM6/PrnJg29RbYnBeMAG4uW1zXsullESslR'
    '13TeOUJOXdqjxjc+5NdK7M3wvufSFSdzbmJjhldi/XInQLpMHqWymuVYg1bYV4Yl97xOT4h5UuoUf1U15PL3K2ku+qYus'
    '1MNDC1bmnZ6ZidWUAO9sQvGX2DTL0sJ3OCor3V5stWOR3A=='
)
FIXTURE_FINGERPRINT = 'E0873DCDD80D1368CF12955473371469F85F71EF'


@unittest.skipUnless(shutil.which('gpgv'), 'gpgv fixture verifier required')
class AuthenticationTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.sources = self.root / 'sources'
        self.sources.mkdir()
        self.cached = self.root / 'trusted-stream-input'
        self.cached.mkdir()

    def miniature(self, sublevel='163'):
        lock = copy.deepcopy(LOCK['kernel'])
        paths = kernel.retained_paths(lock, self.cached)
        source, signature = paths['sha256'], paths['signature_sha256']
        root = lock['source_root']
        unpacked_tar = self.root / 'miniature.tar'
        notices = {path: ('Test notice ' + path + '\n').encode() for path in lock['notices']}
        with tarfile.open(unpacked_tar, 'w') as tar:
            makefile = f'VERSION = 6\nPATCHLEVEL = 6\nSUBLEVEL = {sublevel}\nEXTRAVERSION =\nNAME = Fixture\n'
            for name, content in {**notices, 'Makefile': makefile.encode(), 'include/input.h': b'fixture'}.items():
                member = tarfile.TarInfo(root + '/' + name)
                member.size = len(content)
                tar.addfile(member, io.BytesIO(content))
            link = tarfile.TarInfo(root + '/scripts/headers')
            link.type, link.linkname = tarfile.SYMTYPE, '../include'
            tar.addfile(link)
        source.write_bytes(lzma.compress(unpacked_tar.read_bytes()))
        signature.write_bytes(base64.b64decode(SIG_163 if sublevel == '163' else SIG_128))
        lock.update(sha256=sha256(source), signature_sha256=sha256(signature), signer_fingerprint=FIXTURE_FINGERPRINT,
                    key_base64=PUBLIC_KEY,
                    key_sha256=hashlib.sha256(base64.b64decode(PUBLIC_KEY)).hexdigest(),
                    notices={path: hashlib.sha256(content).hexdigest() for path, content in notices.items()})
        return lock, paths

    def test_signed_source_is_atomic_exact_and_reconstructible(self):
        lock, paths = self.miniature()
        with patch.object(kernel, 'download', side_effect=AssertionError('no network')):
            receipt = kernel.acquire(lock, self.sources, STREAM, verified_input=self.cached)
        self.assertEqual(receipt['kernel_org']['signature'], 'PASS')
        self.assertEqual(receipt['kernel_org']['sha256'], sha256(paths['sha256']))
        self.assertEqual(receipt['stream_archive_sha256'], STREAM['archive_sha256'])
        self.assertEqual(receipt['stream_nested_sha256'], STREAM['nested_sha256'])
        self.assertTrue((self.sources / lock['source_root'] / 'scripts/headers').is_symlink())
        self.assertEqual((self.sources / lock['source_root'] / 'scripts/headers/input.h').read_bytes(), b'fixture')
        self.assertEqual(sorted(self.root.glob('.kernel-source-*')), [])
        packages = self.root / 'packages'
        scratch = self.root / 'scratch'
        packages.mkdir()
        scratch.mkdir()
        for name in kernel.BUILD_RECEIPTS:
            (scratch / name).write_text('effective ' + name)
        checksums = kernel.copy_receipts(scratch, packages)
        manifest_path = packages / 'manifest.json'
        manifest_path.write_text(json.dumps({'status': 'PASS', 'source_sha256': lock['sha256'],
            'kernel_input': receipt, 'kernel_build_receipts': checksums}))
        retained = self.sources / kernel.RETAINED
        members, actual = kernel.corresponding_inputs(
            lock, LOCK['stream'], retained, packages, 'linux-upstream', lock['package_version'])
        self.assertEqual(actual, receipt)
        self.assertEqual([(name, sha256(path)) for path, name, kind in members if kind == 'sources'],
                         [('kernel.org/linux-6.6.163.tar.xz', lock['sha256'])])
        self.assertIn('native-build/kernel-patch-state.txt', {name for path, name, cat in members})
        asset = self.root / 'kernel-source.tar.gz'
        records = bundle(members, asset, 1773859634)
        component = dict(name='linux-image-6.6.163-vyos', architecture='amd64',
                         version=lock['package_version'], source_package='linux-upstream',
                         source_version=lock['package_version'], source_identity=lock['sha256'],
                         source_origin=receipt, **records)
        inventory = {'components': [component], 'shipped_packages': [component]}
        with patch('producer.kernel_source.locked_source', return_value=lock):
            verified = verify_source_integrity(self.root, inventory, [{'role': 'source', 'name': asset.name}])
            self.assertEqual(verified[(asset.name, 'kernel.org/linux-6.6.163.tar.xz')], lock['sha256'])
            component['source_origin'] = {}
            with self.assertRaisesRegex(ValueError, 'provenance'):
                verify_source_integrity(self.root, inventory, [{'role': 'source', 'name': asset.name}])
        with self.assertRaisesRegex(ValueError, 'Installed Linux source identity'):
            kernel.corresponding_inputs(lock, LOCK['stream'], retained, packages, 'linux-upstream', '6.6.128-1')
        for path, expected in [(packages / 'kernel.config', 'recipe/config/patch'),
                               (retained / 'notices/COPYING', 'legal notice')]:
            pristine = path.read_bytes()
            try:
                path.write_bytes(pristine + b' changed')
                with self.assertRaisesRegex(ValueError, expected):
                    kernel.corresponding_inputs(
                        lock, LOCK['stream'], retained, packages, 'linux-upstream', lock['package_version'])
            finally:
                path.write_bytes(pristine)

    def test_bad_archive_never_reaches_extraction(self):
        lock, paths = self.miniature()
        paths['sha256'].write_bytes(b'not the signed source')
        with patch.object(kernel, 'extract', side_effect=AssertionError('extracted unverified bytes')):
            with self.assertRaisesRegex(ValueError, 'checksum'):
                kernel.acquire(lock, self.sources, STREAM, verified_input=self.cached)
        self.assertEqual(list(self.sources.iterdir()), [])

    def test_pinned_checksum_without_valid_detached_signature_is_rejected(self):
        lock, paths = self.miniature()
        paths['sha256'].write_bytes(lzma.compress(b'other tar data'))
        lock['sha256'] = sha256(paths['sha256'])
        with patch.object(kernel, 'extract', side_effect=AssertionError('extracted unverified bytes')):
            with self.assertRaisesRegex(ValueError, 'detached release signature'):
                kernel.acquire(lock, self.sources, STREAM, verified_input=self.cached)
        self.assertEqual(list(self.sources.iterdir()), [])

    def test_signed_correct_root_with_wrong_makefile_version_is_rejected(self):
        lock, _ = self.miniature(sublevel='128')
        with self.assertRaisesRegex(ValueError, 'Makefile version'):
            kernel.acquire(lock, self.sources, STREAM, verified_input=self.cached)
        self.assertEqual(list(self.sources.iterdir()), [])

    def test_signature_must_match_exact_pinned_fingerprint(self):
        lock, _ = self.miniature()
        lock['signer_fingerprint'] = '1' * 40
        with patch.object(kernel, 'extract', side_effect=AssertionError('extracted mismatched signer')):
            with self.assertRaisesRegex(ValueError, 'pinned signing key'):
                kernel.acquire(lock, self.sources, STREAM, verified_input=self.cached)
        self.assertEqual(list(self.sources.iterdir()), [])


class NestedSourceAuditTests(unittest.TestCase):
    def test_nested_kernel_source_rejects_private_key_and_wrong_fixture_identity(self):
        logical = LOCK['kernel']['source_root'] + '/tools/testing/selftests/sgx/sign_key.pem'
        key = b'-----BEGIN ' + b'PRIVATE KEY-----\n' + b'A' * 72 + b'\n'
        archive = io.BytesIO()
        with tarfile.open(fileobj=archive, mode='w:xz') as tar:
            member = tarfile.TarInfo(logical)
            member.size = len(key)
            tar.addfile(member, io.BytesIO(key))
        for claimed in ('0' * 64, LOCK['kernel']['sha256']):
            archive.seek(0)
            with self.assertRaisesRegex(ValueError, 'Private signing key'):
                kernel.scan_original(archive, LOCK['kernel']['source_root'], claimed)


if __name__ == '__main__':
    unittest.main()
