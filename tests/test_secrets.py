from pathlib import Path
import tempfile
import unittest
from producer.release import scan_secrets


class PrivateKeyScanTests(unittest.TestCase):
    def test_format_delimiters_are_not_key_material(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'parser'
            for data in [b'\x7fELFparser\0-----BEGIN RSA PRIVATE KEY-----\n\0-----END RSA PRIVATE KEY-----',
                         b'const char *begin="-----BEGIN OPENSSH PRIVATE KEY-----\\n";']:
                path.write_bytes(data);scan_secrets(path)

    def test_armored_key_bytes_and_boundary_splits_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'payload'
            for label in [b'RSA ',b'EC ',b'DSA ',b'OPENSSH ',b'ENCRYPTED ',b'']:
                for padding in [0,1024**2-24,1024**2-85]:
                    material=b'-----BEGIN '+label+b'PRIVATE KEY-----\n'+b'A'*64+b'\n'
                    path.write_bytes(b'_'*padding+material)
                    with self.assertRaises(ValueError):scan_secrets(path)
            path.write_bytes(b'-----BEGIN RSA PRIVATE KEY-----\nProc-Type: 4,ENCRYPTED\nDEK-Info: AES-256-CBC,012345\n\n'+b'A'*64)
            with self.assertRaises(ValueError):scan_secrets(path)
