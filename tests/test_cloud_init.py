"""Cloud initialization must not introduce shared credentials or leak inputs."""
import ast
import contextlib
import io
from pathlib import Path
import subprocess
import unittest
from types import SimpleNamespace, ModuleType
from unittest.mock import MagicMock, patch

from guest import first_boot
from producer.cloud_init import redact_diagnostics


class CloudInitTests(unittest.TestCase):
    def test_only_installed_admin_with_complete_native_ssh_authentication_is_accepted(self):
        users=['root','vyos','cloudadmin','incomplete','systemaccount','locked']
        prefix=('system','login','user')
        values={prefix+('cloudadmin','authentication','public-keys','instance','key'):'public-key-test-value',
                prefix+('cloudadmin','authentication','public-keys','instance','type'):'ssh-ed25519',
                prefix+('incomplete','authentication','public-keys','instance','key'):'missing-type',
                prefix+('systemaccount','authentication','encrypted-password'):'$6$test$hash',
                prefix+('locked','authentication','encrypted-password'):'!'}
        config=MagicMock()
        config.exists.side_effect=lambda path:any(tuple(path)==key[:len(path)] for key in values)
        config.return_value.side_effect=lambda path:values.get(tuple(path),'')
        config.list_nodes.side_effect=lambda path:users if tuple(path)==prefix else ['instance']
        module=ModuleType('vyos.configtree');module.ConfigTree=lambda text:config
        def account(name):
            return SimpleNamespace(pw_uid=999 if name=='systemaccount' else 1000,pw_shell='/bin/vbash')
        with patch.dict('sys.modules',{'vyos':ModuleType('vyos'),'vyos.configtree':module}), \
             patch.object(first_boot,'Path',return_value=MagicMock()), \
             patch.object(first_boot.pwd,'getpwnam',side_effect=account):
            self.assertEqual(first_boot.configured_administrators(),['cloudadmin'])

    def test_multiline_native_diagnostics_redact_all_interpolated_values(self):
        text = '''logger.debug("User-Data: {}".format(secret))
logger.error(
    "Unable to configure command: {}".format(error))
logger.debug("fixed phase")
'''
        actual = redact_diagnostics(text)
        ast.parse(actual)
        self.assertNotIn('secret', actual)
        self.assertNotIn('.format(', actual)
        self.assertIn('fixed phase', actual)
        self.assertIn('Unable to configure command', actual)

    def test_failed_or_unrun_cloud_config_never_suppresses_console(self):
        for state in ['Result=success\nExecMainStatus=0\nSubState=dead',
                      'Result=exit-code\nExecMainStatus=1\nSubState=failed']:
            with patch.object(first_boot.subprocess, 'run', return_value=
                    subprocess.CompletedProcess([], 0, state)), \
                 patch.object(first_boot, 'configured_administrators') as admins:
                self.assertIsNone(first_boot.cloud_administrator())
                admins.assert_not_called()

    def test_success_without_valid_administrator_falls_back_to_console(self):
        state = 'Result=success\nExecMainStatus=0\nSubState=exited'
        with patch.object(first_boot.subprocess, 'run', return_value=
                subprocess.CompletedProcess([], 0, state)), \
             patch.object(first_boot, 'configured_administrators', return_value=[]):
            self.assertIsNone(first_boot.cloud_administrator())

    def test_cloud_key_administrator_does_not_enter_console_enrollment(self):
        factory = MagicMock()
        enrolled = MagicMock(); enrolled.is_file.return_value = False
        with patch.object(first_boot, 'Path', return_value=factory), \
             patch.object(first_boot, 'ENROLLED', enrolled), \
             patch.object(first_boot, 'MARKER', MagicMock()), \
             patch.object(first_boot, 'wait_for_router'), \
             patch.object(first_boot, 'cloud_administrator', return_value='cloudadmin'), \
             patch.object(first_boot, 'finish_enrollment') as finish, \
             patch('builtins.input', side_effect=AssertionError('Duplicate console enrollment')), \
             contextlib.redirect_stdout(io.StringIO()):
            first_boot.main()
        finish.assert_called_once_with(factory, 'cloudadmin')
