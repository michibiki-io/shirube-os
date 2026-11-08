"""Enrollment diagnostics must never expose native password/hash output."""
import contextlib
import io
import subprocess
import unittest
from unittest.mock import MagicMock, patch

from guest import first_boot


class FirstBootTests(unittest.TestCase):
    def test_native_failure_reports_only_fixed_phase_and_status(self):
        password = 'not-a-real-instance-password'
        password_hash = '$6$test-only$not-a-real-password-hash'
        factory = MagicMock()
        marker = MagicMock()

        def command(args, **kwargs):
            if args[0] == '/usr/bin/sg':
                return subprocess.CompletedProcess(args, 1,
                    'SHIRUBE_ENROLL_COMMIT\n' + password_hash + '\n' + password,
                    password_hash + '\n' + password)
            return subprocess.CompletedProcess(args, 0)

        output = io.StringIO()
        with patch.object(first_boot, 'Path', return_value=factory), \
             patch.object(first_boot, 'MARKER', marker), \
             patch.object(first_boot, 'wait_for_router'), \
             patch('builtins.input', side_effect=['', 'root', 'instanceadmin']) as name_input, \
             patch.object(first_boot.pwd, 'getpwnam', side_effect=KeyError), \
             patch.object(first_boot.getpass, 'getpass', side_effect=['short', 'different', password, password]), \
             patch.object(first_boot.crypt, 'crypt', return_value=password_hash), \
             patch.object(first_boot.subprocess, 'run', side_effect=command), \
             contextlib.redirect_stdout(output):
            with self.assertRaisesRegex(RuntimeError, 'Native administrator'):
                first_boot.main()
        self.assertEqual(name_input.call_count,3)
        self.assertIn('name cannot be empty',output.getvalue())
        self.assertIn('Please try again.',output.getvalue())
        self.assertIn('failed at commit (exit 1)', output.getvalue())
        self.assertNotIn(password, output.getvalue())
        self.assertNotIn(password_hash, output.getvalue())
        marker.unlink.assert_not_called()
        factory.unlink.assert_not_called()

    def test_saved_enrollment_resumes_login_without_creating_another_user(self):
        factory=MagicMock();marker=MagicMock();marker.exists.return_value=True
        enrolled=MagicMock();enrolled.read_text.return_value='instanceadmin\n'
        with patch.object(first_boot,'Path',return_value=factory), \
             patch.object(first_boot,'MARKER',marker), \
             patch.object(first_boot,'ENROLLED',enrolled), \
             patch.object(first_boot,'wait_for_router'), \
             patch.object(first_boot,'saved_password',return_value='$6$test$already-configured'), \
             patch.object(first_boot.pwd,'getpwnam'), \
             patch.object(first_boot,'enable_logins') as logins, \
             patch('builtins.input',side_effect=AssertionError('Enrollment must not repeat')):
            first_boot.main()
        factory.unlink.assert_called_once_with(missing_ok=True)
        logins.assert_called_once()
        marker.unlink.assert_called_once_with(missing_ok=True)
        enrolled.write_text.assert_not_called()
