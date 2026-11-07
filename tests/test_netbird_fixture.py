import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from producer.netbird_fixture import Fixture


class NetbirdFixtureTests(unittest.TestCase):
    def fixture(self, root):
        return Fixture('amd64', 'sha256:'+'a'*64, Path(root)/'private')

    def test_cleanup_refuses_container_with_changed_ownership(self):
        with tempfile.TemporaryDirectory() as root:
            fixture = self.fixture(root)
            fixture.container = 'container-id'
            with patch.object(fixture, 'docker', return_value=b'other-task') as docker:
                with self.assertRaises(RuntimeError):
                    fixture.close()
                self.assertEqual(len(docker.call_args_list), 1)
                self.assertEqual(docker.call_args.args[0], 'inspect')
            self.assertTrue(fixture.work.exists())

    def test_api_credentials_stay_in_private_pipe_and_error_is_redacted(self):
        with tempfile.TemporaryDirectory() as root:
            fixture = self.fixture(root); fixture.token = 'private-test-token'
            with patch('producer.netbird_fixture.subprocess.run') as run:
                run.return_value = subprocess.CompletedProcess([], 1, b'', b'private-test-token')
                with self.assertRaises(RuntimeError) as failure:
                    fixture.request('/api/setup-keys', {'password': 'private-test-password'}, True)
                self.assertNotIn('private-test-token', str(failure.exception))
                args, kwargs = run.call_args
                self.assertNotIn('private-test-token', str(args))
                self.assertNotIn('private-test-password', str(args))
                payload = json.loads(kwargs['input'])
                self.assertEqual(payload['token'], 'private-test-token')
                self.assertEqual(payload['body']['password'], 'private-test-password')
