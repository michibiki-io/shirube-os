import hashlib
import io
import unittest
from producer.public_test_keys import allowed,allowed_file,scan
from producer.release import SECRET


class PublicTestKeyTests(unittest.TestCase):
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
        self.assertTrue(allowed('vpp','usr/bin/vpp_echo','amd64'))
        self.assertTrue(allowed('vpp-plugin-core','usr/lib/aarch64-linux-gnu/vpp_plugins/http_static_plugin.so','arm64'))
        self.assertFalse(allowed('other','usr/bin/vpp_echo','amd64'))
        self.assertFalse(allowed('vpp','etc/ssh/ssh_host_rsa_key','amd64'))
        self.assertFalse(allowed('vpp-plugin-core','usr/lib/x86_64-linux-gnu/vpp_plugins/http_static_plugin.so','arm64'))
        member='opt/saltstack/salt/lib/python3.11/site-packages/Cryptodome/SelfTest/PublicKey/test_import_RSA.py'
        self.assertTrue(allowed_file('salt-common',member))
        self.assertIsNone(allowed_file('salt-minion',member))
        self.assertIsNone(allowed_file('salt-common',member+'.changed'))
