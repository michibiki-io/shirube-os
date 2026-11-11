from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from producer.livebuild_transport import build


class LiveBuildTransportTests(unittest.TestCase):
    def test_only_live_build_resumes_with_completed_workspace_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory)
            root = source/'build'
            for path in ['chroot/var/lib/apt/lists', 'chroot/var/cache/apt/archives', '.build']:
                (root/path).mkdir(parents=True)
            marker = root/'.build/bootstrap'
            marker.write_text('completed')
            command = ['./build-vyos-image', 'generic']
            environment = {'EXISTING': 'same'}
            with patch('producer.livebuild_transport.attempt', side_effect=[(1, 'transient'), (0, '')]) as attempt, \
                    patch('producer.livebuild_transport.recover_install_failure', return_value=True) as recover:
                build(command, cwd=source, env=environment)
                self.assertEqual(marker.read_text(), 'completed')
                self.assertEqual(attempt.call_args_list[0].args, (command, source, environment))
                self.assertEqual(attempt.call_args_list[1].args, (['lb', 'build'], root, environment))
                self.assertEqual(recover.call_args.args[2], root/'chroot')

    def test_unqualified_or_repeated_failures_do_not_restart_the_original_build(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory)
            for path in ['var/lib/apt/lists', 'var/cache/apt/archives']:
                (source/'build/chroot'/path).mkdir(parents=True)
            for recoverable in [False, True]:
                with self.subTest(recoverable=recoverable), \
                        patch('producer.livebuild_transport.attempt', return_value=(1, 'failure')) as attempt, \
                        patch('producer.livebuild_transport.recover_install_failure', return_value=recoverable):
                    with self.assertRaises(subprocess.CalledProcessError):
                        build(['./build-vyos-image'], cwd=source, env={})
                    self.assertEqual(attempt.call_count, 2 if recoverable else 1)
