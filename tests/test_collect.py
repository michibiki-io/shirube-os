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
            data={'architecture':'amd64','restored_offline_component_rebuild':'PASS',
                'reference_binary_sha256':digest(binary),'compiler':'1.26.10','go_source_sha256':'b'*64,
                'go_notice_sha256':digest(standard),'linked_modules':[{'module':'example.test/module','version':'v1.0.0',
                'source_identity':'h1:fixture','source':{'path':'inputs/'+archive.name,'sha256':digest(archive)},
                'license_notice':{'path':'notices/'+notice.name,'sha256':digest(notice)}}]}
            bom=doc/'netbird-components.json';bom.write_text(json.dumps(data))
            (correspondence/'components.json').write_bytes(bom.read_bytes())
            self.assertEqual(len(netbird_dependencies(root,correspondence,'amd64')),2)
            for victim in [binary,notice,standard]:
                original=victim.read_bytes();victim.write_bytes(b'changed')
                with self.assertRaises(ValueError):netbird_dependencies(root,correspondence,'amd64')
                victim.write_bytes(original)
            with self.assertRaises(ValueError):netbird_dependencies(root,correspondence,'arm64')

    def test_source_specific_approval_does_not_overwrite_another_identity(self):
        entries={'component@1':{'source_identity':'a'},'component@1#b':{'source_identity':'b'}}
        self.assertEqual(approval_for(entries,'component','1','a')['source_identity'],'a')
        self.assertEqual(approval_for(entries,'component','1','b')['source_identity'],'b')
        self.assertNotEqual(approval_for(entries,'component','1','c')['source_identity'],'c')
