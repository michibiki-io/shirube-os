"""Synthetic module transport recovery and rendered native recipe integration."""
import hashlib
import io
import json
from contextlib import ExitStack
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch
import zipfile

from producer.go_modules import module_hash, prepare_dependencies

TRANSIENT = ('example.test/module@v1.2.3: Get '
             '"https://proxy.golang.org/example.test/module/@v/v1.2.3.zip": '
             'stream error: stream ID 27; INTERNAL_ERROR; received from peer')
GO = '/opt/go/bin/go'


class DependencyTransportTests(unittest.TestCase):
    def run_prepare(self, results, command=None, env=None):
        command = command or [GO, 'list', '-deps', '-tags', 'nobtrfs', '.']
        with patch('producer.go_modules.subprocess.run', side_effect=results) as run, \
             patch('producer.go_modules.time.sleep') as sleep:
            output = prepare_dependencies(command, cwd=Path('/synthetic/source'), env=env)
        return output, run, sleep

    def test_exact_http2_error_then_success_keeps_inputs_and_cache(self):
        command = [GO, 'list', '-deps', '-tags', 'nobtrfs', '.']
        env = {'GOMODCACHE': '/same/modules', 'GOCACHE': '/same/build',
               'GOPROXY': 'https://proxy.golang.org', 'GOSUMDB': 'sum.golang.org',
               'GOFLAGS': '-p=2', 'GOMEMLIMIT': '512MiB'}
        output, run, sleep = self.run_prepare([
            subprocess.CompletedProcess(command, 1, '', 'go: downloading example.test/module v1.2.3\n'+TRANSIENT),
            subprocess.CompletedProcess(command, 0, 'example.test/package\n', '')], command, env)
        self.assertEqual(output, 'example.test/package\n')
        self.assertEqual(run.call_count, 2)
        self.assertEqual(run.call_args_list[0], run.call_args_list[1])
        self.assertEqual(run.call_args.args[0], command)
        self.assertEqual(run.call_args.kwargs['env'], env)
        self.assertEqual(run.call_args.kwargs['cwd'], Path('/synthetic/source'))
        sleep.assert_called_once_with(1)

    def test_attempt_exhaustion(self):
        command = [GO, 'list', '-deps', './cmd/telegraf']
        failure = subprocess.CompletedProcess(command, 1, '', TRANSIENT)
        with patch('producer.go_modules.subprocess.run', return_value=failure) as run, \
             patch('producer.go_modules.time.sleep') as sleep:
            with self.assertRaises(subprocess.CalledProcessError) as raised:
                prepare_dependencies(command, cwd='/same')
        self.assertEqual(run.call_count, 3)
        self.assertTrue(all(call == run.call_args_list[0] for call in run.call_args_list))
        self.assertEqual([c.args[0] for c in sleep.call_args_list], [1, 2])
        self.assertEqual(raised.exception.stderr, TRANSIENT)

    def test_final_diagnostics_and_bounded_retry_markers_are_logged(self):
        checksum = 'verifying example.test/module@v1.2.3: checksum mismatch\nSECURITY ERROR'
        for download, error, stderr in [
                (False, TRANSIENT, TRANSIENT),
                (False, checksum, checksum),
                (True, TRANSIENT, ''),
                (True, checksum, 'go: downloading example.test/module v1.2.3\n'),
                (True, checksum, checksum)]:
            command = ([GO, 'mod', 'download', '-json', 'example.test/module@v1.2.3']
                       if download else [GO, 'list', '-deps', '.'])
            output = json.dumps({'Error': error, 'Zip': 'do-not-log.zip'}) if download else 'do-not-log/package\n'
            log = io.StringIO()
            with self.subTest(download=download, error=error, stderr=stderr), \
                 patch('producer.go_modules.subprocess.run', return_value=
                       subprocess.CompletedProcess(command, 1, output, stderr)) as run, \
                 patch('producer.go_modules.time.sleep') as sleep, \
                 patch('sys.stderr', log):
                with self.assertRaises(subprocess.CalledProcessError) as raised:
                    prepare_dependencies(command, cwd='/same')
                self.assertEqual(raised.exception.output, output)
                self.assertEqual(raised.exception.stderr, stderr)
                self.assertEqual(log.getvalue().count(error), 1)
                self.assertNotIn('do-not-log', log.getvalue())
                if error == TRANSIENT:
                    self.assertEqual(run.call_count, 3)
                    self.assertEqual([c.args[0] for c in sleep.call_args_list], [1, 2])
                    self.assertEqual(log.getvalue().splitlines()[:2], [
                        'Go dependency preparation attempt 1/3 failed; retrying attempt 2/3 in 1s',
                        'Go dependency preparation attempt 2/3 failed; retrying attempt 3/3 in 2s'])
                else:
                    self.assertEqual(run.call_count, 1)
                    sleep.assert_not_called()
                    self.assertNotIn('retrying', log.getvalue())

    def test_successful_package_listing_is_not_logged(self):
        log = io.StringIO()
        with patch('sys.stderr', log):
            output, _, _ = self.run_prepare([
                subprocess.CompletedProcess([], 0, 'example.test/package\n', '')])
        self.assertEqual(output, 'example.test/package\n')
        self.assertEqual(log.getvalue(), '')

    def test_permanent_integrity_auth_and_compiler_errors_never_retry(self):
        prefix = 'Get "https://proxy.golang.org/example.test/module/@v/v1.2.3.zip": '
        errors = [prefix+'404 Not Found', prefix+'401 Unauthorized', prefix+'403 Forbidden',
                  'verifying example.test/module@v1.2.3: checksum mismatch\nSECURITY ERROR',
                  'module ZIP integrity failure', 'x.go:2: undefined: broken',
                  'x509: certificate signed by unknown authority',
                  TRANSIENT+'\nchecksum mismatch', TRANSIENT+'\n'+prefix+'404 Not Found',
                  'dial tcp: unrelated timeout']
        for error in errors:
            with self.subTest(error=error), \
                 patch('producer.go_modules.subprocess.run', return_value=
                       subprocess.CompletedProcess([], 1, '', error)) as run, \
                 patch('producer.go_modules.time.sleep') as sleep:
                with self.assertRaises(subprocess.CalledProcessError):
                    prepare_dependencies([GO, 'list', '-deps', '.'], cwd='/same')
                self.assertEqual(run.call_count, 1)
                sleep.assert_not_called()

    def test_fixed_download_json_error_recovery_and_permanent_failure(self):
        command = [GO, 'mod', 'download', '-json', 'example.test/module@v1.2.3']
        for error, count in [(TRANSIENT, 2), ('checksum mismatch', 1), ('401 Unauthorized', 1),
                             ('404 Not Found', 1), ('integrity failure', 1)]:
            with self.subTest(error=error), \
                 patch('producer.go_modules.subprocess.run', side_effect=[
                     subprocess.CompletedProcess(command, 1, json.dumps({'Error': error}), ''),
                     subprocess.CompletedProcess(command, 0, '{"Zip":"same.zip"}', '')]) as run, \
                 patch('producer.go_modules.time.sleep'):
                if count == 2:
                    self.assertEqual(prepare_dependencies(command, cwd='/same'), '{"Zip":"same.zip"}')
                    self.assertEqual(run.call_args_list[0], run.call_args_list[1])
                    self.assertIsNone(run.call_args.kwargs['env'])
                else:
                    with self.assertRaises(subprocess.CalledProcessError):
                        prepare_dependencies(command, cwd='/same')
                self.assertEqual(run.call_count, count)

    def test_compile_commands_are_excluded(self):
        with patch('producer.go_modules.subprocess.run') as run:
            with self.assertRaises(ValueError):
                prepare_dependencies([GO, 'build', '.'], cwd='/same')
            run.assert_not_called()


class RenderedExtrasTests(unittest.TestCase):
    def test_preparation_uses_the_existing_shell_resource_environment(self):
        for architecture in ['amd64', 'arm64']:
            with self.subTest(architecture=architecture), \
                 patch.object(sys, 'path', list(sys.path)):
                extras = types.ModuleType('rendered_extras')
                template = (Path('containers/native/extra_common.py.in').read_text()+'\n'+Path('containers/native/extra_runtime.py.in').read_text().replace('from extra_common import *', '')).replace('@ARCH@', architecture)
                exec(compile(template, 'extras-'+architecture+'.py', 'exec'), extras.__dict__)
                from producer.resources import environment
                base = {'PATH': '/bin', 'GOMODCACHE': '/same/modules', 'GOCACHE': '/same/build',
                        'GOPROXY': 'https://proxy.golang.org', 'GOSUMDB': 'sum.golang.org'}
                budget = {'compile_jobs': 2, 'cpu_limit': 4, 'go_jobs': 3, 'go_memory_limit_mib': 512}
                with patch.dict('os.environ', base, clear=True), \
                     patch('producer.resources.profile', return_value=budget), \
                     patch('producer.go_modules.subprocess.run', return_value=
                           subprocess.CompletedProcess([], 0, '', '')) as run:
                    expected = environment()
                    expected.update(DEBIAN_FRONTEND='noninteractive', PATH='/opt/go/bin:/bin')
                    prepare_dependencies([GO, 'list', '-deps', '.'], cwd='/same',
                                         env=extras.shell_environment())
                    extras.shell('go build .', '/same')
                self.assertEqual(run.call_args_list[0].kwargs['env'], expected)
                self.assertEqual(run.call_args_list[1].kwargs['env'], expected)
                self.assertEqual(expected['GOFLAGS'], '-p=3')
                self.assertEqual(expected['GOMAXPROCS'], '4')
                self.assertEqual(expected['GOMEMLIMIT'], '512MiB')

    def exercise(self, architecture, *, failed_prep=False, failed_compile=False, bad_hash=None):
        with tempfile.TemporaryDirectory() as temporary, ExitStack() as stack:
            root = Path(temporary)
            archive = root/'module.zip'
            module, version = 'example.test/module', 'v1.2.3'
            with zipfile.ZipFile(archive, 'w') as source:
                source.writestr(module+'@'+version+'/go.mod', 'module '+module+'\n')
                source.writestr(module+'@'+version+'/LICENSE', 'Synthetic permission notice')
            record = {'version': version, 'sha256': hashlib.sha256(archive.read_bytes()).hexdigest(),
                      'h1': module_hash(archive, module, version)}
            if bad_hash:
                record[bad_hash] = 'invalid'
            lock = root/'source-lock.json'
            lock.write_text(json.dumps({'go': {'version': '1.26.10'},
                                        'go_dependency_overrides': {'telegraf': {module: record}}}))
            extras = types.ModuleType('rendered_extras')
            stack.enter_context(patch.object(sys, 'path', list(sys.path)))
            template = (Path('containers/native/extra_common.py.in').read_text()+'\n'+Path('containers/native/extra_runtime.py.in').read_text().replace('from extra_common import *', '')).replace('@ARCH@', architecture)
            exec(compile(template, 'extras-'+architecture+'.py', 'exec'), extras.__dict__)
            events = []
            attempts = {}
            env = {'PATH': '/opt/go/bin:/bin', 'GOFLAGS': '-p=2', 'GOMAXPROCS': '2',
                   'GOMEMLIMIT': '512MiB', 'GOMODCACHE': '/same/modules', 'GOCACHE': '/same/build',
                   'GOPROXY': 'https://proxy.golang.org', 'GOSUMDB': 'sum.golang.org'}

            def snapshot(path, target):
                target.mkdir()
                return target

            def run(command, **kwargs):
                if command[1:3] == ['mod', 'edit']:
                    events.append(('edit', command, kwargs))
                    return subprocess.CompletedProcess(command, 0)
                events.append(('prepare', command, kwargs))
                key = (str(kwargs['cwd']), tuple(command))
                attempts[key] = attempts.get(key, 0)+1
                if failed_prep or attempts[key] == 1:
                    output = json.dumps({'Error': TRANSIENT}) if command[1] == 'mod' else ''
                    return subprocess.CompletedProcess(command, 1, output, '' if output else TRANSIENT)
                output = json.dumps({'Zip': str(archive)}) if command[1] == 'mod' else 'package\n'
                return subprocess.CompletedProcess(command, 0, output, '')

            def shell(command, cwd=None):
                if command.startswith('go build'):
                    self.assertEqual(events[-1][0], 'prepare')
                    self.assertEqual(events[-1][2]['cwd'], cwd)
                    events.append(('compile', command, cwd))
                    if failed_compile:
                        raise subprocess.CalledProcessError(1, command, stderr='x.go: undefined: broken')
                else:
                    events.append(('package', command, cwd))

            def output(command, **kwargs):
                if command[0] == GO:
                    return '/node_exporter: go1.26.10\n'
                name = Path(command[0]).name
                return {'node_exporter': '1.9.1', 'frr_exporter': '1.5.0',
                        'blackbox_exporter': '0.26.0', 'telegraf': '1.28.3'}[name]

            stack.enter_context(patch.object(extras, 'snapshot', side_effect=snapshot))
            stack.enter_context(patch.object(extras, 'shell', side_effect=shell))
            stack.enter_context(patch.object(extras, 'shell_environment', return_value=env))
            stack.enter_context(patch.object(extras, 'copy_debs'))
            stack.enter_context(patch.object(extras, 'Path', side_effect=
                lambda path: lock if str(path) == '/src/source-lock.json' else Path(path)))
            stack.enter_context(patch('producer.go_modules.subprocess.run', side_effect=run))
            stack.enter_context(patch.object(extras.subprocess, 'check_output', side_effect=output))
            sleep = stack.enter_context(patch('producer.go_modules.time.sleep'))
            if failed_prep or failed_compile:
                with self.assertRaises(subprocess.CalledProcessError):
                    extras.go_packages(root)
            elif bad_hash:
                with self.assertRaisesRegex(ValueError, 'Fixed licensed Go dependency source differs'):
                    extras.go_packages(root)
            else:
                extras.go_packages(root)
            return events, sleep

    def test_both_architectures_prepare_exact_targets_before_one_compile(self):
        for architecture in ['amd64', 'arm64']:
            with self.subTest(architecture=architecture):
                events, sleep = self.exercise(architecture)
                compiles = [e for e in events if e[0] == 'compile']
                self.assertEqual([e[1] for e in compiles], [
                    'go build -tags nobtrfs -ldflags "-X github.com/prometheus/common/version.Version=1.9.1" -o node_exporter',
                    'go build  -ldflags "-X github.com/prometheus/common/version.Version=1.5.0" -o frr_exporter',
                    'go build  -ldflags "-X github.com/prometheus/common/version.Version=0.26.0" -o blackbox_exporter',
                    'go build -ldflags "-X github.com/influxdata/telegraf/internal.Version=1.28.3" -o telegraf ./cmd/telegraf'])
                preps = [e for e in events if e[0] == 'prepare']
                self.assertEqual(len(preps), 10)
                for first, second in zip(preps[::2], preps[1::2]):
                    self.assertEqual(first, second)
                lists = [e[1] for e in preps[::2] if e[1][1] == 'list']
                self.assertEqual(lists, [[GO, 'list', '-deps', '-tags', 'nobtrfs', '.'],
                                        [GO, 'list', '-deps', '.'], [GO, 'list', '-deps', '.'],
                                        [GO, 'list', '-deps', './cmd/telegraf']])
                self.assertEqual([e[1] for e in preps if e[1][1] == 'mod'],
                                 [[GO, 'mod', 'download', '-json', 'example.test/module@v1.2.3']]*2)
                self.assertTrue(all('--architecture '+architecture in e[1]
                                    for e in events if e[0] == 'package'))
                self.assertEqual(sleep.call_count, 5)

    def test_failed_preparation_prevents_compile_on_both_architectures(self):
        for architecture in ['amd64', 'arm64']:
            events, sleep = self.exercise(architecture, failed_prep=True)
            self.assertEqual(len(events), 3)
            self.assertTrue(all(e[0] == 'prepare' for e in events))
            self.assertEqual([c.args[0] for c in sleep.call_args_list], [1, 2])

    def test_compilation_failure_is_never_retried(self):
        for architecture in ['amd64', 'arm64']:
            events, _ = self.exercise(architecture, failed_compile=True)
            self.assertEqual([e[0] for e in events], ['prepare', 'prepare', 'compile'])

    def test_override_sha256_and_h1_are_both_required(self):
        for architecture in ['amd64', 'arm64']:
            for bad_hash in ['sha256', 'h1']:
                with self.subTest(architecture=architecture, bad_hash=bad_hash):
                    events, _ = self.exercise(architecture, bad_hash=bad_hash)
                    self.assertEqual(len([e for e in events if e[0] == 'compile']), 3)
                    self.assertEqual(events[-1][1][1:4], ['mod', 'download', '-json'])
