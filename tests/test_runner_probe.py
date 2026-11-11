import os
from pathlib import Path
import unittest
from unittest.mock import patch

from producer import runner_probe


class HostedRunnerProbeTests(unittest.TestCase):
    def test_self_hosted_or_local_environment_cannot_mutate_docker(self):
        for environment in [{}, {'GITHUB_ACTIONS': 'true', 'RUNNER_ENVIRONMENT': 'self-hosted',
                                 'RUNNER_OS': 'Linux'}]:
            with patch.dict(os.environ, environment, clear=True), \
                    patch.object(runner_probe.subprocess, 'run') as run, \
                    patch.object(runner_probe.subprocess, 'check_output') as read:
                with self.assertRaisesRegex(ValueError, 'ephemeral GitHub-hosted'):
                    runner_probe.probe('amd64', Path('.'))
                run.assert_not_called()
                read.assert_not_called()

    def test_wrong_daemon_architecture_fails_before_creating_resources(self):
        with patch.dict(os.environ, {'GITHUB_ACTIONS': 'true', 'RUNNER_ENVIRONMENT': 'github-hosted',
                'RUNNER_OS': 'Linux'}, clear=True), patch.object(runner_probe, 'native'), \
                patch.object(runner_probe.subprocess, 'check_output', return_value='aarch64\n'), \
                patch.object(runner_probe.subprocess, 'run') as run:
            with self.assertRaisesRegex(ValueError, 'Docker daemon architecture'):
                runner_probe.probe('amd64', Path('.'))
            run.assert_not_called()

    def test_failed_guest_command_cannot_pass_and_only_owned_resources_are_removed(self):
        def read(command, **kwargs):
            if command[:2] == ['docker', 'info']:
                return 'x86_64\n'
            if command[:2] == ['git', 'rev-parse']:
                return 'b'*40+'\n'
            if command[:2] == ['docker', 'create']:
                return 'c'*64+'\n'
            if command[:2] == ['docker', 'inspect']:
                return 'exited 1\n'
            self.fail('Unexpected probe read')
        with patch.dict(os.environ, {'GITHUB_ACTIONS': 'true', 'RUNNER_ENVIRONMENT': 'github-hosted',
                'RUNNER_OS': 'Linux', 'SHIRUBE_TEST_HEAD': 'a'*40}, clear=True), \
                patch.object(runner_probe, 'native'), \
                patch.object(runner_probe.subprocess, 'check_output', side_effect=read), \
                patch.object(runner_probe.subprocess, 'run') as run:
            with self.assertRaisesRegex(ValueError, 'did not complete successfully'):
                runner_probe.probe('amd64', Path('.'))
            commands = [call.args[0] for call in run.call_args_list]
            self.assertEqual(commands[-2], ['docker', 'rm', '-f', 'c'*64])
            self.assertEqual(commands[-1][:3], ['docker', 'image', 'rm'])
            self.assertRegex(commands[-1][-1], r'^shirube-runner-probe:[0-9a-f]{32}$')
            self.assertFalse(any('prune' in command for command in commands))

    def fixture_probe(self, failure=None):
        import hashlib
        import io
        import json
        import subprocess
        import tarfile
        config = json.dumps({'architecture':'amd64','os':'linux'}).encode()
        image = 'sha256:'+hashlib.sha256(config).hexdigest()
        containers = iter(['c'*64, 'd'*64]); commands = []; archives = []; diagnostics = []
        def read(command, **kwargs):
            commands.append(command)
            if command[:2] == ['docker','info']: return 'x86_64\n'
            if command[:2] == ['git','rev-parse']: return 'b'*40+'\n'
            if command[:2] == ['docker','create']: return next(containers)+'\n'
            if command[:2] == ['docker','inspect']:
                return 'exited 1\n' if failure == 'namespace-state' and command[-1] == 'd'*64 else 'exited 0\n'
            if command[:3] == ['docker','image','inspect']:
                return json.dumps([{'Id':image,'Architecture':'amd64','Os':'linux'}])
            if command[:2] == ['docker','version']: return '29.0.0\n'
            self.fail(str(command))
        def run(command, **kwargs):
            commands.append(command)
            if command[:3] == ['docker','image','save']:
                archive = Path(command[command.index('--output')+1]); archives.append(archive)
                self.assertEqual(command[-1], image)
                with tarfile.open(archive,'w') as out:
                    for name, data in [('manifest.json', json.dumps([{'Config':'config.json'}]).encode()),
                                       ('config.json', config)]:
                        member=tarfile.TarInfo(name);member.size=len(data);out.addfile(member,io.BytesIO(data))
                    if failure == 'archive':
                        member=tarfile.TarInfo('link');member.type=tarfile.SYMTYPE;member.linkname='config.json';out.addfile(member)
            if failure == 'namespace-start' and command == ['docker','start','-a','d'*64]:
                raise subprocess.CalledProcessError(1,command)
        validate = runner_probe.archive_identity
        def validating(*args):
            self.assertTrue(diagnostics, 'Diagnostics must precede validation')
            return validate(*args)
        environment = {'GITHUB_ACTIONS':'true','RUNNER_ENVIRONMENT':'github-hosted',
                       'RUNNER_OS':'Linux','SHIRUBE_TEST_HEAD':'a'*40}
        with patch.dict(os.environ, environment, clear=True), patch.object(runner_probe,'native'), \
             patch.object(runner_probe.subprocess,'check_output',side_effect=read), \
             patch.object(runner_probe.subprocess,'run',side_effect=run), \
             patch.object(runner_probe,'archive_identity',side_effect=validating) as validator, \
             patch('builtins.print',side_effect=lambda *args, **kwargs: diagnostics.append((args,kwargs))):
            if failure:
                with self.assertRaises((ValueError, subprocess.CalledProcessError)):
                    runner_probe.probe('amd64',Path('.'))
                result = None
            else:
                result = runner_probe.probe('amd64',Path('.'))
        validator.assert_called_once()
        self.assertEqual(len(archives),1);self.assertFalse(archives[0].exists())
        self.assertTrue(diagnostics[0][1]['flush'])
        diagnostic=json.loads(diagnostics[0][0][0])['fixture_docker_save']
        self.assertEqual(diagnostic['docker_server_version'],'29.0.0')
        self.assertEqual(diagnostic['image_id'],image)
        self.assertEqual(diagnostic['config_sha256'],image.removeprefix('sha256:'))
        self.assertGreater(diagnostic['archive_bytes'],0)
        self.assertRegex(diagnostic['archive_sha256'],r'^[0-9a-f]{64}$')
        self.assertEqual(diagnostic['member_type_counts'], {'0':2, **({'2':1} if failure=='archive' else {})})
        removed=[c for c in commands if c[:2]==['docker','rm']]
        expected=['c'*64] if failure=='archive' else ['d'*64,'c'*64]
        self.assertEqual(removed,[['docker','rm','-f',c] for c in expected])
        self.assertEqual(commands[-1][:3], ['docker','image','rm'])
        self.assertFalse(any('prune' in c for c in commands))
        creates=[c for c in commands if c[:2]==['docker','create']]
        if failure=='archive':self.assertEqual(len(creates),1)
        else:
            command=creates[1]
            self.assertEqual(command[:12], ['docker','create','--network=none','--log-driver=none',
                '--cap-add=SYS_ADMIN','--cpus=1','--memory=256m','--memory-swap=256m','--pids-limit=256',
                '--label',creates[0][10],'--entrypoint'])
            self.assertEqual(command[12:16], ['sh',image,'-ec',command[-1]])
            self.assertIn('before=$(readlink /proc/self/ns/net)',command[-1])
            self.assertIn('after=$(unshare --net readlink /proc/self/ns/net)',command[-1])
            self.assertIn('test "$before" != "$after"',command[-1])
        return result, image

    def test_saved_fixture_identity_and_capability_only_namespace_success(self):
        result,image=self.fixture_probe()
        saved=result['fixture_docker_save']
        self.assertEqual(saved['config_sha256'],image.removeprefix('sha256:'))
        self.assertEqual(saved['config_member'],'config.json')
        self.assertEqual(saved['status'],'PASS_CAPABILITY_ONLY')
        self.assertEqual(result['sys_admin_network_namespace'],'PASS_CAPABILITY_ONLY')
        self.assertIn('not NetBird reconstruction evidence',result['scope'])

    def test_invalid_archive_logs_diagnostics_before_rejection_and_cleans_up(self):
        self.fixture_probe('archive')

    def test_namespace_failure_cannot_pass_and_cleans_only_owned_resources(self):
        for failure in ['namespace-state','namespace-start']:
            with self.subTest(failure=failure):self.fixture_probe(failure)
