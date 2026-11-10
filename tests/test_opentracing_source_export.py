"""A preferred-source license repair must preserve code and reject drift first."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from producer import opentracing_source_export as exporter
from producer.debian_source_export import prepare


class OpenTracingSourceExportTests(unittest.TestCase):
    def fixture(self, root):
        digest=lambda data:hashlib.sha256(data).hexdigest()
        inputs=root/'inputs';inputs.mkdir();archive=inputs/'opentracing-cpp_1.6.0.orig.tar.xz'
        archive.write_bytes(b'original source input')
        dsc=inputs/'opentracing-cpp_1.6.0-4.dsc'
        dsc.write_text('Source: opentracing-cpp\nVersion: 1.6.0-4\nChecksums-Sha256:\n '+
            digest(archive.read_bytes())+' '+str(archive.stat().st_size)+' '+archive.name+'\n')
        tree=root/'tree';html=tree/exporter.HTML_MEMBER;html.parent.mkdir(parents=True)
        html.write_bytes(b'<html>saved page fixture</html>')
        header=tree/exporter.HEADER_MEMBER;header.write_bytes(b'unchanged Catch code and copyright')
        (tree/'runtime.cpp').write_bytes(b'unchanged runtime code')
        constants={'DSC':digest(dsc.read_bytes()),'HTML':digest(html.read_bytes()),
                   'HEADER':digest(header.read_bytes())}
        rule={'source':'opentracing-cpp','version':'1.6.0-4','descriptor_sha256':constants['DSC'],
            'replacement':{'member':exporter.HTML_MEMBER,'original_sha256':constants['HTML'],
                'notice':'LICENSES/BSL-1.0.txt','replacement_sha256':exporter.LICENSE},
            'header':{'member':exporter.HEADER_MEMBER,'sha256':constants['HEADER']}}
        return inputs,tree,constants,{'schema_version':1,'components':[rule]}

    def test_replaces_only_license_page_and_retains_exact_code_and_descriptor(self):
        with tempfile.TemporaryDirectory() as temporary:
            inputs,tree,constants,rules=self.fixture(Path(temporary))
            before={str(p.relative_to(tree)):p.read_bytes() for p in tree.rglob('*') if p.is_file()}
            with patch.multiple(exporter,**constants):result=prepare('opentracing-cpp','1.6.0-4',inputs,tree,rules)
            for member,data in before.items():
                if member!=exporter.HTML_MEMBER:self.assertEqual((tree/member).read_bytes(),data)
            self.assertEqual((tree/exporter.HTML_MEMBER).read_bytes(),Path('LICENSES/BSL-1.0.txt').read_bytes())
            self.assertEqual((tree/'SHIRUBE-ORIGINAL-SOURCE.dsc').read_bytes(),next(inputs.glob('*.dsc')).read_bytes())
            record=json.loads((tree/'SHIRUBE-SOURCE-EXPORT.json').read_text())
            self.assertIn('Every code, test, build',record['reconstruction'])
            self.assertEqual(result['sha256'],hashlib.sha256((tree/'SHIRUBE-SOURCE-EXPORT.json').read_bytes()).hexdigest())
            self.assertFalse(list(tree.glob('*.tar.*')))

    def test_last_header_drift_alias_and_unknown_policy_reject_before_replacement(self):
        for change in ['header','alias','policy']:
            with self.subTest(change=change),tempfile.TemporaryDirectory() as temporary:
                inputs,tree,constants,rules=self.fixture(Path(temporary))
                html=tree/exporter.HTML_MEMBER;before=html.read_bytes()
                if change=='header':(tree/exporter.HEADER_MEMBER).write_bytes(b'different header')
                elif change=='alias':
                    header=tree/exporter.HEADER_MEMBER;data=header.read_bytes();header.unlink()
                    target=tree/'alias';target.write_bytes(data);header.symlink_to(target)
                else:rules['components'][0]['replacement']['member']='runtime.cpp'
                with patch.multiple(exporter,**constants),self.assertRaises(ValueError):
                    prepare('opentracing-cpp','1.6.0-4',inputs,tree,rules)
                self.assertEqual(html.read_bytes(),before)
                self.assertFalse((tree/'SHIRUBE-SOURCE-EXPORT.json').exists())

    def test_real_export_permission_does_not_clear_the_raw_html_source_archive(self):
        from producer.collect import approval_for
        root=Path(__file__).resolve().parents[1]
        audit=json.loads((root/'license-audit.json').read_text())['components']
        proof=json.loads((root/'docs/evidence/diagnostic-debian-opentracing-preferred-source-export.json').read_text())
        self.assertEqual(approval_for(audit,'opentracing-cpp','1.6.0-4',exporter.DSC,root),{})
        selected=approval_for(audit,'opentracing-cpp','1.6.0-4',proof['preferred_source_identity'],root)
        self.assertTrue(selected['redistributable'])
        self.assertEqual(selected['original_source_identity'],exporter.DSC)
