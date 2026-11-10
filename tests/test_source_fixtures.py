"""Exact public-source fixture recognition never grants a binary-key exception."""
import copy
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from producer.bundles import bundle
from producer.release import scan_secrets,verify_sources


class PublicSourceFixtureTests(unittest.TestCase):
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
