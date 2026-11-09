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
    def test_project_recipe_binds_one_shared_archive_without_copying_it(self):
        from producer.collect import project_recipe
        from producer.sources import sha256
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);project=root/'project.tar';project.write_bytes(b'complete project source')
            ref={'asset':'shared.tar.gz','member':'project-source.tar','sha256':sha256(project)}
            recipe=project_recipe(project,[ref],root/'recipe.json')
            self.assertEqual(json.loads(recipe.read_text())['project_source'],ref)
            with self.assertRaisesRegex(ValueError,'shared project'):
                project_recipe(project,[],root/'missing.json')
            project.write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError,'shared project'):
                project_recipe(project,[ref],root/'changed.json')

    def test_shared_cargo_leaf_keeps_both_build_closure_references(self):
        import copy
        from producer.collect import merge_bundled_components
        first={'name':'cargo:shared','version':'1','source_identity':'a'*64,
               'license':'UNREVIEWED','redistributable':False,
               'sources':[{'asset':'source.tar.gz','member':'first/source.crate','sha256':'a'*64}],
               'license_files':[{'asset':'source.tar.gz','member':'first/notice','sha256':'b'*64}],
               'build_inputs':[{'asset':'source.tar.gz','member':'recipe','sha256':'c'*64}]}
        second=copy.deepcopy(first)
        second['sources'][0]['member']='second/source.crate'
        second['license_files'][0]['member']='second/notice'
        merged=merge_bundled_components([first,second])
        self.assertEqual(len(merged),1)
        self.assertEqual(len(merged[0]['sources']),2)
        self.assertEqual(len(merged[0]['license_files']),2)
        self.assertEqual(len(merged[0]['build_inputs']),1)
        self.assertFalse(merged[0]['redistributable'])
        self.assertEqual(len(first['sources']),1)
        for field,value in [('source_identity','d'*64),('license','MIT'),('redistributable',True)]:
            other=copy.deepcopy(second);other[field]=value
            with self.subTest(field=field),self.assertRaisesRegex(ValueError,'Conflicting bundled'):
                merge_bundled_components([first,other])
        for category in ['sources','license_files']:
            other=copy.deepcopy(second);other[category][0]['sha256']='e'*64
            with self.subTest(category=category),self.assertRaisesRegex(ValueError,'Conflicting bundled'):
                merge_bundled_components([first,other])

    def test_batch_source_plan_distinguishes_exact_custom_packages_from_debian_sources(self):
        from producer.collect import debian_requirements
        listing=('native\t1\tamd64\tnative-source\t1\n'
                 'podman\t4\tamd64\tpodman\t4\n'
                 'debian-helper\t2+b1\tamd64\tpodman\t2\n'
                 'simple\t3\tall\t\t\n')
        produced={'native':{('1','amd64')},'podman':{('4','amd64')}}
        self.assertEqual(debian_requirements(listing,produced,{'native':Path('native')},{'podman':'podman'}),
                         {('podman','2'),('simple','3')})
        with self.assertRaisesRegex(ValueError,'differs from produced'):
            debian_requirements(listing.replace('native\t1\t','native\t9\t'),produced,{'native':Path('native')},{'podman':'podman'})

    def test_salt_version_stamp_must_match_the_shipped_source_version(self):
        from producer.collect import salt_version_input
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);(root/'etc').mkdir()
            path=root/'opt/saltstack/salt/lib/python3.11/site-packages/salt/_version.txt'
            path.parent.mkdir(parents=True);path.write_text('3006.21+0.ge35fd3e')
            self.assertEqual(salt_version_input(root,'3006.21+0.ge35fd3e').read_text(),'3006.21+0.ge35fd3e')
            for content in ['3006.21','3006.21+0.ge35fd3e\nextra state']:
                path.write_text(content)
                with self.assertRaisesRegex(ValueError,'version stamp'):
                    salt_version_input(root,'3006.21+0.ge35fd3e')

    def test_kea_export_keeps_preferred_code_and_prepared_recipes_without_build_state(self):
        from producer.collect import kea_source_files
        with tempfile.TemporaryDirectory() as temporary:
            base=Path(temporary);preferred=base/'original';prepared=base/'compiled';packaging=base/'packaging'
            (preferred/'src').mkdir(parents=True);(preferred/'src/main.cc').write_text('original code')
            (packaging/'debian').mkdir(parents=True);(prepared/'debian').mkdir(parents=True)
            for name in ['rules','control','changelog']:
                (packaging/'debian'/name).write_text('upstream recipe')
                (prepared/'debian'/name).write_text('prepared '+name)
            subprocess.run(['git','init','-q',str(packaging)],check=True)
            subprocess.run(['git','-C',str(packaging),'add','.'],check=True)
            (prepared/'debian/not-installed').write_text('intentional documentation exclusions')
            for member in ['build/generated-test-key.pem','debian/isc-kea/usr/bin/generated']:
                file=prepared/member;file.parent.mkdir(parents=True,exist_ok=True);file.write_text('generated')
            archive=prepared/'shirube-corresponding-inputs/meson-1.tar.gz'
            archive.parent.mkdir();archive.write_bytes(b'original Meson source')
            tool={'version':'1','sha256':hashlib.sha256(archive.read_bytes()).hexdigest()}
            selected={name:path.read_bytes() for path,name in kea_source_files(prepared,preferred,packaging,tool)}
            self.assertEqual(selected['src/main.cc'],b'original code')
            self.assertEqual(selected['debian/rules'],b'prepared rules')
            self.assertIn('debian/not-installed',selected)
            self.assertFalse(any(name.startswith('build/') or '/usr/bin/' in name for name in selected))
            archive.write_bytes(b'changed tool')
            with self.assertRaisesRegex(ValueError,'Meson build source differs'):
                list(kea_source_files(prepared,preferred,packaging,tool))

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
