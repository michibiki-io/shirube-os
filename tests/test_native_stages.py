"""Bounded source/SDK and dependency contracts with synthetic files."""
import json
import copy
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest
from unittest.mock import patch

from producer.native_stages import select_jobs, snapshot_files, copy_selected, restore_ocaml_evidence
from producer.compile_vpp import export_vpp_sdk
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


class StagedI2utilTests(unittest.TestCase):
    """Synthetic Git observations and real source exports/bundles; no compilation."""

    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name)
        self.lock={'url':'https://example.invalid/i2util','commit':'a'*40}
        self.source=self.root/'extra/base/i2util';self.source.mkdir(parents=True)
        for name,text in [('main.c','original source'),('LICENSE','synthetic upstream notice'),
                          ('debian/copyright','synthetic package notice')]:
            path=self.source/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(text)
        subprocess.run(['git','init','-q',str(self.source)],check=True)
        subprocess.run(['git','-C',str(self.source),'add','.'],check=True)
        # Retain actual patched bytes, rather than the index's original bytes.
        (self.source/'main.c').write_text('patched source')
        (self.source/'generated.o').write_bytes(b'untracked compiler output')
        native=self.root/'src/native';native.mkdir(parents=True)
        (native/'packages.json').write_text(json.dumps({'jobs':[]}))
        self.lockfile=native/'external-sources.lock.json'
        self.lockfile.write_text(json.dumps({'i2util':self.lock}))
        self.sources=self.root/'sources'
        builder=self.sources/'vyos-build/vyos-build';builder.mkdir(parents=True)
        (builder/'LICENSE').write_text('synthetic builder notice')
        self.material=self.root/'export/material'
        self.staged=self.material/'extra/base/i2util'
        self.project=self.root/'project.tar';self.project.write_bytes(b'synthetic project input')
        self.output=self.root/'output';self.output.mkdir()

    def mapped_path(self,value='.',*parts):
        value=str(value)
        for prefix in ['/src','/extra','/debs']:
            if value==prefix or value.startswith(prefix+'/'):
                return self.root/value.lstrip('/')/Path(*parts)
        return Path(value,*parts)

    def export(self,revision=None):
        from producer import native_stages, compile_runtime
        original=subprocess.check_output
        def observe(args,**kwargs):
            if args[-2:]==['rev-parse','HEAD']:
                self.assertEqual(args,['git','-C',str(self.source),'rev-parse','HEAD'])
                return revision if revision is not None else self.lock['commit']+'\n'
            return original(args,**kwargs)
        with patch.object(native_stages,'Path',side_effect=self.mapped_path), \
             patch.object(compile_runtime,'Path',side_effect=self.mapped_path), \
             patch.object(native_stages,'component',return_value=compile_runtime), \
             patch.object(compile_runtime,'SNAPSHOTS',{}), \
             patch.object(compile_runtime,'GIT_SOURCES',{'runtime':('base/i2util',)}), \
             patch.object(subprocess,'check_output',side_effect=observe) as git:
            native_stages.export_compile('runtime',self.sources,self.root/'packages',
                                         self.root/'export','amd64')
            self.assertTrue(any(call.args[0][-2:]==['rev-parse','HEAD'] for call in git.call_args_list))

    def collect(self,material=True):
        from producer import collect
        with patch.object(collect,'Path',side_effect=self.mapped_path):
            return collect.static_i2util(self.output,'amd64','v2026.03.0',self.project,[],
                                        self.material if material else None)

    def test_export_and_static_collection_without_git(self):
        self.export()
        metadata=json.loads((self.staged/'shirube-stage-source.json').read_text())
        self.assertEqual(metadata['revision_evidence']['commit'],self.lock['commit'])
        self.assertEqual({r['name'] for r in metadata['revision_evidence']['files']},
                         {'main.c','LICENSE','debian/copyright'})
        self.assertFalse((self.staged/'.git').exists())
        self.assertFalse((self.staged/'generated.o').exists())
        with patch.object(subprocess,'check_output',side_effect=AssertionError('No Git in clearance')):
            archive,record=self.collect()
        self.assertEqual(record['name'],'i2util')
        self.assertEqual(len(record['license_files']),2)
        with tarfile.open(archive) as bundle:
            self.assertEqual(bundle.extractfile('source/main.c').read(),b'patched source')
            self.assertEqual(bundle.extractfile('copyright').read(),b'synthetic package notice')

    def test_export_rejects_wrong_git_revision(self):
        with self.assertRaisesRegex(ValueError,'source revision differs'):
            self.export('b'*40+'\n')
        self.assertFalse(self.staged.exists())

    def test_staged_evidence_fails_closed_without_git_fallback(self):
        self.export()
        marker=self.staged/'shirube-stage-source.json'
        original=json.loads(marker.read_text())
        variants=[]
        for field,value in [('commit','b'*40),('lock_sha256','0'*64),('schema_version',2),
                            ('files',[])]:
            changed=copy.deepcopy(original);changed['revision_evidence'][field]=value
            variants.append((field,changed))
        missing=copy.deepcopy(original);del missing['revision_evidence']
        variants.extend([('missing evidence',missing),('missing marker',None)])
        changed=copy.deepcopy(original);changed['revision_evidence']['files'][0]['sha256']='0'*64
        variants.append(('tampered source hash',changed))
        changed=copy.deepcopy(original);changed['kind']='unverified'
        variants.append(('wrong source kind',changed))
        changed=copy.deepcopy(original);changed['members'].remove('main.c')
        variants.append(('changed member inventory',changed))
        with patch.object(subprocess,'check_output',side_effect=AssertionError('No Git fallback')):
            for name,data in variants:
                with self.subTest(name=name):
                    if data is None:marker.unlink()
                    else:marker.write_text(json.dumps(data))
                    with self.assertRaises(ValueError):self.collect()
                    self.assertEqual(list(self.output.iterdir()),[])
            marker.write_text(json.dumps(original))
            for name in ['main.c','LICENSE','debian/copyright']:
                path=self.staged/name;content=path.read_bytes()
                for altered in [b'tampered bytes',None]:
                    with self.subTest(member=name,altered=altered):
                        if altered is None:path.unlink()
                        else:path.write_bytes(altered)
                        with self.assertRaises(ValueError):self.collect()
                        path.write_bytes(content)
            marker.unlink();marker.symlink_to(self.source/'main.c')
            with self.assertRaisesRegex(ValueError,'missing/unsafe'):self.collect()
            marker.unlink();marker.write_text(json.dumps(original))
            for field,value in [('commit','b'*40),('url','https://example.invalid/other')]:
                self.lockfile.write_text(json.dumps({'i2util':{**self.lock,field:value}}))
                with self.subTest(lock=field),self.assertRaises(ValueError):self.collect()

    def test_legacy_source_still_requires_git_revision(self):
        original=subprocess.check_output
        def observe(args,**kwargs):
            return self.lock['commit']+'\n' if args[-2:]==['rev-parse','HEAD'] else original(args,**kwargs)
        with patch.object(subprocess,'check_output',side_effect=observe):
            archive,_=self.collect(material=False)
        self.assertTrue(archive.is_file())
        with patch.object(subprocess,'check_output',return_value='b'*40+'\n'):
            with self.assertRaisesRegex(ValueError,'source revision differs'):self.collect(material=False)




class StageEntryPointTests(unittest.TestCase):
    def test_synthetic_cold_warm_rerun_and_corrupt_cache_rebuild(self):
        """Exercise the real transfer/cache dispatch, mocking only compilation."""
        import sys
        from types import SimpleNamespace
        from producer import staged, staged_compile
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);src=root/'src';src.mkdir()
            from component_fixture import component_project
            from producer.component_inputs import outer_key
            component_project(src, container=True)
            expected=build_scope(src,'native','amd64','sha256:'+'b'*64,{})
            hosted_store = {}
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
            def invoke(name,commit='c'*40, compiler='sha256:'+'b'*64):
                nonlocal expected
                expected=build_scope(src,'native','amd64',compiler,{})
                key=outer_key(expected)
                # Model exact immutable hosted restore/save with disposable local cache.
                import shutil
                cache=root/'cache'
                if cache.exists(): shutil.rmtree(cache)
                if key in hosted_store: shutil.copytree(hosted_store[key],cache)
                output=root/name;output.mkdir()
                # Each invocation represents a fresh disposable container.
                work=root/'work'
                if work.exists():
                    import shutil
                    shutil.rmtree(work)
                with patch.object(staged_compile,'Path',side_effect=mapped), \
                     patch('producer.architecture.native'), \
                     patch('producer.library_cache.compiler_fingerprint',return_value=compiler), \
                     patch.object(staged_compile,'restore_inputs',return_value=({},{})), \
                     patch.dict(sys.modules,{'native':SimpleNamespace(build_compile=SimpleNamespace(main=synthetic_compile))}), \
                     patch.object(sys,'argv',['staged','native','amd64','--commit',commit,'--version','v2026.03.0',
                                              '--output',str(output),'--cache',str(root/'cache')]), \
                     patch.object(sys,'path',list(sys.path)):
                    staged.main()
                if key not in hosted_store:
                    stored=root/('hosted-'+str(len(hosted_store)))
                    shutil.copytree(cache,stored);hosted_store[key]=stored
                return json.loads((output/'transfer.json').read_text()),json.loads((output/'resources.json').read_text())
            first,cold=invoke('cold')
            self.assertEqual(cold['cache'],'MISS');self.assertEqual(len(builds),1)
            second,warm=invoke('warm','d'*40)
            self.assertEqual(warm['cache'],'HIT');self.assertEqual(len(builds),1)
            self.assertEqual(second['source_commit'],'d'*40)
            self.assertEqual(second['content_source_commit'],'c'*40)
            self.assertEqual(first['payload_id'],second['payload_id'])
            (hosted_store[outer_key(expected)]/expected['input_id']/'transfer.tar').write_bytes(b'corrupt fixture')
            third,retry=invoke('retry')
            self.assertEqual(retry['cache'],'MISS_INVALID');self.assertEqual(len(builds),2)
            self.assertEqual(first['payload_id'],third['payload_id'])
            # Same declared manifest, different actual compiler: new immutable
            # outer key gets saved; the following fresh runner is an inner HIT.
            fourth,changed=invoke('changed-compiler', compiler='sha256:'+'e'*64)
            self.assertEqual(changed['cache'],'MISS');self.assertEqual(len(builds),3)
            self.assertEqual(first['scope']['inputs'],fourth['scope']['inputs'])
            self.assertEqual(len(hosted_store),2)
            fifth,rewarm=invoke('changed-compiler-warm','f'*40,compiler='sha256:'+'e'*64)
            self.assertEqual(rewarm['cache'],'HIT');self.assertEqual(len(builds),3)
            self.assertEqual(fifth['source_commit'],'f'*40)
            self.assertEqual(fifth['content_source_commit'],'c'*40)
            self.assertEqual(fourth['payload_id'],fifth['payload_id'])


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
        from producer import staged_release as staged
        from producer.sources import sha256
        from itertools import product
        from producer.pipeline import complete_candidate
        from types import SimpleNamespace
        for arch, changed in product(('amd64', 'arm64'), (False, True, 'proof', 'certificate', 'kernel', 'audit', 'storage')):
            with self.subTest(arch=arch, changed=changed),tempfile.TemporaryDirectory() as temporary:
                root=Path(temporary);payload=root/'cleared';payload.mkdir();output=root/'output';output.mkdir()
                kernel=payload/'guest-kernel';kernel.mkdir()
                (kernel/f'linux-image-fixture_1_{arch}.deb').write_bytes(b'synthetic kernel package')
                (kernel/'cert.der').write_bytes(b'public certificate')
                iso=payload/f'shirube-os-v2026.03.0-{arch}.iso';iso.write_bytes(b'same built ISO')
                (payload/'manifest.json').write_text('{"manifest_kind":"candidate-source-set"}')
                (payload/'licenses.json').write_text('{}');(payload/'source.tar.gz').write_bytes(b'synthetic sources')
                record={'source_commit':'a'*40,'version':'v2026.03.0','architecture':arch,
                        'assembly':{'iso':iso.name,'generation':'d'*64,'kernel_certificate_sha256':sha256(kernel/'cert.der')},
                        'iso_sha256':sha256(iso),'branding_verified':True,
                        'source':{'inventory':'licenses.json','archives':['source.tar.gz'],'cleared':True},
                        'reconstruction':{'proof':{'status':'PASS_SOURCE_SET_ONLY'}}}
                project=root/'project-source.tar';project.write_bytes(b'synthetic exact project source')
                assets=[{'name':name,'architecture':arch,'role':role,'size':(payload/name).stat().st_size,
                         'sha256':sha256(payload/name)} for name,role in [('licenses.json','licenses'),('source.tar.gz','source')]]
                header={'source_commit':'a'*40,'version':'v2026.03.0','source_license_clearance':'PASS',
                        'manifest_kind':'candidate-source-set','assets':assets}
                proof={'status':'PASS_SOURCE_SET_ONLY','source_commit':'a'*40,'architecture':arch,
                       'actual_project_build_input_binding':'PASS','actual_project_build_input_sha256':sha256(project)}
                record['reconstruction']={'input_manifest':header,'proof':proof}
                (payload/'manifest.json').write_text(json.dumps({**header,'source_set_reconstruction':record['reconstruction']}))
                (payload/'clearance.json').write_text(json.dumps(record))
                if changed is True:iso.write_bytes(b'changed ISO')
                if changed == 'proof': record['reconstruction']['proof']['status']='FAIL'
                if changed == 'certificate': (kernel/'cert.der').write_bytes(b'changed cert')
                if changed == 'kernel': (kernel/f'linux-image-fixture_1_{arch}.deb').unlink()
                (payload/'clearance.json').write_text(json.dumps(record))
                original_path=Path
                def mapped(value='.',*parts):
                    return original_path(root/'debs',*parts) if str(value)=='/debs' else original_path(value,*parts)
                verify=staged.verify_clearance
                with patch.object(staged,'Path',side_effect=mapped), \
                     patch('producer.packages.audit', side_effect=ValueError('kernel audit failed') if changed=='audit' else None) as audit, \
                     patch.object(staged,'verify_clearance',side_effect=lambda *args:verify(*args,project=project)), \
                     patch('producer.pipeline.complete_candidate', side_effect=complete_candidate if changed=='storage' else None) as complete, \
                     patch('producer.architecture.release_accelerator'), \
                     patch('shutil.disk_usage', return_value=SimpleNamespace(free=55*1024**3)), \
                     patch('producer.vm.build_disks') as disks, \
                     patch.dict(sys.modules,{'image':type('NoAssembly',(),{})}):
                    if changed:
                        message={True:'Cleared ISO changed', 'proof':'Cleared source proof',
                                 'certificate':'kernel certificate', 'kernel':'One cleared guest',
                                 'audit':'kernel audit failed', 'storage':'Fresh guest disk stage'}[changed]
                        with self.assertRaisesRegex(ValueError,message):
                            staged.guests({'clearance':payload},output,arch,'v2026.03.0','a'*40,None,'tcg')
                        if changed=='storage':
                            complete.assert_called_once()
                            audit.assert_called_once_with(kernel/f'linux-image-fixture_1_{arch}.deb',arch,kernel/'cert.der')
                        else: complete.assert_not_called()
                        disks.assert_not_called()
                    else:
                        staged.guests({'clearance':payload},output,arch,'v2026.03.0','a'*40,None,'tcg')
                        audit.assert_called_once_with(kernel/f'linux-image-fixture_1_{arch}.deb',arch,kernel/'cert.der')
                        result=complete.call_args.args[0]
                        self.assertEqual(result['generation'],'d'*64)
                        self.assertEqual(result['kernel_certificate_sha256'],record['assembly']['kernel_certificate_sha256'])
                        self.assertEqual(sha256(result['iso']),record['iso_sha256'])
                        self.assertFalse((output/'guest-kernel').exists())
                        self.assertFalse((output/'manifest.json').exists())


class PrerequisiteTests(unittest.TestCase):
    def test_missing_extra_and_cross_arch_prerequisites_fail_before_consumption(self):
        from producer import staged_release as staged
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
        from producer import staged_release as staged
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
            root=Path(temporary)/'src'
            from component_fixture import component_project
            component_project(root, container=True)
            (root/'guest').mkdir()
            (root/'guest/service.py').write_text('synthetic guest')
            compiler='sha256:'+'b'*64
            before=build_scope(root,'vpp','amd64',compiler,{})
            downstream=build_scope(root,'clearance','amd64',compiler,{'iso':'c'*64})
            (root/'license-audit.json').write_text('new notice decision')
            (root/'guest/service.py').write_text('new guest behavior')
            self.assertEqual(before,build_scope(root,'vpp','amd64',compiler,{}))
            self.assertNotEqual(downstream,build_scope(root,'clearance','amd64',compiler,{'iso':'c'*64}))
            path=root/'native/extra_vpp.py.in'
            path.write_text(path.read_text()+'\n# changed compiler recipe\n')
            self.assertNotEqual(before,build_scope(root,'vpp','amd64',compiler,{}))


class StageLauncherTests(unittest.TestCase):
    def test_exact_stage_capabilities_mounts_and_credential_boundary(self):
        import importlib.util
        import sys
        spec = importlib.util.spec_from_file_location('stage_launcher', 'scripts/run-native-stage.py')
        launcher = importlib.util.module_from_spec(spec);spec.loader.exec_module(launcher)
        for stage, discard in [(s, False) for s in launcher.STAGES] + [(s, True) for s in ('iso', 'clearance', 'guests')]:
            with self.subTest(stage=stage, discard=discard), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary); output = root/'output'; inputs = root/'inputs'; cache = root/'cache'
                image = 'sha256:'+'b'*64; commit = 'a'*40
                def read(command, **kwargs):
                    if command == ['git','rev-parse','HEAD']: return commit+'\n'
                    if command == ['git','status','--porcelain']: return ''
                    if command == ['docker','image','inspect',image,'--format','{{.Architecture}}']: return 'amd64\n'
                    self.fail(str(command))
                def run(*command, **kwargs):
                    if command[:2] == ('git','archive'): return
                    (output/'resources.json').write_text('{}')
                    if stage != 'guests':
                        for suffix in ('tar','json'): (output/('transfer.'+suffix)).write_text('fixture')
                argv = ['launcher','--stage',stage,'--architecture','amd64','--image',image,
                        '--commit',commit,'--version','v2026.03.0','--output',str(output),
                        '--inputs',str(inputs),'--cache',str(cache),
                        *(['--discard-input-archives'] if discard else [])]
                with patch.object(sys,'argv',argv), patch.object(launcher,'native'), \
                     patch.object(launcher.subprocess,'check_output',side_effect=read), \
                     patch.object(launcher,'run',side_effect=run) as execute, \
                     patch.dict('os.environ', {'GH_TOKEN':'never-forward','GITHUB_TOKEN':'never-forward'}):
                    launcher.main()
                command = execute.call_args.args
                if stage == 'guests':
                    project = command[command.index('--project-source')+1]
                    self.assertTrue(project.endswith('/project-source.tar'))
                    self.assertEqual(command, (sys.executable,'scripts/run-release-container.py',
                        '--architecture','amd64','--image',image,'--output',str(output.resolve()),
                        '--project-source',project,'--private',str(Path(project).parent/'fixture'),
                        '--version','v2026.03.0','--commit',commit,'--qemu-accelerator','tcg',
                        '--staged-inputs',str(inputs.resolve()),
                        *(['--discard-input-archives'] if discard else [])))
                    continue
                privileges = {'iso':['--privileged'], 'integration':['--cap-add=SYS_ADMIN']}.get(stage, [])
                prefix = ('docker','run','--rm',*privileges,'--cpus=4','--memory=8g',
                          '--memory-swap=8g','--pids-limit=4096')
                self.assertEqual(command[:len(prefix)], prefix)
                tail = command[len(prefix):]
                project_mount = tail[5]
                self.assertTrue(project_mount.endswith('/project-source.tar:/project-source.tar:ro'))
                self.assertEqual(tail, ('-v',str(output.resolve())+':/output',
                    '-v',str(inputs.resolve())+':/inputs:'+('rw' if discard else 'ro'),'-v',project_mount,
                    '-v',str(cache.resolve())+':/stage-cache','--entrypoint','python3',image,
                    '-m','producer.staged',stage,'amd64','--commit',commit,'--version','v2026.03.0',
                    '--cache','/stage-cache',*(['--discard-input-archives'] if discard else [])))
                self.assertNotIn('never-forward', str(command))
