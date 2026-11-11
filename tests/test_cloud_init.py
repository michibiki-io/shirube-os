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
    def test_factory_install_retains_key_only_administrator_and_key_options_with_ssh(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);target=root/'factory.boot'
            target.write_text('factory configuration')
            saved=MagicMock();saved.exists.return_value=True;saved.list_nodes.return_value=['instance']
            values={'key':'public fixture','type':'ssh-ed25519','options':'no-agent-forwarding'}
            saved.return_value.side_effect=lambda path:values[path[-1]]
            config=MagicMock();config.to_string.return_value='retained configuration'
            namespace={'Path':Path,'DIR_CONFIG':str(root),'ConfigTree':MagicMock(side_effect=[config,saved]),
                       'read_file':lambda path:Path(path).read_text()}
            (root/'config.boot').write_text('saved configuration')
            exec(AUTHENTICATION,namespace)
            namespace['configure_authentication'](str(target),'','cloud-admin')
            prefix=['system','login','user','cloud-admin','authentication','public-keys']
            for field,value in values.items():
                config.set.assert_any_call(prefix+['instance',field],value=value,replace=True)
            config.set_tag.assert_any_call(prefix)
            config.set.assert_any_call(['service','ssh'])
            self.assertEqual(target.read_text(),'retained configuration')

    def test_reboot_requires_new_boot_id_and_retries_admin_and_metadata_timeouts(self):
        from producer.cloud_init_guest import verify
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);state={'reboot':False,'probes':0,'metadata_attempts':0}
            vm=MagicMock();vm.ssh_port=12345;vm.process.poll.return_value=None;vm.process.wait.return_value=0
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
                    elif command[-1]=='true':
                        state['metadata_attempts']+=1
                        if state['metadata_attempts']==1:
                            raise subprocess.TimeoutExpired(['ssh','PRIVATE_TRANSPORT_ARGUMENT'],180,
                                                            stderr='PRIVATE_TRANSPORT_STDERR')
                    elif command[-1].startswith('sudo -n python3'):
                        if state['reboot']:
                            state['probes']+=1
                            if state['probes']==1:
                                raise subprocess.TimeoutExpired(command,180)
                        boot='new-boot' if state['probes']>=3 else 'original-boot'
                        return subprocess.CompletedProcess(command,0,'machine-id\nhost-key\n'+boot+'\n','')
                return subprocess.CompletedProcess(command,0,'','')
            output=io.StringIO()
            with contextlib.redirect_stdout(output), \
                 patch('producer.vm.VM',return_value=vm), \
                 patch('producer.cloud_init_guest.monitor_console',return_value=monitor), \
                 patch('producer.cloud_init_guest.subprocess.run',side_effect=run), \
                 patch('producer.cloud_init_guest.time.sleep'):
                self.assertEqual(verify(root/'factory.qcow2','amd64',root/'test','tcg'),'PASS')
            self.assertEqual(state['probes'],3)
            self.assertEqual(state['metadata_attempts'],3)
            self.assertIn('CLOUD_INIT_SSH_TIMEOUT: initial_metadata_access',output.getvalue())
            self.assertIn('CLOUD_INIT_SSH_TIMEOUT: post_reboot_admin_access',output.getvalue())
            self.assertIn('CLOUD_INIT_SSH_PHASE: shutdown_request',output.getvalue())
            self.assertNotIn('PRIVATE_TRANSPORT_ARGUMENT',output.getvalue())
            self.assertNotIn('PRIVATE_TRANSPORT_STDERR',output.getvalue())
            vm.eject_cloud_seed.assert_called_once()
            vm.close.assert_called_once()

    def _verify_transition(self, overrides=None, same_boot=False, shutdown_status=0,
                           transport_stderr='PRIVATE_GUEST_STDERR', transition_after_requests=1,
                           clock_ticks=None):
        """Exercise the real verifier with a disposable, fully mocked guest."""
        from producer.cloud_init_guest import verify
        overrides=overrides or {}
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);reboot_requests=0;operations=[]
            vm=MagicMock();vm.ssh_port=12345;vm.process.poll.return_value=None;vm.process.wait.return_value=0
            if isinstance(shutdown_status,Exception):vm.process.wait.side_effect=shutdown_status
            else:vm.process.wait.return_value=shutdown_status
            monitor=(MagicMock(),MagicMock(),{'enrollment_prompt':False})
            def run(command,**kwargs):
                nonlocal reboot_requests
                if command[0]=='ssh-keygen':
                    key=Path(command[command.index('-f')+1]);key.write_text('private fixture')
                    key.with_suffix('.pub').write_text('ssh-ed25519 public fixture')
                if command[0]=='ssh':
                    text=command[-1];operations.append(text)
                    if 'systemctl reboot' in text:reboot_requests+=1
                    for match,outcome in overrides.items():
                        if match in text:
                            if isinstance(outcome,list):outcome=outcome.pop(0)
                            if isinstance(outcome,Exception):raise outcome
                            if isinstance(outcome,str):
                                return subprocess.CompletedProcess(command,0,outcome,'')
                            return subprocess.CompletedProcess(command,outcome,
                                'PRIVATE_GUEST_STDOUT',transport_stderr)
                    if 'systemctl show reboot.target' in text:
                        return subprocess.CompletedProcess(command,0,'ActiveState=inactive\nJob=\n','')
                    if text.startswith('sudo -n python3'):
                        boot='new' if reboot_requests>=transition_after_requests and not same_boot else 'original'
                        return subprocess.CompletedProcess(command,0,'machine\nhost\n'+boot+'\n','')
                return subprocess.CompletedProcess(command,0,'','')
            output=io.StringIO()
            # A deterministic deadline bounds the same-boot negative case.
            ticks=iter(range(0,10000,100) if clock_ticks is None else clock_ticks)
            with contextlib.redirect_stdout(output), \
                 patch('producer.vm.VM',return_value=vm), \
                 patch('producer.cloud_init_guest.monitor_console',return_value=monitor), \
                 patch('producer.cloud_init_guest.subprocess.run',side_effect=run), \
                 patch('producer.cloud_init_guest.time.monotonic',side_effect=lambda:next(ticks)), \
                 patch('producer.cloud_init_guest.time.sleep'):
                try:
                    result=verify(root/'factory.qcow2','amd64',root/'test','tcg')
                except RuntimeError as error:
                    result=error
            vm.close.assert_called_once()
            self.assertFalse((root/'test/instance-key').exists())
            return result,output.getvalue(),operations,vm

    def test_rejected_power_requests_fail_immediately_without_leaking_diagnostics(self):
        for action,phase in [('reboot','reboot_request'),('poweroff','shutdown_request')]:
            result,output,operations,vm=self._verify_transition({'systemctl '+action:1})
            self.assertIsInstance(result,RuntimeError)
            self.assertIn(phase,str(result))
            self.assertNotIn('PRIVATE_',output+str(result))
            self.assertEqual(sum('systemctl '+action in text for text in operations),1)
            self.assertIn('systemctl '+action,operations[-1])
            vm.process.wait.assert_not_called()

    def test_uncertain_power_requests_require_observed_transition(self):
        for action in ['reboot','poweroff']:
            for outcome in [255,subprocess.TimeoutExpired(
                    ['ssh','PRIVATE_TRANSPORT_ARGUMENT'],180,
                    output='PRIVATE_GUEST_STDOUT',stderr='PRIVATE_GUEST_STDERR')]:
                with self.subTest(action=action,outcome=type(outcome).__name__):
                    result,output,operations,vm=self._verify_transition(
                        {'systemctl '+action:outcome})
                    self.assertEqual(result,'PASS')
                    self.assertIn('CLOUD_INIT_TRANSITION_UNCERTAIN:',output)
                    self.assertNotIn('PRIVATE_',output)
                    vm.process.wait.assert_called_once_with(timeout=180)
                    if action=='reboot':
                        result,output,operations,vm=self._verify_transition(
                            {'systemctl reboot':outcome},same_boot=True)
                        self.assertIsInstance(result,RuntimeError)
                        self.assertNotIn('PRIVATE_',output+str(result))
                        self.assertFalse(any('poweroff' in text for text in operations))
                    else:
                        result,output,operations,vm=self._verify_transition(
                            {'systemctl poweroff':outcome},shutdown_status=
                            subprocess.TimeoutExpired(['PRIVATE_VM_ARGUMENT'],180))
                        self.assertIsInstance(result,RuntimeError)
                        self.assertNotIn('PRIVATE_',output+str(result))
                        self.assertTrue(result.__suppress_context__)

    def test_unaccepted_uncertain_reboot_retries_after_idle_target_proof(self):
        for initial in [255,subprocess.TimeoutExpired(['PRIVATE_ARG'],180)]:
            result,output,operations,vm=self._verify_transition(
                {'systemctl reboot':[initial,0]},transition_after_requests=2)
            self.assertEqual(result,'PASS')
            self.assertEqual(sum('systemctl reboot' in text for text in operations),2)
            self.assertEqual(sum('systemctl show reboot.target' in text for text in operations),1)
            self.assertNotIn('PRIVATE_',output)

    def test_expired_reboot_deadline_does_not_start_state_query(self):
        # Initial access finishes at t=1. Post-reboot's deadline is t=1202;
        # its probe starts at t=3 but returns the original boot after expiry.
        result,output,operations,vm=self._verify_transition(
            {'systemctl reboot':255},same_boot=True,clock_ticks=[0,1,2,3,1203,1204])
        self.assertIsInstance(result,RuntimeError)
        self.assertIn('before deadline',str(result))
        self.assertEqual(sum('systemctl reboot' in text for text in operations),1)
        self.assertFalse(any('systemctl show reboot.target' in text for text in operations))
        self.assertFalse(any('poweroff' in text for text in operations))

    def test_unaccepted_reboot_is_retried_at_most_twice(self):
        result,output,operations,vm=self._verify_transition({'systemctl reboot':255},same_boot=True)
        self.assertIsInstance(result,RuntimeError)
        self.assertEqual(sum('systemctl reboot' in text for text in operations),3)
        self.assertEqual(sum('systemctl show reboot.target' in text for text in operations),2)

    def test_acknowledged_reboot_is_never_retried(self):
        for responses,count in [(0,1),([255,0],2)]:
            result,output,operations,vm=self._verify_transition(
                {'systemctl reboot':responses},same_boot=True)
            self.assertIsInstance(result,RuntimeError)
            self.assertEqual(sum('systemctl reboot' in text for text in operations),count)
            self.assertEqual(sum('systemctl show reboot.target' in text for text in operations),count-1)

    def test_pending_or_unknown_reboot_state_never_retries(self):
        for state in ['ActiveState=active\nJob=\n','ActiveState=deactivating\nJob=\n',
                'ActiveState=inactive\nJob=123\n','ActiveState=inactive\n',
                'PRIVATE_UNKNOWN_STATE',1,255,subprocess.TimeoutExpired(['PRIVATE_ARG'],180)]:
            result,output,operations,vm=self._verify_transition(
                {'systemctl reboot':255,'systemctl show reboot.target':state},same_boot=True)
            self.assertIsInstance(result,RuntimeError)
            self.assertEqual(sum('systemctl reboot' in text for text in operations),1)
            self.assertNotIn('PRIVATE_',output+str(result))

    def test_transport_diagnostics_log_only_fixed_categories(self):
        for category,diagnostic in [('broken_pipe','Broken pipe'),
                ('connection_closed_by_remote','Connection closed by remote host'),
                ('kex','kex_exchange_identification: PRIVATE_GUEST_STDERR'),
                ('command_not_found','systemctl: command not found')]:
            result,output,operations,vm=self._verify_transition(
                {'systemctl reboot':255},transport_stderr=diagnostic+' PRIVATE_GUEST_STDERR')
            self.assertEqual(result,'PASS')
            self.assertIn('category='+category,output)
            self.assertNotIn('PRIVATE_',output)
            calls=[call[0] for call in vm.mock_calls]
            self.assertLess(calls.index('process.wait'),calls.index('close'))

    def test_acknowledged_shutdown_requires_successful_guest_exit(self):
        for status in [1,subprocess.TimeoutExpired(['PRIVATE_VM_ARGUMENT'],180)]:
            result,output,operations,vm=self._verify_transition(shutdown_status=status)
            self.assertIsInstance(result,RuntimeError)
            self.assertIn('shutdown',str(result))
            self.assertNotIn('PRIVATE_',output+str(result))
            vm.process.wait.assert_called_once_with(timeout=180)

    def test_acknowledged_reboot_with_unchanged_boot_id_never_passes(self):
        result,output,operations,vm=self._verify_transition(same_boot=True)
        self.assertIsInstance(result,RuntimeError)
        self.assertIn('before deadline',str(result))
        self.assertFalse(any('poweroff' in text for text in operations))

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
