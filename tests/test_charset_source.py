import hashlib
import io
import tarfile
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch
from producer.charset_source import KIND, ROOT, write_export


class CharsetSourceExportTests(unittest.TestCase):
    def fixture(self, directory, extra=None):
        members={ROOT+'/'+name:b'original '+name.encode() for name in
                 ['LICENSE','data/NOTICE.md','setup.py','setup.cfg','charset_normalizer/__init__.py',
                  'charset_normalizer/api.py','data/sample-original.txt']}
        if extra:members.update(extra)
        original=directory/'original.tar.gz'
        with tarfile.open(original,'w:gz') as archive:
            for name,data in members.items():
                member=tarfile.TarInfo(name);member.size=len(data);member.mode=0o755 if name.endswith('setup.py') else 0o644
                archive.addfile(member,io.BytesIO(data))
        sample=ROOT+'/data/sample-original.txt'
        declaration={'kind':KIND,'excluded':[{'member':sample,'sha256':hashlib.sha256(members[sample]).hexdigest()}]}
        return original,members,declaration

    def test_scoped_export_preserves_library_build_and_legal_bytes_deterministically(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);original,members,policy=self.fixture(root)
            with patch('producer.charset_source.ORIGINAL',hashlib.sha256(original.read_bytes()).hexdigest()):
                first=root/'first.tar.gz';second=root/'second.tar.gz'
                self.assertEqual(write_export(original,first,policy),write_export(original,second,policy))
            with tarfile.open(first) as archive:
                actual={member.name:archive.extractfile(member).read() for member in archive}
                self.assertEqual(archive.getmember(ROOT+'/setup.py').mode,0o755)
            sample=policy['excluded'][0]['member'];self.assertNotIn(sample,actual)
            for name,content in members.items():
                if name!=sample:self.assertEqual(actual[name],content)
            self.assertIn(ROOT+'/SHIRUBE-SOURCE-EXPORT.json',actual)
            with self.assertRaisesRegex(ValueError,'Unexpected.*input'):
                write_export(original,root/'unverified.tar.gz',policy)

    def test_runtime_code_cannot_be_excluded_or_sample_changes_silently_accepted(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);original,_,policy=self.fixture(root)
            with patch('producer.charset_source.ORIGINAL',hashlib.sha256(original.read_bytes()).hexdigest()):
                changed={'kind':KIND,'excluded':[{'member':ROOT+'/charset_normalizer/api.py','sha256':'0'*64}]}
                with self.assertRaisesRegex(ValueError,'Only exact source-only'):
                    write_export(original,root/'runtime.tar.gz',changed)
                policy['excluded'][0]['sha256']='0'*64
                with self.assertRaisesRegex(ValueError,'sample bytes differ'):
                    write_export(original,root/'changed.tar.gz',policy)

    def test_unclassified_data_and_unsafe_members_stop_export(self):
        for name in [ROOT+'/data/sample-new.txt',ROOT+'/../outside.txt']:
            with self.subTest(name=name),tempfile.TemporaryDirectory() as temporary:
                root=Path(temporary);original,_,policy=self.fixture(root,{name:b'new unreviewed bytes'})
                with patch('producer.charset_source.ORIGINAL',hashlib.sha256(original.read_bytes()).hexdigest()):
                    with self.assertRaises(ValueError):write_export(original,root/'export.tar.gz',policy)
