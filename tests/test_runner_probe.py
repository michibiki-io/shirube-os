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
