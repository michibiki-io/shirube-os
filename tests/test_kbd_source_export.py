"""An unused restricted asset must not remove executable sources or bypass drift."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from producer import kbd_source_export as exporter
from producer.debian_source_export import prepare


class KbdSourceExportTests(unittest.TestCase):
    def fixture(self, root):
        digest=lambda b:hashlib.sha256(b).hexdigest()
        inputs=root/'inputs';inputs.mkdir()
        archive=inputs/'kbd_2.5.1.orig.tar.gz';archive.write_bytes(b'original')
        dsc=inputs/'kbd_2.5.1-1.dsc'
        dsc.write_text('Source: kbd\nVersion: 2.5.1-1\nChecksums-Sha256:\n '+
            digest(archive.read_bytes())+' 8 '+archive.name+'\n')
        tree=root/'tree';tree.mkdir();excluded={}
        for name in exporter.EXCLUDED:
            p=tree/name;p.parent.mkdir(parents=True,exist_ok=True)
            p.write_bytes(name.encode());excluded[name]=digest(p.read_bytes())
        notice=tree/'data/consolefonts/README.Ethiopic';notice.write_bytes(b'restrictions')
        rules=tree/'debian/rules';rules.parent.mkdir();rules.write_bytes(b'install-exec only')
        (tree/'runtime.c').write_bytes(b'unchanged runtime')
        (tree/'data/consolefonts/Goha.psfu').write_bytes(b'unchanged GPL font')
        constants={'DSC':digest(dsc.read_bytes()),'NOTICE':digest(notice.read_bytes()),
                   'RULES':digest(rules.read_bytes()),'EXCLUDED':excluded}
        rule={'source':'kbd','version':'2.5.1-1','descriptor_sha256':constants['DSC'],
            'licensing_sha256':constants['NOTICE'],'debian_rules_sha256':constants['RULES'],
            'excluded':[{'member':p,'sha256':h} for p,h in excluded.items()]}
        return inputs,tree,constants,{'schema_version':1,'components':[rule]}

    def test_only_unused_restricted_fonts_omitted(self):
        with tempfile.TemporaryDirectory() as temporary:
            inputs,tree,constants,rules=self.fixture(Path(temporary))
            before={str(p.relative_to(tree)):p.read_bytes() for p in tree.rglob('*') if p.is_file()}
            with patch.multiple(exporter,**constants):result=prepare('kbd','2.5.1-1',inputs,tree,rules)
            for member,data in before.items():
                if member in constants['EXCLUDED']:self.assertFalse((tree/member).exists())
                else:self.assertEqual((tree/member).read_bytes(),data)
            self.assertEqual(result['sha256'],hashlib.sha256((tree/'SHIRUBE-SOURCE-EXPORT.json').read_bytes()).hexdigest())
            self.assertEqual((tree/'SHIRUBE-ORIGINAL-SOURCE.dsc').read_bytes(),next(inputs.glob('*.dsc')).read_bytes())

    def test_packaging_drift_alias_and_expanded_exclusion_reject_before_removal(self):
        for change in ('packaging','alias','policy'):
            with self.subTest(change=change),tempfile.TemporaryDirectory() as temporary:
                inputs,tree,constants,rules=self.fixture(Path(temporary))
                if change=='packaging':(tree/'debian/rules').write_bytes(b'install fonts too')
                elif change=='alias':
                    p=tree/'debian/rules';data=p.read_bytes();p.unlink()
                    other=tree/'alias';other.write_bytes(data);p.symlink_to(other)
                else:rules['components'][0]['excluded'].append({'member':'runtime.c','sha256':'0'*64})
                with patch.multiple(exporter,**constants),self.assertRaises(ValueError):
                    prepare('kbd','2.5.1-1',inputs,tree,rules)
                self.assertTrue(all((tree/p).exists() for p in constants['EXCLUDED']))
                self.assertFalse((tree/'SHIRUBE-SOURCE-EXPORT.json').exists())

    def test_raw_restricted_source_is_not_cleared_by_preferred_export(self):
        from producer.collect import approval_for
        root=Path(__file__).resolve().parents[1]
        audit=json.loads((root/'license-audit.json').read_text())['components']
        proof=json.loads((root/'docs/evidence/diagnostic-debian-kbd-preferred-source-export.json').read_text())
        self.assertEqual(approval_for(audit,'kbd','2.5.1-1',exporter.DSC,root),{})
        row=approval_for(audit,'kbd','2.5.1-1',proof['preferred_source_identity'],root)
        self.assertTrue(row['redistributable'])
        self.assertEqual(row['original_source_identity'],exporter.DSC)
