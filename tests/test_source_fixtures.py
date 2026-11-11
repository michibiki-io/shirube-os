"""Exact public-source fixture recognition never grants a binary-key exception."""
import copy
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from producer.bundles import bundle
from producer.release import scan_secrets,verify_sources
from producer.source_batches import batch


class PublicSourceFixtureTests(unittest.TestCase):
    def test_batched_debian_source_keeps_exact_fixture_binding(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);vector=root/'vector';vector.write_bytes(b'-----BEGIN PRIVATE KEY-----\n'+b'A'*64+b'\n')
            plain=root/'plain';plain.write_text('ordinary source')
            notice=root/'notice';notice.write_text('permission notice')
            recipe=root/'recipe';recipe.write_text('build recipe')
            origin='debian:'+'a'*64;member='source/test/vector';digest=hashlib.sha256(vector.read_bytes()).hexdigest()
            with patch.dict('producer.source_fixtures.KNOWN',{origin:{member:digest}}):
                components=[];archives=[]
                for number,data in enumerate([vector,plain,plain]):
                    archive=root/f'shirube-os-v2026.03.0-amd64-source-{number:04d}.tar.gz'
                    refs=bundle([(data,member,'sources'),(notice,'copyright','license_files'),(recipe,'recipe','build_inputs')],archive,0,source_origin=origin)
                    components.append({'name':'fixture'+str(number),'version':'1','architecture':'amd64','license':'MIT','redistributable':True,
                        'source_origin':{'debian_preferred_source_export':{'original_descriptor_sha256':'a'*64}},**refs})
                    archives.append(archive)
                parts,replaced=batch(archives,components,root,'amd64','v2026.03.0')
                self.assertEqual(len(parts),1)
                self.assertNotIn(archives[0].name,replaced)
                self.assertTrue(archives[0].is_file())
                self.assertEqual(components[0]['sources'][0]['member'],member)
                assets=[{'name':p.name,'role':'source'}for p in [archives[0],*parts]]
                inventory={'components':components,'shipped_packages':[{k:c[k]for k in ['name','version','architecture']}for c in components]}
                verify_sources(root,inventory,assets)
                for asset in assets:scan_secrets(root/asset['name'])
                for mutation in ['architecture','version','directory','digest','origin','category']:
                    changed=copy.deepcopy(inventory);component=changed['components'][0];ref=component['sources'][0]
                    if mutation=='architecture':ref['asset']=ref['asset'].replace('-amd64-source-','-arm64-source-')
                    elif mutation=='version':ref['asset']=ref['asset'].replace('v2026.03.0','v2026.04.0')
                    elif mutation=='directory':ref['member']=ref['member'].replace('source/','elsewhere/',1)
                    elif mutation=='digest':ref['sha256']='b'*64
                    elif mutation=='origin':component['source_origin']['debian_preferred_source_export']['original_descriptor_sha256']='b'*64
                    else:component['build_inputs'].append(component['sources'].pop())
                    with self.subTest(mutation=mutation),self.assertRaisesRegex(ValueError,'Private signing key'):
                        verify_sources(root,changed,assets)

    def test_origin_member_and_whole_file_must_match_in_both_source_checks(self):
        for kind, field, length in [('stream','stream_archive_sha256',64),
                                    ('netbird','netbird_source_commit',40),
                                    ('debian','debian_preferred_source_export',64)]:
            with self.subTest(kind=kind):
                self.check_source_origin(kind,field,length)

    def check_source_origin(self,kind,field,length):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);fixture=root/'vector';fixture.write_bytes(b'-----BEGIN PRIVATE KEY-----\n'+b'A'*64+b'\n')
            notice=root/'notice';notice.write_text('fixture permission notice')
            recipe=root/'recipe';recipe.write_text('fixture build recipe')
            member='source/test/vector';origin=kind+':'+'a'*length
            digest=hashlib.sha256(fixture.read_bytes()).hexdigest()
            inputs=[(fixture,member,'sources'),(notice,'copyright','license_files'),(recipe,'recipe','build_inputs')]
            with patch.dict('producer.source_fixtures.KNOWN',{origin:{member:digest}}):
                archive=root/'source.tar.gz';records=bundle(inputs,archive,0,source_origin=origin)
                package={'name':'fixture','version':'1','architecture':'amd64'}
                provenance=({'original_descriptor_sha256':'a'*length} if kind=='debian' else 'a'*length)
                component={**package,**records,'license':'MIT','redistributable':True,
                    'source_origin':{field:provenance}}
                inventory={'components':[component],'shipped_packages':[package]}
                assets=[{'name':archive.name,'role':'source'}]
                verify_sources(root,inventory,assets)
                changed=copy.deepcopy(inventory);changed['components'][0].pop('source_origin')
                with self.assertRaisesRegex(ValueError,'Private signing key'):verify_sources(root,changed,assets)
                if kind=='debian':
                    changed=copy.deepcopy(inventory)
                    changed['components'][0]['source_origin'][field]['original_descriptor_sha256']='b'*length
                    with self.assertRaisesRegex(ValueError,'Private signing key'):verify_sources(root,changed,assets)
                with self.assertRaisesRegex(ValueError,'Private key'):scan_secrets(fixture)
                with self.assertRaisesRegex(ValueError,'Private key'):bundle(inputs,root/'wrong-origin.tar.gz',0,source_origin='other')
                renamed=[(fixture,member+'-other','sources'),*inputs[1:]]
                with self.assertRaisesRegex(ValueError,'Private key'):bundle(renamed,root/'wrong-member.tar.gz',0,source_origin=origin)
                misplaced=[(fixture,member,'build_inputs'),*inputs[1:]]
                with self.assertRaisesRegex(ValueError,'Private key'):bundle(misplaced,root/'wrong-category.tar.gz',0,source_origin=origin)
                fixture.write_bytes(fixture.read_bytes()+b'changed')
                with self.assertRaisesRegex(ValueError,'Private key'):bundle(inputs,root/'changed.tar.gz',0,source_origin=origin)
