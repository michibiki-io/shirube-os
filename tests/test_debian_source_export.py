import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from producer.debian_source_export import prepare


class DebianPreferredSourceExportTests(unittest.TestCase):
    def fixture(self, base):
        originals=base/'inputs';originals.mkdir()
        archive=originals/'libxcrypt_4.4.33.orig.tar.xz';archive.write_bytes(b'authenticated original')
        digest=lambda data:hashlib.sha256(data).hexdigest()
        descriptor=originals/'libxcrypt_4.4.33-2.dsc'
        descriptor.write_text('Source: libxcrypt\nVersion: 1:4.4.33-2\nChecksums-Sha256:\n '+
                              digest(archive.read_bytes())+' '+str(archive.stat().st_size)+' '+archive.name+'\n')
        tree=base/'tree';(tree/'test').mkdir(parents=True);(tree/'lib').mkdir()
        (tree/'lib/crypt.c').write_bytes(b'unchanged installed algorithm')
        (tree/'LICENSING').write_bytes(b'upstream detailed file permissions')
        (tree/'Makefile.am').write_bytes(b'# Original attribution\nnoinst_HEADERS = \\\n\tlib/crypt.h \\\n\ttest/des-cases.h \\\n\ttest/licensed.h\n')
        names=['alg-des.c','alg-md4.c','alg-md5.c','alg-sha256.c','alg-sha512.c',
               'des-cases.h','des-obsolete.c','des-obsolete_r.c','gensalt.c']
        excluded=[]
        for name in names:
            member='test/'+name;content=member.encode();(tree/member).write_bytes(content)
            excluded.append({'member':member,'sha256':digest(content)})
        rule={'source':'libxcrypt','version':'1:4.4.33-2','descriptor_sha256':digest(descriptor.read_bytes()),
              'licensing_sha256':digest((tree/'LICENSING').read_bytes()),'excluded':excluded,
              'build_control':{'path':'Makefile.am','sha256':digest((tree/'Makefile.am').read_bytes())}}
        return originals,tree,{'schema_version':1,'components':[rule]}

    def test_export_preserves_installed_code_and_distinguishes_original_provenance(self):
        with tempfile.TemporaryDirectory() as temporary:
            original,tree,rules=self.fixture(Path(temporary))
            before={str(p.relative_to(tree)):p.read_bytes() for p in tree.rglob('*') if p.is_file()}
            result=prepare('libxcrypt','1:4.4.33-2',original,tree,rules)
            record=json.loads((tree/'SHIRUBE-SOURCE-EXPORT.json').read_text())
            excluded={row['member'] for row in record['excluded']}
            for name,data in before.items():
                if name in excluded:self.assertFalse((tree/name).exists())
                elif name=='Makefile.am':self.assertIn(b'# Original attribution',(tree/name).read_bytes())
                else:self.assertEqual((tree/name).read_bytes(),data)
            self.assertEqual((tree/'SHIRUBE-ORIGINAL-SOURCE.dsc').read_bytes(),next(original.glob('*.dsc')).read_bytes())
            self.assertFalse(list(tree.glob('*.tar.*')))
            self.assertIn('not a new signed Debian source set',record['reconstruction'])
            self.assertIn('incomplete',record['reconstruction'])
            self.assertEqual(result['sha256'],hashlib.sha256((tree/'SHIRUBE-SOURCE-EXPORT.json').read_bytes()).hexdigest())

    def test_changed_last_test_or_runtime_exclusion_rejects_before_any_deletion(self):
        for change in ['last-bytes','runtime-exclusion','alias']:
            with self.subTest(change=change),tempfile.TemporaryDirectory() as temporary:
                originals,tree,rules=self.fixture(Path(temporary))
                if change=='last-bytes':(tree/'test/gensalt.c').write_bytes(b'changed')
                elif change=='runtime-exclusion':rules['components'][0]['excluded'][-1]['member']='lib/crypt.c'
                else:
                    test=tree/'test/gensalt.c';test.unlink();test.symlink_to('../lib/crypt.c')
                with self.assertRaises(ValueError):prepare('libxcrypt','1:4.4.33-2',originals,tree,rules)
                self.assertTrue((tree/'test/alg-des.c').is_file())
                self.assertFalse((tree/'SHIRUBE-SOURCE-EXPORT.json').exists())
                self.assertEqual((tree/'lib/crypt.c').read_bytes(),b'unchanged installed algorithm')

    def test_unknown_version_never_falls_back_to_uncleared_raw_archives(self):
        with tempfile.TemporaryDirectory() as temporary:
            originals,tree,rules=self.fixture(Path(temporary))
            with self.assertRaisesRegex(ValueError,'Unreviewed'):
                prepare('libxcrypt','1:4.4.34-1',originals,tree,rules)
            self.assertIsNone(prepare('unselected-package','1',originals,tree,rules))

    def test_real_policy_approval_cannot_clear_the_original_unfiltered_source_set(self):
        from producer.collect import approval_for
        root=Path(__file__).resolve().parents[1]
        rules=json.loads((root/'containers/native/debian-source-exports.json').read_text())
        audit=json.loads((root/'license-audit.json').read_text())['components']
        original=rules['components'][0]['descriptor_sha256']
        self.assertEqual(approval_for(audit,'libxcrypt','1:4.4.33-2',original,root),{})
        proof=json.loads((root/'docs/evidence/diagnostic-debian-libxcrypt-preferred-source-export.json').read_text())
        selected=approval_for(audit,'libxcrypt','1:4.4.33-2',proof['preferred_source_identity'],root)
        self.assertTrue(selected['redistributable'])
        self.assertNotEqual(selected['source_identity'],original)
