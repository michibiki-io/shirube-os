import unittest
from producer.guest_diagnostics import GuestCommandError


class GuestDiagnosticsTests(unittest.TestCase):
    def test_fixed_failure_fields_do_not_retain_secrets_or_console(self):
        secret='PRIVATE-TEST-ENROLLMENT-INPUT'
        output=secret+'\n$6$test$private-hash\nOSError: [Errno 5] private path\n'
        error=GuestCommandError(1,output.replace("\n","\r\n"))
        self.assertEqual(error.diagnostics,{'exit_status':1,'exception_type':'OSError','errno':5})
        self.assertNotIn(secret,repr(error.__dict__)+str(error))
        self.assertNotIn('private-hash',repr(error.__dict__)+str(error))
