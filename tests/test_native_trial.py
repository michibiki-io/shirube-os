import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from producer.native_trial import receipt


class NativeTrialReceiptTests(unittest.TestCase):
    def fixture(self, root):
        version='v2026.03.0';arch='amd64';commit='a'*40
        name=f'shirube-os-{version}-{arch}.iso';(root/name).write_bytes(b'exact produced ISO')
        inventory=f'shirube-os-{version}-{arch}-licenses.json'
        source=f'shirube-os-{version}-{arch}-source-0001.tar.gz'
        (root/inventory).write_text('{}');(root/source).write_bytes(b'exact source')
        record={'kind':'iso-build','project':'Shirube OS','architecture':arch,
                'version':version,'source_commit':commit,'source_signature':'PASS',
                'source_collection':'PASS','iso':name,
                'sha256':hashlib.sha256((root/name).read_bytes()).hexdigest(),
                'source_archives':[source],'source_inventory':inventory,
                'release_clearance':'NOT_CLEARED','license_clearance':'FAIL'}
        return record,arch,version,commit

    def test_iso_source_receipt_does_not_imply_license_or_release_clearance(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);record,arch,version,commit=self.fixture(root)
            (root/'iso-build.json').write_text(json.dumps(record))
            accepted=receipt(root,arch,version,commit)
            self.assertEqual(accepted['release_clearance'],'NOT_CLEARED')
            self.assertEqual(accepted['license_clearance'],'FAIL')

    def test_stale_head_missing_sources_and_changed_iso_cannot_pass(self):
        for failure in ['stale','collection','bytes','missing-source','traversal']:
            with self.subTest(failure=failure),tempfile.TemporaryDirectory() as temporary:
                root=Path(temporary);record,arch,version,commit=self.fixture(root)
                if failure=='stale':record['source_commit']='b'*40
                elif failure=='collection':record['source_collection']='NOT_RUN'
                elif failure=='bytes':(root/record['iso']).write_bytes(b'other ISO')
                elif failure=='missing-source':(root/record['source_archives'][0]).unlink()
                else:record['source_archives']=['../../outside.tar.gz']
                (root/'iso-build.json').write_text(json.dumps(record))
                with self.assertRaises(ValueError):receipt(root,arch,version,commit)
