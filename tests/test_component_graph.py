"""Physical dependency boundaries and fail-closed static closure, offline only."""
from contextlib import contextmanager
import importlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from component_fixture import component_project
from producer.component_graph import COMPONENTS, graph
from producer.component_inputs import manifest
from producer.stage_artifacts import digest
from producer.staged import build_scope, DEPENDENCIES

COMPILER = 'sha256:'+'b'*64
ARCHITECTURES = ('amd64', 'arm64')


class ComponentGraphTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.project = component_project(self.root/'project')

    @contextmanager
    def edit(self, name, content=None):
        path = self.project/name
        saved = path.read_bytes() if path.exists() else None
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content if content is not None else (saved or b'')+b'\n# scoped mutation\n')
        try: yield path
        finally:
            if saved is None: path.unlink(missing_ok=True)
            else: path.write_bytes(saved)

    def scopes(self, arch, *, propagate=False):
        records = {}
        for stage in COMPONENTS:
            predecessors = {'vpp': digest({'input_id': records['vpp']['input_id'], 'payload_id': 'c'*64})
                            if propagate else 'a'*64} if stage == 'kernel' else {}
            records[stage] = build_scope(self.project, stage, arch, COMPILER, predecessors)
        return records

    def test_real_recipe_declaration_selector_and_security_code_matrix_both_architectures(self):
        for arch in ARCHITECTURES:
            before = self.scopes(arch, propagate=True)
            cases = []
            for stage in COMPONENTS:
                affected = {stage, 'kernel'} if stage == 'vpp' else {stage}
                cases += [('producer/compile_'+stage+'.py', affected),
                          ('containers/native/inputs-'+stage+'.json', affected)]
                if stage != 'native': cases.append(('containers/native/extra_'+stage+'.py.in', affected))
            cases += [('producer/select_vpp.py', {'vpp', 'kernel'}),
                      ('producer/select_kea.py', {'kea'}), ('producer/select_runtime.py', {'runtime'}),
                      ('containers/native/package_native.sh', {'native'}),
                      ('containers/native/package_native_early.sh', {'native'}),
                      ('containers/native/package_kernel.sh', {'kernel'}),
                      ('containers/native/security_native.py', {'native'}),
                      ('containers/native/security_kernel.py', {'kernel'}),
                      ('containers/native/security_common.py', {'native', 'kernel'}),
                      ('containers/native/package_common.sh', {'native', 'kernel'}),
                      ('containers/native/package_validate.sh', {'native', 'kernel'}),
                      ('containers/native/build_compile.py', set(COMPONENTS)),
                      ('producer/source_selectors.py', set(COMPONENTS)),
                      ('producer/component_graph.py', set(COMPONENTS)),
                      ('producer/component_inputs.py', set(COMPONENTS)),
                      ('producer/compile_scope.py', set(COMPONENTS)),
                      *[(name, set(COMPONENTS)) for name in ('producer/staged.py',
                        'producer/staged_compile.py', 'producer/stage_transport.py',
                        'producer/stage_metrics.py', 'producer/library_cache.py')],
                      ('producer/packages.py', set(COMPONENTS)),
                      ('producer/security_contract.py', set(COMPONENTS)),
                      ('producer/source_contract.py', {'native', 'salt'}),
                      ('producer/stage_artifacts.py', set(COMPONENTS)),
                      ('containers/native/inputs-common.json', set(COMPONENTS))]
            for name, expected in cases:
                # JSON whitespace changes bind whole declaration files too.
                content = (self.project/name).read_bytes()+b'\n' if name.endswith('.json') else None
                with self.subTest(arch=arch, name=name), self.edit(name, content):
                    after = self.scopes(arch, propagate=True)
                    self.assertEqual({s for s in COMPONENTS if before[s] != after[s]}, expected)

    def test_full_build_accel_missing_sdk_uses_scoped_helper_both_architectures(self):
        # Execute the actual rendered fallback, stopping at the SDK call before any build.
        import importlib.util
        from producer import vpp_sdk
        class SDKReached(Exception): pass
        for arch, multi in [('amd64', 'x86_64-linux-gnu'), ('arm64', 'aarch64-linux-gnu')]:
            modules = {}
            for name in ('extra_common', 'extra_kernel'):
                source = (self.project/('containers/native/'+name+'.py.in')).read_text()
                target = self.root/(arch+'-'+name+'.py')
                target.write_text(source.replace('@ARCH@', arch).replace('@MULTIARCH@', multi))
                spec = importlib.util.spec_from_file_location(name, target)
                module = importlib.util.module_from_spec(spec)
                with patch.dict(sys.modules, modules): spec.loader.exec_module(module)
                modules[name] = module
            kernel = modules['extra_kernel']
            missing = self.root/(arch+'-missing-sdk')
            original = self.root/(arch+'-vpp-source')
            def mapped(value):
                return {'/vpp-sdk':missing, '/extra/vpp/vpp':original}.get(str(value), Path(value))
            with self.subTest(arch=arch), patch.object(kernel, 'headers', return_value='synthetic-headers'), \
                 patch.object(kernel, 'Path', side_effect=mapped), \
                 patch.object(vpp_sdk, 'export_vpp_sdk', side_effect=SDKReached) as export:
                with self.assertRaises(SDKReached): kernel.accel(self.root/'work')
                export.assert_called_once_with(original, missing, multi, arch)

    def test_component_specific_sdk_and_signing_helper_code_is_scoped(self):
        # Mutate actual helper behavior, not only a comment or patch filename.
        cases = [('producer/vpp_sdk.py', "'header_counts': counts", "'mutated_header_counts': counts", {'vpp', 'kernel'}),
                 ('producer/compile_kernel.py', "signing / 'key.pem'", "signing / 'changed-key.pem'", {'kernel'})]
        for arch in ARCHITECTURES:
            before = self.scopes(arch, propagate=True)
            for name, old, new, expected in cases:
                original = (self.project/name).read_text()
                self.assertIn(old, original)
                with self.subTest(arch=arch, name=name), self.edit(name, original.replace(old, new).encode()):
                    after = self.scopes(arch, propagate=True)
                    self.assertEqual({stage for stage in COMPONENTS if before[stage] != after[stage]}, expected)

    def test_compile_closure_contains_no_noncompile_modules_or_templates(self):
        forbidden = {'producer/collect.py', 'producer/staged_release.py', 'producer/pipeline.py',
                     'producer/release.py', 'producer/geoip.py', 'producer/integration.py', 'producer/cloud_init.py',
                     'producer/netbird_guest.py', 'producer/image_templates.py',
                     'containers/native/image.py', 'containers/native/build_release.py',
                     'containers/native/build.py', 'containers/native/extras.py.in',
                     'containers/native/repo_metadata.py.in'}
        for stage in COMPONENTS:
            _, nodes = graph(self.project, stage)
            self.assertFalse(forbidden & nodes.keys())

    def test_new_imports_delayed_imports_rendered_imports_and_shell_paths_require_edges(self):
        imports = [b'\nimport producer.geoip\n', b'\ndef added():\n    from producer import geoip\n',
                   b'\nimport native.security_kernel\n', b'\ndef added():\n    import absent_local_helper\n',
                   b'\nimport importlib\nimportlib.import_module("producer.geoip")\n',
                   b'\nimport importlib\nimportlib.import_module(route)\n']
        for stage in COMPONENTS:
            name = 'producer/compile_'+stage+'.py'
            for addition in imports:
                with self.subTest(stage=stage, addition=addition), self.edit(name, (self.project/name).read_bytes()+addition):
                    with self.assertRaises(ValueError): manifest(self.project, stage, 'amd64')
        for addition in [b'\nfrom producer.geoip import verify_source\n',
                         b'\nshell("python3 /src/native/http_api.py")\n',
                         b'\nshell("python3 /opt/stream/sign-modules.py")\n',
                         b'\nPath("/src/native").glob("*.py.in")\n',
                         b'\nPath("/src/native") / "http_api.py"\n',
                         b'\nshell("cat /src/vyos-patches/security/frr/*.patch")\n']:
            name='containers/native/extra_kea.py.in'
            with self.subTest(addition=addition), self.edit(name, (self.project/name).read_bytes()+addition):
                with self.assertRaises(ValueError): manifest(self.project, 'kea', 'amd64')

    def test_new_dynamic_route_and_omitted_rendered_template_are_rejected(self):
        name='producer/native_stages.py'
        with self.edit(name,(self.project/name).read_bytes()+b"\ndef added_route():\n    import importlib\n    importlib.import_module(new_route)\n"):
            for arch in ARCHITECTURES:
                with self.assertRaises(ValueError):manifest(self.project,'vpp',arch)
        for stage in COMPONENTS:
            if stage=='native':continue
            name='containers/native/inputs-'+stage+'.json';data=json.loads((self.project/name).read_text())
            data['templates'].remove('extra_common.py.in')
            with self.edit(name,json.dumps(data).encode()):
                for arch in ARCHITECTURES:
                    with self.assertRaises(ValueError):manifest(self.project,stage,arch)

    def test_actual_dispatch_selected_targets_and_new_calls_fail_closed(self):
        from producer import staged, staged_compile, staged_release
        for stage in staged.STAGES:
            expected = staged_compile if stage in COMPONENTS else staged_release
            self.assertIs(staged.engine(stage), expected)
            with patch.object(expected, 'main') as execute, \
                 patch.object(sys, 'argv', ['staged', stage, 'amd64']):
                staged.main()
                execute.assert_called_once_with()
        name = 'producer/staged.py'
        original = (self.project/name).read_bytes()
        mutations = [original + b"\ndef added():\n    return importlib.import_module(new_route)\n",
                     original.replace(b"'native': 'producer.staged_compile'",
                                      b"'native': 'producer.staged_release'")]
        for content in mutations:
            with self.edit(name, content):
                for arch in ARCHITECTURES:
                    with self.assertRaises(ValueError): manifest(self.project, 'native', arch)
        # Whole physical files, including facade and actual executing driver, bind
        # every component, not just scope construction or AST-selected functions.
        for stage in COMPONENTS:
            files = manifest(self.project, stage, 'amd64')['files']
            for name in ('producer/staged.py', 'producer/staged_compile.py',
                         'producer/stage_transport.py', 'producer/stage_metrics.py'):
                from producer.sources import sha256
                self.assertEqual(files[name], {'sha256': sha256(self.project/name),
                    'executable': bool((self.project/name).stat().st_mode & 0o111)})

    def test_declared_transitive_helper_addition_mutation_removal_and_missing_edge(self):
        for arch in ARCHITECTURES:
            for stage in COMPONENTS:
                code='producer/compile_'+stage+'.py'; decl='containers/native/inputs-'+stage+'.json'
                helper='producer/fixture_'+stage+'.py'; leaf='producer/fixture_leaf_'+stage+'.py'
                before=self.scopes(arch, propagate=True)
                data=json.loads((self.project/decl).read_text())
                data['graph'][code].append(helper); data['graph'][helper]=[leaf]; data['graph'][leaf]=[]
                with self.edit(code, (self.project/code).read_bytes()+('\ndef added():\n    import producer.fixture_'+stage+'\n').encode()), \
                     self.edit(helper, ('from producer.fixture_leaf_'+stage+' import value\n').encode()), \
                     self.edit(leaf, b'value = 1\n'), self.edit(decl, json.dumps(data).encode()):
                    added=self.scopes(arch, propagate=True)
                    expected={stage,'kernel'} if stage=='vpp' else {stage}
                    self.assertEqual({s for s in COMPONENTS if before[s]!=added[s]},expected)
                    with self.edit(leaf):
                        changed=self.scopes(arch,propagate=True)
                        self.assertEqual({s for s in COMPONENTS if added[s]!=changed[s]},expected)
                    (self.project/leaf).unlink()
                    with self.assertRaises(ValueError): manifest(self.project,stage,arch)
                self.assertEqual(before,self.scopes(arch,propagate=True))

    def test_shared_helper_addition_mutation_removal_and_undeclared_lock_selection(self):
        for arch in ARCHITECTURES:
            before=self.scopes(arch)
            decl='containers/native/inputs-common.json'
            data=json.loads((self.project/decl).read_text())
            data['graph']['producer/component_inputs.py'].append('producer/shared_fixture.py')
            data['graph']['producer/shared_fixture.py']=[]
            code='producer/component_inputs.py'
            with self.edit(code,(self.project/code).read_bytes()+b'\nfrom .shared_fixture import value\n'), \
                 self.edit('producer/shared_fixture.py',b'value=1\n'),self.edit(decl,json.dumps(data).encode()):
                after=self.scopes(arch)
                self.assertEqual({s for s in COMPONENTS if before[s]!=after[s]},set(COMPONENTS))
                with self.edit('producer/shared_fixture.py'):
                    mutated=self.scopes(arch)
                    self.assertEqual({s for s in COMPONENTS if after[s]!=mutated[s]},set(COMPONENTS))
            self.assertEqual(before,self.scopes(arch))
            for stage in COMPONENTS:
                code='producer/compile_'+stage+'.py';decl='containers/native/inputs-'+stage+'.json'
                added=b"\ndef added_lock_read():\n    return json.loads(Path('/src/source-lock.json').read_text())['fixture_selection']\n"
                data=json.loads((self.project/decl).read_text())
                if 'source-lock.json' not in data['graph'][code]: data['graph'][code].append('source-lock.json')
                lock=json.loads((self.project/'source-lock.json').read_text());lock['fixture_selection']={'value':1}
                with self.edit(code,(self.project/code).read_bytes()+added), \
                     self.edit('source-lock.json',json.dumps(lock).encode()):
                    with self.edit(decl,json.dumps(data).encode()):
                        with self.assertRaises(ValueError):manifest(self.project,stage,arch)
                    data['lock_values'].append('fixture_selection')
                    with self.edit(decl,json.dumps(data).encode()):
                        added_scope=self.scopes(arch)
                        self.assertEqual({s for s in COMPONENTS if before[s]!=added_scope[s]},{stage})
                        lock['fixture_selection']['value']=2
                        with self.edit('source-lock.json',json.dumps(lock).encode()):
                            changed=self.scopes(arch)
                            self.assertEqual({s for s in COMPONENTS if added_scope[s]!=changed[s]},{stage})

    def test_noncompile_only_changes_leave_all_compile_keys_stable_both_architectures(self):
        for arch in ARCHITECTURES:
            before=self.scopes(arch,propagate=True)
            for name in ('producer/collect.py','producer/staged_release.py','producer/release.py',
                         'producer/image_templates.py','producer/netbird.py','producer/cloud_init.py',
                         'producer/geoip.py','guest/first_boot.py','containers/native/build_release.py',
                         'containers/native/extras.py.in','containers/native/image.py'):
                with self.subTest(arch=arch,name=name),self.edit(name):
                    self.assertEqual(before,self.scopes(arch,propagate=True))

    def test_malformed_unknown_missing_and_alias_inputs_fail_closed(self):
        for arch in ARCHITECTURES:
            for stage in COMPONENTS:
                decl='containers/native/inputs-'+stage+'.json';original=json.loads((self.project/decl).read_text())
                for kind in ('unknown', 'missing', 'malformed', 'route', 'root'):
                    data=json.loads(json.dumps(original))
                    if kind=='unknown': data['graph']['producer/compile_'+stage+'.py'].append('unknown.py')
                    elif kind=='missing': del data['graph']
                    elif kind=='malformed': data['graph']['producer/compile_'+stage+'.py']=[{}]
                    elif kind=='route': data['dynamic']['missing.py']=['unknown.py']
                    else: data['roots'].append('unknown.py')
                    with self.subTest(stage=stage,arch=arch,kind=kind), self.edit(decl,json.dumps(data).encode()):
                        with self.assertRaises(ValueError): manifest(self.project,stage,arch)
                path=self.project/('producer/compile_'+stage+'.py');saved=path.read_bytes();path.unlink()
                with self.assertRaises(ValueError):manifest(self.project,stage,arch)
                path.mkdir()
                with self.assertRaises(ValueError):manifest(self.project,stage,arch)
                path.rmdir();path.write_bytes(saved)
        # Internal aliases are rejected too, including aliases at parents.
        parent=self.project/'producer';real=self.project/'real-producer';parent.rename(real);parent.symlink_to(real, target_is_directory=True)
        with self.assertRaises(ValueError):manifest(self.project,'vpp','amd64')
        parent.unlink();real.rename(parent)
        container=component_project(self.root/'container-parent/src',container=True)
        patches=container.parent/'kernel-patches';other=container.parent/'real-patches';patches.rename(other);patches.symlink_to(other,target_is_directory=True)
        with self.assertRaises(ValueError):manifest(container,'kernel','amd64')

    def test_noncompile_scope_covers_every_new_production_location_both_architectures(self):
        locations = ['producer/compile_'+s+'.py' for s in COMPONENTS]
        locations += ['containers/native/inputs-'+s+'.json' for s in (*COMPONENTS,'common')]
        locations += ['containers/native/extra_'+s+'.py.in' for s in ('common','vpp','kea','salt','runtime','kernel')]
        locations += ['containers/native/security_'+s+'.py' for s in ('common','native','kernel')]
        locations += ['containers/native/package_'+s+'.sh' for s in ('common','validate','native','native_early','kernel')]
        locations += ['containers/native/build_compile.py','containers/native/build_release.py',
                      'producer/staged.py','producer/staged_compile.py','producer/staged_release.py',
                      'producer/stage_transport.py','producer/stage_metrics.py',
                      'producer/compile_scope.py','producer/component_graph.py','producer/source_selectors.py',
                      'producer/select_vpp.py','producer/select_kea.py','producer/select_runtime.py','producer/image_templates.py',
                      'producer/security_contract.py','producer/source_contract.py','producer/vpp_sdk.py']
        for arch in ARCHITECTURES:
            before={s:build_scope(self.project,s,arch,COMPILER,{d:'a'*64 for d in DEPENDENCIES[s]})
                    for s in ('integration','iso','clearance','guests')}
            for name in locations:
                with self.subTest(arch=arch,name=name),self.edit(name):
                    for s,record in before.items():
                        self.assertNotEqual(record,build_scope(self.project,s,arch,COMPILER,{d:'a'*64 for d in DEPENDENCIES[s]}))

    def test_actual_rendered_component_and_iso_template_imports_and_help(self):
        from producer import native_common, image_templates
        for arch,multi in [('amd64','x86_64-linux-gnu'),('arm64','aarch64-linux-gnu')]:
            for stage in (*COMPONENTS,'iso'):
                output=self.root/(arch+'-'+stage)
                def mapped(value):
                    return {'/opt/stream':output,'/src/native':self.project/'containers/native',
                            '/src':self.project}.get(str(value),Path(value))
                generator=image_templates if stage=='iso' else native_common
                real_copy=shutil.copy2
                def copy(source,dest):
                    return real_copy(self.project/'containers/native/external-sources.lock.json' if str(source)=='/src/native/external-sources.lock.json' else source,dest)
                with patch.object(generator,'Path',side_effect=mapped), \
                     patch.object(generator.subprocess,'check_output',return_value=multi+'\n'), \
                     patch.object(generator.shutil,'copy2',side_effect=copy):
                    generator.generate(arch, **({} if stage=='iso' else {'stage':stage}))
                if stage != 'iso':
                    declared=json.loads((self.project/('containers/native/inputs-'+stage+'.json')).read_text())['templates']
                    self.assertEqual({p.name for p in output.glob('*.py')}, {n.removesuffix('.in') for n in declared})
                if stage=='native':self.assertEqual(list(output.glob('*.py')),[]);continue
                script=output/('extras.py' if stage=='iso' else 'extra_'+stage+'.py')
                result=subprocess.run([sys.executable,str(script),'--help'],capture_output=True,text=True)
                self.assertEqual(result.returncode,0,result.stderr)
