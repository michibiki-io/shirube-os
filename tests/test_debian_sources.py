from pathlib import Path
import hashlib
import shutil
import tempfile
import unittest
from unittest.mock import patch
from producer.debian_sources import acquire,descriptor,legal_files


class DebianSourceTests(unittest.TestCase):
    def source_set(self, directory, name, version):
        directory.mkdir()
        archive=directory/(name+'_1.orig.tar.gz');archive.write_bytes(('original '+name).encode())
        dsc=directory/(name+'_1.dsc')
        dsc.write_text('Source: '+name+'\nVersion: '+version+'\nChecksums-Sha256:\n '+
                       hashlib.sha256(archive.read_bytes()).hexdigest()+' '+str(archive.stat().st_size)+' '+archive.name+'\n')
        return directory

    def test_batch_acquisition_binds_each_exact_version_with_one_apt_call(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            originals=[self.source_set(root/'one','first','1:1-2'),self.source_set(root/'two','second','2.0')]
            def fetch(args,**kwargs):
                self.assertEqual(args,['apt-get','source','--download-only','--only-source','first=1:1-2','second=2.0'])
                for directory in originals:
                    for file in directory.iterdir():shutil.copy2(file,kwargs['cwd']/file.name)
            with patch('producer.debian_sources.subprocess.run',side_effect=fetch) as command:
                result=acquire({('second','2.0'),('first','1:1-2')},root/'batch')
                self.assertEqual(command.call_count,1)
            self.assertEqual(set(result),{('first','1:1-2'),('second','2.0')})
            for key,directory in result.items():
                self.assertTrue(descriptor(directory,*key).is_file())
                archive=next(directory.glob('*.tar.gz'))
                self.assertEqual(archive.stat().st_ino,(root/'batch/downloads'/archive.name).stat().st_ino)

    def test_batch_rejects_missing_requested_source_and_changed_archive(self):
        for changed in [False,True]:
            with self.subTest(changed=changed),tempfile.TemporaryDirectory() as temporary:
                root=Path(temporary);original=self.source_set(root/'one','first','1')
                def fetch(args,**kwargs):
                    for file in original.iterdir():shutil.copy2(file,kwargs['cwd']/file.name)
                    if changed:(kwargs['cwd']/'first_1.orig.tar.gz').write_bytes(b'changed source')
                requested={('first','1')} if changed else {('first','1'),('missing','2')}
                with patch('producer.debian_sources.subprocess.run',side_effect=fetch):
                    with self.assertRaises(ValueError):acquire(requested,root/'batch')

    def test_batch_rejects_request_options_before_running_apt(self):
        with tempfile.TemporaryDirectory() as temporary,patch('producer.debian_sources.subprocess.run') as command:
            with self.assertRaisesRegex(ValueError,'Invalid exact'):
                acquire({('--untrusted-option','1')},Path(temporary)/'batch')
            command.assert_not_called()

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
            (root/'COPYRIGHT').symlink_to('sub/LICENSES/BSD-3-Clause.txt')
            selected={member:path for path,member in legal_files(root)}
            self.assertFalse(selected['COPYRIGHT'].is_symlink())
            self.assertEqual(selected['COPYRIGHT'].read_bytes(),b'original notice')
            (root/'COPYRIGHT').unlink()
            outside=Path(temporary)/'external';outside.write_text('outside state')
            (root/'COPYRIGHT').symlink_to(outside)
            with self.assertRaisesRegex(ValueError,'outside'):list(legal_files(root))
