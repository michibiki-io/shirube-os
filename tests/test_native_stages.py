"""Bounded source/SDK and dependency contracts with synthetic files."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from producer.native_stages import select_jobs, export_vpp_sdk, snapshot_files, copy_selected, restore_ocaml_evidence
from producer.collect import tracked_source_files, vpp_source_files, files
from producer.staged import DEPENDENCIES, merge_tree, build_scope
from producer.pipeline import complete_candidate


class NativeStagesTests(unittest.TestCase):
    def test_all_26_jobs_exactly_once_and_all_six_edges_retained(self):
        catalog=json.loads(Path('containers/native/packages.json').read_text())['jobs']
        native=select_jobs(catalog,'native');kernel=select_jobs(catalog,'kernel')
        self.assertEqual(len(native),25);self.assertEqual([r['name'] for r in kernel],['kernel'])
        self.assertEqual(sum(len(j.get('requires',[])) for j in native),6)
        self.assertTrue(all(d in {j['name'] for j in native} for j in native for d in j.get('requires',[])))
        self.assertEqual(DEPENDENCIES['kernel'],('vpp',))
        self.assertEqual(DEPENDENCIES['integration'],('native','vpp'))
        self.assertEqual(DEPENDENCIES['guests'],('clearance',))
        self.assertEqual(select_jobs(catalog,'salt'),[])

    def test_sdk_requires_generated_headers_and_libraries_and_excludes_objects(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);vpp=root/'vpp';out=root/'out'
            def fixture(name,data=b'header'):
                p=vpp/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(data)
            fixture('src/vpp-api/client.h');fixture('src/vpp-api/client.c',b'not SDK')
            fixture('build-root/build-vpp-native/vpp/CMakeFiles/vpp-api/generated.h')
            fixture('build-root/build-vpp-native/vpp/CMakeFiles/vpp-api/object.o')
            fixture('build-root/install-vpp-native/vpp/lib/test-triplet/libvpp.so',
                    b'\x7fELF\x02\x01'+b'\0'*12+(62).to_bytes(2,'little'))
            export_vpp_sdk(vpp,out,'test-triplet','amd64')
            names={str(p.relative_to(out)) for p in out.rglob('*') if p.is_file()}
            self.assertEqual(names,{'sdk.json','source/vpp-api/client.h','generated/generated.h','lib/libvpp.so'})
            (vpp/'build-root/build-vpp-native/vpp/CMakeFiles/vpp-api/generated.h').unlink()
            with self.assertRaises(ValueError):export_vpp_sdk(vpp,root/'bad','test-triplet','amd64')

    def test_snapshot_export_selects_original_paths_and_explicit_dependency_inputs(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);preferred=root/'preferred';actual=root/'actual'
            preferred.mkdir();actual.mkdir()
            (preferred/'main.c').write_text('upstream');(actual/'main.c').write_text('patched')
            (actual/'compiler-scratch').write_text('unrelated')
            (actual/'package').mkdir();(actual/'package/binary').write_text('built binary')
            (actual/'shirube-corresponding-inputs').mkdir()
            (actual/'shirube-corresponding-inputs/inventory.json').write_text('[]')
            selected=dict((name,path.read_text()) for path,name in snapshot_files(actual,preferred))
            self.assertEqual(selected,{'main.c':'patched','shirube-corresponding-inputs/inventory.json':'[]'})

    def test_inventoried_source_uses_no_git_or_compiler_state(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);(root/'main.c').write_text('source')
            (root/'shirube-stage-source.json').write_text('{"kind":"tracked","members":["main.c"]}')
            with patch('producer.collect.subprocess.check_output',side_effect=AssertionError('No git allowed')):
                self.assertEqual([name for _,name in tracked_source_files(root)],['main.c'])
                self.assertEqual([name for _,name in vpp_source_files(root)],['main.c'])

    def test_conflicting_producer_outputs_block_merge(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);a=root/'a';b=root/'b';a.mkdir();b.mkdir()
            (a/'notice').write_text('one');(b/'notice').write_text('two')
            with self.assertRaises(ValueError):merge_tree(a,b)

    def test_no_disk_work_without_source_and_license_clearance(self):
        with patch('producer.vm.build_disks') as disks:
            for source,reconstruction in [({'cleared':False},{'proof':{'status':'PASS_SOURCE_SET_ONLY'}}),
                                           ({'cleared':True},{'proof':{'status':'FAIL'}})]:
                with self.assertRaises(ValueError):
                    complete_candidate({},source,reconstruction,Path('/unused'),'amd64','v2026.03.0','a'*40,None)
            disks.assert_not_called()


class StageEntryPointTests(unittest.TestCase):
    def test_synthetic_cold_warm_rerun_and_corrupt_cache_rebuild(self):
        """Exercise the real transfer/cache dispatch, mocking only compilation."""
        import sys
        from types import SimpleNamespace
        from producer import staged
        from producer.stage_artifacts import scope
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);src=root/'src';src.mkdir()
            (src/'native').mkdir()
            (src/'native/packages.json').write_text('{"jobs":[]}')
            (src/'source-lock.json').write_text('{"stream":{"version":"2026.03"}}')
            expected=scope('native','amd64',{'recipe':'a'*64},'sha256:'+'b'*64,{})
            original_path=Path
            def mapped(value='.',*parts):
                value=str(value)
                for prefix,name in [('/src','src'),('/build-private','work'),('/stage-dependencies','dependencies')]:
                    if value==prefix or value.startswith(prefix+'/'):
                        return original_path(root/name/value[len(prefix):].lstrip('/'),*parts)
                return original_path(value,*parts)
            builds=[]
            def synthetic_compile():
                builds.append(tuple(sys.argv))
                output=original_path(sys.argv[sys.argv.index('--output')+1])
                payload=output/'stage';payload.mkdir()
                (payload/'preferred-source.c').write_text('synthetic preferred source')
                (payload/'fixture.deb').write_text('mock package; never compiled')
            def invoke(name,commit='c'*40):
                output=root/name;output.mkdir()
                # Each invocation represents a fresh disposable container.
                work=root/'work'
                if work.exists():
                    import shutil
                    shutil.rmtree(work)
                with patch.object(staged,'Path',side_effect=mapped), \
                     patch('producer.architecture.native'), \
                     patch('producer.library_cache.compiler_fingerprint',return_value='sha256:'+'b'*64), \
                     patch.object(staged,'restore_inputs',return_value=({},{})), \
                     patch.object(staged,'build_scope',return_value=expected), \
                     patch.dict(sys.modules,{'build':SimpleNamespace(main=synthetic_compile)}), \
                     patch.object(sys,'argv',['staged','native','amd64','--commit',commit,'--version','v2026.03.0',
                                              '--output',str(output),'--cache',str(root/'cache')]), \
                     patch.object(sys,'path',list(sys.path)):
                    staged.main()
                return json.loads((output/'transfer.json').read_text()),json.loads((output/'resources.json').read_text())
            first,cold=invoke('cold')
            self.assertEqual(cold['cache'],'MISS');self.assertEqual(len(builds),1)
            second,warm=invoke('warm','d'*40)
            self.assertEqual(warm['cache'],'HIT');self.assertEqual(len(builds),1)
            self.assertEqual(second['source_commit'],'d'*40)
            self.assertEqual(second['content_source_commit'],'c'*40)
            self.assertEqual(first['payload_id'],second['payload_id'])
            (root/'cache'/expected['input_id']/'transfer.tar').write_bytes(b'corrupt fixture')
            third,retry=invoke('retry')
            self.assertEqual(retry['cache'],'MISS_INVALID');self.assertEqual(len(builds),2)
            self.assertEqual(first['payload_id'],third['payload_id'])


class SourceHandoffTests(unittest.TestCase):
    def test_ocaml_archive_and_notices_rebound_without_compiler_switch(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);material=root/'material';material.mkdir();(material/'notices').mkdir()
            (material/'sources.tar.gz').write_bytes(b'synthetic source archive')
            (material/'notices/component.copyright').write_text('original public notice')
            receipt={'records':{'sources':[{'asset':'sources.tar.gz','member':'source.c','sha256':'a'*64}]},
                     'components':[{'notice':'/old/private/compiler/component.copyright',
                                    'sources':[{'asset':'sources.tar.gz','member':'source.c','sha256':'a'*64}]}]}
            (material/'receipt.json').write_text(json.dumps(receipt))
            records,components=restore_ocaml_evidence(material,root/'release-source.tar.gz',root/'work')
            self.assertEqual(records['sources'][0]['asset'],'release-source.tar.gz')
            self.assertEqual(components[0]['sources'][0]['asset'],'release-source.tar.gz')
            self.assertEqual(components[0]['notice'],root/'work/component.copyright')
            self.assertEqual(components[0]['notice'].read_text(),'original public notice')

    def test_guest_stage_consumes_same_iso_generation_and_certificate_without_assembly(self):
        import sys
        from producer import staged
        from producer.sources import sha256
        for changed in (False,True):
            with self.subTest(changed=changed),tempfile.TemporaryDirectory() as temporary:
                root=Path(temporary);payload=root/'cleared';payload.mkdir();output=root/'output';output.mkdir()
                kernel=payload/'guest-kernel';kernel.mkdir()
                (kernel/'linux-image-fixture_1_amd64.deb').write_bytes(b'synthetic kernel package')
                (kernel/'cert.der').write_bytes(b'public certificate')
                iso=payload/'shirube-os-v2026.03.0-amd64.iso';iso.write_bytes(b'same built ISO')
                (payload/'manifest.json').write_text('{"manifest_kind":"candidate-source-set"}')
                (payload/'licenses.json').write_text('{}');(payload/'source.tar.gz').write_bytes(b'synthetic sources')
                record={'source_commit':'a'*40,'version':'v2026.03.0','architecture':'amd64',
                        'assembly':{'iso':iso.name,'generation':'d'*64,'kernel_certificate_sha256':sha256(kernel/'cert.der')},
                        'iso_sha256':sha256(iso),'branding_verified':True,
                        'source':{'inventory':'licenses.json','archives':['source.tar.gz'],'cleared':True},
                        'reconstruction':{'proof':{'status':'PASS_SOURCE_SET_ONLY'}}}
                project=root/'project-source.tar';project.write_bytes(b'synthetic exact project source')
                assets=[{'name':name,'architecture':'amd64','role':role,'size':(payload/name).stat().st_size,
                         'sha256':sha256(payload/name)} for name,role in [('licenses.json','licenses'),('source.tar.gz','source')]]
                header={'source_commit':'a'*40,'version':'v2026.03.0','source_license_clearance':'PASS',
                        'manifest_kind':'candidate-source-set','assets':assets}
                proof={'status':'PASS_SOURCE_SET_ONLY','source_commit':'a'*40,'architecture':'amd64',
                       'actual_project_build_input_binding':'PASS','actual_project_build_input_sha256':sha256(project)}
                record['reconstruction']={'input_manifest':header,'proof':proof}
                (payload/'manifest.json').write_text(json.dumps({**header,'source_set_reconstruction':record['reconstruction']}))
                (payload/'clearance.json').write_text(json.dumps(record))
                if changed:iso.write_bytes(b'changed ISO')
                original_path=Path
                def mapped(value='.',*parts):
                    return original_path(root/'debs',*parts) if str(value)=='/debs' else original_path(value,*parts)
                verify=staged.verify_clearance
                with patch.object(staged,'Path',side_effect=mapped),patch('producer.packages.audit'), \
                     patch.object(staged,'verify_clearance',side_effect=lambda *args:verify(*args,project=project)), \
                     patch('producer.pipeline.complete_candidate') as complete, \
                     patch.dict(sys.modules,{'image':type('NoAssembly',(),{})}):
                    if changed:
                        with self.assertRaisesRegex(ValueError,'Cleared ISO changed'):
                            staged.guests({'clearance':payload},output,'amd64','v2026.03.0','a'*40,None,'tcg')
                        complete.assert_not_called()
                    else:
                        staged.guests({'clearance':payload},output,'amd64','v2026.03.0','a'*40,None,'tcg')
                        result=complete.call_args.args[0]
                        self.assertEqual(result['generation'],'d'*64)
                        self.assertEqual(result['kernel_certificate_sha256'],record['assembly']['kernel_certificate_sha256'])
                        self.assertEqual(sha256(result['iso']),record['iso_sha256'])
                        self.assertFalse((output/'guest-kernel').exists())
                        self.assertFalse((output/'manifest.json').exists())


class PrerequisiteTests(unittest.TestCase):
    def test_missing_extra_and_cross_arch_prerequisites_fail_before_consumption(self):
        from producer import staged
        from producer.stage_artifacts import pack, scope
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);inputs=root/'inputs';inputs.mkdir();payload=root/'payload';payload.mkdir()
            (payload/'notice').write_text('synthetic VPP payload')
            expected=scope('vpp','amd64',{'source':'a'*64},'sha256:'+'b'*64,{})
            transfer=pack(payload,inputs/'vpp.tar',expected,'c'*40,'v2026.03.0')
            (inputs/'vpp.json').write_text(json.dumps(transfer))
            with patch.object(staged,'build_scope',return_value=expected):
                ids,restored=staged.restore_inputs(inputs,root/'good','kernel','amd64','c'*40,'v2026.03.0','sha256:'+'b'*64)
                self.assertEqual(set(ids),{'vpp'});self.assertEqual(set(restored),{'vpp'})
                (inputs/'unexpected').write_text('extra')
                with self.assertRaisesRegex(ValueError,'Missing/extra'):
                    staged.restore_inputs(inputs,root/'bad','kernel','amd64','c'*40,'v2026.03.0','sha256:'+'b'*64)
                (inputs/'unexpected').unlink();(inputs/'vpp.tar').unlink()
                with self.assertRaisesRegex(ValueError,'Missing/extra'):
                    staged.restore_inputs(inputs,root/'bad','kernel','amd64','c'*40,'v2026.03.0','sha256:'+'b'*64)
            expected_arm=scope('vpp','arm64',{'source':'a'*64},'sha256:'+'b'*64,{})
            pack(payload,inputs/'vpp.tar',expected_arm,'c'*40,'v2026.03.0')
            with patch.object(staged,'build_scope',return_value=expected),self.assertRaises(ValueError):
                staged.restore_inputs(inputs,root/'bad','kernel','amd64','c'*40,'v2026.03.0','sha256:'+'b'*64)


class AssemblyHandoffTests(unittest.TestCase):
    def test_export_contains_actual_assembled_debs_after_metadata_repair(self):
        import sys
        from types import SimpleNamespace
        from producer import staged
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);merged=root/'merged';(merged/'debs').mkdir(parents=True)
            (merged/'debs/original_1_amd64.deb').write_bytes(b'synthetic original package')
            actual=root/'actual-debs';actual.mkdir()
            output=root/'output';output.mkdir()
            original_path=Path
            def mapped(value='.',*parts):
                return original_path(actual,*parts) if str(value)=='/debs' else original_path(value,*parts)
            def assemble(sources,packages,architecture,version,destination,*,prepared):
                self.assertEqual(packages,merged/'debs');self.assertEqual(prepared,merged)
                (actual/'corrected_1+local_amd64.deb').write_bytes(b'actual ISO package metadata')
                iso=destination/'image.iso';iso.write_bytes(b'synthetic ISO; never compiled')
                return {'iso':iso}
            with patch.object(staged,'Path',side_effect=mapped), \
                 patch.object(staged,'merged_compile_material',return_value=merged), \
                 patch.object(staged,'link_material'), \
                 patch.dict(sys.modules,{'image':SimpleNamespace(assemble=assemble)}), \
                 patch.object(sys,'path',list(sys.path)):
                staged.iso({},output,'amd64','v2026.03.0')
            self.assertEqual([p.name for p in (output/'debs').iterdir()],['corrected_1+local_amd64.deb'])


class ContentIdentityTests(unittest.TestCase):
    def test_license_decisions_and_guest_changes_do_not_recompile_dependencies(self):
        from producer.staged import build_scope
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            for name in ['source-lock.json','native/Dockerfile','native/build.py','native/package.sh',
                         'native/extras.py.in','native/image.py','producer/recipe.py','LICENSE',
                         'shirube-os-logo.svg','NOTICE','license-audit.json','guest/service.py']:
                path=root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text('synthetic input')
            compiler='sha256:'+'b'*64
            before=build_scope(root,'vpp','amd64',compiler,{})
            downstream=build_scope(root,'clearance','amd64',compiler,{'iso':'c'*64})
            (root/'license-audit.json').write_text('new notice decision')
            (root/'guest/service.py').write_text('new guest behavior')
            self.assertEqual(before,build_scope(root,'vpp','amd64',compiler,{}))
            self.assertNotEqual(downstream,build_scope(root,'clearance','amd64',compiler,{'iso':'c'*64}))
            (root/'native/extras.py.in').write_text('changed compiler recipe')
            self.assertNotEqual(before,build_scope(root,'vpp','amd64',compiler,{}))
