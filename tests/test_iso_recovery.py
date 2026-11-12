"""Synthetic ISO/clearance handoffs and canonical builder imports; no builds or Docker."""
from contextlib import ExitStack
import importlib.util
import io
from itertools import product
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

from producer import staged
from producer.collect import files
from producer.native_stages import copy_selected
from producer.notices import NOTICE, mark_changed, snapshot
from producer.stage_artifacts import digest, pack, scope, unpack
from producer.sources import sha256


def load_module(name, filename):
    spec = importlib.util.spec_from_file_location(name, filename)
    module = importlib.util.module_from_spec(spec)
    # image.py retains its existing container-specific import path behavior.
    with patch.object(sys, 'path', list(sys.path)):
        spec.loader.exec_module(module)
    return module


class ISOInputRetirementTests(unittest.TestCase):
    stage = 'iso'
    architecture = 'amd64'

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.inputs = self.root/'inputs'; self.inputs.mkdir()
        self.output = self.root/'output'; self.output.mkdir()
        self.project = self.root/'src'
        from component_fixture import component_project
        component_project(self.project, container=True)
        self.compiler = 'sha256:'+'b'*64
        self.commit = 'c'*40; self.version = 'v2026.03.0'
        self.records = {}
        self.identities = {}
        for stage in (*staged.DEPENDENCIES['iso'], 'iso', 'clearance'):
            if stage == self.stage:
                break
            self.write_transfer(stage)

    def write_transfer(self, stage, dependencies=None, commit=None):
        payload = self.root/('payload-'+stage); payload.mkdir(exist_ok=True)
        (payload/'notice').write_text('synthetic '+stage+' public payload')
        if stage == 'iso' or stage == 'clearance':
            for name, data in self.iso_files.items():
                path = payload/name; path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
        dependencies = dependencies if dependencies is not None else {
            parent: self.identities[parent] for parent in staged.DEPENDENCIES[stage]}
        expected = staged.build_scope(self.project, stage, self.architecture, self.compiler, dependencies)
        directory = self.inputs if stage in staged.DEPENDENCIES[self.stage] else self.root
        archive = directory/(stage+'.tar'); archive.unlink(missing_ok=True)
        record = pack(payload, archive, expected, commit or self.commit, self.version)
        (directory/(stage+'.json')).write_text(json.dumps(record))
        if directory == self.inputs:
            self.records[stage] = record
        self.identities[stage] = digest({'input_id': expected['input_id'], 'payload_id': record['payload_id']})

    iso_files = {
        'fixture.iso': b'synthetic ISO bytes',
        'assembly.json': b'{"iso":"fixture.iso"}',
        'iso-binding.json': b'synthetic binding',
        'signing/cert.der': b'public certificate',
        'debs/kernel.deb': b'synthetic package',
        'packages/source.tar': b'corresponding source',
        'material/recipe.py': b'build material',
    }

    def mapped(self, value='.', *parts):
        value = str(value)
        for prefix, name in (('/src', 'src'), ('/build-private', 'work'),
                             ('/stage-dependencies', 'restored')):
            if value == prefix or value.startswith(prefix+'/'):
                return self.root/name/value[len(prefix):].lstrip('/')/Path(*parts)
        return Path(value, *parts)

    def invoke(self, discard=True, failure=None):
        before = {p.name: p.read_bytes() for p in self.inputs.iterdir()}
        from producer.architecture import require_storage
        def assemble(payloads, output, arch, version, *args, **kwargs):
            self.assertEqual(arch, self.architecture)
            self.assertEqual(set(payloads), set(staged.DEPENDENCIES[self.stage]))
            self.assertEqual({p.stem for p in self.inputs.glob('*.tar')},
                             set() if discard else set(staged.DEPENDENCIES[self.stage]))
            for stage, payload in payloads.items():
                self.assertEqual((payload/'notice').read_text(), 'synthetic '+stage+' public payload')
                self.assertEqual(json.loads((self.inputs/(stage+'.json')).read_text()), self.records[stage])
            if self.stage in {'clearance', 'guests'}:
                for name, data in self.iso_files.items():
                    self.assertEqual((payloads[staged.DEPENDENCIES[self.stage][0]]/name).read_bytes(), data)
            if self.stage == 'guests':
                storage(output, 56, phase='Fresh guest disk stage')
            (output/'fixture.iso').write_bytes(b'synthetic ISO')
            (output/'assembly.json').write_text('{"iso":"fixture.iso"}')
        def storage(root, minimum, **kwargs):
            self.assertEqual(minimum, 56 if self.stage == 'guests' else 75)
            self.assertEqual({p.stem for p in self.inputs.glob('*.tar')},
                             set() if discard else set(staged.DEPENDENCIES[self.stage]))
            if failure == 'storage':
                with patch('shutil.disk_usage', return_value=SimpleNamespace(free=(minimum - 1) * 1024**3)):
                    require_storage(root, minimum, **kwargs)
        from producer.stage_metrics import measure
        details = {}
        def observe_measure(*args, **kwargs):
            details.update(record=kwargs['details'])
            return measure(*args, **kwargs)
        unlink = Path.unlink
        def retire(path, *args, **kwargs):
            if path.parent == self.inputs and path.suffix == '.tar':
                # Metrics and the consumer's scope must exist before any deletion.
                self.assertEqual(details['record']['inputs'], [
                    {'stage': stage, 'sha256': record['sha256'], 'bytes': record['bytes']}
                    for stage, record in self.records.items()])
                self.assertIn('input_id', details['record'])
                self.assertEqual(details['record']['compiler_fingerprint'], self.compiler)
            if path == self.inputs/(staged.DEPENDENCIES[self.stage][0]+'.tar') and failure == 'cleanup':
                raise OSError('cannot retire input')
            return unlink(path, *args, **kwargs)
        argv = ['staged', self.stage, self.architecture, '--commit', self.commit, '--version', self.version,
                '--inputs', str(self.inputs), '--output', str(self.output),
                *(['--netbird-fixture-stdin'] if self.stage == 'guests' else []),
                *(['--discard-input-archives'] if discard else [])]
        with ExitStack() as stack:
            stack.enter_context(patch.object(sys, 'argv', argv))
            if self.stage == 'guests':
                stack.enter_context(patch('producer.netbird_guest.Management'))
                stack.enter_context(patch.object(sys, 'stdin', io.StringIO('{}')))
                stack.enter_context(patch('os.dup2'))
            stack.enter_context(patch.object(staged, 'Path', side_effect=self.mapped))
            stack.enter_context(patch('producer.architecture.native'))
            admission = stack.enter_context(patch('producer.architecture.require_storage', side_effect=storage))
            stack.enter_context(patch('producer.library_cache.compiler_fingerprint', return_value=self.compiler))
            stack.enter_context(patch('producer.stage_metrics.measure', side_effect=observe_measure))
            stack.enter_context(patch.object(Path, 'unlink', retire))
            assembly = stack.enter_context(patch.object(staged, self.stage, side_effect=assemble))
            export = stack.enter_context(patch.object(staged, 'pack', wraps=pack))
            if failure == 'scope':
                build_scope = staged.build_scope
                def fail_consumer_scope(project, stage, *args):
                    if stage == self.stage:
                        raise ValueError('consumer scope failed')
                    return build_scope(project, stage, *args)
                stack.enter_context(patch.object(staged, 'build_scope', side_effect=fail_consumer_scope))
            if failure:
                with self.assertRaises((ValueError, OSError)):
                    staged.main()
                if self.stage != 'guests' or failure != 'storage':
                    assembly.assert_not_called()
                export.assert_not_called()
                if failure != 'storage':
                    admission.assert_not_called()
                self.assertFalse((self.output/'transfer.tar').exists())
            else:
                staged.main()
                assembly.assert_called_once()
                self.assertEqual(export.call_count, 0 if self.stage == 'guests' else 1)
        if failure and failure != 'storage':
            self.assertEqual({p.name: p.read_bytes() for p in self.inputs.iterdir()}, before)
        metrics = json.loads((self.output/'resources.json').read_text())
        self.assertEqual(metrics['status'], 'FAIL' if failure else 'PASS')
        if failure not in ('corruption', 'ancestry', 'metadata', 'attribution', 'inventory', 'nonregular'):
            self.assertEqual(metrics['inputs'], [
                {'stage': stage, 'sha256': record['sha256'], 'bytes': record['bytes']}
                for stage, record in self.records.items()])
        return metrics

    def test_opt_in_retires_only_tars_after_metrics_and_before_unchanged_gate(self):
        self.invoke()
        self.assertEqual({p.name for p in self.inputs.iterdir()},
                         {stage+'.json' for stage in staged.DEPENDENCIES[self.stage]})
        if self.stage == 'guests':
            self.assertTrue((self.output/'candidate/fixture.iso').is_file())
        else:
            self.assertTrue((self.output/'transfer.tar').is_file())
            self.assertTrue((self.output/'transfer.json').is_file())

    def test_default_retains_entire_cohort(self):
        self.invoke(discard=False)
        self.assertEqual(len(list(self.inputs.glob('*.tar'))), len(staged.DEPENDENCIES[self.stage]))

    def test_corrupt_last_predecessor_retains_every_archive(self):
        (self.inputs/(staged.DEPENDENCIES[self.stage][-1]+'.tar')).write_bytes(b'corrupt last input')
        self.invoke(failure='corruption')
        self.assertEqual(len(list(self.inputs.glob('*.tar'))), len(staged.DEPENDENCIES[self.stage]))

    def test_hash_valid_scope_or_ancestry_mismatch_retains_every_archive(self):
        dependency = staged.DEPENDENCIES[self.stage][-1]
        if self.stage in {'clearance', 'guests'}:
            original = self.architecture
            self.architecture = 'arm64' if original == 'amd64' else 'amd64'
            self.write_transfer(dependency)
            self.architecture = original
        else:
            dependencies = dict(self.records[dependency]['scope']['dependencies'])
            dependencies[next(iter(dependencies))] = 'f'*64
            self.write_transfer(dependency, dependencies)
        self.invoke(failure='ancestry')
        self.assertEqual(len(list(self.inputs.glob('*.tar'))), len(staged.DEPENDENCIES[self.stage]))

    def test_metadata_mismatch_retains_every_archive(self):
        dependency = staged.DEPENDENCIES[self.stage][-1]
        path = self.inputs/(dependency+'.json')
        record = json.loads(path.read_text()); record['bytes'] += 1
        path.write_text(json.dumps(record))
        self.invoke(failure='metadata')
        self.assertEqual(len(list(self.inputs.glob('*.tar'))), len(self.records))

    def test_hash_valid_wrong_attribution_retains_every_archive(self):
        self.write_transfer(staged.DEPENDENCIES[self.stage][-1], commit='d'*40)
        self.invoke(failure='attribution')
        self.assertEqual(len(list(self.inputs.glob('*.tar'))), len(self.records))

    def test_hash_valid_extra_inventory_member_retains_every_archive(self):
        dependency = staged.DEPENDENCIES[self.stage][-1]
        archive = self.inputs/(dependency+'.tar')
        with tarfile.open(archive, 'a') as target:
            member = tarfile.TarInfo('payload/unlisted'); member.size = 1
            target.addfile(member, io.BytesIO(b'x'))
        record = self.records[dependency]
        record.update(sha256=sha256(archive), bytes=archive.stat().st_size)
        (self.inputs/(dependency+'.json')).write_text(json.dumps(record))
        self.invoke(failure='inventory')
        self.assertEqual(len(list(self.inputs.glob('*.tar'))), len(self.records))

    def test_symlink_archive_retains_entire_cohort(self):
        archive = self.inputs/(staged.DEPENDENCIES[self.stage][-1]+'.tar')
        target = self.root/'linked.tar'; archive.rename(target); archive.symlink_to(target)
        self.invoke(failure='nonregular')
        self.assertTrue(archive.is_symlink()); self.assertTrue(target.is_file())
        self.assertEqual(len(list(self.inputs.glob('*.tar'))), len(self.records))

    def test_consumer_scope_failure_retains_every_archive(self):
        self.invoke(failure='scope')
        self.assertEqual(len(list(self.inputs.glob('*.tar'))), len(self.records))

    def test_cleanup_failure_stops_assembly_and_export(self):
        self.invoke(failure='cleanup')
        self.assertEqual(len(list(self.inputs.glob('*.tar'))), len(staged.DEPENDENCIES[self.stage]))

    def test_storage_failure_still_blocks_assembly_and_export(self):
        self.invoke(failure='storage')
        self.assertEqual(list(self.inputs.glob('*.tar')), [])


class ARM64ISOInputRetirementTests(ISOInputRetirementTests):
    architecture = 'arm64'


class ClearanceInputRetirementTests(ISOInputRetirementTests):
    stage = 'clearance'


class ARM64ClearanceInputRetirementTests(ClearanceInputRetirementTests):
    architecture = 'arm64'


class GuestInputRetirementTests(ISOInputRetirementTests):
    stage = 'guests'
    iso_files = {**ISOInputRetirementTests.iso_files,
                 'clearance.json': b'{"source_proof":"retained"}',
                 'manifest.json': b'{"reconstruction":"retained"}',
                 'guest-kernel/cert.der': b'public guest certificate',
                 'guest-kernel/kernel.deb': b'guest kernel'}


class ARM64GuestInputRetirementTests(GuestInputRetirementTests):
    architecture = 'arm64'


class WritableInputBoundaryTests(unittest.TestCase):
    def test_container_default_absolute_mounts_accept_retirement(self):
        # Use real absolute container paths; remapping Path hides this contract.
        for stage, arch in product(('iso', 'clearance', 'guests'), ('amd64', 'arm64')):
            with self.subTest(stage=stage, architecture=arch):
                staged.validate_discard_inputs(stage, Path('/inputs'), Path('/output'))
                argv = ['staged', stage, arch, '--commit', 'c'*40,
                        '--version', 'v2026.03.0', '--discard-input-archives']
                with patch.object(sys, 'argv', argv), \
                     patch('producer.architecture.native', side_effect=RuntimeError('validation passed')) as native:
                    with self.assertRaisesRegex(RuntimeError, 'validation passed'):
                        staged.main()
                    native.assert_called_once_with(arch)

    def test_container_absolute_protected_roots_and_overlaps_remain_rejected(self):
        for stage in ('iso', 'clearance', 'guests'):
            for directory in ('/', '/tmp', '/var/tmp', '/src/inputs',
                              '/build-private/inputs', '/stage-dependencies/inputs'):
                with self.subTest(stage=stage, inputs=directory), self.assertRaises(ValueError):
                    staged.validate_discard_inputs(stage, directory, '/output')
            for output, cache in (('/inputs', None), ('/inputs/output', None),
                                  ('/', None), ('/output', '/inputs/cache'),
                                  ('/output', '/inputs'), ('/output', '/')):
                with self.subTest(stage=stage, output=output, cache=cache), self.assertRaises(ValueError):
                    staged.validate_discard_inputs(stage, '/inputs', output, cache)

    def test_launcher_mount_and_flag_defaults_for_both_architectures(self):
        launcher = load_module('stage_launcher_mounts', 'scripts/run-native-stage.py')
        for stage, arch, discard in product(('iso', 'clearance'), ('amd64', 'arm64'), (False, True)):
            with self.subTest(stage=stage, architecture=arch, discard=discard), \
                 tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary); inputs = root/'inputs'; inputs.mkdir()
                output = root/'output'
                argv = ['launcher', '--stage', stage, '--architecture', arch,
                        '--image', 'sha256:'+'b'*64, '--commit', 'c'*40,
                        '--version', 'v2026.03.0', '--inputs', str(inputs), '--output', str(output),
                        *(['--discard-input-archives'] if discard else [])]
                def run(*command, **kwargs):
                    if command[:2] == ('docker', 'run'):
                        self.assertIn(str(inputs)+':/inputs:'+('rw' if discard else 'ro'), command)
                        self.assertEqual('--discard-input-archives' in command, discard)
                        for name in ('transfer.tar', 'transfer.json', 'resources.json'):
                            (output/name).write_text('synthetic output')
                with patch.object(sys, 'argv', argv), patch.object(launcher, 'native'), \
                     patch.object(launcher.subprocess, 'check_output', side_effect=['c'*40, '', arch]), \
                     patch.object(launcher, 'run', side_effect=run) as execute:
                    launcher.main()
                self.assertEqual(execute.call_count, 2)
                self.assertTrue((output/(stage+'.tar')).is_file())

    def test_launcher_rejects_unsafe_writable_mount_before_output_or_docker(self):
        launcher = load_module('stage_launcher_paths', 'scripts/run-native-stage.py')
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); inputs = root/'inputs'; inputs.mkdir()
            alias = root/'alias'; alias.symlink_to(inputs, target_is_directory=True)
            paths = tuple((Path(path), root/'output') for path in
                          ('/', '/mnt', '/etc', '/var', '/usr', '/home', '/inputs', '/tmp')) + (
                     (alias, root/'output'), (inputs, inputs/'output'))
            for stage, arch, (directory, output) in product(
                    ('iso', 'clearance', 'guests'), ('amd64', 'arm64'), paths):
                argv = ['launcher', '--stage', stage, '--architecture', arch,
                        '--image', 'sha256:'+'b'*64, '--commit', 'c'*40,
                        '--version', 'v2026.03.0', '--inputs', str(directory),
                        '--output', str(output), '--discard-input-archives']
                with self.subTest(stage=stage, architecture=arch, inputs=directory, output=output), \
                     patch.object(sys, 'argv', argv), patch.object(launcher, 'native') as native, \
                     patch.object(launcher.subprocess, 'check_output') as read, \
                     patch.object(launcher, 'run') as run:
                    with self.assertRaises(ValueError):
                        launcher.main()
                    self.assertFalse(output.exists())
                    native.assert_not_called(); read.assert_not_called(); run.assert_not_called()

    def test_other_stage_flags_rejected_before_side_effects_in_both_clis(self):
        launcher = load_module('stage_launcher_boundary', 'scripts/run-native-stage.py')
        for stage in staged.STAGES:
            if stage in {'iso', 'clearance', 'guests'}:
                continue
            for cli in (launcher, staged):
                with self.subTest(stage=stage, cli=cli.__name__):
                    argv = (['launcher', '--stage', stage, '--architecture', 'amd64',
                             '--image', 'sha256:'+'b'*64] if cli is launcher else ['staged', stage, 'amd64'])
                    argv += ['--commit', 'c'*40, '--version', 'v2026.03.0',
                             '--inputs', '/tmp/task-inputs', '--output', '/tmp/task-output',
                             '--discard-input-archives']
                    with patch.object(sys, 'argv', argv), patch.object(cli.subprocess, 'check_output') as read, \
                         patch('producer.architecture.native') as native:
                        with self.assertRaisesRegex(ValueError, 'requires ISO'):
                            cli.main()
                        read.assert_not_called(); native.assert_not_called()

    def test_input_roots_links_and_output_cache_overlap_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); inputs = root/'inputs'; inputs.mkdir()
            output = root/'output'
            alias = root/'alias'; alias.symlink_to(inputs, target_is_directory=True)
            parent_alias = root/'parent-alias'; parent_alias.symlink_to(root, target_is_directory=True)
            paths = (
                    (Path('/'), output, None), (Path('/tmp'), output, None),
                    (Path.home(), output, None), (Path.cwd(), output, None),
                    (alias, output, None), (parent_alias/'inputs', output, None),
                    (inputs, inputs, None), (inputs, inputs/'output', None),
                    (inputs, root, None), (inputs, output, inputs/'cache'),
                    (inputs, output, inputs), (inputs, output, root))
            for stage, (directory, destination, cache) in product(('iso', 'clearance'), paths):
                with self.subTest(stage=stage, inputs=directory, output=destination, cache=cache):
                    with self.assertRaises(ValueError):
                        staged.validate_discard_inputs(stage, directory, destination, cache)
            for stage in ('iso', 'clearance'):
                staged.validate_discard_inputs(stage, inputs, output, root/'cache')


class CanonicalImageBuilderTests(unittest.TestCase):
    def test_materialized_alias_and_canonical_import_branding_for_both_architectures(self):
        image = load_module('native_image_recovery', 'containers/native/image.py')
        for arch, console in (('amd64', 'ttyS0'), ('arm64', 'ttyAMA0')):
            with self.subTest(architecture=arch), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary); upstream = root/'upstream'
                adjacent = upstream/'scripts/image-build'; adjacent.mkdir(parents=True)
                (adjacent/'defaults.py').write_text('VALUE = "sibling defaults"\n')
                script = adjacent/'build-vyos-image'
                script.write_text(f'#!{sys.executable}\n' + '''# Synthetic upstream copyright and license notice
from defaults import VALUE
import json, os, sys
print(json.dumps(dict(value=VALUE, cwd=os.getcwd(), file=__file__, args=sys.argv[1:],
                     marker=os.environ['BUILDER_MARKER'], brand="""NAME="VyOS"
PRETTY_NAME="VyOS Stream"
ID=vyos
--iso-application "VyOS"
--iso-volume "VyOS"
console=ttyS0,115200""")))
''')
                script.chmod(0o755)
                (upstream/'build-vyos-image').symlink_to('scripts/image-build/build-vyos-image')
                material = root/'material'; copy_selected(files(upstream), material)
                expected = scope('native', arch, {'recipe': 'a'*64}, 'sha256:'+'b'*64, {})
                archive = root/'transfer.tar'; record = pack(material, archive, expected, 'c'*40, 'v2026.03.0')
                restored = root/'restored'
                unpack(archive, restored, expected, 'c'*40, 'v2026.03.0', record['sha256'])
                alias = restored/'build-vyos-image'
                self.assertFalse(alias.is_symlink())
                self.assertEqual(alias.read_bytes(), script.read_bytes())
                env = {k: v for k, v in os.environ.items() if k not in ('PYTHONPATH', 'PYTHONHOME')}
                env.update(PYTHONDONTWRITEBYTECODE='1', BUILDER_MARKER='retained environment')
                failed = subprocess.run(['./build-vyos-image'], cwd=restored, env=env,
                                        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                self.assertNotEqual(failed.returncode, 0)
                self.assertIn(b"No module named 'defaults'", failed.stderr)
                before = snapshot(restored)
                build = image.brand_image_builder(restored, arch)
                self.assertEqual(mark_changed(before), [build])
                self.assertIn(NOTICE, build.read_text())
                self.assertIn('# Synthetic upstream copyright and license notice', build.read_text())
                def execute(command, **kwargs):
                    self.assertEqual(kwargs, {'cwd': restored, 'env': env})
                    result = subprocess.run(command, check=True, text=True,
                                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, **kwargs)
                    actual = json.loads(result.stdout)
                    self.assertEqual(Path(actual['file']).resolve(), build.resolve())
                    self.assertEqual(actual['cwd'], str(restored))
                    self.assertEqual(actual['value'], 'sibling defaults')
                    self.assertEqual(actual['marker'], 'retained environment')
                    self.assertEqual(actual['args'], ['--architecture', arch, '--build-type', 'release',
                        '--version', '2026.03.0', '--build-by', 'michibiki-io', '--vyos-mirror',
                        'http://127.0.0.1:18080', '--custom-apt-key', str(root/'public.asc'), 'generic'])
                    self.assertEqual(actual['brand'], 'NAME="Shirube OS"\nPRETTY_NAME="Shirube OS Stream"\n'
                        'ID=shirube-os\nID_LIKE="vyos debian"\n--iso-application "Shirube OS"\n'
                        '--iso-volume "Shirube OS"\nconsole='+console+',115200')
                with patch('producer.livebuild_transport.build', side_effect=execute) as invoke:
                    image.run_image_builder(build, restored, arch, 'v2026.03.0', root/'public.asc', env)
                invoke.assert_called_once()
                self.assertEqual(alias.read_bytes(), script.read_bytes())

    def test_escaping_source_alias_remains_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); source = root/'source'; source.mkdir()
            (root/'outside').write_text('outside source')
            (source/'build-vyos-image').symlink_to(root/'outside')
            with self.assertRaisesRegex(ValueError, 'escapes component'):
                copy_selected(files(source), root/'material')
