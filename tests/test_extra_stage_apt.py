"""Synthetic extras-boundary regressions; never run APT or compile packages."""
from contextlib import contextmanager
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from producer import native_stages, staged


ARCHITECTURES = ('amd64', 'arm64')
# Keep the expected recipes independent of the production dispatch table.
EXTRA_RECIPES = {'vpp': ('vpp',), 'kea': ('kea',), 'salt': ('salt',),
                 'runtime': ('base', 'go', 'podman'), 'kernel': ('modules', 'accel')}
STRICT_UPDATE = ('apt-get', '-o', 'APT::Update::Error-Mode=any', 'update')
COMPILER = 'sha256:' + 'b' * 64


class ExtraStageAPTTests(unittest.TestCase):
    @contextmanager
    def environment(self, stage, architecture, failure=None):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            sources = root / 'prepared-sources'; sources.mkdir()
            packages = root / 'packages'; packages.mkdir()
            (packages / 'source-preparation.json').write_text('{}')
            # Stand-ins for already completed catalog/signing and verified VPP inputs.
            for name, content in {
                'stage-dependencies/vpp/vpp-sdk/sdk.json': '{}',
                'build-private/kernel/source/certs/signing_key.pem': 'synthetic key',
                'build-private/kernel/source/certs/signing_key.x509': 'synthetic certificate',
            }.items():
                path = root / name; path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content)
            from component_fixture import component_project
            component_project(root / 'src', container=True)
            (root / 'inputs').mkdir()
            (root / 'stage-dependencies/vpp/debs').mkdir()

            def mapped(value='.', *parts):
                value = str(value)
                prefixes = ('/sources', '/debs', '/stage-dependencies', '/vpp-sdk',
                            '/kernel-signing', '/build-private', '/src')
                if any(value == prefix or value.startswith(prefix + '/') for prefix in prefixes):
                    return root / value.lstrip('/') / Path(*parts)
                return Path(value, *parts)

            events = []

            def run(*args):
                events.append(args)
                self.assertTrue((root / 'sources').is_symlink())
                if stage == 'kernel':
                    self.assertTrue((root / 'vpp-sdk/sdk.json').is_file())
                    self.assertEqual((root / 'kernel-signing/key.pem').stat().st_mode & 0o777, 0o600)
                if args[0] == 'apt-get' and failure is not None:
                    raise failure

            def export(actual_stage, actual_sources, actual_packages, output, actual_architecture):
                self.assertEqual((actual_stage, actual_sources, actual_packages, actual_architecture),
                                 (stage, sources, packages, architecture))
                events.append(('export', stage))
                output.mkdir(parents=True)
                (output / 'preferred-source.c').write_text('synthetic public source')

            generate = Mock(return_value=root / 'generated')
            exporter = Mock(side_effect=export)
            with patch.object(native_stages, 'Path', side_effect=mapped), \
                 patch.object(staged, 'Path', side_effect=mapped), \
                 patch('producer.native_common.generate', generate), \
                 patch('producer.native_common.run', run), \
                 patch.object(native_stages, 'export_compile', exporter), \
                 patch('producer.library_cache.compiler_fingerprint', return_value=COMPILER), \
                 patch.object(native_stages.subprocess, 'check_output',
                              side_effect=AssertionError('No external package inspection allowed')), \
                 patch.object(sys, 'path', list(sys.path)):
                yield SimpleNamespace(root=root, sources=sources, packages=packages,
                                      events=events, generate=generate, export=exporter)

    def finish(self, env, stage, architecture, output=None):
        native_stages.finish_compile(stage, env.sources, env.packages,
                                     output or env.root / 'export', architecture)

    def test_each_extra_stage_refreshes_once_before_recipes_on_both_architectures(self):
        for architecture in ARCHITECTURES:
            for stage, recipes in EXTRA_RECIPES.items():
                with self.subTest(stage=stage, architecture=architecture), self.environment(stage, architecture) as env:
                    self.finish(env, stage, architecture)
                    self.assertEqual(env.events, [STRICT_UPDATE, *[
                        ('python3', str(env.root / 'generated/extras.py'), job) for job in recipes
                    ], ('export', stage)])
                    env.generate.assert_called_once_with(architecture)
                    env.export.assert_called_once()
                    receipt = json.loads((env.root / 'export/provenance' / (stage + '-compiler.json')).read_text())
                    self.assertEqual(receipt['post_build_compiler_fingerprint'], COMPILER)

    def test_failed_refresh_stops_all_recipes_export_and_success_provenance(self):
        for architecture in ARCHITECTURES:
            for stage in EXTRA_RECIPES:
                # Both complete and partial index failures must propagate unchanged.
                for diagnostic in ('repository unavailable', 'some index files failed to download'):
                    failure = subprocess.CalledProcessError(100, STRICT_UPDATE, stderr=diagnostic)
                    with self.subTest(stage=stage, architecture=architecture, diagnostic=diagnostic), \
                         self.environment(stage, architecture, failure) as env:
                        with self.assertRaises(subprocess.CalledProcessError) as raised:
                            self.finish(env, stage, architecture)
                        self.assertIs(raised.exception, failure)
                        self.assertEqual(env.events, [STRICT_UPDATE])
                        env.export.assert_not_called()
                        self.assertFalse((env.root / 'export').exists())

    def test_catalog_only_native_has_no_refresh_or_extra_recipe(self):
        for architecture in ARCHITECTURES:
            with self.subTest(architecture=architecture), self.environment('native', architecture) as env:
                self.finish(env, 'native', architecture)
                self.assertEqual(env.events, [('export', 'native')])
                env.export.assert_called_once()

    def test_verified_cache_hit_bypasses_finish_and_refresh(self):
        """Real stage dispatch/scope/pack/unpack; only catalog compilation is synthetic."""
        for architecture in ARCHITECTURES:
            for stage in ('native', 'vpp', 'kea', 'salt', 'runtime'):
                with self.subTest(stage=stage, architecture=architecture), self.environment(stage, architecture) as env:
                    builds = []

                    def compile_catalog():
                        builds.append(tuple(sys.argv))
                        output = Path(sys.argv[sys.argv.index('--output') + 1])
                        self.finish(env, stage, architecture, output / 'stage')

                    def invoke(name, commit):
                        output = env.root / name; output.mkdir()
                        # Model a fresh disposable container, retaining only its cache.
                        for path in (env.root / 'build-private', env.root / 'debs'):
                            if path.exists(): shutil.rmtree(path)
                        if (env.root / 'sources').is_symlink(): (env.root / 'sources').unlink()
                        with patch('producer.architecture.native') as native, \
                             patch.dict(sys.modules, {'build': SimpleNamespace(main=compile_catalog)}), \
                             patch.object(sys, 'argv', ['staged', stage, architecture, '--commit', commit,
                                 '--version', 'v2026.03.0', '--inputs', str(env.root / 'inputs'),
                                 '--output', str(output), '--cache', str(env.root / 'cache')]):
                            staged.main()
                            native.assert_called_once_with(architecture)
                        return (json.loads((output / 'transfer.json').read_text()),
                                json.loads((output / 'resources.json').read_text()))

                    cold, cold_metrics = invoke('cold', 'c' * 40)
                    self.assertEqual(cold_metrics['cache'], 'MISS')
                    self.assertEqual(len(builds), 1)
                    self.assertEqual(env.events.count(STRICT_UPDATE), 0 if stage == 'native' else 1)
                    env.events.clear(); env.generate.reset_mock(); env.export.reset_mock()
                    warm, warm_metrics = invoke('warm', 'd' * 40)
                    self.assertEqual(warm_metrics['cache'], 'HIT')
                    self.assertEqual(len(builds), 1)
                    self.assertEqual(env.events, [])
                    env.generate.assert_not_called(); env.export.assert_not_called()
                    self.assertEqual(cold['payload_id'], warm['payload_id'])
                    self.assertEqual(warm['source_commit'], 'd' * 40)
                    self.assertEqual(warm['content_source_commit'], 'c' * 40)
