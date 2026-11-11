"""A source-only export must preserve runtime files and reject changed scope."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from producer import debian_optional_source_export as exporter
from producer.debian_source_export import prepare


class OptionalSourceExportTests(unittest.TestCase):
    def fixture(self, root, source):
        digest=lambda b:hashlib.sha256(b).hexdigest()
        version=exporter.POLICIES[source][0]
        inputs=root/'inputs';inputs.mkdir()
        archive=inputs/'source.tar.gz';archive.write_bytes(b'original')
        dsc=inputs/'source.dsc';dsc.write_text('Source: '+source+'\nVersion: '+version+
            '\nChecksums-Sha256:\n '+digest(archive.read_bytes())+' 8 source.tar.gz\n')
        tree=root/'tree';tree.mkdir()
        for name in ('runtime.c','build.cfg','optional.txt'):(tree/name).write_bytes(name.encode())
        rule={'source':source,'version':version,'descriptor_sha256':digest(dsc.read_bytes()),
            'kind':'fixture','unchanged_controls':[{'member':'build.cfg','sha256':digest(b'build.cfg')}],
            'excluded':[],'replacements':[],'reconstruction':'No runtime code changes.'}
        if source=='chardet':rule['excluded']=[{'member':'optional.txt','sha256':digest(b'optional.txt')}]
        else:rule['replacements']=[{'member':'optional.txt','original_sha256':digest(b'optional.txt'),
            'content':'diagnostic stub\n','replacement_sha256':digest(b'diagnostic stub\n')}]
        policies={source:(version,exporter.policy_hash(rule))}
        return inputs,tree,{'schema_version':1,'components':[rule]},policies,version

    def test_runtime_and_original_controls_retained_in_both_export_forms(self):
        for source in exporter.POLICIES:
            with self.subTest(source=source),tempfile.TemporaryDirectory() as temporary:
                inputs,tree,rules,policies,version=self.fixture(Path(temporary),source)
                with patch.object(exporter,'POLICIES',policies):result=prepare(source,version,inputs,tree,rules)
                self.assertEqual((tree/'runtime.c').read_bytes(),b'runtime.c')
                self.assertEqual((tree/'build.cfg').read_bytes(),b'build.cfg')
                if source=='chardet':self.assertFalse((tree/'optional.txt').exists())
                else:self.assertEqual((tree/'optional.txt').read_bytes(),b'diagnostic stub\n')
                self.assertEqual(result['sha256'],hashlib.sha256((tree/'SHIRUBE-SOURCE-EXPORT.json').read_bytes()).hexdigest())
                self.assertEqual((tree/'SHIRUBE-ORIGINAL-SOURCE.dsc').read_bytes(),next(inputs.glob('*.dsc')).read_bytes())

    def test_control_drift_alias_and_policy_expansion_leave_optional_input_intact(self):
        for change in ('control','alias','policy'):
            with self.subTest(change=change),tempfile.TemporaryDirectory() as temporary:
                inputs,tree,rules,policies,version=self.fixture(Path(temporary),'chardet')
                if change=='control':(tree/'build.cfg').write_bytes(b'changed packaging')
                elif change=='alias':
                    (tree/'build.cfg').unlink();(tree/'other').write_bytes(b'build.cfg')
                    (tree/'build.cfg').symlink_to(tree/'other')
                else:rules['components'][0]['excluded'].append({'member':'runtime.c','sha256':'0'*64})
                with patch.object(exporter,'POLICIES',policies),self.assertRaises(ValueError):
                    prepare('chardet',version,inputs,tree,rules)
                self.assertEqual((tree/'optional.txt').read_bytes(),b'optional.txt')
                self.assertFalse((tree/'SHIRUBE-SOURCE-EXPORT.json').exists())

    def test_real_permissions_clear_only_the_exact_export_not_raw_sources(self):
        from producer.collect import approval_for
        root=Path(__file__).resolve().parents[1]
        audit=json.loads((root/'license-audit.json').read_text())['components']
        declarations=json.loads((root/'containers/native/debian-source-exports.json').read_text())
        for source,(version,expected) in exporter.POLICIES.items():
            rule=next(x for x in declarations['components'] if x['source']==source)
            self.assertEqual(exporter.policy_hash(rule),expected)
            proof=json.loads((root/('docs/evidence/diagnostic-debian-'+source+'-preferred-source-export.json')).read_text())
            self.assertEqual(approval_for(audit,source,version,rule['descriptor_sha256'],root),{})
            row=approval_for(audit,source,version,proof['preferred_source_identity'],root)
            self.assertTrue(row['redistributable'])
            self.assertEqual(row['original_source_identity'],rule['descriptor_sha256'])
