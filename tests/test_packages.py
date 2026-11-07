import io
import tarfile
import unittest
from unittest.mock import MagicMock, patch
from producer.packages import audit


class PackageArchitectureTests(unittest.TestCase):
    def test_wrong_architecture_extension_below_usr_share_is_rejected(self):
        data=bytearray(64);data[:6]=b'\x7fELF\x02\x01';data[18:20]=(183).to_bytes(2,'little')
        payload=io.BytesIO()
        with tarfile.open(fileobj=payload,mode='w') as archive:
            member=tarfile.TarInfo('usr/share/vyos-http-api-tools/lib/python3.11/site-packages/module.so')
            member.size=len(data);archive.addfile(member,io.BytesIO(data))
        payload.seek(0)
        process=MagicMock();process.stdout=payload;process.wait.return_value=0
        process.__enter__.return_value=process
        with patch('producer.packages.subprocess.Popen',return_value=process):
            with self.assertRaisesRegex(ValueError,'Wrong ELF architecture'):
                audit('synthetic-package.deb','amd64')
