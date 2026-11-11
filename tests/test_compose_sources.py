"""Source reuse must retain input integrity and refuse stale package/notice data."""
import contextlib
import hashlib
import io
import json
from pathlib import Path
import runpy
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch


class ComposeSourceTests(unittest.TestCase):
    def prepare(self,root,batched=False):
        repo=Path(__file__).resolve().parents[1];lock=json.loads((repo/'source-lock.json').read_text())
        for name in ['base','additions','notices/alpha','overlays','output']:(root/name).mkdir(parents=True)
        notice=b'Copyright fixture; MIT permission fixture\n';(root/'notices/alpha/copyright').write_bytes(notice)
        records={key:[] for key in ['sources','license_files','build_inputs']}
        with tarfile.open(root/'base/source.tar.gz','w:gz') as archive:
            for member,data,category in [('source.c',b'fixture source\n','sources'),('copyright',notice,'license_files'),('recipe',b'fixture recipe\n','build_inputs')]:
                if batched:member='components/original-source/'+member
                header=tarfile.TarInfo(member);header.size=len(data);archive.addfile(header,io.BytesIO(data))
                records[category].append({'asset':'source.tar.gz','member':member,'sha256':hashlib.sha256(data).hexdigest()})
        component={'name':'alpha','version':'1','architecture':'amd64','source_package':'alpha','source_version':'1',
                   'source_identity':'f'*64,'license':'MIT','redistributable':True,'bundled_components':[],**records}
        (root/'base/inventory.json').write_text(json.dumps({'components':[component],'shipped_packages':[{'name':'alpha','version':'1','architecture':'amd64'}]}))
        assets=[]
        for name,role in [('source.tar.gz','source'),('inventory.json','licenses')]:
            p=root/'base'/name;assets.append({'name':name,'role':role,'architecture':'amd64','size':p.stat().st_size,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()})
        (root/'base/manifest.json').write_text(json.dumps({'project':'Shirube OS','version':'v2026.03.0','source_commit':'b'*40,
            'upstream':{'version':lock['stream']['version'],'sha256':lock['stream']['sha256'],
                        'vpp_commit':lock['external_git']['vpp']['commit'],'vpp_patches_commit':lock['external_git']['vpp-patches']['commit']},'assets':assets}))
        (root/'additions/source-delta.json').write_text(json.dumps({'kind':'partial-source-additions','components':[],'assets':[]}))
        (root/'packages').write_text('alpha\t1\tamd64\talpha\t1\n')
        (root/'overlays/overlays.json').write_text('{}')
        with tarfile.open(root/'project.tar','w',format=tarfile.PAX_FORMAT,pax_headers={'comment':'a'*40}) as archive:
            archive.addfile(tarfile.TarInfo('fixture'))
        return repo/'scripts/compose-initialization-sources.py'

    def invoke(self,root,script):
        argv=[str(script),'--commit','a'*40,'--architecture','amd64','--version','v2026.03.0']
        for key in ['base','additions','packages','notices','overlays','output','work']:
            argv+=['--'+key,str(root/key)]
        argv+=['--project-source',str(root/'project.tar')]
        with patch.object(sys,'argv',argv),contextlib.redirect_stdout(io.StringIO()):
            runpy.run_path(str(script))['main']()

    def test_verified_source_reuse_retains_original_bytes_and_never_clears_binaries(self):
        with tempfile.TemporaryDirectory() as temporary,patch('subprocess.run') as command:
            root=Path(temporary);script=self.prepare(root);before=(root/'base/source.tar.gz').read_bytes()
            self.invoke(root,script)
            self.assertEqual((root/'base/source.tar.gz').read_bytes(),before)
            receipt=json.loads((root/'output/source-set.json').read_text())
            self.assertEqual(receipt['source_license_clearance'],'PASS')
            self.assertEqual(receipt['release_clearance'],'NOT_CLEARED')
            self.assertEqual(receipt['binary_compilation'],'NOT_PERFORMED')
            inventory=json.loads((root/'output/shirube-os-v2026.03.0-amd64-licenses.json').read_text())
            refs=inventory['components'][0]['license_files']
            self.assertTrue(any(r['member']=='distribution-notices/NOTICE' for r in refs))
            command.assert_not_called()

    def test_changed_package_version_cannot_inherit_cached_sources(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);script=self.prepare(root);(root/'packages').write_text('alpha\t2\tamd64\talpha\t2\n')
            with self.assertRaisesRegex(ValueError,'differ from installed packages'):self.invoke(root,script)

    def test_batched_installed_notice_keeps_exact_original_binding(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);script=self.prepare(root,batched=True)
            self.invoke(root,script)
            inventory=json.loads((root/'output/shirube-os-v2026.03.0-amd64-licenses.json').read_text())
            self.assertEqual(inventory['components'][0]['license_files'][0]['member'],
                             'components/original-source/copyright')

    def test_changed_notice_cannot_inherit_cached_permission(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);script=self.prepare(root);(root/'notices/alpha/copyright').write_text('different terms')
            with self.assertRaisesRegex(ValueError,'lacks an exact permission decision'):self.invoke(root,script)

    def test_corrupt_source_archive_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);script=self.prepare(root)
            with (root/'base/source.tar.gz').open('ab') as out:out.write(b'changed')
            with self.assertRaisesRegex(ValueError,'asset missing or changed'):self.invoke(root,script)
