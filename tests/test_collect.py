"""Source/notice binding fixtures; these do not certify a guest or license."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from producer.collect import netbird_dependencies,approval_for,shared_build_inputs,vpp_source_files
import tarfile
import subprocess


class ComponentInventoryTests(unittest.TestCase):
    def test_preferred_source_selection_excludes_generated_staging_but_keeps_source_tests(self):
        from producer.collect import files
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)/'source';root.mkdir()
            for member in ['scripts/package/mkdebian','tests/public-vector.pem',
                           'debian/linux-image/DEBIAN/control','.git/objects/history']:
                path=root/member;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(member)
            selected={member for _,member in files(root,exclude=('debian',))}
            self.assertEqual(selected,{'scripts/package/mkdebian','tests/public-vector.pem'})
            outside=Path(temporary)/'outside';outside.write_text('not component source')
            (root/'external.c').symlink_to(outside)
            with self.assertRaisesRegex(ValueError,'escapes component'):
                list(files(root,exclude=('debian',)))

    def test_vpp_export_keeps_patched_sources_and_original_external_archives(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            subprocess.run(['git','init','-q',str(root)],check=True)
            (root/'source.c').write_text('original source')
            (root/'build-root').mkdir();(root/'build-root/Makefile').write_text('upstream recipe')
            (root/'build-root/alias').symlink_to('..',target_is_directory=True)
            subprocess.run(['git','-C',str(root),'add','.'],check=True)
            (root/'source.c').write_text('patched source')
            (root/'build-root/generated-binary').write_bytes(b'generated runtime')
            cache=root/'build/external/downloads';cache.mkdir(parents=True)
            (cache/'dependency.tar.gz').write_bytes(b'original dependency archive')
            selected=dict((member,path.read_bytes()) for path,member in vpp_source_files(root))
            self.assertEqual(selected['source.c'],b'patched source')
            self.assertIn('build-root/Makefile',selected)
            self.assertIn('build/external/downloads/dependency.tar.gz',selected)
            self.assertNotIn('build-root/generated-binary',selected)
            self.assertNotIn('build-root/alias/build-root/generated-binary',selected)
            self.assertEqual(selected['build-root/alias/source.c'],b'patched source')
            (root/'source.c').unlink()
            with self.assertRaisesRegex(ValueError,'Missing patched'):list(vpp_source_files(root))

    def test_shared_sources_preserve_real_recipe_patch_and_notice_bytes(self):
        with tempfile.TemporaryDirectory() as temporary:
            base=Path(temporary);project=base/'project.tar';project.write_bytes(b'project fixture')
            builder=base/'builder';patches=base/'patches'
            for root in [builder,patches]:
                root.mkdir();(root/'LICENSE').write_text('fixture upstream notice')
            (builder/'kernel.cfg').write_text('fixture kernel configuration')
            (patches/'change.patch').write_text('fixture upstream patch')
            archive=base/'shared.tar.gz'
            refs=shared_build_inputs(project,builder,patches,archive)
            with tarfile.open(archive) as source:
                for ref in refs:
                    self.assertEqual(hashlib.sha256(source.extractfile(ref['member']).read()).hexdigest(),ref['sha256'])
            self.assertTrue({'stream-builder/kernel.cfg','vpp-patches/change.patch',
                'stream-builder/LICENSE','vpp-patches/LICENSE','project-source.tar'}.issubset({r['member'] for r in refs}))
            (patches/'LICENSE').unlink()
            with self.assertRaises(ValueError):shared_build_inputs(project,builder,patches,base/'missing.tar.gz')

    def test_netbird_inventory_is_bound_to_shipped_binary_and_notices(self):
        with tempfile.TemporaryDirectory() as temporary:
            base=Path(temporary);root=base/'root';(root/'etc').mkdir(parents=True)
            doc=root/'usr/share/doc/shirube-integration';doc.mkdir(parents=True)
            binary=root/'usr/libexec/shirube/netbird';binary.parent.mkdir(parents=True);binary.write_bytes(b'fixture client')
            correspondence=base/'inputs';(correspondence/'linked/inputs').mkdir(parents=True)
            archive=correspondence/'linked/inputs'/('a'*64+'.zip');archive.write_bytes(b'fixture source archive')
            notice=doc/'netbird-licenses/notices'/('a'*64+'.txt');notice.parent.mkdir(parents=True);notice.write_bytes(b'fixture module notice')
            standard=doc/'netbird-licenses/go-copyright';standard.write_bytes(b'fixture Go notice')
            digest=lambda path:hashlib.sha256(path.read_bytes()).hexdigest()
            mapping=correspondence/'internal-packages.json'
            mapping.write_text(json.dumps({'packages':[{'import':'fixture client package'}],'agpl_directory_packages':[]}))
            data={'architecture':'amd64','restored_offline_component_rebuild':'PASS',
                'reference_binary_sha256':digest(binary),'compiler':'1.26.10','go_source_sha256':'b'*64,
                'internal_package_map_sha256':digest(mapping),'internal_package_count':1,'agpl_directory_packages':[],
                'go_notice_sha256':digest(standard),'linked_modules':[{'module':'example.test/module','version':'v1.0.0',
                'source_identity':'h1:fixture','source':{'path':'inputs/'+archive.name,'sha256':digest(archive)},
                'license_notice':{'path':'notices/'+notice.name,'sha256':digest(notice)}}]}
            bom=doc/'netbird-components.json';bom.write_text(json.dumps(data))
            (correspondence/'components.json').write_bytes(bom.read_bytes())
            self.assertEqual(len(netbird_dependencies(root,correspondence,'amd64')),2)
            for victim in [binary,notice,standard,mapping]:
                original=victim.read_bytes();victim.write_bytes(b'changed')
                with self.assertRaises(ValueError):netbird_dependencies(root,correspondence,'amd64')
                victim.write_bytes(original)
            with self.assertRaises(ValueError):netbird_dependencies(root,correspondence,'arm64')

    def test_source_specific_approval_does_not_overwrite_another_identity(self):
        entries={'component@1':{'source_identity':'a'},'component@1#b':{'source_identity':'b'}}
        self.assertEqual(approval_for(entries,'component','1','a')['source_identity'],'a')
        self.assertEqual(approval_for(entries,'component','1','b')['source_identity'],'b')
        self.assertNotEqual(approval_for(entries,'component','1','c')['source_identity'],'c')

class DistributorNoticeTests(unittest.TestCase):
    def test_required_notice_hash_and_retained_members_gate_clearance(self):
        from producer.collect import distribution_notices
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);(root/'LICENSES').mkdir()
            (root/'NOTICE').write_text('required attribution')
            (root/'LICENSE').write_text('own license')
            extra=root/'LICENSES/extra.txt';extra.write_text('original upstream license')
            record={'source_identity':'source-fixture','redistributable':True,
                    'required_notice_files':[{'path':'LICENSES/extra.txt','sha256':hashlib.sha256(extra.read_bytes()).hexdigest()}]}
            approvals={'example@1':record}
            self.assertEqual(approval_for(approvals,'example','1','source-fixture',root),record)
            self.assertEqual({name for _,name in distribution_notices(root)}, {'NOTICE','LICENSE','LICENSES/extra.txt'})
            extra.write_text('changed terms')
            self.assertEqual(approval_for(approvals,'example','1','source-fixture',root),{})
            extra.unlink()
            self.assertEqual(approval_for(approvals,'example','1','source-fixture',root),{})
            record['required_notice_files'][0]['path']='../outside.txt'
            with self.assertRaisesRegex(ValueError,'Unsafe required'):
                approval_for(approvals,'example','1','source-fixture',root)

    def test_image_must_contain_the_exact_required_distributor_notices(self):
        from producer.collect import verify_distribution_notices
        with tempfile.TemporaryDirectory() as temporary:
            base=Path(temporary);project=base/'project';guest=base/'guest'
            (project/'LICENSES').mkdir(parents=True);(guest/'etc').mkdir(parents=True)
            doc=guest/'usr/share/doc/shirube-integration';(doc/'distribution-licenses').mkdir(parents=True)
            for original,installed,text in [('LICENSE','distributor-LICENSE','own grant'),('NOTICE','NOTICE','required acknowledgement'),('LICENSES/extra.txt','distribution-licenses/extra.txt','original inherited conditions')]:
                (project/original).write_text(text);(doc/installed).write_text(text)
            verify_distribution_notices(guest,project)
            (doc/'distribution-licenses/extra.txt').write_text('incomplete conditions')
            with self.assertRaisesRegex(ValueError,'absent or differs'):
                verify_distribution_notices(guest,project)
