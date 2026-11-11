"""Legal-metadata repair must preserve package payload and maintainer scripts."""
from pathlib import Path
import subprocess
import tempfile
import unittest
from producer.package_notices import retain


class PackageNoticeTests(unittest.TestCase):
    def test_retains_original_terms_without_changing_runtime_payload(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            package = base/'package'
            (package/'DEBIAN').mkdir(parents=True)
            control = ('Package: python3-pyhumps\nVersion: 3.8.0-1\nArchitecture: all\n'
                       'Maintainer: Fixture <fixture@example.test>\nDescription: fixture\n')
            (package/'DEBIAN/control').write_text(control)
            script = package/'DEBIAN/postinst'
            script.write_text('#!/bin/sh\nexit 0\n');script.chmod(0o755)
            binary = package/'usr/lib/fixture/module.ko'
            binary.parent.mkdir(parents=True)
            binary.write_bytes(b'fixture payload with an unchanged module-signature trailer')
            binary.chmod(0o644)
            old = package/'usr/share/doc/python3-pyhumps/copyright'
            old.parent.mkdir(parents=True);old.write_bytes(b'Existing upstream notice\n')
            source = base/'source';source.mkdir()
            (source/'LICENSE').write_bytes(b'Original license bytes\n')
            (source/'third-party').mkdir()
            (source/'third-party/NOTICE').write_bytes(b'Original third-party attribution\n')
            deb = base/'fixture.deb'
            subprocess.run(['dpkg-deb', '-b', str(package), str(deb)], check=True, stdout=subprocess.DEVNULL)
            retain(deb, source, base/'repair')
            result = base/'result'
            subprocess.run(['dpkg-deb', '-R', str(deb), str(result)], check=True)
            for relative in ['DEBIAN/control', 'DEBIAN/postinst', 'usr/lib/fixture/module.ko']:
                self.assertEqual((result/relative).read_bytes(), (package/relative).read_bytes())
                self.assertEqual((result/relative).stat().st_mode & 0o777, (package/relative).stat().st_mode & 0o777)
            doc = result/'usr/share/doc/python3-pyhumps'
            self.assertTrue((doc/'copyright').read_bytes().startswith(old.read_bytes()))
            for relative in ['LICENSE', 'third-party/NOTICE']:
                self.assertEqual((doc/'upstream-notices'/relative).read_bytes(), (source/relative).read_bytes())
            self.assertIn('does not approve or relicense', (doc/'copyright').read_text())

