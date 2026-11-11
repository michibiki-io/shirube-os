"""Explicit virtualization/runtime policy; fixtures are not boot evidence."""
import json
import os
import subprocess
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, MagicMock

from producer.architecture import release_accelerator,container_runtime
from producer.guestfs_kernel import appliance_kernel


class ExecutionTests(unittest.TestCase):
    def test_seed_ejection_ignores_qmp_events_and_targets_only_metadata_drive(self):
        from producer.vm import VM
        vm=object.__new__(VM);vm.work=Path('/test-only');vm.cloud_seed=Path('/seed.iso')
        channel=MagicMock()
        channel.readline.side_effect=[b'{"QMP":{}}\n',b'{"return":{},"id":0}\n',
            b'{"event":"DEVICE_TRAY_MOVED","data":{"device":"seed"}}\n',
            b'{"return":{},"id":1}\n']
        peer=MagicMock();peer.makefile.return_value.__enter__.return_value=channel
        with patch('producer.vm.socket.socket') as socket_type:
            socket_type.return_value.__enter__.return_value=peer
            vm.eject_cloud_seed()
        calls=[json.loads(call.args[0]) for call in channel.write.call_args_list]
        self.assertEqual(calls,[{'execute':'qmp_capabilities','id':0},
            {'execute':'eject','arguments':{'device':'seed','force':True},'id':1}])
        self.assertIsNone(vm.cloud_seed)

    def test_bios_has_no_efi_state_and_cannot_be_selected_for_arm64(self):
        from producer.vm import prepare_firmware
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            self.assertEqual(prepare_firmware('amd64','bios',root),[])
            self.assertFalse((root/'nvram.fd').exists())
            for arch,mode in [('arm64','bios'),('amd64','auto'),('unknown','uefi')]:
                with self.assertRaises(ValueError):prepare_firmware(arch,mode,root)

    def test_uefi_keeps_a_fresh_separate_variable_store(self):
        from producer.vm import prepare_firmware
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);code=root/'code';variables=root/'vars'
            code.write_bytes(b'firmware fixture');variables.write_bytes(b'pristine variables')
            work=root/'guest';work.mkdir()
            with patch('producer.vm.FIRMWARE',{'amd64':[(str(code),str(variables))]}):
                args=prepare_firmware('amd64','uefi',work)
            self.assertIn('if=pflash,format=raw,unit=0,readonly=on,file='+str(code),args)
            self.assertIn('if=pflash,format=raw,unit=1,file='+str(work/'nvram.fd'),args)
            (work/'nvram.fd').write_bytes(b'instance changed variables')
            self.assertEqual(variables.read_bytes(),b'pristine variables')

    def test_no_automatic_accelerator_or_runtime_fallback(self):
        self.assertEqual(release_accelerator('amd64','tcg'),'tcg')
        self.assertEqual(release_accelerator('arm64','kvm'),'kvm')
        self.assertEqual(release_accelerator('arm64','tcg'),'tcg')
        for arch,mode in [('arm64','auto'),('amd64','auto'),('unknown','kvm')]:
            with self.assertRaises(ValueError):release_accelerator(arch,mode)
        with patch('subprocess.check_output',return_value=json.dumps({'runc':{}})) as query:
            self.assertEqual(container_runtime('runc',registered=True),'runc')
            with self.assertRaises(ValueError):container_runtime('missing',registered=True)
            self.assertTrue(all(c.args[0][1]=='info' for c in query.call_args_list))
        with self.assertRaises(ValueError):container_runtime('--runtime=other')
        error=subprocess.CalledProcessError(1,['docker','info'],stderr=b'private-context-fixture')
        with patch('subprocess.check_output',side_effect=error):
            with self.assertRaisesRegex(ValueError,'Could not inspect') as failure:
                container_runtime('runc',registered=True)
            self.assertNotIn('private-context-fixture',str(failure.exception))

    def test_preflight_tcg_does_not_require_kvm_and_conflicting_choice_fails(self):
        from producer.cli import main
        argv=['producer.cli','preflight','amd64','--qemu-accelerator','tcg','--memory-gib','3']
        with patch('sys.argv',argv),patch('producer.cli.preflight') as probe:
            main();self.assertFalse(probe.call_args.kwargs['require_kvm'])
        with patch('sys.argv',[value if value!='tcg' else 'kvm' for value in argv]),patch('producer.cli.preflight') as probe:
            main();self.assertTrue(probe.call_args.kwargs['require_kvm'])
        with patch('sys.argv',argv+['--require-kvm']),patch('producer.cli.preflight') as probe:
            with self.assertRaises(ValueError):main()
            probe.assert_not_called()

    def test_appliance_backend_is_explicit_and_environment_is_restored_on_failure(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);packages=root/'packages';packages.mkdir()
            (packages/'linux-image-test_amd64.deb').write_bytes(b'fixture package')
            header=bytearray(0x238);header[0x202:0x206]=b'HdrS';header[0x236]=1
            def unpack(command,**kwargs):
                target=Path(command[-1]);(target/'boot').mkdir(parents=True)
                (target/'boot/vmlinuz-test').write_bytes(header)
                (target/'lib/modules/test').mkdir(parents=True)
            original_is_dir=Path.is_dir
            def directory(path):return path==Path('/lib/modules') or original_is_dir(path)
            previous=dict(os.environ)
            with patch.object(Path,'is_dir',directory),patch('producer.guestfs_kernel.subprocess.check_output',return_value='amd64\n'),patch('producer.guestfs_kernel.subprocess.run',side_effect=unpack):
                with self.assertRaisesRegex(RuntimeError,'fixture failure'):
                    with appliance_kernel(packages,'amd64',root/'appliance',accelerator='tcg') as receipt:
                        self.assertEqual(os.environ['LIBGUESTFS_BACKEND_SETTINGS'],'force_tcg')
                        self.assertEqual(receipt['accelerator'],'tcg')
                        raise RuntimeError('fixture failure')
            self.assertTrue(dict(os.environ)==previous,'Appliance context changed the process environment')
