"""Dependency mutation matrix and canonical host/container scopes, offline only."""
from contextlib import contextmanager, redirect_stdout
import json
import io
import sys
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from component_fixture import component_project
from producer import component_inputs as inputs
from producer.library_cache import toolchain_identity
from producer.staged import DEPENDENCIES, build_scope

COMPILER = 'sha256:' + 'b' * 64
STAGES = (*inputs.CACHEABLE, 'kernel')


class ComponentInputTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.directory = Path(temp.name)
        self.project = component_project(self.directory/'host')

    def scopes(self, architecture='amd64', compiler=COMPILER):
        return {stage: build_scope(self.project, stage, architecture, compiler,
                                  {'vpp': 'a'*64} if stage == 'kernel' else {}) for stage in STAGES}

    @contextmanager
    def mutate_file(self, name):
        path = self.project/name
        original = path.read_bytes() if path.exists() else None
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes((original or b'') + b'\n# dependency mutation\n')
        try:
            yield
        finally:
            if original is None: path.unlink()
            else: path.write_bytes(original)

    @contextmanager
    def mutate_json(self, name, selection):
        path = self.project/name; original = path.read_bytes()
        data = json.loads(original); parent = data
        for key in selection[:-1]: parent = parent[key]
        value = parent[selection[-1]]
        parent[selection[-1]] = {**value, 'mutation': True} if isinstance(value, dict) else str(value)+'-mutation'
        path.write_text(json.dumps(data))
        try:
            yield
        finally:
            path.write_bytes(original)

    def assert_changes(self, before, stages):
        after = self.scopes()
        self.assertEqual({stage for stage in STAGES if after[stage] != before[stage]}, set(stages))

    def test_unrelated_netbird_guest_cloud_init_geoip_keep_all_compile_and_toolchain_keys(self):
        before = self.scopes(); compiler = toolchain_identity(self.project, 'amd64')
        for name in ('producer/netbird.py', 'producer/netbird_sources.py', 'guest/first_boot.py',
                     'producer/cloud_init.py', 'producer/geoip.py', 'third_party/geoip/fixture.txt',
                     'containers/native/image.py'):
            with self.subTest(file=name), self.mutate_file(name):
                self.assert_changes(before, ())
                self.assertEqual(toolchain_identity(self.project, 'amd64'), compiler)
        for key in ('netbird', 'cloud_init', 'geoip'):
            with self.subTest(lock=key), self.mutate_json('source-lock.json', (key,)):
                self.assert_changes(before, ())
                self.assertEqual(toolchain_identity(self.project, 'amd64'), compiler)

    def test_image_edits_invalidate_downstream_integration_and_iso(self):
        for architecture in ('amd64', 'arm64'):
            for stage in ('integration', 'iso'):
                with self.subTest(stage=stage, architecture=architecture):
                    dependencies = {name: 'a'*64 for name in DEPENDENCIES[stage]}
                    before = build_scope(self.project, stage, architecture, COMPILER, dependencies)
                    with self.mutate_file('containers/native/image.py'):
                        self.assertNotEqual(before, build_scope(self.project, stage, architecture, COMPILER, dependencies))

    def test_selected_locks_mutation_matrix(self):
        before = self.scopes()
        cases = [
            ('containers/native/external-sources.lock.json', ('vpp',), ('vpp',)),
            ('containers/native/external-sources.lock.json', ('vpp-patches',), ('vpp',)),
            ('containers/native/external-sources.lock.json', ('kea-packaging',), ('kea',)),
            ('containers/native/external-sources.lock.json', ('salt-tools',), ('salt',)),
            ('containers/native/external-sources.lock.json', ('i2util',), ('runtime',)),
            ('containers/native/external-sources.lock.json', ('nat-rtsp',), ('kernel',)),
            ('source-lock.json', ('kea_build_tool',), ('kea',)),
            ('source-lock.json', ('go',), ('runtime', 'kernel')),
            ('source-lock.json', ('go_dependency_overrides', 'telegraf'), ('runtime',)),
            ('source-lock.json', ('go_notice_overrides',), ('runtime', 'kernel')),
            ('containers/native/http-api.lock.json', ('version',), ('native',)),
            ('containers/native/http-rust.lock.json', ('rustc_version_output',), ('native',)),
            ('containers/native/salt-sources.lock.json', ('python',), ('salt',)),
            ('containers/native/vpp-python.lock.json', ('schema_version',), ('vpp',)),
            ('containers/native/vpp-external.lock.json', ('schema_version',), ('vpp',)),
        ]
        for filename, selection, changed in cases:
            with self.subTest(file=filename, selection=selection), self.mutate_json(filename, selection):
                self.assert_changes(before, changed)

    def test_recipes_notices_common_contracts_and_patch_matrix(self):
        before = self.scopes()
        cases = [('producer/go_notices.py', ('runtime', 'kernel')),
                 ('producer/go_modules.py', ('runtime', 'kernel')),
                 ('producer/vpp_inputs.py', ('vpp',)),
                 ('producer/salt_python.py', ('salt',)),
                 ('containers/native/http_api.py', ('native',)),
                 ('containers/native/package_common.sh', ('native', 'kernel')),
                 ('containers/native/extra_common.py.in', ('vpp', 'kea', 'salt', 'runtime', 'kernel')),
                 ('patches/kernel/new.patch', ('kernel',)),
                 ('patches/vyos/force_mtu_host.sh', STAGES),
                 ('producer/source_selectors.py', STAGES), ('producer/apt_archive.py', STAGES),
                 ('producer/stage_artifacts.py', STAGES), ('producer/branding.py', STAGES),
                 ('producer/native_common.py', STAGES), ('LICENSE', STAGES), ('shirube-os-logo.svg', STAGES)]
        for name, changed in cases:
            with self.subTest(file=name), self.mutate_file(name): self.assert_changes(before, changed)
        patch_file = next((self.project/'patches/kernel').glob('*.patch'))
        saved = patch_file.read_bytes(); patch_file.unlink()
        self.assert_changes(before, ('kernel',)); patch_file.write_bytes(saved)

    def test_authentication_snapshots_toolchain_and_architecture(self):
        before = self.scopes()
        for selection in [('stream',), ('debian_snapshot',), ('debian_security_snapshot',), ('container_bases', 'amd64')]:
            with self.subTest(selection=selection), self.mutate_json('source-lock.json', selection):
                self.assert_changes(before, STAGES)
        for name in ('containers/native/install_gems.py', 'containers/native/rubygems.lock.json'):
            with self.subTest(file=name), self.mutate_file(name): self.assert_changes(before, STAGES)
        self.assertTrue(all(before[stage] != record for stage, record in self.scopes('arm64').items()))
        self.assertTrue(all(before[stage] != record for stage, record in self.scopes(compiler='sha256:'+'c'*64).items()))
        with self.assertRaises(ValueError): build_scope(self.project, 'kernel', 'amd64', COMPILER, {})
        kernel = build_scope(self.project, 'kernel', 'amd64', COMPILER, {'vpp': 'd'*64})
        self.assertNotEqual(kernel, before['kernel'])

    def test_catalog_subsets_include_graph_flags_order_and_schema(self):
        before = self.scopes(); filename = 'containers/native/packages.json'
        for index, changed in [(0, ('native',)), (-1, ('kernel',))]:
            with self.mutate_json(filename, ('jobs', index)):
                self.assert_changes(before, changed)
        with self.mutate_json(filename, ('source_sha256',)):
            self.assert_changes(before, STAGES)
        path = self.project/filename; data = json.loads(path.read_text())
        data['jobs'][0]['requires'] = ['unknown-job']; path.write_text(json.dumps(data))
        with self.assertRaises(ValueError): inputs.manifest(self.project, 'native', 'amd64')

    def test_missing_required_files_unknown_selections_and_patch_tree_fail_closed(self):
        for stage in STAGES:
            name = 'producer/compile_'+stage+'.py'; path = self.project/name; saved = path.read_bytes(); path.unlink()
            with self.subTest(stage=stage), self.assertRaises(ValueError): inputs.manifest(self.project, stage, 'amd64')
            path.write_bytes(saved)
        shutil.rmtree(self.project/'patches/kernel')
        with self.assertRaises(ValueError): inputs.manifest(self.project, 'kernel', 'amd64')
        with self.assertRaises(ValueError): inputs.manifest(self.project, 'unknown', 'amd64')
        with self.assertRaises(ValueError): inputs.manifest(self.project, 'native', 'mips')
        path = self.project/'containers/native/inputs-native.json'
        data=json.loads(path.read_text()); data['lock_values'].append('nonexistent'); path.write_text(json.dumps(data))
        with self.assertRaises(ValueError): inputs.manifest(self.project, 'native', 'amd64')

    def test_host_container_aliases_equal_for_each_stage_and_architecture(self):
        container = component_project(self.directory/'src', container=True)
        for architecture in ('amd64', 'arm64'):
            for stage in STAGES:
                with self.subTest(stage=stage, architecture=architecture):
                    self.assertEqual(inputs.manifest(self.project, stage, architecture),
                                     inputs.manifest(container, stage, architecture))

    def test_outer_key_exact_inner_identity_including_effective_fingerprint(self):
        before = self.scopes(); after = self.scopes(compiler='sha256:'+'c'*64)
        for stage in inputs.CACHEABLE:
            self.assertEqual(before[stage]['inputs'], after[stage]['inputs'])
            self.assertEqual(inputs.outer_key(before[stage]), 'shirube-stage-v2-'+stage+'-amd64-'+before[stage]['input_id'])
            self.assertNotEqual(inputs.outer_key(before[stage]), inputs.outer_key(after[stage]))
        with self.assertRaises(ValueError): inputs.outer_key(before['kernel'])

    def test_cli_outer_key_observes_actual_fingerprint_and_same_build_scope(self):
        for stage in inputs.CACHEABLE:
            for compiler in (COMPILER, 'sha256:'+'e'*64):
                stdout = io.StringIO()
                with patch.object(sys, 'argv', ['component_inputs', 'stage-key', '--project', str(self.project),
                                               '--stage', stage, '--architecture', 'amd64']), \
                     patch.object(inputs, 'compiler_fingerprint', return_value=compiler), redirect_stdout(stdout):
                    inputs.main()
                self.assertEqual(stdout.getvalue(), 'key='+inputs.outer_key(
                    build_scope(self.project, stage, 'amd64', compiler, {}))+'\n')

    def test_compiler_namespace_tracks_toolchain_prefix_without_copy_content(self):
        before = toolchain_identity(self.project, 'amd64')
        path = self.project/'containers/native/Dockerfile'
        original = path.read_text()
        path.write_text(original+'\n# producer-only recipe change\n')
        self.assertEqual(before, toolchain_identity(self.project, 'amd64'))
        path.write_text('# toolchain recipe change\n'+original)
        self.assertNotEqual(before, toolchain_identity(self.project, 'amd64'))

    def test_required_input_symlinks_are_rejected_and_executable_state_is_bound(self):
        before = self.scopes()
        path = self.project/'patches/vyos/force_mtu_host.sh'
        mode = path.stat().st_mode
        path.chmod(mode ^ 0o111)
        self.assert_changes(before, STAGES)
        path.chmod(mode)
        saved = path.read_bytes(); path.unlink()
        target = self.directory/'external-script'; target.write_bytes(saved); path.symlink_to(target)
        with self.assertRaises(ValueError): inputs.manifest(self.project, 'vpp', 'amd64')
