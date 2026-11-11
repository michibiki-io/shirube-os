import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from producer.snapshot_fetch import fetch


class SnapshotFetchTests(unittest.TestCase):
    def invoke(self, uri, outcomes):
        with tempfile.TemporaryDirectory() as directory:
            lock = Path(directory) / 'lock.json'
            lock.write_text(json.dumps({'debian_snapshot':
                'https://snapshot.debian.org/archive/debian/20261105T000000Z/',
                'debian_security_snapshot':
                'https://snapshot.debian.org/archive/debian-security/20261105T000000Z/'}))
            with patch('producer.snapshot_fetch.attempt', side_effect=outcomes) as attempt, \
                    patch('producer.snapshot_fetch.time.sleep'):
                return fetch(['-nv', '-O', 'a b', uri], lock), attempt

    uri = ('http://snapshot.debian.org/archive/debian/20261105T000000Z/'
           'pool/main/d/dpkg/dpkg_1.21.23_amd64.deb')

    def test_transient_object_uses_official_mirror_and_preserves_output_arguments(self):
        result, attempt = self.invoke(self.uri, [(8, 'ERROR 503: No healthy backends.'), (0, '')])
        self.assertEqual(result, 0)
        self.assertEqual(attempt.call_args.args[0], ['-nv', '-O', 'a b',
            'https://deb.debian.org/debian/pool/main/d/dpkg/dpkg_1.21.23_amd64.deb'])

    def test_denials_indexes_other_hosts_and_snapshots_do_not_switch(self):
        for uri, output in [(self.uri, 'ERROR 403: Forbidden.'),
                            (self.uri, 'ERROR 503: bad\nERROR 401: Unauthorized.'),
                            (self.uri.replace('20261105', '20261106'), 'ERROR 503: bad'),
                            (self.uri.replace('snapshot.debian.org', 'example.org'), 'ERROR 503: bad'),
                            (self.uri.replace('pool/main/d/dpkg/dpkg_1.21.23_amd64.deb',
                                              'dists/bookworm/InRelease'), 'ERROR 503: bad')]:
            with self.subTest(uri=uri, output=output):
                result, attempt = self.invoke(uri, [(8, output)])
                self.assertEqual(result, 8)
                self.assertEqual(attempt.call_count, 1)

    def test_network_retry_is_bounded_and_preserves_other_statuses(self):
        for outcomes, expected in [([(4, '')]*3, 4), ([(4, ''), (0, '')], 0), ([(5, '')], 5)]:
            result, attempt = self.invoke(self.uri, outcomes)
            self.assertEqual(result, expected)
            self.assertEqual(attempt.call_count, len(outcomes))
