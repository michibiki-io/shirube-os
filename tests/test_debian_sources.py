from pathlib import Path
import hashlib
import tempfile
import unittest
from producer.debian_sources import descriptor,legal_files


class DebianSourceTests(unittest.TestCase):
    def test_descriptor_identity_and_archive_integrity(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);data=b'fixed source archive';archive=root/'example_1.orig.tar.gz';archive.write_bytes(data)
            digest=hashlib.sha256(data).hexdigest()
            content=f'Source: example\nVersion: 1:1-2\nChecksums-Sha256:\n {digest} {len(data)} {archive.name}\n'
            dsc=root/'example_1-2.dsc';dsc.write_text(content)
            self.assertEqual(descriptor(root,'example','1:1-2'),dsc)
            with self.assertRaisesRegex(ValueError,'identity differs'):descriptor(root,'example','2')
            unexpected=root/'unverified.tar.gz';unexpected.write_bytes(b'uninventoried input')
            with self.assertRaisesRegex(ValueError,'Unverified files'):descriptor(root,'example','1:1-2')
            unexpected.unlink()
            archive.write_bytes(b'changed source')
            with self.assertRaisesRegex(ValueError,'missing or differs'):descriptor(root,'example','1:1-2')
            dsc.write_text(content.replace(archive.name,'../outside.tar.gz'))
            with self.assertRaisesRegex(ValueError,'Unsafe'):descriptor(root,'example','1:1-2')
            dsc.write_text(content+'Source: duplicate\n')
            with self.assertRaisesRegex(ValueError,'duplicate'):descriptor(root,'example','1:1-2')

    def test_original_legal_notices_are_retained_without_external_links(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)/'source';(root/'sub/LICENSES').mkdir(parents=True)
            for name in ['COPYING','COPYING3','LICENSE.md','NOTICE','debian/copyright.in','sub/LICENSES/BSD-3-Clause.txt','license.py']:
                path=root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text('original notice')
            self.assertEqual({name for _,name in legal_files(root)}, {'COPYING','COPYING3','LICENSE.md','NOTICE','debian/copyright.in','sub/LICENSES/BSD-3-Clause.txt'})
            outside=Path(temporary)/'external';outside.write_text('outside state')
            (root/'COPYRIGHT').symlink_to(outside)
            with self.assertRaisesRegex(ValueError,'outside'):list(legal_files(root))
