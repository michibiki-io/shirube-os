"""Synthetic ISO handoffs and canonical builder imports; no builds or Docker."""
from contextlib import ExitStack
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from producer import staged
from producer.collect import files
from producer.native_stages import copy_selected
from producer.notices import NOTICE, mark_changed, snapshot
from producer.stage_artifacts import digest, pack, scope, unpack


def load_module(name, filename):
    spec = importlib.util.spec_from_file_location(name, filename)
    module = importlib.util.module_from_spec(spec)
    # image.py retains its existing container-specific import path behavior.
    with patch.object(sys, 'path', list(sys.path)):
        spec.loader.exec_module(module)
    return module


class ISOInputRetirementTests(unittest.TestCase):
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
        for stage in staged.DEPENDENCIES['iso']:
            self.write_transfer(stage)

    def write_transfer(self, stage, dependencies=None):
        payload = self.root/('payload-'+stage); payload.mkdir(exist_ok=True)
        (payload/'notice').write_text('synthetic '+stage+' public payload')
        dependencies = dependencies if dependencies is not None else {
            parent: self.identities[parent] for parent in staged.DEPENDENCIES[stage]}
        expected = staged.build_scope(self.project, stage, 'amd64', self.compiler, dependencies)
        archive = self.inputs/(stage+'.tar'); archive.unlink(missing_ok=True)
        record = pack(payload, archive, expected, self.commit, self.version)
        (self.inputs/(stage+'.json')).write_text(json.dumps(record))
        self.records[stage] = record
        self.identities[stage] = digest({'input_id': expected['input_id'], 'payload_id': record['payload_id']})

    def mapped(self, value='.', *parts):
        value = str(value)
        for prefix, name in (('/src', 'src'), ('/build-private', 'work'),
                             ('/stage-dependencies', 'restored')):
            if value == prefix or value.startswith(prefix+'/'):
                return self.root/name/value[len(prefix):].lstrip('/')/Path(*parts)
        return Path(value, *parts)

    def invoke(self, discard=True, failure=None):
        def assemble(payloads, output, arch, version):
            self.assertEqual(set(payloads), set(staged.DEPENDENCIES['iso']))
            self.assertEqual({p.stem for p in self.inputs.glob('*.tar')},
                             set() if discard else set(staged.DEPENDENCIES['iso']))
            for stage, payload in payloads.items():
                self.assertEqual((payload/'notice').read_text(), 'synthetic '+stage+' public payload')
                self.assertEqual(json.loads((self.inputs/(stage+'.json')).read_text()), self.records[stage])
            (output/'fixture.iso').write_bytes(b'synthetic ISO')
            (output/'assembly.json').write_text('{"iso":"fixture.iso"}')
        def storage(root, minimum, **kwargs):
            self.assertEqual(minimum, 75)
            self.assertEqual({p.stem for p in self.inputs.glob('*.tar')},
                             set() if discard else set(staged.DEPENDENCIES['iso']))
            if failure == 'storage':
                raise ValueError('75 GiB unavailable')
        unlink = Path.unlink
        def retire(path, *args, **kwargs):
            if path == self.inputs/'native.tar' and failure == 'cleanup':
                raise OSError('cannot retire input')
            return unlink(path, *args, **kwargs)
        argv = ['staged', 'iso', 'amd64', '--commit', self.commit, '--version', self.version,
                '--inputs', str(self.inputs), '--output', str(self.output),
                *(['--discard-input-archives'] if discard else [])]
        with ExitStack() as stack:
            stack.enter_context(patch.object(sys, 'argv', argv))
            stack.enter_context(patch.object(staged, 'Path', side_effect=self.mapped))
            stack.enter_context(patch('producer.architecture.native'))
            stack.enter_context(patch('producer.architecture.require_storage', side_effect=storage))
            stack.enter_context(patch('producer.library_cache.compiler_fingerprint', return_value=self.compiler))
            stack.enter_context(patch.object(Path, 'unlink', retire))
            assembly = stack.enter_context(patch.object(staged, 'iso', side_effect=assemble))
            export = stack.enter_context(patch.object(staged, 'pack', wraps=pack))
            if failure:
                with self.assertRaises((ValueError, OSError)):
                    staged.main()
                assembly.assert_not_called(); export.assert_not_called()
                self.assertFalse((self.output/'transfer.tar').exists())
            else:
                staged.main()
                assembly.assert_called_once(); export.assert_called_once()
        metrics = json.loads((self.output/'resources.json').read_text())
        self.assertEqual(metrics['status'], 'FAIL' if failure else 'PASS')
        if failure not in ('corruption', 'ancestry'):
            self.assertEqual(metrics['inputs'], [
                {'stage': stage, 'sha256': record['sha256'], 'bytes': record['bytes']}
                for stage, record in self.records.items()])
        return metrics

    def test_opt_in_retires_only_tars_after_metrics_and_before_unchanged_gate(self):
        self.invoke()
        self.assertEqual({p.name for p in self.inputs.iterdir()},
                         {stage+'.json' for stage in staged.DEPENDENCIES['iso']})
        self.assertTrue((self.output/'transfer.tar').is_file())
        self.assertTrue((self.output/'transfer.json').is_file())

    def test_default_retains_entire_cohort(self):
        self.invoke(discard=False)
        self.assertEqual(len(list(self.inputs.glob('*.tar'))), 7)

    def test_corrupt_last_predecessor_retains_every_archive(self):
        (self.inputs/'integration.tar').write_bytes(b'corrupt last input')
        self.invoke(failure='corruption')
        self.assertEqual(len(list(self.inputs.glob('*.tar'))), 7)

    def test_hash_valid_ancestry_mismatch_retains_every_archive(self):
        self.write_transfer('integration', {'native': 'f'*64, 'vpp': self.identities['vpp']})
        self.invoke(failure='ancestry')
        self.assertEqual(len(list(self.inputs.glob('*.tar'))), 7)

    def test_cleanup_failure_stops_assembly_and_export(self):
        self.invoke(failure='cleanup')
        self.assertEqual(len(list(self.inputs.glob('*.tar'))), 7)

    def test_storage_failure_still_blocks_assembly_and_export(self):
        self.invoke(failure='storage')
        self.assertEqual(list(self.inputs.glob('*.tar')), [])


class WritableInputBoundaryTests(unittest.TestCase):
    def test_launcher_rejects_unsafe_writable_mount_before_output_or_docker(self):
        launcher = load_module('stage_launcher_paths', 'scripts/run-native-stage.py')
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); inputs = root/'inputs'; inputs.mkdir()
            alias = root/'alias'; alias.symlink_to(inputs, target_is_directory=True)
            for directory, output in ((Path('/mnt'), root/'output'),
                                      (alias, root/'output'), (inputs, inputs/'output')):
                argv = ['launcher', '--stage', 'iso', '--architecture', 'amd64',
                        '--image', 'sha256:'+'b'*64, '--commit', 'c'*40,
                        '--version', 'v2026.03.0', '--inputs', str(directory),
                        '--output', str(output), '--discard-input-archives']
                with self.subTest(inputs=directory, output=output), \
                     patch.object(sys, 'argv', argv), patch.object(launcher, 'native') as native, \
                     patch.object(launcher.subprocess, 'check_output') as read, \
                     patch.object(launcher, 'run') as run:
                    with self.assertRaises(ValueError):
                        launcher.main()
                    self.assertFalse(output.exists())
                    native.assert_not_called(); read.assert_not_called(); run.assert_not_called()

    def test_non_iso_flags_rejected_before_side_effects_in_both_clis(self):
        launcher = load_module('stage_launcher_boundary', 'scripts/run-native-stage.py')
        for stage in staged.STAGES:
            if stage == 'iso':
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
            for directory, destination, cache in (
                    (Path('/'), output, None), (Path('/tmp'), output, None),
                    (Path.home(), output, None), (Path.cwd(), output, None),
                    (alias, output, None), (parent_alias/'inputs', output, None),
                    (inputs, inputs, None), (inputs, inputs/'output', None),
                    (inputs, root, None), (inputs, output, inputs/'cache'),
                    (inputs, output, inputs), (inputs, output, root)):
                with self.subTest(inputs=directory, output=destination, cache=cache):
                    with self.assertRaises(ValueError):
                        staged.validate_discard_inputs('iso', directory, destination, cache)
            staged.validate_discard_inputs('iso', inputs, output, root/'cache')


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
