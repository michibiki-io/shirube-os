import copy
import hashlib
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest
from producer.vpp_legal import install, verify
from producer.release import verify_vpp_inputs
from producer.sources import sha256


class VPPOriginalNoticeTests(unittest.TestCase):
    def test_exact_archive_legal_bytes_installed_and_rechecked(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); downloads=root/'downloads';downloads.mkdir();native=root/'native';native.mkdir()
            for kind,name in [('external','library'),('python','tool')]:
                path=downloads/(name+'.tar.gz');legal=b'Original complete synthetic license and attribution\n'
                with tarfile.open(path,'w:gz') as archive:
                    member=tarfile.TarInfo(name+'/LICENSE');member.size=len(legal);archive.addfile(member,io.BytesIO(legal))
                lock={'source_commit':'a'*40,'patches_commit':'b'*40,'inputs':[{'name':name,'version':'1','filename':path.name,'url':'https://example.test/'+path.name,'sha256':sha256(path),'legal_notices':[{'member':name+'/LICENSE','sha256':hashlib.sha256(legal).hexdigest()}]}]}
                (native/('vpp-'+kind+'.lock.json')).write_text(json.dumps(lock))
            out=root/'installed';inventory=install(downloads,native,out)
            self.assertEqual(verify(downloads,native,out),inventory)
            self.assertEqual(len(inventory['components']),2)
            notice=out/inventory['components'][0]['notice']['filename'];self.assertIn(legal,notice.read_bytes())
            notice.write_bytes(b'copyright omitted')
            with self.assertRaisesRegex(ValueError,'legal notice'):
                verify(downloads,native,out)
            with tarfile.open(downloads/'unrecorded.tar.gz','w:gz'):pass
            with self.assertRaisesRegex(ValueError,'incomplete or unexpected'):
                verify(downloads,native,out)

    def test_external_archive_cannot_hide_under_parent_license(self):
        source={'asset':'source.tar.gz','member':'source/build/external/downloads/input.tar.gz','sha256':'a'*64}
        notice={'asset':'source.tar.gz','member':'notice','sha256':'b'*64}
        child={'name':'vpp-native:library','version':'1','source_identity':'a'*64,'sources':[source],'license_files':[notice]}
        origin={'vpp_retained_inputs':{child['name']:{'version':'1','source_identity':'a'*64,'notice_sha256':'b'*64}}}
        parent={'sources':[source],'source_origin':origin,'source_identity':hashlib.sha256(json.dumps(origin,sort_keys=True).encode()).hexdigest(),'bundled_components':[child]}
        verify_vpp_inputs(parent)
        missing=copy.deepcopy(parent);missing['bundled_components']=[]
        with self.assertRaisesRegex(ValueError,'Complete VPP'):
            verify_vpp_inputs(missing)
        changed=copy.deepcopy(parent);changed['bundled_components'][0]['source_identity']='c'*64
        with self.assertRaisesRegex(ValueError,'binding differs'):
            verify_vpp_inputs(changed)
