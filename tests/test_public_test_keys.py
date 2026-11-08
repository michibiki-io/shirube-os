import hashlib
import io
import unittest
from pathlib import Path
import tempfile
from unittest.mock import patch
from producer.public_test_keys import allowed,allowed_file,cached_fixture_keys,scan
from producer.release import SECRET


class PublicTestKeyTests(unittest.TestCase):
    def test_generated_cache_does_not_hide_unknown_keys_or_changed_source(self):
        fixture=b'-----BEGIN PRIVATE KEY-----\n'+b'A'*64+b'\n-----END PRIVATE KEY-----'
        source=b'public_test_key = '+repr(fixture).encode()+b'\n'
        base='opt/saltstack/salt/lib/python3.11/site-packages/'
        member=base+'Cryptodome/SelfTest/fixture.py'
        cache=base+'Cryptodome/SelfTest/__pycache__/fixture.cpython-311.pyc'
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);(root/'etc').mkdir()
            path=root/member;path.parent.mkdir(parents=True);path.write_bytes(source)
            with patch('producer.public_test_keys.allowed_file',return_value=hashlib.sha256(source).hexdigest()):
                permit=cached_fixture_keys(root,cache,'salt-common')
                scan(io.BytesIO(b'cache-header'+fixture),SECRET,permit)
                with self.assertRaises(ValueError):
                    scan(io.BytesIO(fixture+fixture.replace(b'A'*64,b'B'*64)),SECRET,permit)
                path.write_bytes(source+b'changed = True\n')
                with self.assertRaisesRegex(ValueError,'Changed source'):
                    cached_fixture_keys(root,cache,'salt-common')
            self.assertFalse(cached_fixture_keys(root,cache,'unrelated-package'))

    def test_exact_public_fixture_across_chunks_does_not_hide_another_key(self):
        fixture=b'-----BEGIN PRIVATE KEY-----\n'+b'A'*64+b'\n-----END PRIVATE KEY-----'
        permit={hashlib.sha256(fixture).hexdigest()}
        payload=b'x'*(1024**2-20)+fixture+b'\x00tail'
        scan(io.BytesIO(payload),SECRET,permit)
        scan(io.BytesIO(payload.replace(b'\n',b'\r\n')),SECRET,permit)
        for data in [payload.replace(b'A'*64,b'B'*64),payload+fixture.replace(b'A'*64,b'C'*64),
                     payload[:-12],payload]:
            with self.subTest(length=len(data)):
                with self.assertRaises(ValueError):
                    scan(io.BytesIO(data),SECRET,() if data==payload else permit)

    def test_allowance_is_limited_to_declared_upstream_payloads(self):
        self.assertEqual(len(allowed('libgnutls30','usr/lib/x86_64-linux-gnu/libgnutls.so.30.34.3','amd64')),6)
        self.assertFalse(allowed('libgnutls30','etc/ssl/private/server.key','amd64'))
        self.assertFalse(allowed('libgnutls30','usr/lib/x86_64-linux-gnu/libgnutls.so.30.34.3','arm64'))
        fixture='usr/lib/python3/dist-packages/certbot/tests/testdata/rsa2048_key.pem'
        self.assertTrue(allowed_file('python3-certbot',fixture))
        self.assertIsNone(allowed_file('unrelated-package',fixture))
        self.assertIsNone(allowed_file('python3-certbot','etc/ssl/private/server.key'))
        self.assertTrue(allowed('vpp','usr/bin/vpp_echo','amd64'))
        self.assertTrue(allowed('vpp-plugin-core','usr/lib/aarch64-linux-gnu/vpp_plugins/http_static_plugin.so','arm64'))
        self.assertFalse(allowed('other','usr/bin/vpp_echo','amd64'))
        self.assertFalse(allowed('vpp','etc/ssh/ssh_host_rsa_key','amd64'))
        self.assertFalse(allowed('vpp-plugin-core','usr/lib/x86_64-linux-gnu/vpp_plugins/http_static_plugin.so','arm64'))
        member='opt/saltstack/salt/lib/python3.11/site-packages/Cryptodome/SelfTest/PublicKey/test_import_RSA.py'
        self.assertTrue(allowed_file('salt-common',member))
        self.assertIsNone(allowed_file('salt-minion',member))
        self.assertIsNone(allowed_file('salt-common',member+'.changed'))
