"""Cloud initialization must not introduce shared credentials or leak inputs."""
import ast
import contextlib
import io
import re
import shutil
from pathlib import Path
import subprocess
import tempfile
import unittest
from types import SimpleNamespace, ModuleType
from unittest.mock import MagicMock, patch

from guest import first_boot
from producer.cloud_init import redact_diagnostics
from producer.installer_admin import AUTHENTICATION


class CloudInitTests(unittest.TestCase):
    def test_reboot_requires_new_boot_id_and_retries_a_timed_out_ssh_probe(self):
        from producer.cloud_init_guest import verify
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);state={'reboot':False,'probes':0}
            vm=MagicMock();vm.ssh_port=12345;vm.process.poll.return_value=None
            monitor=(MagicMock(),MagicMock(),{'enrollment_prompt':False})
            def run(command,**kwargs):
                if command[0]=='ssh-keygen':
                    key=Path(command[command.index('-f')+1]);key.write_text('private fixture')
                    key.with_suffix('.pub').write_text('ssh-ed25519 public fixture')
                elif command[0]=='xorriso':
                    Path(command[command.index('-o')+1]).write_bytes(b'seed fixture')
                elif command[0]=='qemu-img':
                    Path(command[-1]).write_bytes(b'overlay fixture')
                elif command[0]=='ssh':
                    self.assertIn('ServerAliveInterval=5',command)
                    if 'systemctl reboot' in command[-1]:state['reboot']=True
                    elif command[-1].startswith('sudo -n python3'):
                        if state['reboot']:
                            state['probes']+=1
                            if state['probes']==1:
                                raise subprocess.TimeoutExpired(command,180)
                        boot='new-boot' if state['probes']>=3 else 'original-boot'
                        return subprocess.CompletedProcess(command,0,'machine-id\nhost-key\n'+boot+'\n','')
                return subprocess.CompletedProcess(command,0,'','')
            with patch('producer.vm.VM',return_value=vm), \
                 patch('producer.cloud_init_guest.monitor_console',return_value=monitor), \
                 patch('producer.cloud_init_guest.subprocess.run',side_effect=run), \
                 patch('producer.cloud_init_guest.time.sleep'):
                self.assertEqual(verify(root/'factory.qcow2','amd64',root/'test','tcg'),'PASS')
            self.assertEqual(state['probes'],3)
            vm.eject_cloud_seed.assert_called_once()
            vm.close.assert_called_once()

    def test_live_install_preserves_persistent_files_only_for_selected_active_config(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);source=root/'config';source.mkdir()
            (source/'config.boot').write_text('native configuration fixture')
            (source/'cloud-file').write_text('persistent userdata fixture')
            (source/'auth').mkdir();(source/'auth/instance.pub').write_text('public fixture')
            (source/'linked-file').symlink_to('cloud-file')
            target=root/'installed';target.mkdir()
            namespace={'Path':Path,'DIR_CONFIG':str(source),'copytree':shutil.copytree,
                       'copy_preserve_owner':shutil.copy2}
            exec(AUTHENTICATION,namespace)
            preserve=namespace['preserve_live_configuration']
            preserve(str(root/'factory-default'),str(target))
            self.assertEqual(list(target.iterdir()),[])
            preserve(str(source/'config.boot'),str(target))
            self.assertEqual((target/'cloud-file').read_text(),'persistent userdata fixture')
            self.assertEqual((target/'auth/instance.pub').read_text(),'public fixture')
            self.assertTrue((target/'linked-file').is_symlink())

    def test_another_saved_cloud_administrator_can_install_with_its_own_key(self):
        marker=MagicMock();marker.read_text.return_value='cloud-admin\n'
        config=MagicMock()
        config.exists.side_effect=lambda path:path[-1]=='public-keys'
        config.list_nodes.return_value=['metadata-key']
        config.return_value.side_effect=lambda path:'ssh-ed25519' if path[-1]=='type' else 'public-test-value'
        namespace={'environ':{'SUDO_USER':'shirube'},'Path':lambda path:marker,
                   'DIR_CONFIG':'/config','ConfigTree':lambda text:config,
                   'read_file':lambda path:'native-config-fixture','re':re}
        exec(AUTHENTICATION,namespace)
        self.assertEqual(namespace['enrolled_administrator'](),('shirube',''))
        namespace['environ']['SUDO_USER']='root'
        with self.assertRaisesRegex(RuntimeError,'administrator session'):
            namespace['enrolled_administrator']()

    def test_only_installed_admin_with_complete_native_ssh_authentication_is_accepted(self):
        users=['root','vyos','cloud-admin','incomplete','systemaccount','locked']
        prefix=('system','login','user')
        values={prefix+('cloud-admin','authentication','public-keys','instance','key'):'public-key-test-value',
                prefix+('cloud-admin','authentication','public-keys','instance','type'):'ssh-ed25519',
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
            self.assertEqual(first_boot.configured_administrators(),['cloud-admin'])

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
