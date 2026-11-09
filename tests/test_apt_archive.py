import hashlib
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import urllib.error

from producer.apt_archive import cache_snapshot_package


class AptArchiveTests(unittest.TestCase):
    package = 'libmbedcrypto7'
    filename = 'libmbedcrypto7_2.28.3-1_amd64.deb'
    snapshot = 'https://snapshot.debian.org/archive/debian/20261105T000000Z/'
    payload = b'authenticated archive fixture'

    def metadata(self):
        return ('Package: libmbedcrypto7\nVersion: 2.28.3-1\nArchitecture: amd64\n'
                'Filename: pool/main/m/mbedtls/' + self.filename + '\nSize: '
                + str(len(self.payload)) + '\nSHA256: '
                + hashlib.sha256(self.payload).hexdigest() + '\n')

    def run_with(self, cache, acquire, metadata=None):
        absent = subprocess.CompletedProcess([], 1, stdout='', stderr='')
        with patch('producer.apt_archive.subprocess.check_output', return_value=metadata or self.metadata()), \
                patch('producer.apt_archive.subprocess.run', return_value=absent), \
                patch('producer.apt_archive.download', side_effect=acquire) as request:
            result = cache_snapshot_package(self.package, 'amd64', self.snapshot, cache)
            return result, request

    def test_transient_snapshot_failure_keeps_the_authenticated_input(self):
        calls = []
        def acquire(url, target):
            calls.append(url)
            if len(calls) == 1:
                raise urllib.error.HTTPError(url, 503, 'unavailable', {}, None)
            Path(target).write_bytes(self.payload)
        with tempfile.TemporaryDirectory() as directory:
            result, _ = self.run_with(Path(directory), acquire)
            self.assertEqual((Path(directory) / self.filename).read_bytes(), self.payload)
            self.assertEqual(result['sha256'], hashlib.sha256(self.payload).hexdigest())
            self.assertTrue(calls[0].startswith(self.snapshot))
            self.assertTrue(calls[1].startswith('https://deb.debian.org/debian/pool/'))
            self.assertEqual(result['version'], '2.28.3-1')

    def test_integrity_and_permission_failures_do_not_try_another_origin(self):
        for denied in [False, True]:
            with self.subTest(permission_denied=denied), tempfile.TemporaryDirectory() as directory:
                calls = []
                def acquire(url, target):
                    calls.append(url)
                    if denied:
                        raise urllib.error.HTTPError(url, 403, 'denied', {}, None)
                    Path(target).write_bytes(b'incorrect archive')
                with self.assertRaises(urllib.error.HTTPError if denied else ValueError):
                    self.run_with(Path(directory), acquire)
                self.assertEqual(len(calls), 1)
                self.assertEqual(list(Path(directory).iterdir()), [])

    def test_existing_archive_is_verified_without_replacement(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory)
            target = cache / self.filename
            target.write_bytes(self.payload)
            result, request = self.run_with(cache, None)
            self.assertEqual(result['status'], 'VERIFIED_CACHE')
            request.assert_not_called()
            target.write_bytes(b'corrupted existing entry')
            with self.assertRaises(ValueError):
                self.run_with(cache, lambda *_: self.fail('Corrupt cache must fail before download'))
            self.assertEqual(target.read_bytes(), b'corrupted existing entry')

    def test_unsafe_and_ambiguous_metadata_cannot_write_the_cache(self):
        for metadata in [self.metadata().replace('pool/main/', 'pool/main/../'),
                         self.metadata() + 'SHA256: ' + '0' * 64 + '\n']:
            with self.subTest(metadata=metadata), tempfile.TemporaryDirectory() as directory:
                with self.assertRaises(ValueError):
                    self.run_with(Path(directory),
                                  lambda *_: self.fail('Unsafe metadata must fail before download'), metadata)
                self.assertEqual(list(Path(directory).iterdir()), [])


class AptRetryTests(unittest.TestCase):
    def invoke(self, output, status=100, args=None):
        from producer.apt_archive import main
        root = 'https://snapshot.debian.org/archive/debian/20261105T000000Z/'
        with tempfile.TemporaryDirectory() as directory:
            lock = Path(directory) / 'lock.json'
            import json
            lock.write_text(json.dumps({'debian_snapshot': root,
                'debian_security_snapshot': root.replace('/debian/', '/debian-security/')}))
            with patch('producer.apt_archive._apt', side_effect=[(status, output), (0, '')]) as apt, \
                    patch('producer.apt_archive.cache_snapshot_package', return_value={'status': 'VERIFIED_CACHE'}) as cache:
                result = main(args or ['install', '-y', 'libxpm4'], lock)
                return result, apt, cache

    def test_security_epoch_and_encoded_filename_keep_exact_metadata_scope(self):
        line = ('E: Failed to fetch http://snapshot.debian.org/archive/debian-security/'
                '20261105T000000Z/pool/updates/main/libx/libxpm/'
                'libxpm4_3.5.12-1.1%2bdeb12u1_arm64.deb  503 No healthy backends')
        result, apt, cache = self.invoke(line)
        self.assertEqual(result, 0)
        self.assertEqual(apt.call_count, 2)
        cache.assert_called_once_with('libxpm4', 'arm64',
            'https://snapshot.debian.org/archive/debian-security/20261105T000000Z/',
            expected_filename='pool/updates/main/libx/libxpm/libxpm4_3.5.12-1.1+deb12u1_arm64.deb')

    def test_only_transient_fixed_snapshot_install_failures_are_retried(self):
        good = ('E: Failed to fetch http://snapshot.debian.org/archive/debian/'
                '20261105T000000Z/pool/main/p/pkg/pkg_1_all.deb  503 unavailable')
        variants = [good.replace('503', '403'), good.replace('20261105', '20261106'),
                    good.replace('snapshot.debian.org', 'example.org'),
                    good + '\nE: Failed to fetch https://example.org/a.deb  404 Missing',
                    good + '\nE: Hash Sum mismatch', good + '\nGPG error: invalid signature']
        for output in variants:
            with self.subTest(output=output):
                result, apt, cache = self.invoke(output)
                self.assertEqual(result, 100)
                self.assertEqual(apt.call_count, 1)
                cache.assert_not_called()
        result, apt, cache = self.invoke(good, args=['update'])
        self.assertEqual(result, 100)
        cache.assert_not_called()
        result, apt, cache = self.invoke(good, status=0)
        self.assertEqual(result, 0)
        cache.assert_not_called()

class AptMergedIndexTests(unittest.TestCase):
    package = AptArchiveTests.package
    filename = AptArchiveTests.filename
    snapshot = AptArchiveTests.snapshot
    payload = AptArchiveTests.payload
    metadata = AptArchiveTests.metadata
    def test_same_archive_in_security_and_main_indexes_preserves_digest(self):
        absent = subprocess.CompletedProcess([], 1, stdout='', stderr='')
        requested = 'pool/updates/main/m/mbedtls/' + self.filename
        def acquire(url, target):
            self.assertIn('/debian-security/', url)
            self.assertTrue(url.endswith(requested))
            Path(target).write_bytes(self.payload)
        with tempfile.TemporaryDirectory() as directory, \
                patch('producer.apt_archive.subprocess.check_output', return_value=self.metadata()), \
                patch('producer.apt_archive.subprocess.run', return_value=absent), \
                patch('producer.apt_archive.download', side_effect=acquire):
            result = cache_snapshot_package(self.package, 'amd64',
                self.snapshot.replace('/debian/', '/debian-security/'), Path(directory), requested)
            self.assertEqual(result['sha256'], hashlib.sha256(self.payload).hexdigest())
            with self.assertRaises(ValueError):
                cache_snapshot_package(self.package, 'amd64', self.snapshot, Path(directory),
                    requested.replace('2.28.3-1', '2.28.3-2'))

    def test_epoch_is_preserved_in_apt_cache_filename(self):
        absent = subprocess.CompletedProcess([], 1, stdout='', stderr='')
        metadata = self.metadata().replace('Version: 2.28.3-1', 'Version: 1:2.28.3-1')
        with tempfile.TemporaryDirectory() as directory, \
                patch('producer.apt_archive.subprocess.check_output', return_value=metadata), \
                patch('producer.apt_archive.subprocess.run', return_value=absent), \
                patch('producer.apt_archive.download', side_effect=lambda url, target: Path(target).write_bytes(self.payload)):
            cache_snapshot_package(self.package, 'amd64', self.snapshot, Path(directory))
            self.assertEqual((Path(directory) / self.filename.replace('_2.28', '_1%3a2.28')).read_bytes(), self.payload)
