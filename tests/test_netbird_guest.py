import unittest
from unittest.mock import Mock
from producer.netbird_guest import rejected_enrollment


class NativeOperationalFailureTests(unittest.TestCase):
    def test_native_zero_exit_requires_explicit_failure_response(self):
        vm=Mock()
        vm.secret_command.return_value='NetBird operation failed; secret details suppressed'
        rejected_enrollment(vm,'unregistered-test-input')
        vm.secret_command.return_value=''
        with self.assertRaisesRegex(ValueError,'did not reject'):
            rejected_enrollment(vm,'unregistered-test-input')
        vm.secret_command.side_effect=RuntimeError('Private guest command failed')
        rejected_enrollment(vm,'unregistered-test-input')
