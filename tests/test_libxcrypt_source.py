import hashlib
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch
from producer.libxcrypt_source import KIND, ROOT, TESTS, write_export


class LibxcryptSourceExportTests(unittest.TestCase):
    def fixture(self, root, extra=None):
        members={ROOT+'/'+name: b'original '+name.encode() for name in
                 ['LICENSING','COPYING.LIB','AUTHORS','configure','lib/crypt.c','lib/alg-yescrypt-opt.c']}
        members.update({name: b'original source-only test '+name.encode() for name in sorted(TESTS)})
        members.update({ROOT+'/'+name: b'# original attribution\nnoinst_HEADERS = \\\n\tlib/crypt.h \\\n\ttest/des-cases.h \\\n\ttest/ka-table.inc\n' for name in ['Makefile.am','Makefile.in']})
        if extra: members.update(extra)
        source=root/'original.tar.gz'
        with tarfile.open(source,'w:gz') as output:
            for name,content in members.items():
                info=tarfile.TarInfo(name);info.size=len(content)
                info.mode=0o755 if name.endswith('/configure') else 0o644
                output.addfile(info,io.BytesIO(content))
        policy={'kind':KIND,'excluded':[{'member':name,'sha256':hashlib.sha256(members[name]).hexdigest(),
                                      'size':len(members[name])} for name in sorted(TESTS)]}
        return source,members,policy

    def test_only_named_tests_and_their_noninstalled_build_prerequisite_change(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);source,members,policy=self.fixture(root)
            with patch('producer.libxcrypt_source.ORIGINAL',hashlib.sha256(source.read_bytes()).hexdigest()):
                first=root/'first.tar.gz';second=root/'second.tar.gz'
                self.assertEqual(write_export(source,first,policy),write_export(source,second,policy))
            with tarfile.open(first) as archive:
                actual={item.name:archive.extractfile(item).read() for item in archive}
                self.assertEqual(archive.getmember(ROOT+'/configure').mode,0o755)
            for name,content in members.items():
                if name in TESTS: self.assertNotIn(name,actual)
                elif name in {ROOT+'/Makefile.am',ROOT+'/Makefile.in'}:
                    self.assertIn(b'# original attribution',actual[name])
                    self.assertNotIn(b'\ttest/des-cases.h \\\n',actual[name])
                else: self.assertEqual(actual[name],content)
            manifest=json.loads(actual[ROOT+'/SHIRUBE-SOURCE-EXPORT.json'])
            self.assertEqual(len(manifest['modified_build_controls']),2)
            self.assertIn('incomplete',manifest['scope'])
            with self.assertRaisesRegex(ValueError,'Unexpected.*input'):
                write_export(source,root/'unverified.tar.gz',policy)

    def test_runtime_exclusion_changed_test_bytes_and_unsafe_members_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);source,_,policy=self.fixture(root)
            with patch('producer.libxcrypt_source.ORIGINAL',hashlib.sha256(source.read_bytes()).hexdigest()):
                wrong=json.loads(json.dumps(policy));wrong['excluded'][0]['member']=ROOT+'/lib/crypt.c'
                with self.assertRaisesRegex(ValueError,'Only the exact'):
                    write_export(source,root/'runtime.tar.gz',wrong)
                policy['excluded'][0]['sha256']='0'*64
                with self.assertRaisesRegex(ValueError,'test bytes differ'):
                    write_export(source,root/'changed.tar.gz',policy)
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);source,_,policy=self.fixture(root,{ROOT+'/../outside':b'unsafe'})
            with patch('producer.libxcrypt_source.ORIGINAL',hashlib.sha256(source.read_bytes()).hexdigest()):
                with self.assertRaisesRegex(ValueError,'Unsafe'):
                    write_export(source,root/'unsafe.tar.gz',policy)
