import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest
from producer.bundles import bundle
from producer.reconstruction import restore
from producer.release import verify_sources
from producer.sources import sha256


class SourceRestorationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory();self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name);self.output = self.root/'restored'
        self.commit = 'a'*40
        inputs = []
        for name,category in [('source.c','sources'),('COPYING','license_files'),('build.sh','build_inputs')]:
            file = self.root/name;file.write_text('synthetic '+name)
            if name=='build.sh':file.chmod(0o755)
            inputs.append((file,name,category))
        self.archive = self.root/'component.tar.gz'
        records = bundle(inputs,self.archive,0)
        package = {'name':'synthetic','version':'1','architecture':'amd64'}
        self.inventory = {'shipped_packages':[package], 'components':[
            {**package,'license':'UNREVIEWED','redistributable':False,**records}]}
        self.manifest = {'project':'Shirube OS','provider':'michibiki-io',
                         'manifest_kind':'diagnostic-source-set','source_commit':self.commit,'assets':[]}
        self.write()

    def write(self):
        path = self.root/'inventory.json';path.write_text(json.dumps(self.inventory))
        self.manifest['assets'] = [
            {'name':p.name,'role':role,'architecture':'amd64','sha256':sha256(p),'size':p.stat().st_size}
            for p,role in [(self.archive,'source'),(path,'licenses')]]
        (self.root/'manifest.json').write_text(json.dumps(self.manifest))

    def project_source(self):
        project=self.root/'project-source.tar'
        with tarfile.open(project,'w',format=tarfile.PAX_FORMAT,pax_headers={'comment':self.commit}) as archive:
            member=tarfile.TarInfo('producer.py');data=b'synthetic project';member.size=len(data)
            archive.addfile(member,io.BytesIO(data))
        self.archive.unlink()
        records=bundle([(self.root/name,name,category) for name,category in [
            ('source.c','sources'),('COPYING','license_files'),('build.sh','build_inputs')]]+
            [(project,'project-source.tar','build_inputs')],self.archive,0)
        self.inventory['components'][0].update(records);self.write()
        (self.root/'manifest.json').unlink()
        return project,{'inventory':self.root/'inventory.json','archives':[self.archive],'cleared':False}

    def test_candidate_check_binds_actual_build_project_and_keeps_uncleared_state(self):
        from producer.pipeline import source_set_evidence
        project,source=self.project_source()
        result=source_set_evidence(source,self.root,'amd64','v2026.03.0',self.commit,self.output,project)
        self.assertEqual(result['proof']['actual_project_build_input_sha256'],sha256(project))
        self.assertEqual(result['proof']['actual_project_build_input_binding'],'PASS')
        self.assertFalse(self.output.exists())
        candidate=json.loads((self.root/'manifest.json').read_text())
        self.assertEqual(candidate['manifest_kind'],'candidate-source-set')
        self.assertEqual(candidate['source_license_clearance'],'FAIL')
        self.assertEqual(candidate['release_clearance'],'NOT_CLEARED')
        self.assertEqual(candidate['source_set_reconstruction'],result)
        from producer.release import validate
        with self.assertRaisesRegex(ValueError,'Release identity/clean-source mismatch'):
            validate(self.root,self.commit,'v2026.03.0')
        with self.assertRaisesRegex(ValueError,'license clearance'):
            verify_sources(self.root,self.inventory,candidate['assets'])

    def test_candidate_check_rejects_wrong_project_revision_or_missing_recipe_binding(self):
        from producer.pipeline import source_set_evidence
        project,source=self.project_source()
        with self.assertRaisesRegex(ValueError,'archive commit differs'):
            source_set_evidence(source,self.root,'amd64','v2026.03.0','b'*40,self.output,project)
        self.inventory['components'][0]['build_inputs']=[r for r in self.inventory['components'][0]['build_inputs']
                                                       if r['member']!='project-source.tar']
        (self.root/'inventory.json').write_text(json.dumps(self.inventory))
        with self.assertRaisesRegex(ValueError,'actual project build input'):
            source_set_evidence(source,self.root,'amd64','v2026.03.0',self.commit,self.output,project)
        self.assertFalse(self.output.exists())

    def test_uncleared_release_stops_before_expensive_guest_tests(self):
        import sys
        from types import SimpleNamespace
        from unittest import mock
        from producer.pipeline import release_candidate
        with mock.patch.dict(sys.modules,{'image':SimpleNamespace(assemble=lambda *args:{'iso':self.root/'os.iso'})}), \
             mock.patch('producer.pipeline.collect_iso_sources',return_value=(self.root,{'cleared':False})), \
             mock.patch('producer.pipeline.source_set_evidence',return_value={}), \
             mock.patch('producer.vm.build_disks') as guests:
            with self.assertRaisesRegex(ValueError,'redistribution permission'):
                release_candidate(self.root,self.root,self.root,'amd64','v2026.03.0',self.commit,None,'tcg')
            guests.assert_not_called()

    def test_complete_restoration_does_not_waive_publication_license_gate(self):
        proof = restore(self.root,self.output,'amd64',self.commit)
        self.assertEqual(proof['status'],'PASS_SOURCE_SET_ONLY')
        self.assertEqual(proof['binary_reconstruction'],'NOT_RUN')
        self.assertEqual(proof['release_clearance'],'NOT_CLEARED')
        target = self.output/self.archive.name
        self.assertEqual((target/'source.c').read_bytes(),(self.root/'source.c').read_bytes())
        self.assertEqual((target/'build.sh').stat().st_mode & 0o777,0o755)
        self.assertEqual(proof['restored_regular_members'],3)
        self.assertEqual(json.loads((self.output/'restoration.json').read_text()),proof)
        with self.assertRaisesRegex(ValueError,'license clearance'):
            verify_sources(self.root,self.inventory,self.manifest['assets'])

    def test_wrong_commit_outer_archive_or_member_digest_cannot_restore(self):
        with self.assertRaisesRegex(ValueError,'identity differs'):
            restore(self.root,self.output,'amd64','b'*40)
        self.inventory['components'][0]['sources'][0]['sha256']='0'*64;self.write()
        with self.assertRaisesRegex(ValueError,'corresponding-source member'):
            restore(self.root,self.output,'amd64',self.commit)
        self.archive.write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError,'differing source restoration input'):
            restore(self.root,self.output,'amd64',self.commit)
        self.assertFalse(self.output.exists())

    def test_existing_user_directory_is_preserved(self):
        self.output.mkdir();marker=self.output/'user-file';marker.write_text('retain')
        with self.assertRaises(FileExistsError):restore(self.root,self.output,'amd64',self.commit)
        self.assertEqual(marker.read_text(),'retain')

    def test_wrong_architecture_and_duplicate_asset_are_rejected(self):
        self.inventory['shipped_packages'][0]['architecture']='arm64';self.write()
        with self.assertRaisesRegex(ValueError,'another architecture'):
            restore(self.root,self.output,'amd64',self.commit)
        self.inventory['shipped_packages'][0]['architecture']='amd64';self.write()
        self.manifest['assets'].append(self.manifest['assets'][0])
        (self.root/'manifest.json').write_text(json.dumps(self.manifest))
        with self.assertRaisesRegex(ValueError,'duplicate'):
            restore(self.root,self.output,'amd64',self.commit)

    def test_traversal_history_and_links_fail_before_any_destination_is_created(self):
        for name,kind in [('../escape',tarfile.REGTYPE),('.git/config',tarfile.REGTYPE),('link',tarfile.SYMTYPE)]:
            with self.subTest(name=name):
                with tarfile.open(self.archive,'w:gz') as archive:
                    member=tarfile.TarInfo(name);member.type=kind;member.size=1 if kind==tarfile.REGTYPE else 0
                    if kind==tarfile.SYMTYPE:member.linkname='/outside'
                    archive.addfile(member,io.BytesIO(b'x') if member.size else None)
                self.write()
                with self.assertRaisesRegex(ValueError,'Unsafe source archive member'):
                    restore(self.root,self.output,'amd64',self.commit)
                self.assertFalse(self.output.exists())

    def test_member_hierarchy_collision_removes_only_new_partial_output(self):
        import hashlib
        names = [('source','sources'),('COPYING','license_files'),('source/build','build_inputs')]
        with tarfile.open(self.archive,'w:gz') as archive:
            for name,category in names:
                member=tarfile.TarInfo(name);member.size=1;archive.addfile(member,io.BytesIO(b'x'))
                self.inventory['components'][0][category]=[
                    {'asset':self.archive.name,'member':name,'sha256':hashlib.sha256(b'x').hexdigest()}]
        self.write()
        with self.assertRaises(FileExistsError):restore(self.root,self.output,'amd64',self.commit)
        self.assertFalse(self.output.exists())
        self.assertTrue(self.archive.is_file())
